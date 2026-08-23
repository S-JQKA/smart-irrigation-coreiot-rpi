"""Deterministic SmartFarm v2.2 simulator using the CoreIoT Gateway API."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coreiot.gateway_client import GatewayClient, GatewaySettings
from coreiot.gateway_protocol import RpcCommand


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "devices.example.json"
LOG = logging.getLogger("smartfarm.gateway.simulator")


def utc_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ZoneState:
    zone_id: str
    soil_devices: list[str]
    valve_device: str
    water_meter_device: str
    env_device: str
    moisture: float
    valve_state: str = "OFF"
    pulse_counter: int = 0
    water_used: float = 0.0
    decision_reason: str = "INITIALIZED"
    control_mode: str = "AUTO"
    safety_state: str = "SAFE"
    last_command_id: str = ""
    last_ack: str = "NONE"
    safety_block_reason: str = "NONE"


@dataclass
class SiteState:
    pump_device: str
    manifold_device: str
    gateway_device: str
    pump_state: str = "OFF"
    pump_runtime_sec: int = 0
    tank_level_pct: float = 75.0
    tank_low_switch: bool = False
    system_mode: str = "NORMAL"
    max_concurrent_zones: int = 1
    gateway_mode: str = "GATEWAY"
    last_command_id: str = ""
    last_ack: str = "NONE"
    safety_block_reason: str = "NONE"


@dataclass
class SimulationModel:
    zones: dict[str, ZoneState]
    site: SiteState
    thresholds: dict[str, float]
    rng: random.Random = field(default_factory=random.Random)
    interval_seconds: float = 5.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SimulationModel":
        devices = config["devices"]
        by_zone: dict[str, list[dict[str, Any]]] = {}
        for device in devices:
            by_zone.setdefault(device["zoneId"], []).append(device)

        sim = config.get("simulation", {})
        starts = sim.get("startMoisture", {})
        zones: dict[str, ZoneState] = {}
        for zone_id in sorted(key for key in by_zone if key.startswith("field-")):
            entries = by_zone[zone_id]
            by_profile = lambda text: [d["name"] for d in entries if text in d["profile"]]  # noqa: E731
            soil = by_profile("Soil Moisture")
            valve = by_profile("Smart Valve")
            meter = by_profile("Water Meter")
            env = by_profile("Env Sensor")
            if not soil or len(valve) != 1 or len(meter) != 1 or len(env) != 1:
                raise ValueError(f"Incomplete device mapping for {zone_id}")
            zones[zone_id] = ZoneState(
                zone_id=zone_id,
                soil_devices=soil,
                valve_device=valve[0],
                water_meter_device=meter[0],
                env_device=env[0],
                moisture=float(starts.get(zone_id, 40.0)),
            )

        site_devices = by_zone.get("site", [])
        find_one = lambda text: next(d["name"] for d in site_devices if text in d["profile"])  # noqa: E731
        gateway = config["gateway"]["name"]
        site = SiteState(
            pump_device=find_one("Pump Controller"),
            manifold_device=find_one("Manifold Controller"),
            gateway_device=gateway,
            max_concurrent_zones=max(1, int(sim.get("maxConcurrentZones", 1))),
        )
        return cls(
            zones=zones,
            site=site,
            thresholds={k: float(v) for k, v in sim.get("thresholds", {}).items()},
            rng=random.Random(int(sim.get("seed", 20260803))),
            interval_seconds=float(sim.get("intervalSeconds", 5)),
        )

    def _safety_reason(self, zone: ZoneState) -> str:
        flood = self.thresholds.get("floodMoistureThreshold", 85)
        quota = self.thresholds.get("maxWaterPerCycle", 1000)
        if self.site.tank_low_switch:
            return "TANK_LOW"
        if zone.moisture >= flood:
            return "WATERLOGGING_RISK"
        if zone.water_used >= quota:
            return "CYCLE_QUOTA_REACHED"
        if zone.control_mode == "DISABLED":
            return "CONTROL_DISABLED"
        return "NONE"

    def apply_rpc(self, command: RpcCommand) -> tuple[bool, str, str]:
        target_zone = next((z for z in self.zones.values() if z.valve_device == command.device), None)
        if target_zone:
            if command.method == "TURN_ON":
                reason = self._safety_reason(target_zone)
                active_other_zones = sum(
                    zone.valve_state == "ON" and zone.zone_id != target_zone.zone_id
                    for zone in self.zones.values()
                )
                if reason == "NONE" and active_other_zones >= self.site.max_concurrent_zones:
                    reason = "MAX_CONCURRENT_ZONES"
                if reason != "NONE":
                    target_zone.last_command_id = command.command_id
                    target_zone.last_ack = "REJECTED"
                    target_zone.safety_block_reason = reason
                    target_zone.safety_state = "BLOCKED"
                    return False, "OFF", "SAFETY_BLOCK"
                target_zone.valve_state = "ON"
            else:
                target_zone.valve_state = "OFF"
            target_zone.last_command_id = command.command_id
            target_zone.last_ack = "EXECUTED"
            target_zone.safety_block_reason = "NONE"
            target_zone.safety_state = "SAFE"
            return True, target_zone.valve_state, "EXECUTED"

        if command.device == self.site.pump_device:
            if command.method == "TURN_ON" and self.site.tank_low_switch:
                self.site.last_command_id = command.command_id
                self.site.last_ack = "REJECTED"
                self.site.safety_block_reason = "TANK_LOW"
                return False, "OFF", "SAFETY_BLOCK"
            self.site.pump_state = "ON" if command.method == "TURN_ON" else "OFF"
            self.site.last_command_id = command.command_id
            self.site.last_ack = "EXECUTED"
            self.site.safety_block_reason = "NONE"
            return True, self.site.pump_state, "EXECUTED"

        return False, "OFF", "SAFETY_BLOCK"

    def decide(self) -> None:
        minimum = self.thresholds.get("minMoistureThreshold", 30)
        target = self.thresholds.get("targetMoisture", 55)
        candidates: list[ZoneState] = []
        for zone in self.zones.values():
            block = self._safety_reason(zone)
            if block != "NONE":
                zone.valve_state = "OFF"
                zone.safety_state = "BLOCKED"
                zone.safety_block_reason = block
                zone.decision_reason = block
            elif zone.valve_state == "ON" and zone.moisture >= target:
                zone.valve_state = "OFF"
                zone.decision_reason = "TARGET_MOISTURE_REACHED"
            elif zone.control_mode == "AUTO" and zone.moisture < minimum and zone.valve_state != "ON":
                candidates.append(zone)
            else:
                zone.decision_reason = "KEEP_IRRIGATING" if zone.valve_state == "ON" else "NO_CHANGE"
                zone.safety_state = "SAFE"
                zone.safety_block_reason = "NONE"

        active = sum(zone.valve_state == "ON" for zone in self.zones.values())
        available = max(0, self.site.max_concurrent_zones - active)
        for index, zone in enumerate(sorted(candidates, key=lambda item: item.moisture)):
            if index < available:
                zone.valve_state = "ON"
                zone.decision_reason = "BELOW_MIN_MOISTURE"
                zone.safety_state = "SAFE"
                zone.safety_block_reason = "NONE"
            else:
                zone.decision_reason = "WAITING_FOR_SCHEDULER_SLOT"

        self._sync_pump_state()

    def enforce_runtime_safety(self) -> None:
        for zone in self.zones.values():
            block = self._safety_reason(zone)
            if block != "NONE":
                zone.valve_state = "OFF"
                zone.safety_state = "BLOCKED"
                zone.safety_block_reason = block
                zone.decision_reason = block

    def _sync_pump_state(self) -> None:
        active = sum(zone.valve_state == "ON" for zone in self.zones.values())
        self.site.pump_state = "ON" if active and not self.site.tank_low_switch else "OFF"

    def tick(
        self,
        timestamp: int | None = None,
        local_decision: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        now = timestamp or utc_ms()
        if local_decision:
            self.decide()
        else:
            self.enforce_runtime_safety()
            self._sync_pump_state()
        active_zones = sum(zone.valve_state == "ON" for zone in self.zones.values())
        readings: dict[str, list[dict[str, Any]]] = {}

        for zone_index, zone in enumerate(self.zones.values(), start=1):
            if zone.valve_state == "ON":
                zone.moisture = min(100.0, zone.moisture + self.rng.uniform(1.5, 3.0))
                flow_rate = round(self.rng.uniform(8.0, 12.0), 2)
                pulses = max(1, round(flow_rate * self.interval_seconds))
                zone.pulse_counter += pulses
                zone.water_used += flow_rate * self.interval_seconds / 60.0
            else:
                zone.moisture = max(0.0, zone.moisture - self.rng.uniform(0.05, 0.25))
                flow_rate = 0.0

            for sensor_index, device in enumerate(zone.soil_devices, start=1):
                offset = (sensor_index - (len(zone.soil_devices) + 1) / 2) * 0.35
                values = {
                    "moisture": round(max(0, min(100, zone.moisture + offset)), 2),
                    "battery": 92 - sensor_index,
                    "active": True,
                    "dataQuality": "OK",
                }
                readings[device] = [{"ts": now, "values": values}]

            air_temp = 29.0 + zone_index + self.rng.uniform(-0.4, 0.4)
            air_humidity = 62.0 - zone_index + self.rng.uniform(-1.0, 1.0)
            saturation = 0.6108 * math.exp((17.27 * air_temp) / (air_temp + 237.3))
            vpd = saturation * (1 - air_humidity / 100)
            readings[zone.env_device] = [{
                "ts": now,
                "values": {
                    "airTemp": round(air_temp, 2),
                    "airHumidity": round(air_humidity, 2),
                    "lightLux": 18000 + zone_index * 1000,
                    "vpd": round(vpd, 3),
                    "battery": 90,
                    "rssi": -55 - zone_index,
                    "dataQuality": "OK",
                },
            }]
            readings[zone.valve_device] = [{
                "ts": now,
                "values": {
                    "valveState": zone.valve_state,
                    "lastCommandId": zone.last_command_id,
                    "lastAck": zone.last_ack,
                    "safetyBlockReason": zone.safety_block_reason,
                },
            }]
            readings[zone.water_meter_device] = [{
                "ts": now,
                "values": {
                    "pulseCounter": zone.pulse_counter,
                    "flowRate": flow_rate,
                    "battery": 89,
                    "dataQuality": "OK",
                },
            }]

        if self.site.pump_state == "ON":
            self.site.pump_runtime_sec += round(self.interval_seconds)
            self.site.tank_level_pct = max(0.0, self.site.tank_level_pct - 0.05 * active_zones)
        self.site.tank_low_switch = self.site.tank_level_pct <= 10.0
        self.site.system_mode = "SAFE-IDLE" if self.site.tank_low_switch else "NORMAL"
        readings[self.site.pump_device] = [{
            "ts": now,
            "values": {
                "pumpState": self.site.pump_state,
                "runtimeSec": self.site.pump_runtime_sec,
                "lastCommandId": self.site.last_command_id,
                "lastAck": self.site.last_ack,
                "safetyBlockReason": self.site.safety_block_reason,
            },
        }]
        readings[self.site.manifold_device] = [{
            "ts": now,
            "values": {
                "tankLowSwitch": self.site.tank_low_switch,
                "tankLevelPct": round(self.site.tank_level_pct, 2),
                "activeZoneCount": active_zones,
                "controllerState": "ONLINE",
                "systemMode": self.site.system_mode,
            },
        }]
        return readings

    def gateway_health(
        self,
        connected_device_count: int,
        buffer_depth: int,
    ) -> dict[str, Any]:
        now = utc_ms()
        return {
            "ts": now,
            "values": {
                "gatewayMode": self.site.gateway_mode,
                "systemMode": self.site.system_mode,
                "connectedDeviceCount": connected_device_count,
                "bufferDepth": buffer_depth,
                "lastSyncTs": now,
            },
        }


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def settings_from_env(config: dict[str, Any]) -> GatewaySettings:
    gateway = config["gateway"]
    host = os.getenv(gateway["hostEnv"], "")
    token = os.getenv(gateway["tokenEnv"], "")
    port = int(os.getenv(gateway["portEnv"], "1883"))
    tls = os.getenv(gateway["tlsEnv"], "false").lower() in {"1", "true", "yes"}
    return GatewaySettings(host=host, port=port, access_token=token, tls=tls)


def run(config_path: Path, ticks: int | None, interval: float | None, dry_run: bool) -> None:
    config = load_config(config_path)
    if config.get("transportMode") != "GATEWAY":
        raise ValueError("simulator_v22.py only runs transportMode=GATEWAY; use legacy simulator for DIRECT diagnostics")
    model = SimulationModel.from_config(config)
    if interval is not None:
        model.interval_seconds = interval

    client: GatewayClient | None = None
    if not dry_run:
        client = GatewayClient(
            settings_from_env(config),
            model.apply_rpc,
            legacy_manual_off_allowed=lambda command: command.method == "TURN_OFF",
        )
        client.start()
        for device in config["devices"]:
            client.connect_device(device["name"], device["profile"])

    stopped = False

    def stop_handler(signum: int, frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    iteration = 0
    control_authority = "LOCAL_SCHEDULER" if dry_run else "COREIOT_RPC"
    LOG.info("controlAuthority=%s", control_authority)
    try:
        while not stopped and (ticks is None or iteration < ticks):
            iteration += 1
            readings = model.tick(local_decision=dry_run)
            if client:
                client.publish_telemetry(readings)
                client.publish_gateway_telemetry(
                    model.gateway_health(
                        len(config["devices"]),
                        client.buffer_depth,
                    )
                )
            active = sum(z.valve_state == "ON" for z in model.zones.values())
            LOG.info("tick=%s devices=%s activeZones=%s pump=%s", iteration, len(readings), active, model.site.pump_state)
            if ticks is None or iteration < ticks:
                time.sleep(model.interval_seconds)
    finally:
        if client:
            for device in config["devices"]:
                client.disconnect_device(device["name"])
            client.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartFarm v2.2 CoreIoT Gateway API simulator")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ticks", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    run(args.config, args.ticks, args.interval, args.dry_run)
