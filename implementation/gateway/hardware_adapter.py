"""Hardware boundary shared by SIM, HIL and final SmartFarm runtimes."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


RUNTIME_PROFILES = {
    "SIM_TWO_FIELD": ("SYNTHETIC", "SIM", "SIM+PLATFORM"),
    "HIL_FIELD1_3BOARD": ("SYNTHETIC", "LED", "HIL"),
    "HIL_TWO_FIELD_4BOARD": ("SYNTHETIC", "LED", "HIL"),
    "HARDWARE_TWO_FIELD": ("PHYSICAL", "RELAY", "HARDWARE-UNVERIFIED"),
}


@dataclass(frozen=True)
class SensorSample:
    node_id: str
    zone_id: str
    sequence: int
    received_at_ms: int
    soil_moisture: tuple[float | None, ...]
    air_temp: float | None = None
    air_humidity: float | None = None
    light_lux: float | None = None
    flow_rate_lpm: float | None = None
    pulse_counter: int | None = None
    tank_low: bool | None = None


@dataclass(frozen=True)
class PeerStatus:
    peer_id: str
    role: str
    online: bool
    received_at_ms: int
    tank_low: bool | None = None


@dataclass(frozen=True)
class FlowSample:
    """Independent branch water measurement owned by Central."""

    zone_id: str
    sequence: int
    received_at_ms: int
    pulse_counter: int
    flow_rate_lpm: float
    boot_id: str


@dataclass(frozen=True)
class ActuatorAck:
    command_id: str
    zone_id: str
    accepted: bool
    valve_output: str
    pump_output: str
    reason: str
    sequence: int
    received_at_ms: int


HardwareEvent = SensorSample | FlowSample | PeerStatus | ActuatorAck


class HardwareAdapter(ABC):
    """The control engine may observe hardware only through this interface."""

    runtime_profile: str
    sensor_data_origin: str
    actuator_backend: str
    evidence_class: str

    def __init__(self, runtime_profile: str) -> None:
        normalized = runtime_profile.upper()
        if normalized not in RUNTIME_PROFILES:
            raise ValueError(f"unsupported runtime profile: {runtime_profile}")
        self.runtime_profile = normalized
        (
            self.sensor_data_origin,
            self.actuator_backend,
            self.evidence_class,
        ) = RUNTIME_PROFILES[normalized]

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def poll(self) -> list[HardwareEvent]:
        raise NotImplementedError

    @abstractmethod
    def set_zone(
        self,
        zone_id: str,
        state: str,
        command_id: str,
        lease_ms: int = 15_000,
    ) -> ActuatorAck:
        raise NotImplementedError

    @abstractmethod
    def all_off(self, command_id: str) -> list[ActuatorAck]:
        raise NotImplementedError

    @abstractmethod
    def query_state(self) -> dict[str, Any]:
        raise NotImplementedError
