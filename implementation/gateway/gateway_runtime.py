"""ACK-driven SmartFarm Gateway for physical sensors and Central outputs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from copy import deepcopy
from queue import Empty, Queue
from pathlib import Path
from typing import Any

from analytics_hook import AdvisoryAnalyticsRunner
from anomaly_detector import (
    build_analytics_hook as _build_analytics_hook,
    merge_analytics_telemetry as _merge_analytics_telemetry,
)
from coreiot.gateway_client import GatewayClient
from coreiot.gateway_protocol import RpcCommand
from hardware_adapter import PeerStatus, SensorSample
from runtime_state import FieldConfigStore, PersistentCommandLedger, clock_is_ready, runtime_state_dir
from configuration import load_config, settings_from_env, utc_ms
from field_configuration import FieldConfigurationFeedback
from irrigation_controller import (
    LOCAL_SCHEDULE_ATTRIBUTE,
    SHARED_ATTRIBUTE_WATCHES,
    LocalScheduleRunner,
    IrrigationController,
    apply_local_schedule_attribute,
    apply_rpc_with_authority,
    effective_field_config,
    restore_field_configuration,
)
from uart_espnow_adapter import UartEspNowAdapter


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "hardware.example.json"
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
    if not isinstance(payload, dict) or not isinstance(payload.get("localSchedules", []), list):
        LOG.warning("Ignoring malformed schedule state path=%s", path)
        return config
    restored: list[dict[str, Any]] = []
    stale_one_shots: list[dict[str, Any]] = []
    for item in payload.get("localSchedules", []):
        if not isinstance(item, dict):
            continue
        start_at_ms = item.get("startAtMs")
        if not isinstance(start_at_ms, int) or isinstance(start_at_ms, bool):
            continue
        schedule_id = item.get("id")
        zone_id = item.get("zoneId")
        duration = item.get("durationSeconds")
        repeat_every = item.get("repeatEverySeconds") or 0
        enabled = item.get("enabled", True)
        if (
            not isinstance(schedule_id, str)
            or not schedule_id
            or not isinstance(zone_id, str)
            or not zone_id
            or not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration < 1
            or not isinstance(repeat_every, int)
            or isinstance(repeat_every, bool)
            or repeat_every < 0
            or (repeat_every and repeat_every < duration)
            or not isinstance(enabled, bool)
        ):
            continue
        if start_at_ms < now_ms and repeat_every > 0:
            interval_ms = repeat_every * 1000
            missed = ((now_ms - start_at_ms) // interval_ms) + 1
            start_at_ms += missed * interval_ms
        if start_at_ms < now_ms:
            LOG.info("Ignoring stale one-shot schedule id=%s", item.get("id"))
            stale_one_shots.append(dict(item))
            continue
        restored.append({
            "id": schedule_id,
            "zoneId": zone_id,
            "configId": item.get("configId"),
            "enabled": enabled,
            "startAfterSeconds": max(0, int((start_at_ms - now_ms) / 1000)),
            "durationSeconds": duration,
            "repeatEverySeconds": repeat_every or None,
        })
    return {
        **config,
        "localSchedules": restored,
        "_staleOneShotSchedules": stale_one_shots,
    }


def _load_schedule_runner(
    config: dict[str, Any],
    path: Path,
    model: IrrigationController,
    now_ms: int,
) -> LocalScheduleRunner:
    restored = _restore_schedules(config, path, now_ms)
    if "_staleOneShotSchedules" in restored:
        valid_schedules: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in restored.get("localSchedules", []):
            zone_id = item.get("zoneId")
            schedule_id = item.get("id")
            if zone_id not in model.zones or schedule_id in seen_ids:
                continue
            maximum = min(
                model.manual_on_max_seconds,
                int(model._limits_for(zone_id).max_duration_seconds),
            )
            if int(item.get("durationSeconds", 0)) > maximum:
                continue
            seen_ids.add(schedule_id)
            valid_schedules.append(item)
        restored = {**restored, "localSchedules": valid_schedules}
    runner = LocalScheduleRunner.from_config(restored, model, now_ms)
    for item in restored.get("_staleOneShotSchedules", []):
        zone = model.zones.get(str(item.get("zoneId", "")))
        if zone is None:
            continue
        runner.update_from_config(
            model,
            zone.valve_device,
            {
                "scheduleId": item.get("id"),
                "enabled": True,
                "startAtMs": item.get("startAtMs"),
                "durationSeconds": item.get("durationSeconds"),
                "repeatEverySeconds": 0,
            },
            str(item.get("configId") or f"restored-{item.get('id', 'schedule')}"),
            now_ms,
        )
    return runner


def _rpc_detail(model: IrrigationController, command: RpcCommand) -> str | None:
    target = next((zone for zone in model.zones.values() if zone.valve_device == command.device), None)
    if target is not None:
        detail = target.decision_reason or target.safety_block_reason
        return None if detail in {"", "NONE"} else detail
    detail = model.site.safety_block_reason
    return None if detail in {"", "NONE"} else detail


def _enforce_hardware_zone_modes(
    model: IrrigationController,
    hardware_zones: tuple[str, ...],
) -> None:
    """Keep non-physical Fields disabled even when persisted state is restored."""

    for zone_id, zone in model.zones.items():
        if zone_id not in hardware_zones:
            zone.control_mode = "DISABLED"


def _validate_runtime_mapping(
    config: dict[str, Any],
    runtime: dict[str, Any],
    runtime_profile: str,
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str], dict[str, tuple[str, ...]]]:
    """Reject profiles that could falsely announce logical devices as physical."""

    devices = config.get("devices", [])
    known_devices = {
        str(item.get("name")): str(item.get("zoneId", ""))
        for item in devices
        if isinstance(item, dict) and item.get("name")
    }
    mapped_devices = tuple(str(value) for value in runtime.get("mappedDevices", []))
    hardware_zones = tuple(str(value) for value in runtime.get("hardwareZones", []))
    control = config.get("control", {})
    raw_concurrency = control.get("maxConcurrentZones", 1) if isinstance(control, dict) else 1
    if (
        not isinstance(raw_concurrency, int)
        or isinstance(raw_concurrency, bool)
        or raw_concurrency not in {1, 2}
    ):
        raise ValueError("control.maxConcurrentZones must be 1 or 2")
    if (
        not mapped_devices
        or len(set(mapped_devices)) != len(mapped_devices)
        or any(device not in known_devices for device in mapped_devices)
    ):
        raise ValueError("runtime.mappedDevices must contain unique configured devices")
    if not hardware_zones or len(set(hardware_zones)) != len(hardware_zones):
        raise ValueError("runtime.hardwareZones must contain unique Fields")
    if raw_concurrency > len(hardware_zones):
        raise ValueError("control.maxConcurrentZones exceeds mapped hardware Fields")

    raw_peer_map = runtime.get("peerDeviceMap", {})
    if not isinstance(raw_peer_map, dict) or not raw_peer_map:
        raise ValueError("runtime.peerDeviceMap is required")
    peer_device_map: dict[str, tuple[str, ...]] = {}
    owners: dict[str, str] = {}
    for peer, raw_devices in raw_peer_map.items():
        peer_id = str(peer)
        if not peer_id or not isinstance(raw_devices, list):
            raise ValueError("runtime.peerDeviceMap entries must be device lists")
        peer_devices = tuple(str(device) for device in raw_devices)
        for device in peer_devices:
            if device in owners:
                raise ValueError(f"mapped device has multiple physical owners: {device}")
            owners[device] = peer_id
        peer_device_map[peer_id] = peer_devices
    if set(owners) != set(mapped_devices):
        raise ValueError("peerDeviceMap must own every mapped device exactly once")

    raw_sensor_nodes = runtime.get("sensorNodeZones", {})
    if not isinstance(raw_sensor_nodes, dict) or not raw_sensor_nodes:
        raise ValueError("runtime.sensorNodeZones is required")
    sensor_node_zones = {str(key): str(value) for key, value in raw_sensor_nodes.items()}
    if any(node not in peer_device_map for node in sensor_node_zones):
        raise ValueError("every sensor node must have a peerDeviceMap entry")
    if set(sensor_node_zones.values()) != set(hardware_zones):
        raise ValueError("every hardware Field requires a mapped Sensor Node")

    if runtime_profile != "HARDWARE_TWO_FIELD":
        raise ValueError("product runtime requires HARDWARE_TWO_FIELD")
    if set(hardware_zones) != {"field-1", "field-2"} or set(mapped_devices) != set(known_devices):
        raise ValueError("physical runtime requires the complete two-Field topology")
    if len(sensor_node_zones) != 2:
        raise ValueError("physical runtime requires two Sensor Nodes")
    for device, owner in owners.items():
        profile = next(item.get("profile", "") for item in devices if item["name"] == device)
        if any(kind in profile for kind in ("Water Meter", "Smart Valve", "Pump Controller", "Manifold Controller")):
            if owner != "central":
                raise ValueError("hydraulics and actuator devices must be owned by Central")
        elif owner not in sensor_node_zones or sensor_node_zones[owner] != known_devices[device]:
            raise ValueError("soil and environment devices must belong to their Field Sensor Node")

    return mapped_devices, hardware_zones, sensor_node_zones, peer_device_map


def run(
    config_path: Path,
    *,
    offline: bool = False,
    ticks: int | None = None,
    interval: float | None = None,
    advisory_only: bool = False,
) -> None:
    config = load_config(config_path)
    if "simulation" in config or not isinstance(config.get("control"), dict):
        raise ValueError("product runtime requires control configuration without simulation inputs")
    runtime_profile = str(config.get("runtimeProfile", "")).upper()
    if runtime_profile not in {
        "HARDWARE_TWO_FIELD",
    }:
        raise ValueError(
            "gateway_runtime.py requires HARDWARE_TWO_FIELD"
        )
    if str(config.get("controlAuthority", "LOCAL")).upper() != "LOCAL":
        raise ValueError("hardware runtime requires LOCAL authority for every Field")
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("hardware runtime configuration is required")
    serial_port_env = str(runtime.get("serialPortEnv", "SMARTFARM_SERIAL_PORT")).strip()
    serial_port = os.getenv(serial_port_env, str(runtime.get("serialPort", ""))).strip()
    if not serial_port:
        raise ValueError("runtime.serialPort is required")
    mapped_devices, hardware_zones, sensor_node_zones, peer_device_map = (
        _validate_runtime_mapping(config, runtime, runtime_profile)
    )

    state_directory = runtime_state_dir(config_path, runtime_profile)
    schedule_path = state_directory / "schedules.json"
    now_ms = utc_ms()
    model = IrrigationController.from_config(config)
    if interval is not None:
        model.interval_seconds = interval
    expected = runtime.get("expectedSensors", {})
    minimum = runtime.get("minValidSensors", {})
    agreement_tolerance = runtime.get("sensorAgreementTolerancePct", {})
    if any(zone_id not in model.zones for zone_id in hardware_zones):
        raise ValueError("runtime.hardwareZones contains an unknown Field")
    adapter = UartEspNowAdapter(
        serial_port,
        hardware_zones,
        runtime_profile=runtime_profile,
        baudrate=int(runtime.get("baudrate", 115_200)),
        sensor_node_zones=sensor_node_zones,
    )
    if runtime_profile == "HARDWARE_TWO_FIELD" and runtime.get("finalHardwareVerified") is True:
        adapter.evidence_class = "FINAL-HARDWARE"
    model.attach_hardware_adapter(
        adapter,
        expected_sensors=expected if isinstance(expected, dict) else {},
        min_valid_sensors=minimum if isinstance(minimum, dict) else {},
        sensor_agreement_tolerance_pct=(
            agreement_tolerance if isinstance(agreement_tolerance, dict) else {}
        ),
    )
    model.required_config_zones = set(hardware_zones)
    field_store = FieldConfigStore(state_directory / "field-config.json")
    restore_field_configuration(model, field_store.load(), now_ms)
    # A state directory may outlive a profile change.  Never let an old
    # last-known-valid config re-enable a Field that has no physical zone in
    # the active hardware mapping.
    _enforce_hardware_zone_modes(model, hardware_zones)
    field_feedback = FieldConfigurationFeedback(model, field_store)
    attribute_inbox: Queue = Queue(maxsize=128)
    if advisory_only:
        for zone in model.zones.values():
            zone.control_mode = "DISABLED"
        LOG.info("advisory-only mode: RPC/attributes disabled and all Fields locked DISABLED")
    require_ntp = bool(runtime.get("requireNtpSync", True))
    is_clock_ready = lambda: clock_is_ready(require_ntp=require_ntp)  # noqa: E731
    schedule_loaded = is_clock_ready()
    scheduler = (
        _load_schedule_runner(config, schedule_path, model, now_ms)
        if schedule_loaded
        else LocalScheduleRunner.from_config({**config, "localSchedules": []}, model, now_ms)
    )
    analytics = AdvisoryAnalyticsRunner(_build_analytics_hook(config, mapped_devices))

    profile_by_device = {
        item["name"]: item.get("profile")
        for item in config["devices"]
        if item["name"] in mapped_devices
    }
    peer_last_seen: dict[str, int] = {}
    connected_devices: set[str] = set()
    latest_readings: dict[str, list[dict[str, Any]]] = {}
    lease_renewed_at: dict[str, int] = {}

    def handle_rpc(command: RpcCommand) -> tuple[bool, str, str]:
        if advisory_only:
            target = next(
                (zone for zone in model.zones.values() if zone.valve_device == command.device),
                None,
            )
            return False, target.valve_state if target is not None else "OFF", "ADVISORY_ONLY"
        if command.method == "SET_LOCAL_SCHEDULE":
            if not is_clock_ready():
                return False, "OFF", "INVALID_COMMAND"
            result = scheduler.update_from_rpc(model, command, utc_ms())
            if result[0]:
                scheduler.persist(schedule_path)
            return result
        return apply_rpc_with_authority(model, command, "LOCAL", {})

    def handle_attributes(body: dict[str, Any]) -> None:
        if advisory_only:
            LOG.info(
                "attribute ignored device=%s reason=ADVISORY_ONLY",
                body.get("device"),
            )
            return
        target_device = str(body.get("device", ""))
        if target_device not in mapped_devices:
            LOG.warning(
                "attribute ignored device=%s reason=DEVICE_NOT_MAPPED profile=%s",
                target_device,
                runtime_profile,
            )
            return
        applied = field_feedback.apply(body, utc_ms())
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
        raw_value = body.get("value")
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError:
                raw_value = None
        contains_schedule = any(
            LOCAL_SCHEDULE_ATTRIBUTE in scope
            for scope in (
                body,
                body.get("data", {}) if isinstance(body.get("data"), dict) else {},
                body.get("shared", {}) if isinstance(body.get("shared"), dict) else {},
                body.get("values", {}) if isinstance(body.get("values"), dict) else {},
            )
        ) or (
            isinstance(raw_value, dict)
            and raw_value.get("schemaVersion") == 1
            and "scheduleId" in raw_value
        )
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
            try:
                client.submit_telemetry(latest_readings)
            except ValueError:
                LOG.warning("Invalid telemetry snapshot rejected; local control continues", exc_info=True)

    adapter.start()
    startup_acks = adapter.all_off(f"gateway-startup-all-off-{time.time_ns()}")
    for ack in startup_acks:
        model.ingest_hardware_event(ack)
        if not ack.accepted and ack.zone_id in model.zone_runtime:
            model.zone_runtime[ack.zone_id].off_reassert_pending = True
            model.zone_runtime[ack.zone_id].off_reassert_last_ms = now_ms
    startup_state = adapter.query_state()
    startup_outputs_off = (
        startup_state.get("pump") == "OFF"
        and set(startup_state.get("confirmedZones", ())) >= set(hardware_zones)
        and all(state == "OFF" for state in startup_state.get("zones", {}).values())
    )
    model.startup_safe_confirmed = bool(
        startup_acks
        and all(ack.accepted for ack in startup_acks)
        and startup_outputs_off
    )
    if (
        not startup_acks
        or not all(ack.accepted for ack in startup_acks)
        or not startup_outputs_off
    ):
        LOG.warning("Central did not confirm startup ALL_OFF; runtime remains SAFE-IDLE")

    try:
        if not offline:
            client = GatewayClient(
                settings_from_env(config),
                handle_rpc,
                attribute_handler=lambda body: attribute_inbox.put_nowait(deepcopy(body)),
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
            model.set_cloud_connected(
                False,
                now_ms=now_ms - int(model.cloud_loss_timeout_seconds * 1000),
            )
    except Exception:
        adapter.stop()
        raise

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
            # Apply configuration on the control thread, never in the MQTT callback.
            for _ in range(128):
                try:
                    body = attribute_inbox.get_nowait()
                except Empty:
                    break
                if isinstance(body, dict):
                    handle_attributes(body)
            if not schedule_loaded and is_clock_ready():
                scheduler = _load_schedule_runner(config, schedule_path, model, now_ms)
                schedule_loaded = True
                LOG.info("Clock synchronized; persisted schedules loaded against valid time")
            for event in adapter.poll():
                model.ingest_hardware_event(event)
                peer_id = event.node_id if isinstance(event, SensorSample) else event.peer_id if isinstance(event, PeerStatus) else "central"
                peer_last_seen[peer_id] = now_ms
                if client is not None:
                    for device in peer_device_map.get(peer_id, ()):
                        if device not in connected_devices:
                            client.connect_device(device, profile_by_device.get(device))
                            connected_devices.add(device)
            if not model.startup_safe_confirmed:
                observed = adapter.query_state()
                model.startup_safe_confirmed = (
                    model.controller_state == "ONLINE"
                    and observed.get("pump") == "OFF"
                    and set(observed.get("confirmedZones", ())) >= set(hardware_zones)
                    and all(state == "OFF" for state in observed.get("zones", {}).values())
                )
            for zone_id, runtime_state in model.zone_runtime.items():
                if zone_id not in hardware_zones or not runtime_state.off_reassert_pending:
                    continue
                if now_ms - runtime_state.off_reassert_last_ms < 3_000:
                    continue
                runtime_state.off_reassert_last_ms = now_ms
                stopped_ok = model._set_valve(
                    zone_id,
                    "OFF",
                    "OFF_REASSERT",
                    now_ms,
                    f"off-reassert-{zone_id}-{now_ms}",
                    force_hardware=True,
                )
                if not stopped_ok:
                    LOG.error("Central OFF reassert failed zone=%s", zone_id)
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
            field_feedback.annotate(latest_readings)
            latest_readings = {
                device: samples
                for device, samples in latest_readings.items()
                if device in mapped_devices
            }
            analytics_result = analytics.status()
            _merge_analytics_telemetry(latest_readings, analytics_result.telemetry)
            stale_peers = {
                peer_id
                for peer_id, last_seen in peer_last_seen.items()
                if now_ms - last_seen > 300_000
            }
            if client is not None:
                client.poll_telemetry_delivery()
                for peer_id in stale_peers:
                    for device in peer_device_map.get(peer_id, ()):
                        if device in connected_devices:
                            client.disconnect_device(device)
                            connected_devices.remove(device)
                if latest_readings:
                    publish_snapshot()
                diagnostic = model.gateway_health(
                    len(connected_devices),
                    client.buffer_depth,
                    client.expired_buffer_count,
                )
                diagnostic["values"]["clockReady"] = is_clock_ready()
                diagnostic["values"].update(
                    telemetryPublishCount=client.telemetry_publish_count,
                    telemetryPubackCount=client.telemetry_puback_count,
                    telemetryDeliveryFailures=client.telemetry_delivery_failures,
                    lastTelemetryPubackMs=client.last_telemetry_puback_ms,
                )
                client.publish_gateway_telemetry(diagnostic)
            analytics.submit(
                {"ts": now_ms, "readings": latest_readings, "systemMode": model.site.system_mode}
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
        analytics.close()
        adapter.stop()
        if client is not None:
            client.disconnect_devices(tuple(connected_devices))
            client.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartFarm physical-sensor Gateway")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--advisory-only",
        action="store_true",
        help="publish telemetry/analytics while rejecting RPC and cloud configuration",
    )
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
    run(
        args.config,
        offline=args.offline,
        ticks=args.ticks,
        interval=args.interval,
        advisory_only=args.advisory_only,
    )
