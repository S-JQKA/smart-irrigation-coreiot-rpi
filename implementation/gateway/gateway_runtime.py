"""ACK-driven SmartFarm Gateway runtime for HIL and Raspberry Pi hardware."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from analytics_hook import NoOpAnalyticsHook, safe_evaluate
from coreiot.gateway_client import GatewayClient
from coreiot.gateway_protocol import RpcCommand
from hardware_adapter import ActuatorAck, PeerStatus, SensorSample
from runtime_state import FieldConfigStore, PersistentCommandLedger, clock_is_ready, runtime_state_dir
from simulator_v22 import load_config, settings_from_env, utc_ms
from simulator_v23 import (
    FIELD_CONFIGURATION_ATTRIBUTES,
    LOCAL_SCHEDULE_ATTRIBUTE,
    SHARED_ATTRIBUTE_WATCHES,
    LocalScheduleRunner,
    SimulationModelV23,
    apply_field_configuration_attribute,
    apply_local_schedule_attribute,
    apply_rpc_with_authority,
    effective_field_config,
    restore_field_configuration,
)
from uart_espnow_adapter import UartEspNowAdapter


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "devices.v23.hil-field1.json"
LOG = logging.getLogger("smartfarm.gateway.runtime")


def _restore_schedules(
    config: dict[str, Any],
    path: Path,
    now_ms: int,
) -> dict[str, Any]:
    if not path.exists():
        return config
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOG.warning("Ignoring unreadable schedule state path=%s", path, exc_info=True)
        return config
    restored: list[dict[str, Any]] = []
    for item in payload.get("localSchedules", []):
        if not isinstance(item, dict):
            continue
        start_at_ms = item.get("startAtMs")
        if not isinstance(start_at_ms, int) or isinstance(start_at_ms, bool):
            continue
        repeat_every = int(item.get("repeatEverySeconds") or 0)
        if start_at_ms < now_ms and repeat_every > 0:
            interval_ms = repeat_every * 1000
            missed = ((now_ms - start_at_ms) // interval_ms) + 1
            start_at_ms += missed * interval_ms
        if start_at_ms < now_ms:
            LOG.info("Ignoring stale one-shot schedule id=%s", item.get("id"))
            continue
        restored.append({
            "id": item.get("id"),
            "zoneId": item.get("zoneId"),
            "configId": item.get("configId"),
            "enabled": item.get("enabled", True),
            "startAfterSeconds": max(0, int((start_at_ms - now_ms) / 1000)),
            "durationSeconds": item.get("durationSeconds"),
            "repeatEverySeconds": repeat_every or None,
        })
    return {**config, "localSchedules": restored}


def _rpc_detail(model: SimulationModelV23, command: RpcCommand) -> str | None:
    target = next((zone for zone in model.zones.values() if zone.valve_device == command.device), None)
    if target is not None:
        detail = target.decision_reason or target.safety_block_reason
        return None if detail in {"", "NONE"} else detail
    detail = model.site.safety_block_reason
    return None if detail in {"", "NONE"} else detail


def run(
    config_path: Path,
    *,
    offline: bool = False,
    ticks: int | None = None,
    interval: float | None = None,
) -> None:
    config = load_config(config_path)
    runtime_profile = str(config.get("runtimeProfile", "")).upper()
    if runtime_profile not in {"HIL_FIELD1_3BOARD", "HARDWARE_TWO_FIELD"}:
        raise ValueError("gateway_runtime.py requires HIL_FIELD1_3BOARD or HARDWARE_TWO_FIELD")
    if str(config.get("controlAuthority", "LOCAL")).upper() != "LOCAL":
        raise ValueError("hardware runtime requires LOCAL authority for every Field")
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("hardware runtime configuration is required")
    serial_port_env = str(runtime.get("serialPortEnv", "SMARTFARM_SERIAL_PORT")).strip()
    serial_port = os.getenv(serial_port_env, str(runtime.get("serialPort", ""))).strip()
    mapped_devices = tuple(str(value) for value in runtime.get("mappedDevices", []))
    if not serial_port or not mapped_devices:
        raise ValueError("runtime.serialPort and runtime.mappedDevices are required")

    state_directory = runtime_state_dir(config_path, runtime_profile)
    schedule_path = state_directory / "schedules.json"
    now_ms = utc_ms()
    config = _restore_schedules(config, schedule_path, now_ms)
    model = SimulationModelV23.from_config(config)
    if interval is not None:
        model.interval_seconds = interval
    expected = runtime.get("expectedSensors", {})
    minimum = runtime.get("minValidSensors", {})
    hardware_zones = tuple(str(value) for value in runtime.get("hardwareZones", model.zones))
    if not hardware_zones or any(zone_id not in model.zones for zone_id in hardware_zones):
        raise ValueError("runtime.hardwareZones contains an unknown Field")
    adapter = UartEspNowAdapter(
        serial_port,
        hardware_zones,
        runtime_profile=runtime_profile,
        baudrate=int(runtime.get("baudrate", 115_200)),
    )
    model.attach_hardware_adapter(
        adapter,
        expected_sensors=expected if isinstance(expected, dict) else {},
        min_valid_sensors=minimum if isinstance(minimum, dict) else {},
    )
    field_store = FieldConfigStore(state_directory / "field-config.json")
    restore_field_configuration(model, field_store.load(), now_ms)
    scheduler = LocalScheduleRunner.from_config(config, model, now_ms)
    require_ntp = bool(runtime.get("requireNtpSync", True))
    is_clock_ready = lambda: clock_is_ready(require_ntp=require_ntp)  # noqa: E731
    analytics = NoOpAnalyticsHook()

    profile_by_device = {
        item["name"]: item.get("profile")
        for item in config["devices"]
        if item["name"] in mapped_devices
    }
    peer_device_map = {
        str(peer): tuple(str(device) for device in devices if str(device) in profile_by_device)
        for peer, devices in runtime.get("peerDeviceMap", {}).items()
        if isinstance(devices, list)
    }
    peer_last_seen: dict[str, int] = {}
    connected_devices: set[str] = set()
    latest_readings: dict[str, list[dict[str, Any]]] = {}
    lease_renewed_at: dict[str, int] = {}

    def handle_rpc(command: RpcCommand) -> tuple[bool, str, str]:
        if command.method == "SET_LOCAL_SCHEDULE":
            if not is_clock_ready():
                return False, "OFF", "INVALID_COMMAND"
            result = scheduler.update_from_rpc(model, command, utc_ms())
            if result[0]:
                scheduler.persist(schedule_path)
            return result
        return apply_rpc_with_authority(model, command, "LOCAL", {})

    def handle_attributes(body: dict[str, Any]) -> None:
        applied = apply_field_configuration_attribute(model, body, utc_ms(), field_store)
        if applied is not None:
            accepted, reason = applied
            (LOG.info if accepted else LOG.warning)(
                "field configuration device=%s accepted=%s result=%s effective=%s",
                body.get("device"),
                accepted,
                reason,
                effective_field_config(
                    model,
                    next(
                        zone.zone_id
                        for zone in model.zones.values()
                        if zone.valve_device == body.get("device")
                    ),
                ) if any(zone.valve_device == body.get("device") for zone in model.zones.values()) else {},
            )
        contains_schedule = any(
            LOCAL_SCHEDULE_ATTRIBUTE in scope
            for scope in (
                body,
                body.get("data", {}) if isinstance(body.get("data"), dict) else {},
                body.get("shared", {}) if isinstance(body.get("shared"), dict) else {},
                body.get("values", {}) if isinstance(body.get("values"), dict) else {},
            )
        ) or ("value" in body and len(SHARED_ATTRIBUTE_WATCHES) == 1)
        if not contains_schedule:
            return
        if not is_clock_ready():
            LOG.warning("schedule attribute rejected device=%s reason=CLOCK_NOT_READY", body.get("device"))
            return
        result = apply_local_schedule_attribute(scheduler, model, body, utc_ms())
        if result is not None and result[0] and result[2] != "DUPLICATE":
            scheduler.persist(schedule_path)

    client: GatewayClient | None = None

    def publish_snapshot() -> None:
        if client is not None and latest_readings:
            client.publish_telemetry(latest_readings)

    if not offline:
        client = GatewayClient(
            settings_from_env(config),
            handle_rpc,
            attribute_handler=handle_attributes,
            connection_handler=model.set_cloud_connected,
            legacy_manual_off_allowed=lambda command: False,
            command_ledger=PersistentCommandLedger(state_directory / "command-ledger.json"),
            clock_ready=is_clock_ready,
            rpc_detail_handler=lambda command: _rpc_detail(model, command),
            reconnect_snapshot_handler=publish_snapshot,
        )
        client.start()
        for zone in model.zones.values():
            if zone.valve_device in mapped_devices:
                client.watch_shared_attributes(zone.valve_device, SHARED_ATTRIBUTE_WATCHES)
    else:
        model.set_cloud_connected(False, now_ms=now_ms - int(model.cloud_loss_timeout_seconds * 1000))

    adapter.start()
    startup_acks = adapter.all_off("gateway-startup-all-off")
    for ack in startup_acks:
        model.ingest_hardware_event(ack)
    if not startup_acks or not all(ack.accepted for ack in startup_acks):
        LOG.warning("Central did not confirm startup ALL_OFF; runtime remains SAFE-IDLE")

    stopped = False

    def stop_handler(signum: int, frame: Any) -> None:
        del signum, frame
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    iteration = 0
    try:
        while not stopped and (ticks is None or iteration < ticks):
            iteration += 1
            now_ms = utc_ms()
            for event in adapter.poll():
                model.ingest_hardware_event(event)
                peer_id = event.node_id if isinstance(event, SensorSample) else event.peer_id if isinstance(event, PeerStatus) else "central"
                peer_last_seen[peer_id] = now_ms
                if client is not None:
                    for device in peer_device_map.get(peer_id, ()):
                        if device not in connected_devices:
                            client.connect_device(device, profile_by_device.get(device))
                            connected_devices.add(device)
            for zone_id, zone in model.zones.items():
                if zone_id not in hardware_zones or zone.valve_state != "ON":
                    lease_renewed_at.pop(zone_id, None)
                    continue
                if now_ms - lease_renewed_at.get(zone_id, 0) < 5_000:
                    continue
                ack = adapter.set_zone(
                    zone_id,
                    "ON",
                    zone.last_command_id,
                    lease_ms=15_000,
                )
                model.ingest_hardware_event(ack)
                lease_renewed_at[zone_id] = now_ms
                if not ack.accepted:
                    LOG.error("Central lease renewal failed zone=%s reason=%s", zone_id, ack.reason)
                    model._set_valve(
                        zone_id,
                        "OFF",
                        "CONTROLLER_OFFLINE",
                        now_ms,
                        f"lease-fail-off-{zone_id}-{now_ms}",
                    )
            if is_clock_ready():
                scheduler.dispatch_due(model, now_ms)
            latest_readings = model.tick(timestamp=now_ms, local_decision=True)
            latest_readings = {
                device: samples
                for device, samples in latest_readings.items()
                if device in mapped_devices
            }
            stale_peers = {
                peer_id
                for peer_id, last_seen in peer_last_seen.items()
                if now_ms - last_seen > 300_000
            }
            if client is not None:
                for peer_id in stale_peers:
                    for device in peer_device_map.get(peer_id, ()):
                        if device in connected_devices:
                            client.disconnect_device(device)
                            connected_devices.remove(device)
                if latest_readings:
                    client.publish_telemetry(latest_readings)
                diagnostic = model.gateway_health(
                    len(connected_devices),
                    client.buffer_depth,
                    client.expired_buffer_count,
                )
                diagnostic["values"]["clockReady"] = is_clock_ready()
                client.publish_gateway_telemetry(diagnostic)
            analytics_result = safe_evaluate(
                analytics,
                {"ts": now_ms, "readings": latest_readings, "systemMode": model.site.system_mode},
            )
            LOG.info(
                "tick=%s profile=%s mappedOnline=%s controller=%s mode=%s analytics=%s",
                iteration,
                runtime_profile,
                len(connected_devices),
                model.controller_state,
                model.site.system_mode,
                analytics_result.reason,
            )
            if ticks is None or iteration < ticks:
                time.sleep(model.interval_seconds)
    finally:
        try:
            adapter.all_off("gateway-shutdown-all-off")
        finally:
            adapter.stop()
        if client is not None:
            client.disconnect_devices(tuple(connected_devices))
            client.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartFarm ACK-driven HIL/hardware Gateway")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--ticks", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run(args.config, offline=args.offline, ticks=args.ticks, interval=args.interval)
