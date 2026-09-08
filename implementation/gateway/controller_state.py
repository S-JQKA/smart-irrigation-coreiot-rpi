"""Device topology and acknowledged controller state."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from configuration import utc_ms


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
    control_mode: str = "DISABLED"
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
    tank_level_pct: float | None = None
    tank_low_switch: bool = False
    system_mode: str = "NORMAL"
    max_concurrent_zones: int = 1
    gateway_mode: str = "GATEWAY"
    last_command_id: str = ""
    last_ack: str = "NONE"
    safety_block_reason: str = "NONE"


@dataclass
class ControllerState:
    zones: dict[str, ZoneState]
    site: SiteState
    thresholds: dict[str, float]
    interval_seconds: float = 5.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ControllerState":
        devices = config["devices"]
        by_zone: dict[str, list[dict[str, Any]]] = {}
        for device in devices:
            by_zone.setdefault(device["zoneId"], []).append(device)
        settings = config.get("control", {})
        zones: dict[str, ZoneState] = {}
        for zone_id in sorted((key for key in by_zone if key.startswith("field-"))):
            entries = by_zone[zone_id]
            by_profile = lambda text: [
                d["name"] for d in entries if text in d["profile"]
            ]
            soil = by_profile("Soil Moisture")
            valve = by_profile("Smart Valve")
            meter = by_profile("Water Meter")
            env = by_profile("Env Sensor")
            if not soil or len(valve) != 1 or len(meter) != 1 or (len(env) != 1):
                raise ValueError(f"Incomplete device mapping for {zone_id}")
            zones[zone_id] = ZoneState(
                zone_id=zone_id,
                soil_devices=soil,
                valve_device=valve[0],
                water_meter_device=meter[0],
                env_device=env[0],
                moisture=0.0,
            )
        site_devices = by_zone.get("site", [])
        find_one = lambda text: next(
            (d["name"] for d in site_devices if text in d["profile"])
        )
        gateway = config["gateway"]["name"]
        site = SiteState(
            pump_device=find_one("Pump Controller"),
            manifold_device=find_one("Manifold Controller"),
            gateway_device=gateway,
            max_concurrent_zones=max(1, int(settings.get("maxConcurrentZones", 1))),
        )
        return cls(
            zones=zones,
            site=site,
            thresholds={k: float(v) for k, v in settings.get("thresholds", {}).items()},
            interval_seconds=float(settings.get("intervalSeconds", 5)),
        )

    def gateway_health(
        self, connected_device_count: int, buffer_depth: int
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
