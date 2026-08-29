"""UART adapter for Pi -> ESP32 Bridge -> ESP-NOW Central/Sensor nodes.

The serial link uses one canonical JSON object per line with CRC16-CCITT over
the object excluding ``crc16``.  Commands are retried with the same command ID;
Central firmware deduplicates that ID before touching outputs.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from hardware_adapter import ActuatorAck, HardwareAdapter, HardwareEvent, PeerStatus, SensorSample


LOG = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "crc16"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_frame(payload: dict[str, Any]) -> bytes:
    framed = {"version": PROTOCOL_VERSION, **payload}
    framed["crc16"] = crc16_ccitt(_canonical_payload(framed))
    return json.dumps(
        framed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def decode_frame(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("UART frame must be a JSON object")
    if payload.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported UART protocol version")
    expected = payload.get("crc16")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ValueError("UART frame is missing numeric crc16")
    if expected != crc16_ccitt(_canonical_payload(payload)):
        raise ValueError("UART frame CRC mismatch")
    if not isinstance(payload.get("type"), str):
        raise ValueError("UART frame is missing type")
    return payload


class UartEspNowAdapter(HardwareAdapter):
    def __init__(
        self,
        port: str,
        zone_ids: list[str] | tuple[str, ...],
        *,
        runtime_profile: str = "HIL_FIELD1_3BOARD",
        baudrate: int = 115_200,
        ack_timeout_seconds: float = 1.0,
        max_attempts: int = 3,
        serial_factory: Callable[..., Any] | None = None,
        sensor_node_zones: dict[str, str] | None = None,
    ) -> None:
        super().__init__(runtime_profile)
        if runtime_profile == "SIM_TWO_FIELD":
            raise ValueError("UartEspNowAdapter cannot run the SIM profile")
        if not port:
            raise ValueError("UART port is required")
        if ack_timeout_seconds <= 0 or max_attempts < 1:
            raise ValueError("invalid UART retry policy")
        self.port = port
        self.zone_ids = tuple(zone_ids)
        self.baudrate = baudrate
        self.ack_timeout_seconds = ack_timeout_seconds
        self.max_attempts = max_attempts
        self.serial_factory = serial_factory
        self.sensor_node_zones = dict(sensor_node_zones or {})
        if any(zone_id not in self.zone_ids for zone_id in self.sensor_node_zones.values()):
            raise ValueError("sensor node mapping contains an unknown Field")
        self._serial: Any | None = None
        self._running = False
        self._reader: threading.Thread | None = None
        self._events: queue.Queue[HardwareEvent] = queue.Queue()
        self._condition = threading.Condition()
        self._command_lock = threading.Lock()
        self._acks: dict[str, ActuatorAck] = {}
        self._pending_commands: dict[str, tuple[int, str, str]] = {}
        self._sequence = 0
        self._last_sensor_sequence: dict[str, int] = {}
        self._last_sensor_seen_ms: dict[str, int] = {}
        self._zone_states = {zone_id: "OFF" for zone_id in self.zone_ids}
        self._confirmed_zones: set[str] = set()
        self._pump_state = "OFF"

    def start(self) -> None:
        if self._running:
            return
        factory = self.serial_factory
        if factory is None:
            try:
                from serial import Serial  # type: ignore
            except ImportError as exc:
                raise RuntimeError("Install pyserial from implementation/gateway/requirements.txt") from exc
            factory = Serial
        self._serial = factory(self.port, self.baudrate, timeout=0.2)
        if hasattr(self._serial, "reset_input_buffer"):
            self._serial.reset_input_buffer()
        self._running = True
        self._reader = threading.Thread(target=self._reader_loop, name="smartfarm-uart", daemon=True)
        self._reader.start()

    def stop(self) -> None:
        if not self._running:
            return
        try:
            shutdown_acks = self.all_off(f"gateway-adapter-stop-{time.time_ns()}")
            for ack in shutdown_acks:
                if not ack.accepted or ack.valve_output != "OFF":
                    LOG.error(
                        "Central did not confirm shutdown valve OFF zone=%s accepted=%s "
                        "valve=%s reason=%s",
                        ack.zone_id,
                        ack.accepted,
                        ack.valve_output,
                        ack.reason,
                    )
            shutdown_state = self.query_state()
            if shutdown_state.get("pump") != "OFF" or any(
                state != "OFF" for state in shutdown_state.get("zones", {}).values()
            ):
                LOG.error("Central shutdown outputs remain active state=%s", shutdown_state)
        finally:
            self._running = False
            if self._reader is not None:
                self._reader.join(timeout=1.0)
            if self._serial is not None:
                self._serial.close()
            self._serial = None

    def poll(self) -> list[HardwareEvent]:
        events: list[HardwareEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def set_zone(
        self,
        zone_id: str,
        state: str,
        command_id: str,
        lease_ms: int = 15_000,
    ) -> ActuatorAck:
        if zone_id not in self._zone_states or state not in {"ON", "OFF"} or not command_id:
            return self._timeout_ack(command_id, zone_id, "INVALID_COMMAND")
        with self._command_lock:
            return self._set_zone_serial(zone_id, state, command_id, lease_ms)

    def _set_zone_serial(
        self,
        zone_id: str,
        state: str,
        command_id: str,
        lease_ms: int,
    ) -> ActuatorAck:
        lease_ms = max(1_000, min(15_000, int(lease_ms)))
        self._sequence += 1
        payload = {
            "type": "SET_ZONE",
            "sequence": self._sequence,
            "commandId": command_id,
            "zoneId": zone_id,
            "state": state,
            "leaseMs": lease_ms,
        }
        with self._condition:
            self._acks.pop(command_id, None)
            self._pending_commands[command_id] = (self._sequence, zone_id, state)
        try:
            for attempt in range(1, self.max_attempts + 1):
                self._write(payload)
                deadline = time.monotonic() + self.ack_timeout_seconds
                with self._condition:
                    while command_id not in self._acks:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(timeout=remaining)
                    ack = self._acks.pop(command_id, None)
                if ack is not None:
                    return ack
                LOG.warning(
                    "Central ACK wait expired commandId=%s attempt=%s/%s",
                    command_id,
                    attempt,
                    self.max_attempts,
                )
            return self._timeout_ack(command_id, zone_id, "ACK_TIMEOUT")
        finally:
            with self._condition:
                self._pending_commands.pop(command_id, None)
                self._acks.pop(command_id, None)

    def all_off(self, command_id: str) -> list[ActuatorAck]:
        return [
            self.set_zone(zone_id, "OFF", f"{command_id}-{zone_id}")
            for zone_id in self.zone_ids
        ]

    def query_state(self) -> dict[str, Any]:
        return {
            "zones": dict(self._zone_states),
            "pump": self._pump_state,
            "confirmedZones": tuple(sorted(self._confirmed_zones)),
        }

    def _write(self, payload: dict[str, Any]) -> None:
        if not self._running or self._serial is None:
            raise RuntimeError("UART adapter is not started")
        self._serial.write(encode_frame(payload))
        if hasattr(self._serial, "flush"):
            self._serial.flush()

    def _reader_loop(self) -> None:
        assert self._serial is not None
        while self._running:
            raw = self._serial.readline()
            if not raw:
                continue
            try:
                self._handle_frame(decode_frame(raw))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                LOG.warning("Rejected invalid UART frame", exc_info=True)
            except Exception:  # noqa: BLE001
                LOG.exception("Failed to process UART frame")

    def _handle_frame(self, payload: dict[str, Any]) -> None:
        frame_type = payload["type"]
        now_ms = int(time.time() * 1000)
        if frame_type == "ACK":
            command_id = payload.get("commandId")
            zone_id = payload.get("zoneId")
            if not isinstance(command_id, str) or zone_id not in self._zone_states:
                raise ValueError("invalid ACK identity")
            valve = payload.get("valveOutput")
            pump = payload.get("pumpOutput")
            if valve not in {"ON", "OFF"} or pump not in {"ON", "OFF"}:
                raise ValueError("invalid ACK output state")
            accepted = payload.get("accepted") is True
            sequence = payload.get("sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise ValueError("invalid ACK sequence")
            with self._condition:
                pending = self._pending_commands.get(command_id)
            if pending is not None:
                expected_sequence, expected_zone, expected_state = pending
                if sequence != expected_sequence or zone_id != expected_zone:
                    raise ValueError("ACK does not match pending command")
                if accepted and valve != expected_state:
                    raise ValueError("accepted ACK contradicts requested valve state")
                if accepted and expected_state == "ON" and pump != "ON":
                    raise ValueError("accepted ON ACK contradicts pump interlock")
            elif accepted and valve == "ON" and pump != "ON":
                raise ValueError("ACK contradicts pump interlock")
            ack = ActuatorAck(
                command_id=command_id,
                zone_id=zone_id,
                accepted=accepted,
                valve_output=valve,
                pump_output=pump,
                reason=str(payload.get("reason", "EXECUTED" if accepted else "REJECTED")),
                sequence=sequence,
                received_at_ms=now_ms,
            )
            self._zone_states[zone_id] = valve
            self._pump_state = pump
            self._confirmed_zones.add(zone_id)
            if pending is not None:
                with self._condition:
                    self._acks[command_id] = ack
                    self._condition.notify_all()
            self._events.put(ack)
            return
        if frame_type == "SENSOR":
            node_id = payload.get("nodeId")
            zone_id = payload.get("zoneId")
            sequence = payload.get("sequence")
            soil = payload.get("soilMoisture")
            centi_soil = payload.get("soilCentiPct")
            if soil is None and isinstance(centi_soil, list):
                soil = [float(value) / 100.0 for value in centi_soil]
            if (
                not isinstance(node_id, str)
                or zone_id not in self._zone_states
                or not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or not isinstance(soil, list)
                or not soil
            ):
                raise ValueError("invalid SENSOR identity")
            if self.sensor_node_zones and self.sensor_node_zones.get(node_id) != zone_id:
                raise ValueError("SENSOR node is not mapped to this Field")
            now_ms = int(time.time() * 1000)
            last = self._last_sensor_sequence.get(node_id, -1)
            last_seen = self._last_sensor_seen_ms.get(node_id, 0)
            if sequence <= last and now_ms - last_seen <= 30_000:
                return
            values = tuple(float(value) for value in soil)
            if any(not math.isfinite(value) or value < 0 or value > 100 for value in values):
                raise ValueError("soil moisture outside 0..100")
            air_temp = _optional_scaled_float(
                payload.get("airTemp"), payload.get("airTempCentiC"), 100.0
            )
            air_humidity = _optional_scaled_float(
                payload.get("airHumidity"), payload.get("airHumidityCentiPct"), 100.0
            )
            light_lux = _optional_float(payload.get("lightLux"))
            flow_rate = _optional_scaled_float(
                payload.get("flowRateLpm"), payload.get("flowMilliLpm"), 1000.0
            )
            if air_temp is not None and not -10 <= air_temp <= 60:
                raise ValueError("air temperature outside -10..60")
            if air_humidity is not None and not 0 <= air_humidity <= 100:
                raise ValueError("air humidity outside 0..100")
            if light_lux is not None and light_lux < 0:
                raise ValueError("light level must be non-negative")
            if flow_rate is not None and flow_rate < 0:
                raise ValueError("flow rate must be non-negative")
            self._last_sensor_sequence[node_id] = sequence
            self._last_sensor_seen_ms[node_id] = now_ms
            self._events.put(
                SensorSample(
                    node_id=node_id,
                    zone_id=zone_id,
                    sequence=sequence,
                    received_at_ms=now_ms,
                    soil_moisture=values,
                    air_temp=air_temp,
                    air_humidity=air_humidity,
                    light_lux=light_lux,
                    flow_rate_lpm=flow_rate,
                    pulse_counter=_optional_int(payload.get("pulseCounter")),
                    tank_low=payload.get("tankLow") if isinstance(payload.get("tankLow"), bool) else None,
                )
            )
            return
        if frame_type == "PEER":
            peer_id = payload.get("peerId")
            role = payload.get("role")
            if not isinstance(peer_id, str) or not isinstance(role, str):
                raise ValueError("invalid PEER identity")
            self._events.put(
                PeerStatus(
                    peer_id=peer_id,
                    role=role,
                    online=payload.get("online") is True,
                    received_at_ms=now_ms,
                    tank_low=(
                        payload.get("tankLow")
                        if isinstance(payload.get("tankLow"), bool)
                        else None
                    ),
                )
            )
            return
        raise ValueError(f"unsupported UART frame type: {frame_type}")

    def _timeout_ack(self, command_id: str, zone_id: str, reason: str) -> ActuatorAck:
        return ActuatorAck(
            command_id=command_id,
            zone_id=zone_id,
            accepted=False,
            valve_output=self._zone_states.get(zone_id, "OFF"),
            pump_output=self._pump_state,
            reason=reason,
            sequence=self._sequence,
            received_at_ms=int(time.time() * 1000),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("expected numeric value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("expected finite numeric value")
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected non-negative integer")
    return value


def _optional_scaled_float(direct: Any, scaled: Any, divisor: float) -> float | None:
    if direct is not None:
        return _optional_float(direct)
    if scaled is None:
        return None
    return _optional_float(scaled) / divisor
