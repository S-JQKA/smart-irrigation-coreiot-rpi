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
    soil_moisture: tuple[float, ...]
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
class ActuatorAck:
    command_id: str
    zone_id: str
    accepted: bool
    valve_output: str
    pump_output: str
    reason: str
    sequence: int
    received_at_ms: int


HardwareEvent = SensorSample | PeerStatus | ActuatorAck


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


class FakeAdapter(HardwareAdapter):
    """Deterministic immediate-ACK adapter used only by SIM regression tests."""

    def __init__(self, zone_ids: list[str] | tuple[str, ...]) -> None:
        super().__init__("SIM_TWO_FIELD")
        self.zone_states = {zone_id: "OFF" for zone_id in zone_ids}
        self.pump_state = "OFF"
        self.sequence = 0
        self.started = False
        self._events: list[HardwareEvent] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.all_off("fake-adapter-stop")
        self.started = False

    def poll(self) -> list[HardwareEvent]:
        events, self._events = self._events, []
        return events

    def inject(self, event: HardwareEvent) -> None:
        self._events.append(event)

    def set_zone(
        self,
        zone_id: str,
        state: str,
        command_id: str,
        lease_ms: int = 15_000,
    ) -> ActuatorAck:
        del lease_ms
        if zone_id not in self.zone_states or state not in {"ON", "OFF"}:
            return self._ack(command_id, zone_id, False, "INVALID_COMMAND")
        self.zone_states[zone_id] = state
        self.pump_state = "ON" if "ON" in self.zone_states.values() else "OFF"
        return self._ack(command_id, zone_id, True, "EXECUTED")

    def all_off(self, command_id: str) -> list[ActuatorAck]:
        acknowledgements = []
        self.pump_state = "OFF"
        for zone_id in self.zone_states:
            self.zone_states[zone_id] = "OFF"
            acknowledgements.append(self._ack(command_id, zone_id, True, "EXECUTED"))
        return acknowledgements

    def query_state(self) -> dict[str, Any]:
        return {
            "zones": dict(self.zone_states),
            "pump": self.pump_state,
            "confirmedZones": tuple(self.zone_states),
        }

    def _ack(self, command_id: str, zone_id: str, accepted: bool, reason: str) -> ActuatorAck:
        self.sequence += 1
        return ActuatorAck(
            command_id=command_id,
            zone_id=zone_id,
            accepted=accepted,
            valve_output=self.zone_states.get(zone_id, "OFF"),
            pump_output=self.pump_state,
            reason=reason,
            sequence=self.sequence,
            received_at_ms=int(time.time() * 1000),
        )
