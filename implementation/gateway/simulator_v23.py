"""SmartFarm v2.3 simulator with edge-owned multivariable control.

Unlike v2.2, transport and control authority are independent: the local
scheduler can keep controlling simulated hardware while telemetry is published
to a real CoreIoT tenant.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control_engine_v23 import FieldControlInput, FieldDecision, IrrigationThresholds, evaluate_field
from hardware_adapter import ActuatorAck, HardwareAdapter, PeerStatus, SensorSample
from coreiot.gateway_client import GatewayClient
from coreiot.gateway_protocol import RpcCommand, requested_run_duration_seconds
from simulator_v22 import SimulationModel, load_config, settings_from_env, utc_ms
from runtime_state import (
    FieldConfigStore,
    PersistentCommandLedger,
    clock_is_ready,
    runtime_state_dir,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "devices.v23.example.json"
LOG = logging.getLogger("smartfarm.gateway.simulator.v23")

HARD_STOP_REASONS = {
    "TANK_LOW",
    "SYSTEM_SAFE_IDLE",
    "FIELD_DATA_INVALID",
    "WATERLOGGING_RISK",
    "MAX_MOISTURE_AUTO_LOCK",
    "CYCLE_QUOTA_REACHED",
    "DAILY_QUOTA_REACHED",
    "MAX_DURATION_REACHED",
    "CONTROL_DISABLED",
    "MANUAL_OFF_LATCHED",
    "MANUAL_ON_TTL_EXPIRED",
    "ZONE_FLOW_LOW",
    "CONTROLLER_OFFLINE",
}


@dataclass
class ZoneRuntime:
    air_temp: float
    air_humidity: float
    light_lux: float
    vpd: float
    below_min_samples: int = 0
    above_flood_samples: int = 0
    irrigation_runtime_seconds: float = 0.0
    last_irrigation_ended_ms: int = 0
    manual_off_until_ms: int = 0
    manual_on_until_ms: int = 0
    active_request_source: str = ""
    irrigation_task: dict[str, Any] | None = None
    irrigation_task_dirty: bool = False
    transition_sequence: int = 0
    decision: FieldDecision | None = None
    daily_water_liters: float = 0.0
    daily_epoch_day: int = -1
    last_flow_rate: float = 0.0
    zero_flow_seconds: float = 0.0
    data_quality: str = "OK"
    last_sensor_update_ms: int = 0
    sensor_fault: bool = False
    flow_fault: bool = False
    flow_fault_latched: bool = False
    local_schedule: dict[str, Any] | None = None
    soil_samples: tuple[float, ...] = ()
    sensor_node_id: str = ""
    expected_sensors: int = 4
    min_valid_sensors: int = 3


@dataclass(frozen=True)
class LocalSchedule:
    schedule_id: str
    zone_id: str
    start_after_seconds: int
    duration_seconds: int
    repeat_every_seconds: int | None = None
    config_id: str = ""


class LocalScheduleRunner:
    """Emit bounded scheduler commands from deterministic Gateway configuration."""

    def __init__(self, schedules: list[LocalSchedule], started_ms: int) -> None:
        self.schedules = schedules
        self.next_fire_ms = {
            item.schedule_id: started_ms + item.start_after_seconds * 1000
            for item in schedules
        }
        self.sequence = 0
        self._lock = threading.Lock()

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        model: "SimulationModelV23",
        started_ms: int,
    ) -> "LocalScheduleRunner":
        schedules: list[LocalSchedule] = []
        seen_ids: set[str] = set()
        raw_schedules = config.get("localSchedules", [])
        if not isinstance(raw_schedules, list):
            raise ValueError("localSchedules must be a list")
        for index, raw in enumerate(raw_schedules, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"localSchedules[{index}] must be an object")
            if not raw.get("enabled", True):
                continue
            schedule_id = str(raw.get("id", "")).strip()
            zone_id = str(raw.get("zoneId", "")).strip()
            config_id = str(raw.get("configId", "")).strip()
            if not schedule_id or schedule_id in seen_ids:
                raise ValueError(f"localSchedules[{index}] requires a unique id")
            if zone_id not in model.zones:
                raise ValueError(f"localSchedules[{index}] has unknown zoneId={zone_id}")
            start_after = raw.get("startAfterSeconds", 0)
            duration = raw.get("durationSeconds")
            repeat_every = raw.get("repeatEverySeconds")
            if not isinstance(start_after, int) or isinstance(start_after, bool) or start_after < 0:
                raise ValueError(f"localSchedules[{index}] startAfterSeconds must be >= 0")
            max_duration = min(
                model.manual_on_max_seconds,
                int(model._limits_for(zone_id).max_duration_seconds),
            )
            if (
                not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration < 1
                or duration > max_duration
            ):
                raise ValueError(
                    f"localSchedules[{index}] durationSeconds must be 1..{max_duration}"
                )
            if repeat_every is not None and (
                not isinstance(repeat_every, int)
                or isinstance(repeat_every, bool)
                or repeat_every < duration
            ):
                raise ValueError(
                    f"localSchedules[{index}] repeatEverySeconds must be >= durationSeconds"
                )
            schedules.append(
                LocalSchedule(
                    schedule_id=schedule_id,
                    zone_id=zone_id,
                    start_after_seconds=start_after,
                    duration_seconds=duration,
                    repeat_every_seconds=repeat_every,
                    config_id=config_id,
                )
            )
            model.zone_runtime[zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": True,
                "status": "SCHEDULED",
                "nextRunTs": started_ms + start_after * 1000,
                "durationSeconds": duration,
                "repeatEverySeconds": repeat_every or 0,
                "lastRunTs": 0,
                "lastResultReason": "NONE",
                "configCommandId": config_id,
                "configAck": "ACCEPTED" if config_id else "",
                "configReason": "SCHEDULE_RESTORED" if config_id else "",
            }
            seen_ids.add(schedule_id)
        return cls(schedules, started_ms)

    def update_from_rpc(
        self,
        model: "SimulationModelV23",
        command: RpcCommand,
        now_ms: int,
    ) -> tuple[bool, str, str]:
        """Validate and apply one operator-owned local schedule update."""

        return self.update_from_config(
            model,
            command.device,
            command.params,
            command.command_id,
            now_ms,
        )

    def update_from_config(
        self,
        model: "SimulationModelV23",
        device: str,
        params: dict[str, Any],
        config_id: str,
        now_ms: int,
    ) -> tuple[bool, str, str]:
        """Validate and apply one durable operator schedule configuration."""

        target = next(
            (zone for zone in model.zones.values() if zone.valve_device == device),
            None,
        )
        if target is None:
            return False, "OFF", "INVALID_COMMAND"
        if not isinstance(config_id, str) or not config_id.strip():
            return False, target.valve_state, "INVALID_COMMAND"
        schedule_id = str(params.get("scheduleId", "")).strip()
        enabled = params.get("enabled", True)
        duration = params.get("durationSeconds")
        repeat_every = params.get("repeatEverySeconds", 0)
        start_at_ms = params.get("startAtMs")
        start_after = params.get("startAfterSeconds")
        if not schedule_id or not isinstance(enabled, bool):
            return False, target.valve_state, "INVALID_COMMAND"

        existing = next(
            (item for item in self.schedules if item.schedule_id == schedule_id),
            None,
        )
        if existing is not None and existing.zone_id != target.zone_id:
            return False, target.valve_state, "INVALID_COMMAND"

        if not enabled:
            disabled_duration = existing.duration_seconds if existing is not None else 0
            with self._lock:
                self.schedules = [item for item in self.schedules if item.schedule_id != schedule_id]
                self.next_fire_ms.pop(schedule_id, None)
            model.zone_runtime[target.zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": False,
                "status": "CANCELLED",
                "nextRunTs": 0,
                "durationSeconds": disabled_duration,
                "repeatEverySeconds": int(repeat_every or 0),
                "lastRunTs": 0,
                "lastResultReason": "OPERATOR_DISABLED",
                "configCommandId": config_id,
                "configAck": "ACCEPTED",
                "configReason": "SCHEDULE_DISABLED",
            }
            return True, target.valve_state, "EXECUTED"

        max_duration = min(
            model.manual_on_max_seconds,
            int(model._limits_for(target.zone_id).max_duration_seconds),
        )
        valid_duration = isinstance(duration, int) and not isinstance(duration, bool)
        valid_repeat = isinstance(repeat_every, int) and not isinstance(repeat_every, bool)
        if not valid_duration or duration < 1 or duration > max_duration:
            return False, target.valve_state, "INVALID_COMMAND"
        if not valid_repeat or repeat_every < 0 or (repeat_every and repeat_every < duration):
            return False, target.valve_state, "INVALID_COMMAND"

        if start_at_ms is not None:
            if not isinstance(start_at_ms, int) or isinstance(start_at_ms, bool):
                return False, target.valve_state, "INVALID_COMMAND"
            delay_ms = start_at_ms - now_ms
        elif start_after is not None:
            if not isinstance(start_after, int) or isinstance(start_after, bool):
                return False, target.valve_state, "INVALID_COMMAND"
            delay_ms = start_after * 1000
            start_at_ms = now_ms + delay_ms
        else:
            return False, target.valve_state, "INVALID_COMMAND"
        config_reason = "SCHEDULE_UPDATED"
        if delay_ms < 0 and repeat_every > 0:
            interval_ms = repeat_every * 1000
            missed = ((now_ms - start_at_ms) // interval_ms) + 1
            start_at_ms += missed * interval_ms
            delay_ms = start_at_ms - now_ms
            config_reason = "SCHEDULE_ROLLED_FORWARD"
        elif delay_ms < 0:
            with self._lock:
                self.schedules = [
                    item for item in self.schedules if item.schedule_id != schedule_id
                ]
                self.next_fire_ms.pop(schedule_id, None)
            model.zone_runtime[target.zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": True,
                "status": "COMPLETED",
                "nextRunTs": 0,
                "durationSeconds": duration,
                "repeatEverySeconds": 0,
                "lastRunTs": start_at_ms,
                "lastResultReason": "STALE_ONE_SHOT_IGNORED",
                "configCommandId": config_id,
                "configAck": "ACCEPTED",
                "configReason": "STALE_ONE_SHOT_IGNORED",
            }
            return True, target.valve_state, "STALE_ONE_SHOT_IGNORED"
        if delay_ms > 7 * 86_400_000:
            return False, target.valve_state, "INVALID_COMMAND"

        updated = LocalSchedule(
            schedule_id=schedule_id,
            zone_id=target.zone_id,
            start_after_seconds=max(0, int(delay_ms / 1000)),
            duration_seconds=duration,
            repeat_every_seconds=repeat_every or None,
            config_id=config_id,
        )
        with self._lock:
            self.schedules = [item for item in self.schedules if item.schedule_id != schedule_id]
            self.schedules.append(updated)
            self.next_fire_ms[schedule_id] = start_at_ms
        model.zone_runtime[target.zone_id].local_schedule = {
            "id": schedule_id,
            "source": "GATEWAY_LOCAL",
            "enabled": True,
            "status": "SCHEDULED",
            "nextRunTs": start_at_ms,
            "durationSeconds": duration,
            "repeatEverySeconds": repeat_every,
            "lastRunTs": 0,
            "lastResultReason": "NONE",
            "configCommandId": config_id,
            "configAck": "ACCEPTED",
            "configReason": config_reason,
        }
        return True, target.valve_state, "EXECUTED"

    def persist(self, path: Path) -> None:
        """Atomically persist the active runtime schedules for restart recovery."""

        with self._lock:
            schedules = list(self.schedules)
            next_fire = dict(self.next_fire_ms)
        payload = {
            "schemaVersion": 1,
            "localSchedules": [
                {
                    "id": item.schedule_id,
                    "zoneId": item.zone_id,
                    "enabled": True,
                    "startAtMs": next_fire.get(item.schedule_id),
                    "durationSeconds": item.duration_seconds,
                    "repeatEverySeconds": item.repeat_every_seconds or 0,
                    "configId": item.config_id,
                }
                for item in schedules
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def dispatch_due(
        self,
        model: "SimulationModelV23",
        now_ms: int,
    ) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        with self._lock:
            schedules = list(self.schedules)
        for schedule in schedules:
            due_ms = self.next_fire_ms.get(schedule.schedule_id)
            if due_ms is None or now_ms < due_ms:
                continue
            self.sequence += 1
            command = RpcCommand(
                device=model.zones[schedule.zone_id].valve_device,
                request_id=-self.sequence,
                method="TURN_ON",
                params={
                    "commandId": (
                        f"smartfarm-local-schedule-{schedule.schedule_id}-{self.sequence}"
                    ),
                    "source": "SCHEDULER",
                    "requestedAt": now_ms,
                    "ttlSeconds": 30,
                    "runDurationSeconds": schedule.duration_seconds,
                    "scheduleId": schedule.schedule_id,
                },
            )
            accepted, _, ack = model.apply_rpc(command, now_ms=now_ms)
            results.append((schedule.schedule_id, accepted, ack))
            LOG.info(
                "local schedule fired id=%s zone=%s duration=%ss accepted=%s ack=%s",
                schedule.schedule_id,
                schedule.zone_id,
                schedule.duration_seconds,
                accepted,
                ack,
            )
            if schedule.repeat_every_seconds is None:
                self.next_fire_ms[schedule.schedule_id] = None
                next_run_ms = 0
            else:
                interval_ms = schedule.repeat_every_seconds * 1000
                next_run_ms = due_ms + interval_ms
                if next_run_ms <= now_ms:
                    missed = ((now_ms - next_run_ms) // interval_ms) + 1
                    next_run_ms += missed * interval_ms
                self.next_fire_ms[schedule.schedule_id] = next_run_ms
            schedule_state = model.zone_runtime[schedule.zone_id].local_schedule
            if schedule_state is not None:
                detailed_result = (
                    "SCHEDULE_STARTED"
                    if accepted
                    else model.zones[schedule.zone_id].decision_reason or ack
                )
                schedule_state.update({
                    "status": "RUNNING" if accepted else "REJECTED",
                    "nextRunTs": next_run_ms,
                    "lastRunTs": now_ms,
                    "lastResultReason": detailed_result,
                })
        return results


LOCAL_SCHEDULE_ATTRIBUTE = "localScheduleConfig"
FIELD_CONFIGURATION_ATTRIBUTES = (
    "controlMode",
    "criticalMoisture",
    "minMoistureThreshold",
    "targetMoisture",
    "maxMoistureThreshold",
    "floodMoistureThreshold",
)
SHARED_ATTRIBUTE_WATCHES = (*FIELD_CONFIGURATION_ATTRIBUTES, LOCAL_SCHEDULE_ATTRIBUTE)


def _shared_attribute_values(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize live-update and request-response Gateway attribute payloads."""

    values: dict[str, Any] = {}
    for key in (*FIELD_CONFIGURATION_ATTRIBUTES, LOCAL_SCHEDULE_ATTRIBUTE):
        if key in body:
            values[key] = body[key]
    for scope in ("data", "shared", "values"):
        scoped = body.get(scope)
        if isinstance(scoped, dict):
            for key in (*FIELD_CONFIGURATION_ATTRIBUTES, LOCAL_SCHEDULE_ATTRIBUTE):
                if key in scoped:
                    values[key] = scoped[key]
    return values


def _threshold_mapping(limits: IrrigationThresholds) -> dict[str, Any]:
    return {
        "criticalMoisture": limits.critical_moisture,
        "minMoistureThreshold": limits.min_moisture,
        "targetMoisture": limits.target_moisture,
        "maxMoistureThreshold": limits.max_moisture,
        "floodMoistureThreshold": limits.flood_moisture,
        "maxWaterPerCycle": limits.max_water_per_cycle_liters,
        "maxWaterPerDay": limits.max_water_per_day_liters,
        "maxDurationSec": limits.max_duration_seconds,
        "hotTemperatureC": limits.hot_temperature_c,
        "highVpdKpa": limits.high_vpd_kpa,
        "highLightLux": limits.high_light_lux,
        "startDebounceSamples": limits.start_debounce_samples,
        "floodDebounceSamples": limits.flood_debounce_samples,
    }


def effective_field_config(model: "SimulationModelV23", zone_id: str) -> dict[str, Any]:
    values = _threshold_mapping(model._limits_for(zone_id))
    return {
        "controlMode": model.zones[zone_id].control_mode,
        **{key: values[key] for key in FIELD_CONFIGURATION_ATTRIBUTES if key != "controlMode"},
    }


def apply_field_configuration_attribute(
    model: "SimulationModelV23",
    body: dict[str, Any],
    now_ms: int,
    store: FieldConfigStore | None = None,
) -> tuple[bool, str] | None:
    """Atomically validate and apply one Field's cloud-owned soft configuration."""

    device = body.get("device")
    if not isinstance(device, str) or not device:
        return None
    target = next((zone for zone in model.zones.values() if zone.valve_device == device), None)
    if target is None:
        return None
    updates = {
        key: value
        for key, value in _shared_attribute_values(body).items()
        if key in FIELD_CONFIGURATION_ATTRIBUTES
    }
    if not updates:
        return None

    current = effective_field_config(model, target.zone_id)
    candidate = {**current, **updates}
    mode = candidate.get("controlMode")
    if not isinstance(mode, str) or mode.upper() not in {"AUTO", "MANUAL", "DISABLED"}:
        return False, "INVALID_CONTROL_MODE"
    mode = mode.upper()
    candidate["controlMode"] = mode
    numeric_keys = [key for key in FIELD_CONFIGURATION_ATTRIBUTES if key != "controlMode"]
    if any(
        not isinstance(candidate.get(key), (int, float))
        or isinstance(candidate.get(key), bool)
        or not 0 <= float(candidate[key]) <= 100
        for key in numeric_keys
    ):
        return False, "INVALID_THRESHOLD_RANGE"
    ordered = [
        float(candidate["criticalMoisture"]),
        float(candidate["minMoistureThreshold"]),
        float(candidate["targetMoisture"]),
        float(candidate["maxMoistureThreshold"]),
        float(candidate["floodMoistureThreshold"]),
    ]
    if any(left >= right for left, right in zip(ordered, ordered[1:])):
        return False, "INVALID_THRESHOLD_ORDER"

    previous_mode = target.control_mode
    combined = {**_threshold_mapping(model._limits_for(target.zone_id)), **candidate}
    model.zone_limits[target.zone_id] = IrrigationThresholds.from_mapping(combined)
    target.control_mode = mode
    runtime = model.zone_runtime[target.zone_id]
    if mode == "DISABLED" and target.valve_state == "ON":
        model._set_valve(target.zone_id, "OFF", "CONTROL_DISABLED", now_ms)
        model._sync_pump_with_transition()
    elif previous_mode == "AUTO" and mode == "MANUAL" and target.valve_state == "ON" and not runtime.active_request_source:
        model._set_valve(target.zone_id, "OFF", "CONTROL_MODE_CHANGED", now_ms)
        model._sync_pump_with_transition()
    elif previous_mode == "MANUAL" and mode == "AUTO":
        runtime.below_min_samples = 0

    if store is not None:
        store.save({zone_id: effective_field_config(model, zone_id) for zone_id in model.zones})
    return True, "CONFIG_APPLIED"


def restore_field_configuration(
    model: "SimulationModelV23",
    stored: dict[str, dict[str, Any]],
    now_ms: int,
) -> None:
    """Restore previously validated values before the cloud reconnects."""

    for zone_id, values in stored.items():
        zone = model.zones.get(zone_id)
        if zone is None or not isinstance(values, dict):
            continue
        apply_field_configuration_attribute(
            model,
            {"device": zone.valve_device, "data": values},
            now_ms,
        )


def apply_local_schedule_attribute(
    runner: LocalScheduleRunner,
    model: "SimulationModelV23",
    body: dict[str, Any],
    now_ms: int,
) -> tuple[bool, str, str] | None:
    """Apply live-update or request-response forms of one shared attribute."""

    device = body.get("device")
    if not isinstance(device, str) or not device:
        return None
    config: Any = None
    data = body.get("data")
    if isinstance(data, dict) and LOCAL_SCHEDULE_ATTRIBUTE in data:
        config = data[LOCAL_SCHEDULE_ATTRIBUTE]
    elif LOCAL_SCHEDULE_ATTRIBUTE in body:
        config = body[LOCAL_SCHEDULE_ATTRIBUTE]
    elif "value" in body:
        # Current Gateway API requests one key, while older compatible tenants
        # return that single value without repeating the key.
        config = body["value"]
    else:
        for scope in ("shared", "values"):
            values = body.get(scope)
            if isinstance(values, dict) and LOCAL_SCHEDULE_ATTRIBUTE in values:
                config = values[LOCAL_SCHEDULE_ATTRIBUTE]
                break
    if config is None:
        return None
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return False, "OFF", "INVALID_COMMAND"
    if not isinstance(config, dict) or config.get("schemaVersion") != 1:
        return False, "OFF", "INVALID_COMMAND"
    config_id = config.get("configId")
    if not isinstance(config_id, str) or not config_id.strip():
        return False, "OFF", "INVALID_COMMAND"
    target = next((zone for zone in model.zones.values() if zone.valve_device == device), None)
    if target is None:
        return False, "OFF", "INVALID_COMMAND"
    current = model.zone_runtime[target.zone_id].local_schedule or {}
    if current.get("configCommandId") == config_id:
        return True, target.valve_state, "DUPLICATE"
    return runner.update_from_config(model, device, config, config_id, now_ms)


class SimulationModelV23(SimulationModel):
    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SimulationModelV23":
        base = SimulationModel.from_config(config)
        model = cls(
            zones=base.zones,
            site=base.site,
            thresholds=base.thresholds,
            rng=base.rng,
            interval_seconds=base.interval_seconds,
        )
        sim = config.get("simulation", {})
        model.limits = IrrigationThresholds.from_mapping(sim.get("thresholds", {}))
        base_thresholds = sim.get("thresholds", {})
        model.zone_limits = {
            zone_id: IrrigationThresholds.from_mapping({**base_thresholds, **values})
            for zone_id, values in sim.get("thresholdsByZone", {}).items()
            if zone_id in model.zones and isinstance(values, dict)
        }
        model.pulses_per_liter = max(1.0, float(sim.get("pulsesPerLiter", 450)))
        model.manual_off_latch_seconds = max(0, int(sim.get("manualOffLatchSeconds", 300)))
        model.manual_on_max_seconds = max(1, int(sim.get("manualOnMaxSeconds", 300)))
        model.flow_grace_seconds = max(1.0, float(sim.get("flowGraceSeconds", 15)))
        model.minimum_flow_rate = max(0.0, float(sim.get("minimumFlowRate", 0.1)))
        model.sensor_stale_seconds = max(1.0, float(sim.get("sensorStaleSeconds", 30)))
        model.cloud_loss_timeout_seconds = max(1.0, float(sim.get("cloudLossTimeoutSeconds", 60)))
        start_environment = sim.get("startEnvironment", {})
        model.zone_runtime: dict[str, ZoneRuntime] = {}
        for index, zone_id in enumerate(model.zones, start=1):
            values = start_environment.get(zone_id, {})
            temp = float(values.get("airTemp", 29 + index))
            humidity = float(values.get("airHumidity", 62 - index))
            light = float(values.get("lightLux", 18000 + index * 1000))
            model.zone_runtime[zone_id] = ZoneRuntime(
                air_temp=temp,
                air_humidity=humidity,
                light_lux=light,
                vpd=model._calculate_vpd(temp, humidity),
            )
        for zone_id, mode in sim.get("controlModes", {}).items():
            normalized = str(mode).upper()
            if zone_id not in model.zones or normalized not in {"AUTO", "MANUAL", "DISABLED"}:
                raise ValueError(f"invalid control mode mapping: {zone_id}={mode}")
            model.zones[zone_id].control_mode = normalized
        model._pump_transition_sequence = 0
        model.hardware_adapter: HardwareAdapter | None = None
        model.simulate_physics = True
        model.controller_state = "ONLINE"
        model.controller_last_seen_ms = utc_ms()
        model.runtime_mode = "SIM_TWO_FIELD"
        model.sensor_data_origin = "SYNTHETIC"
        model.actuator_backend = "SIM"
        model.evidence_class = "SIM+PLATFORM"
        model.cloud_connected = True
        model.cloud_disconnected_since_ms = 0
        model.cloud_reconnect_count = 0
        return model

    def attach_hardware_adapter(
        self,
        adapter: HardwareAdapter,
        *,
        expected_sensors: dict[str, int],
        min_valid_sensors: dict[str, int],
    ) -> None:
        """Disable synthetic physics and make adapter ACK/samples authoritative."""

        if adapter.runtime_profile == "SIM_TWO_FIELD":
            raise ValueError("hardware runtime requires HIL or HARDWARE adapter")
        self.hardware_adapter = adapter
        self.simulate_physics = False
        self.runtime_mode = adapter.runtime_profile
        self.sensor_data_origin = adapter.sensor_data_origin
        self.actuator_backend = adapter.actuator_backend
        self.evidence_class = adapter.evidence_class
        self.controller_state = "OFFLINE"
        self.controller_last_seen_ms = 0
        self.site.pump_state = "OFF"
        self.site.system_mode = "SAFE-IDLE"
        for zone_id, zone in self.zones.items():
            zone.valve_state = "OFF"
            zone.control_mode = "DISABLED"
            runtime = self.zone_runtime[zone_id]
            runtime.data_quality = "INVALID"
            runtime.last_sensor_update_ms = 0
            runtime.soil_samples = ()
            runtime.expected_sensors = max(1, int(expected_sensors.get(zone_id, 4)))
            runtime.min_valid_sensors = max(
                1,
                min(
                    runtime.expected_sensors,
                    int(min_valid_sensors.get(zone_id, runtime.expected_sensors)),
                ),
            )

    def ingest_hardware_event(self, event: SensorSample | PeerStatus | ActuatorAck) -> None:
        """Apply validated adapter events without synthesizing physical state."""

        if isinstance(event, SensorSample):
            zone = self.zones.get(event.zone_id)
            if zone is None:
                return
            runtime = self.zone_runtime[event.zone_id]
            valid = tuple(value for value in event.soil_moisture if 0 <= value <= 100)
            runtime.sensor_node_id = event.node_id
            runtime.soil_samples = valid[: runtime.expected_sensors]
            runtime.last_sensor_update_ms = event.received_at_ms
            if len(runtime.soil_samples) >= runtime.min_valid_sensors:
                zone.moisture = sum(runtime.soil_samples) / len(runtime.soil_samples)
                runtime.data_quality = "OK"
            else:
                runtime.data_quality = "INVALID"
            if event.air_temp is not None:
                runtime.air_temp = event.air_temp
            if event.air_humidity is not None:
                runtime.air_humidity = event.air_humidity
            if event.light_lux is not None:
                runtime.light_lux = event.light_lux
            if event.air_temp is not None and event.air_humidity is not None:
                runtime.vpd = self._calculate_vpd(event.air_temp, event.air_humidity)
            if event.flow_rate_lpm is not None:
                runtime.last_flow_rate = event.flow_rate_lpm
            if event.pulse_counter is not None:
                previous = zone.pulse_counter
                zone.pulse_counter = max(previous, event.pulse_counter)
                delta_liters = max(0, zone.pulse_counter - previous) / self.pulses_per_liter
                if zone.valve_state == "ON":
                    zone.water_used += delta_liters
                    runtime.daily_water_liters += delta_liters
            if event.tank_low is not None:
                self.site.tank_low_switch = event.tank_low
            return
        if isinstance(event, PeerStatus) and event.role.upper() == "CENTRAL":
            self.controller_state = "ONLINE" if event.online else "OFFLINE"
            self.controller_last_seen_ms = event.received_at_ms
            if not event.online:
                self.site.system_mode = "DEGRADED"
            return
        if isinstance(event, ActuatorAck):
            self.controller_state = "ONLINE"
            self.controller_last_seen_ms = event.received_at_ms

    def _limits_for(self, zone_id: str) -> IrrigationThresholds:
        return self.zone_limits.get(zone_id, self.limits)

    def set_cloud_connected(self, connected: bool, now_ms: int | None = None) -> None:
        """Track cloud transport without surrendering local control authority."""

        now_ms = utc_ms() if now_ms is None else now_ms
        previous = self.cloud_connected
        self.cloud_connected = connected
        if not connected:
            if previous:
                self.cloud_disconnected_since_ms = now_ms
        else:
            if not previous:
                self.cloud_reconnect_count += 1
            self.cloud_disconnected_since_ms = 0
            if not self.site.tank_low_switch:
                self.site.system_mode = "NORMAL"

    def _refresh_operation_mode(self, now_ms: int) -> None:
        if self.site.tank_low_switch:
            self.site.system_mode = "SAFE-IDLE"
        elif not self.simulate_physics and self.controller_state != "ONLINE":
            self.site.system_mode = "SAFE-IDLE"
            self.site.safety_block_reason = "CONTROLLER_OFFLINE"
        elif self.cloud_connected:
            self.site.system_mode = "NORMAL"
        elif (
            self.cloud_disconnected_since_ms
            and now_ms - self.cloud_disconnected_since_ms >= self.cloud_loss_timeout_seconds * 1000
        ):
            self.site.system_mode = "DEGRADED"
        else:
            self.site.system_mode = "NORMAL"
        if not self.site.tank_low_switch and self.site.safety_block_reason == "TANK_LOW":
            self.site.safety_block_reason = "NONE"
        if self.controller_state == "ONLINE" and self.site.safety_block_reason == "CONTROLLER_OFFLINE":
            self.site.safety_block_reason = "NONE"

    def _refresh_sensor_health(self, now_ms: int) -> None:
        for runtime in self.zone_runtime.values():
            if self.simulate_physics and not runtime.sensor_fault:
                runtime.last_sensor_update_ms = now_ms
                runtime.data_quality = "OK"
            elif runtime.last_sensor_update_ms == 0:
                runtime.data_quality = "INVALID"
            elif now_ms - runtime.last_sensor_update_ms > self.sensor_stale_seconds * 1000:
                runtime.data_quality = "STALE"
        if (
            not self.simulate_physics
            and self.controller_last_seen_ms
            and now_ms - self.controller_last_seen_ms > self.sensor_stale_seconds * 1000
        ):
            self.controller_state = "OFFLINE"

    def _reset_daily_counters(self, now_ms: int) -> None:
        epoch_day = now_ms // 86_400_000
        for runtime in self.zone_runtime.values():
            if runtime.daily_epoch_day == -1:
                runtime.daily_epoch_day = epoch_day
            elif runtime.daily_epoch_day != epoch_day:
                runtime.daily_epoch_day = epoch_day
                runtime.daily_water_liters = 0.0

    def _update_debounce_counters(self) -> None:
        for zone_id, zone in self.zones.items():
            runtime = self.zone_runtime[zone_id]
            limits = self._limits_for(zone_id)
            runtime.below_min_samples = (
                runtime.below_min_samples + 1 if zone.moisture < limits.min_moisture else 0
            )
            runtime.above_flood_samples = (
                runtime.above_flood_samples + 1 if zone.moisture >= limits.flood_moisture else 0
            )

    @staticmethod
    def _calculate_vpd(air_temp: float, air_humidity: float) -> float:
        saturation = 0.6108 * math.exp((17.27 * air_temp) / (air_temp + 237.3))
        return saturation * (1 - air_humidity / 100)

    def _update_environment(self) -> None:
        for runtime in self.zone_runtime.values():
            runtime.air_temp += self.rng.uniform(-0.12, 0.12)
            runtime.air_humidity = max(0.0, min(100.0, runtime.air_humidity + self.rng.uniform(-0.3, 0.3)))
            runtime.light_lux = max(0.0, runtime.light_lux + self.rng.uniform(-80, 80))
            runtime.vpd = self._calculate_vpd(runtime.air_temp, runtime.air_humidity)

    def _field_input(self, zone_id: str, now_ms: int) -> FieldControlInput:
        zone = self.zones[zone_id]
        runtime = self.zone_runtime[zone_id]
        seconds_since_last = 0.0
        if runtime.last_irrigation_ended_ms:
            seconds_since_last = max(0.0, (now_ms - runtime.last_irrigation_ended_ms) / 1000)
        stale = bool(
            runtime.last_sensor_update_ms
            and now_ms - runtime.last_sensor_update_ms > self.sensor_stale_seconds * 1000
        )
        data_quality = "STALE" if stale else runtime.data_quality
        return FieldControlInput(
            zone_id=zone_id,
            avg_moisture=zone.moisture,
            air_temp=runtime.air_temp,
            air_humidity=runtime.air_humidity,
            vpd=runtime.vpd,
            light_lux=runtime.light_lux,
            actual_valve_state=zone.valve_state,
            control_mode=zone.control_mode,
            system_mode=self.site.system_mode,
            data_quality=data_quality,
            tank_low=self.site.tank_low_switch,
            cycle_water_liters=zone.water_used,
            daily_water_liters=runtime.daily_water_liters,
            irrigation_runtime_seconds=runtime.irrigation_runtime_seconds,
            below_min_samples=runtime.below_min_samples,
            above_flood_samples=runtime.above_flood_samples,
            seconds_since_last_irrigation=seconds_since_last,
            manual_off_latched=now_ms < runtime.manual_off_until_ms,
            manual_on_expired=bool(runtime.manual_on_until_ms and now_ms >= runtime.manual_on_until_ms),
            flow_rate=runtime.last_flow_rate,
            zero_flow_seconds=runtime.zero_flow_seconds,
            flow_grace_seconds=self.flow_grace_seconds,
            flow_fault_latched=runtime.flow_fault_latched,
        )

    def _evaluate_zone(self, zone_id: str, now_ms: int) -> FieldDecision:
        decision = evaluate_field(self._field_input(zone_id, now_ms), self._limits_for(zone_id))
        runtime = self.zone_runtime[zone_id]
        if (
            runtime.active_request_source == "MANUAL"
            and decision.reason == "TARGET_MOISTURE_REACHED"
        ):
            return FieldDecision(
                "KEEP_ON",
                "MANUAL_DURATION_ACTIVE",
                decision.priority,
                decision.environment_stress,
                decision.stress_factors,
            )
        if (
            decision.reason == "MANUAL_ON_TTL_EXPIRED"
            and runtime.active_request_source == "SCHEDULER"
        ):
            return FieldDecision(
                decision.action,
                "SCHEDULE_DURATION_REACHED",
                decision.priority,
                decision.environment_stress,
                decision.stress_factors,
            )
        return decision

    def _scheduled_task(
        self,
        zone_id: str,
        command: RpcCommand,
        now_ms: int,
        duration_seconds: int,
        status: str,
        reason: str,
    ) -> None:
        runtime = self.zone_runtime[zone_id]
        runtime.irrigation_task = {
            "startTs": now_ms,
            "durationThreshold": duration_seconds * 1000,
            "consumptionThreshold": 0,
            "consumption": 0.0,
            "duration": 0,
            "status": status,
            "resultReason": reason,
            "commandId": command.command_id,
            "scheduleId": command.params.get("scheduleId", ""),
            "source": command.params.get("source", "SCHEDULER"),
        }
        runtime.irrigation_task_dirty = True

    def _finish_scheduled_task(self, zone_id: str, now_ms: int, reason: str) -> None:
        runtime = self.zone_runtime[zone_id]
        task = runtime.irrigation_task
        if not task or task.get("status") != "RUNNING":
            return
        task["duration"] = max(0, now_ms - int(task["startTs"]))
        task["consumption"] = round(self.zones[zone_id].water_used, 3)
        if reason == "MANUAL_OFF":
            task["status"] = "CANCELLED"
        elif reason in HARD_STOP_REASONS:
            task["status"] = "SAFETY_STOPPED"
        else:
            task["status"] = "COMPLETED"
        task["resultReason"] = reason
        runtime.irrigation_task_dirty = True
        schedule = runtime.local_schedule
        if schedule is not None and task.get("scheduleId") == schedule.get("id"):
            schedule["status"] = task["status"]
            schedule["lastResultReason"] = reason

    def _set_valve(
        self,
        zone_id: str,
        state: str,
        reason: str,
        now_ms: int,
        command_id: str | None = None,
    ) -> bool:
        zone = self.zones[zone_id]
        runtime = self.zone_runtime[zone_id]
        generated_transition = zone.valve_state != state
        if generated_transition:
            runtime.transition_sequence += 1
        effective_command_id = command_id or f"local-{zone_id}-{runtime.transition_sequence}"
        if self.hardware_adapter is not None and generated_transition:
            ack = self.hardware_adapter.set_zone(
                zone_id,
                state,
                effective_command_id,
                lease_ms=15_000,
            )
            self.ingest_hardware_event(ack)
            zone.last_command_id = effective_command_id
            if not ack.accepted:
                zone.last_ack = "REJECTED"
                zone.decision_reason = ack.reason
                zone.safety_state = "BLOCKED"
                zone.safety_block_reason = ack.reason
                self.site.last_ack = "REJECTED"
                self.site.safety_block_reason = ack.reason
                self.site.system_mode = "DEGRADED"
                return False
            zone.valve_state = ack.valve_output
            self.site.pump_state = ack.pump_output
            zone.last_ack = "EXECUTED"
            self.site.last_command_id = effective_command_id
            self.site.last_ack = "EXECUTED"
            self.site.safety_block_reason = "NONE"
        elif generated_transition:
            zone.last_command_id = effective_command_id
            zone.last_ack = "EXECUTED"
            zone.valve_state = state
        if zone.valve_state != state:
            return False
        if generated_transition and state == "ON":
            runtime.irrigation_runtime_seconds = 0.0
            zone.water_used = 0.0
        elif generated_transition:
            self._finish_scheduled_task(zone_id, now_ms, reason)
            runtime.last_irrigation_ended_ms = now_ms
            runtime.manual_on_until_ms = 0
            runtime.active_request_source = ""
        zone.decision_reason = reason
        return True

    def decide(self, now_ms: int | None = None, local_zone_ids: set[str] | None = None) -> None:
        now_ms = utc_ms() if now_ms is None else now_ms
        active_before = [zone_id for zone_id, zone in self.zones.items() if zone.valve_state == "ON"]
        pump_dry_run = bool(active_before) and all(
            self.zone_runtime[zone_id].zero_flow_seconds >= self.flow_grace_seconds
            for zone_id in active_before
        )
        candidates: list[tuple[str, FieldDecision]] = []
        for zone_id, zone in self.zones.items():
            decision = self._evaluate_zone(zone_id, now_ms)
            self.zone_runtime[zone_id].decision = decision
            if local_zone_ids is not None and zone_id not in local_zone_ids:
                if decision.reason in HARD_STOP_REASONS and decision.action in {"STOP", "BLOCK"}:
                    if decision.reason == "ZONE_FLOW_LOW":
                        self.zone_runtime[zone_id].flow_fault_latched = True
                    self._set_valve(zone_id, "OFF", decision.reason, now_ms)
                    zone.safety_state = "BLOCKED"
                    zone.safety_block_reason = decision.reason
                else:
                    zone.decision_reason = "AWAITING_COREIOT_REQUEST"
                    zone.safety_state = "SAFE"
                    zone.safety_block_reason = "NONE"
                continue
            if decision.action in {"STOP", "BLOCK"}:
                if decision.reason == "ZONE_FLOW_LOW":
                    self.zone_runtime[zone_id].flow_fault_latched = True
                self._set_valve(zone_id, "OFF", decision.reason, now_ms)
                is_safety_stop = decision.reason in HARD_STOP_REASONS
                zone.safety_state = "BLOCKED" if is_safety_stop else "SAFE"
                zone.safety_block_reason = decision.reason if is_safety_stop else "NONE"
            elif decision.action == "REQUEST_START":
                candidates.append((zone_id, decision))
            else:
                zone.decision_reason = decision.reason
                zone.safety_state = "SAFE"
                zone.safety_block_reason = "NONE"

        active = sum(zone.valve_state == "ON" for zone in self.zones.values())
        available = max(0, self.site.max_concurrent_zones - active)
        candidates.sort(
            key=lambda item: (
                -item[1].priority,
                self.zones[item[0]].moisture,
                self.zone_runtime[item[0]].last_irrigation_ended_ms,
            )
        )
        for index, (zone_id, decision) in enumerate(candidates):
            zone = self.zones[zone_id]
            if index < available:
                self._set_valve(zone_id, "ON", decision.reason, now_ms)
                zone.safety_state = "SAFE"
                zone.safety_block_reason = "NONE"
            else:
                zone.decision_reason = "WAITING_FOR_SCHEDULER_SLOT"

        self._sync_pump_with_transition()
        if pump_dry_run:
            self.site.safety_block_reason = "PUMP_DRY_RUN"
            self.site.last_ack = "REJECTED"
        elif self.site.pump_state == "ON":
            self.site.safety_block_reason = "NONE"

    def _sync_pump_with_transition(self) -> None:
        if self.hardware_adapter is not None:
            return
        previous = self.site.pump_state
        self._sync_pump_state()
        if previous != self.site.pump_state:
            self._pump_transition_sequence += 1
            self.site.last_command_id = f"local-pump-{self._pump_transition_sequence}"
            self.site.last_ack = "EXECUTED"

    def clear_flow_fault(self, zone_id: str) -> None:
        runtime = self.zone_runtime[zone_id]
        runtime.flow_fault = False
        runtime.flow_fault_latched = False
        runtime.zero_flow_seconds = 0.0
        zone = self.zones[zone_id]
        if zone.safety_block_reason == "ZONE_FLOW_LOW":
            zone.safety_block_reason = "NONE"
            zone.safety_state = "SAFE"
        if not any(item.flow_fault_latched for item in self.zone_runtime.values()):
            if self.site.safety_block_reason == "PUMP_DRY_RUN":
                self.site.safety_block_reason = "NONE"

    def _enforce_tank_low_immediately(self, now_ms: int) -> None:
        if not self.site.tank_low_switch:
            return
        for zone_id, zone in self.zones.items():
            if zone.valve_state == "ON":
                self._set_valve(zone_id, "OFF", "TANK_LOW", now_ms)
            zone.safety_state = "BLOCKED"
            zone.safety_block_reason = "TANK_LOW"
        self.site.system_mode = "SAFE-IDLE"
        self.site.safety_block_reason = "TANK_LOW"
        self._sync_pump_with_transition()

    def enforce_runtime_safety(self, now_ms: int | None = None) -> None:
        now_ms = utc_ms() if now_ms is None else now_ms
        for zone_id, zone in self.zones.items():
            inputs = self._field_input(zone_id, now_ms)
            decision = self._evaluate_zone(zone_id, now_ms)
            self.zone_runtime[zone_id].decision = decision
            if decision.reason in HARD_STOP_REASONS and decision.action in {"STOP", "BLOCK"}:
                if decision.reason == "ZONE_FLOW_LOW":
                    self.zone_runtime[zone_id].flow_fault_latched = True
                self._set_valve(zone_id, "OFF", decision.reason, now_ms)
                zone.safety_state = "BLOCKED"
                zone.safety_block_reason = decision.reason
            else:
                zone.decision_reason = "AWAITING_COREIOT_REQUEST"
                zone.safety_state = "SAFE"
                zone.safety_block_reason = "NONE"
        self._sync_pump_with_transition()

    def apply_rpc(
        self,
        command: RpcCommand,
        now_ms: int | None = None,
    ) -> tuple[bool, str, str]:
        target = next((item for item in self.zones.values() if item.valve_device == command.device), None)
        if target is None:
            if command.device != self.site.pump_device:
                return False, "OFF", "SAFETY_BLOCK"
            now_ms = utc_ms() if now_ms is None else now_ms
            if command.method == "TURN_OFF":
                all_stopped = True
                for zone_id, zone in self.zones.items():
                    self.zone_runtime[zone_id].manual_off_until_ms = (
                        now_ms + self.manual_off_latch_seconds * 1000
                    )
                    stopped = self._set_valve(
                        zone_id,
                        "OFF",
                        "MANUAL_PUMP_OFF",
                        now_ms,
                        f"{command.command_id}-{zone_id}",
                    )
                    all_stopped = all_stopped and stopped
                self._sync_pump_with_transition()
                self.site.last_command_id = command.command_id
                self.site.last_ack = "EXECUTED" if all_stopped else "REJECTED"
                return (
                    all_stopped,
                    self.site.pump_state,
                    "EXECUTED" if all_stopped else "SAFETY_BLOCK",
                )
            self.site.safety_block_reason = "DIRECT_PUMP_ON_FORBIDDEN"
            self.site.last_command_id = command.command_id
            self.site.last_ack = "REJECTED"
            return False, self.site.pump_state, "SAFETY_BLOCK"
        now_ms = utc_ms() if now_ms is None else now_ms
        runtime = self.zone_runtime[target.zone_id]
        source = str(command.params.get("source", "")).upper()
        requested_duration = 0
        if source in {"MANUAL", "SCHEDULER"} and command.method == "TURN_ON":
            raw_duration = requested_run_duration_seconds(command)
            if raw_duration is None:
                target.last_command_id = command.command_id
                target.last_ack = "REJECTED"
                target.decision_reason = "MISSING_RUN_DURATION"
                return False, target.valve_state, "INVALID_COMMAND"
            if source == "MANUAL" and "runDurationSeconds" not in command.params:
                LOG.warning(
                    "manualTtlSeconds is deprecated commandId=%s; use runDurationSeconds",
                    command.command_id,
                )
            requested_duration = max(
                1,
                min(
                    int(raw_duration),
                    self.manual_on_max_seconds,
                    int(self._limits_for(target.zone_id).max_duration_seconds),
                ),
            )
        if command.method == "TURN_OFF":
            latch_seconds = max(0, int(command.params.get("latchSeconds", self.manual_off_latch_seconds)))
            runtime.manual_off_until_ms = now_ms + latch_seconds * 1000
            stopped = self._set_valve(
                target.zone_id,
                "OFF",
                "MANUAL_OFF",
                now_ms,
                command.command_id,
            )
            self._sync_pump_with_transition()
            if not stopped:
                return False, target.valve_state, "SAFETY_BLOCK"
            target.last_command_id = command.command_id
            target.last_ack = "EXECUTED"
            return True, "OFF", "EXECUTED"

        if target.valve_state == "ON":
            if source == "SCHEDULER":
                self._scheduled_task(
                    target.zone_id,
                    command,
                    now_ms,
                    requested_duration,
                    "REJECTED",
                    "ALREADY_ACTIVE",
                )
            target.last_command_id = command.command_id
            target.last_ack = "REJECTED"
            target.decision_reason = "ALREADY_ACTIVE"
            return False, "ON", "SAFETY_BLOCK"
        inputs = self._field_input(target.zone_id, now_ms)
        blocked = evaluate_field(inputs, self._limits_for(target.zone_id))
        active_other = sum(z.valve_state == "ON" and z.zone_id != target.zone_id for z in self.zones.values())
        reason = blocked.reason if blocked.reason in HARD_STOP_REASONS else "NONE"
        if reason == "NONE" and active_other >= self.site.max_concurrent_zones:
            reason = "MAX_CONCURRENT_ZONES"
        if reason != "NONE":
            if source == "SCHEDULER":
                self._scheduled_task(
                    target.zone_id, command, now_ms, requested_duration, "REJECTED", reason
                )
            target.last_command_id = command.command_id
            target.last_ack = "REJECTED"
            target.safety_state = "BLOCKED"
            target.safety_block_reason = reason
            target.decision_reason = reason
            return False, "OFF", "SAFETY_BLOCK"
        runtime.manual_off_until_ms = 0
        if source == "MANUAL":
            runtime.manual_on_until_ms = now_ms + requested_duration * 1000
            runtime.active_request_source = "MANUAL"
        elif source == "SCHEDULER":
            runtime.manual_on_until_ms = now_ms + requested_duration * 1000
            runtime.active_request_source = "SCHEDULER"
            self._scheduled_task(
                target.zone_id,
                command,
                now_ms,
                requested_duration,
                "RUNNING",
                "SCHEDULE_STARTED",
            )
        else:
            runtime.manual_on_until_ms = 0
            runtime.active_request_source = ""
        start_reason = "SCHEDULED_ON" if source == "SCHEDULER" else "MANUAL_ON"
        started = self._set_valve(
            target.zone_id,
            "ON",
            start_reason,
            now_ms,
            command.command_id,
        )
        if not started:
            if source == "SCHEDULER":
                self._finish_scheduled_task(target.zone_id, now_ms, target.decision_reason)
            runtime.manual_on_until_ms = 0
            runtime.active_request_source = ""
            return False, target.valve_state, "SAFETY_BLOCK"
        target.last_command_id = command.command_id
        target.last_ack = "EXECUTED"
        self._sync_pump_with_transition()
        return True, "ON", "EXECUTED"

    def tick(
        self,
        timestamp: int | None = None,
        local_decision: bool = True,
        local_control_zones: set[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        now = timestamp or utc_ms()
        self._reset_daily_counters(now)
        self._refresh_sensor_health(now)
        self._update_debounce_counters()
        if self.simulate_physics:
            self.site.tank_low_switch = self.site.tank_level_pct <= 10.0
        self._enforce_tank_low_immediately(now)
        self._refresh_operation_mode(now)
        if self.simulate_physics:
            self._update_environment()
        if local_control_zones is not None:
            self.decide(now, local_control_zones)
        elif local_decision:
            self.decide(now)
        else:
            self.enforce_runtime_safety(now)

        readings: dict[str, list[dict[str, Any]]] = {}
        active_zones = sum(zone.valve_state == "ON" for zone in self.zones.values())
        for sensor_zone_index, (zone_id, zone) in enumerate(self.zones.items(), start=1):
            runtime = self.zone_runtime[zone_id]
            if zone.valve_state == "ON":
                flow_rate = (
                    0.0 if runtime.flow_fault else round(self.rng.uniform(8.0, 12.0), 2)
                ) if self.simulate_physics else runtime.last_flow_rate
                if self.simulate_physics:
                    if flow_rate >= self.minimum_flow_rate:
                        zone.moisture = min(100.0, zone.moisture + self.rng.uniform(1.5, 3.0))
                    liters = flow_rate * self.interval_seconds / 60.0
                    pulses = round(liters * self.pulses_per_liter)
                    zone.pulse_counter += pulses
                    zone.water_used += pulses / self.pulses_per_liter
                    runtime.daily_water_liters += pulses / self.pulses_per_liter
                runtime.irrigation_runtime_seconds += self.interval_seconds
                if flow_rate < self.minimum_flow_rate:
                    runtime.zero_flow_seconds += self.interval_seconds
                else:
                    runtime.zero_flow_seconds = 0.0
            else:
                if self.simulate_physics:
                    zone.moisture = max(0.0, zone.moisture - self.rng.uniform(0.05, 0.25))
                flow_rate = 0.0 if self.simulate_physics else runtime.last_flow_rate
                runtime.zero_flow_seconds = 0.0
            runtime.last_flow_rate = flow_rate

            for sensor_index, device in enumerate(zone.soil_devices, start=1):
                sensor_values: dict[str, Any] = {"dataQuality": runtime.data_quality}
                if self.simulate_physics:
                    offset = (sensor_index - (len(zone.soil_devices) + 1) / 2) * 0.35
                    sensor_values.update({
                        "moisture": round(max(0, min(100, zone.moisture + offset)), 2),
                        "battery": 92 - sensor_index,
                    })
                elif sensor_index <= len(runtime.soil_samples):
                    sensor_values["moisture"] = round(runtime.soil_samples[sensor_index - 1], 2)
                readings[device] = [{"ts": now, "values": sensor_values}]

            env_values: dict[str, Any] = {"dataQuality": runtime.data_quality}
            if self.simulate_physics or runtime.last_sensor_update_ms:
                env_values.update({
                    "airTemp": round(runtime.air_temp, 2),
                    "airHumidity": round(runtime.air_humidity, 2),
                    "lightLux": round(runtime.light_lux, 1),
                    "vpd": round(runtime.vpd, 3),
                })
            if self.simulate_physics:
                env_values.update({"battery": 90, "rssi": -55 - sensor_zone_index})
            readings[zone.env_device] = [{"ts": now, "values": env_values}]
            decision = runtime.decision or FieldDecision("KEEP_OFF", "INITIALIZED", 0, False, ())
            valve_values = {
                "valveState": zone.valve_state,
                "actualValveState": zone.valve_state,
                "lastCommandId": zone.last_command_id,
                "lastAck": zone.last_ack,
                "commandStatus": zone.last_ack,
                "decisionReason": zone.decision_reason,
                "irrigationPriority": decision.priority,
                "environmentStress": decision.environment_stress,
                "stressFactors": list(decision.stress_factors),
                "safetyBlockReason": zone.safety_block_reason,
                "controlMode": zone.control_mode,
                "systemMode": self.site.system_mode,
                "irrigationRuntimeSec": round(runtime.irrigation_runtime_seconds, 1),
                "waterloggingRisk": zone.safety_block_reason == "WATERLOGGING_RISK",
                "irrigationWatchdog": zone.safety_block_reason == "MAX_DURATION_REACHED",
                "waterQuotaExceeded": zone.safety_block_reason in {"CYCLE_QUOTA_REACHED", "DAILY_QUOTA_REACHED"},
                "fieldDataInvalid": zone.safety_block_reason == "FIELD_DATA_INVALID",
            }
            effective_limits = self._limits_for(zone_id)
            valve_values.update({
                "criticalMoisture": effective_limits.critical_moisture,
                "minMoistureThreshold": effective_limits.min_moisture,
                "targetMoisture": effective_limits.target_moisture,
                "maxMoistureThreshold": effective_limits.max_moisture,
                "floodMoistureThreshold": effective_limits.flood_moisture,
            })
            schedule = runtime.local_schedule
            if schedule is not None:
                valve_values.update({
                    "gatewayScheduleId": schedule["id"],
                    "gatewayScheduleSource": schedule["source"],
                    "gatewayScheduleEnabled": schedule["enabled"],
                    "gatewayScheduleStatus": schedule["status"],
                    "gatewayScheduleNextRunTs": schedule["nextRunTs"],
                    "gatewayScheduleDurationSec": schedule["durationSeconds"],
                    "gatewayScheduleRepeatEverySec": schedule["repeatEverySeconds"],
                    "gatewayScheduleLastRunTs": schedule["lastRunTs"],
                    "gatewayScheduleLastResultReason": schedule["lastResultReason"],
                    "gatewayScheduleConfigCommandId": schedule.get("configCommandId", ""),
                    "gatewayScheduleConfigAck": schedule.get("configAck", "NONE"),
                    "gatewayScheduleConfigReason": schedule.get("configReason", "NONE"),
                })
            valve_values.update({
                "waterConsumptionLiters": round(zone.pulse_counter / self.pulses_per_liter, 3),
                "cycleWaterLiters": round(zone.water_used, 3),
                "dailyWaterLiters": round(runtime.daily_water_liters, 3),
            })
            readings[zone.valve_device] = [{"ts": now, "values": valve_values}]
            task = runtime.irrigation_task
            if task and (task.get("status") == "RUNNING" or runtime.irrigation_task_dirty):
                task_snapshot = dict(task)
                if task_snapshot.get("status") == "RUNNING":
                    task_snapshot["duration"] = max(0, now - int(task_snapshot["startTs"]))
                    task_snapshot["consumption"] = round(zone.water_used, 3)
                readings[zone.valve_device].append({
                    "ts": int(task_snapshot["startTs"]),
                    "values": {"irrigationTask": task_snapshot},
                })
                runtime.irrigation_task_dirty = False
            meter_values: dict[str, Any] = {
                "pulseCounter": zone.pulse_counter,
                "flowRate": flow_rate,
                "waterConsumptionLiters": round(zone.pulse_counter / self.pulses_per_liter, 3),
                "cycleWaterLiters": round(zone.water_used, 3),
                "dailyWaterLiters": round(runtime.daily_water_liters, 3),
                "pulsesPerLiter": self.pulses_per_liter,
                "zeroFlowSeconds": round(runtime.zero_flow_seconds, 1),
                "zoneFlowLow": zone.safety_block_reason == "ZONE_FLOW_LOW",
                "dataQuality": runtime.data_quality,
            }
            if self.simulate_physics:
                meter_values["battery"] = 89
            readings[zone.water_meter_device] = [{"ts": now, "values": meter_values}]

        if self.site.pump_state == "ON":
            self.site.pump_runtime_sec += round(self.interval_seconds)
            if self.simulate_physics:
                self.site.tank_level_pct = max(0.0, self.site.tank_level_pct - 0.05 * active_zones)
        if self.simulate_physics:
            self.site.tank_low_switch = self.site.tank_level_pct <= 10.0
        self._enforce_tank_low_immediately(now)
        self._refresh_operation_mode(now)
        active_zones = sum(zone.valve_state == "ON" for zone in self.zones.values())
        for zone in self.zones.values():
            values = readings[zone.valve_device][0]["values"]
            values["valveState"] = zone.valve_state
            values["actualValveState"] = zone.valve_state
            values["decisionReason"] = zone.decision_reason
            values["safetyBlockReason"] = zone.safety_block_reason
        readings[self.site.pump_device] = [{"ts": now, "values": {
            "pumpState": self.site.pump_state,
            "runtimeSec": self.site.pump_runtime_sec,
            "lastCommandId": self.site.last_command_id,
            "lastAck": self.site.last_ack,
            "safetyBlockReason": self.site.safety_block_reason,
            "pumpDryRun": self.site.safety_block_reason == "PUMP_DRY_RUN",
        }}]
        readings[self.site.manifold_device] = [{"ts": now, "values": {
            "tankLowSwitch": self.site.tank_low_switch,
            "tankLevelPct": round(self.site.tank_level_pct, 2),
            "activeZoneCount": active_zones,
            "controllerState": self.controller_state,
            "systemMode": self.site.system_mode,
            "cloudConnected": self.cloud_connected,
            "cloudReconnectCount": self.cloud_reconnect_count,
            "waterloggingRisk": any(
                zone.safety_block_reason == "WATERLOGGING_RISK" for zone in self.zones.values()
            ),
        }}]
        return readings

    def gateway_health(
        self,
        connected_device_count: int,
        buffer_depth: int,
        expired_buffer_count: int = 0,
    ) -> dict[str, Any]:
        health = super().gateway_health(connected_device_count, buffer_depth)
        health["values"].update({
            "cloudConnected": self.cloud_connected,
            "cloudDisconnectedSince": self.cloud_disconnected_since_ms,
            "cloudReconnectCount": self.cloud_reconnect_count,
            "operationMode": self.site.system_mode,
            "expiredBufferCount": expired_buffer_count,
            "gatewayDegraded": self.site.system_mode in {"DEGRADED", "SAFE-IDLE"},
            "runtimeMode": self.runtime_mode,
            "sensorDataOrigin": self.sensor_data_origin,
            "actuatorBackend": self.actuator_backend,
            "evidenceClass": self.evidence_class,
        })
        return health


def effective_rpc_authority(
    model: SimulationModelV23,
    device_name: str,
    authority: str,
    zone_authorities: dict[str, str],
) -> str:
    """Resolve cloud command authority for a valve; shared/unknown devices are local in MIXED mode."""

    if authority != "MIXED":
        return authority
    target = next(
        (zone for zone in model.zones.values() if zone.valve_device == device_name),
        None,
    )
    if target is None:
        return "LOCAL"
    return zone_authorities.get(target.zone_id, "LOCAL")


def legacy_manual_off_allowed(
    model: SimulationModelV23,
    command: RpcCommand,
    authority: str,
    zone_authorities: dict[str, str],
) -> bool:
    """Keep the parameter-less OFF fallback only on explicitly cloud-controlled zones."""

    return (
        command.method == "TURN_OFF"
        and effective_rpc_authority(model, command.device, authority, zone_authorities)
        == "COREIOT_REQUEST"
    )


def apply_rpc_with_authority(
    model: SimulationModelV23,
    command: RpcCommand,
    authority: str,
    zone_authorities: dict[str, str],
) -> tuple[bool, str, str]:
    """Reject cloud automation on local zones while preserving operator manual/scheduled requests."""

    effective_authority = effective_rpc_authority(
        model,
        command.device,
        authority,
        zone_authorities,
    )
    source = command.params.get("source")
    if effective_authority == "LOCAL" and source not in {"MANUAL", "SCHEDULER"}:
        target = next(
            (zone for zone in model.zones.values() if zone.valve_device == command.device),
            None,
        )
        current_state = target.valve_state if target is not None else model.site.pump_state
        LOG.warning(
            "rpc rejected device=%s method=%s source=%s reason=LOCAL_AUTHORITY",
            command.device,
            command.method,
            source,
        )
        return False, current_state, "SAFETY_BLOCK"
    return model.apply_rpc(command)


FAULT_INJECTION_ACTIONS = {
    "SET_TANK_LEVEL_PCT",
    "SET_FLOW_FAULT",
    "CLEAR_FLOW_FAULT",
    "DISCONNECT_CLOUD",
    "RECONNECT_CLOUD",
}


def fault_injections_from_config(
    config: dict[str, Any],
    model: SimulationModelV23,
) -> dict[int, list[dict[str, Any]]]:
    """Validate an opt-in, iteration-based live fault plan."""

    raw_events = config.get("simulation", {}).get("faultInjections", [])
    if not isinstance(raw_events, list):
        raise ValueError("simulation.faultInjections must be a list")
    plan: dict[int, list[dict[str, Any]]] = {}
    for index, raw in enumerate(raw_events, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"faultInjections[{index}] must be an object")
        iteration = raw.get("atIteration")
        action = str(raw.get("action", "")).upper()
        if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
            raise ValueError(f"faultInjections[{index}] atIteration must be >= 1")
        if action not in FAULT_INJECTION_ACTIONS:
            raise ValueError(f"faultInjections[{index}] has unsupported action={action}")
        event = {**raw, "action": action}
        if action == "SET_TANK_LEVEL_PCT":
            value = raw.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
                raise ValueError(f"faultInjections[{index}] tank value must be 0..100")
        if action in {"SET_FLOW_FAULT", "CLEAR_FLOW_FAULT"}:
            zone_id = str(raw.get("zoneId", ""))
            if zone_id not in model.zones:
                raise ValueError(f"faultInjections[{index}] has unknown zoneId={zone_id}")
        plan.setdefault(iteration, []).append(event)
    return plan


def apply_fault_injection(
    event: dict[str, Any],
    model: SimulationModelV23,
    client: GatewayClient | None,
    iteration: int,
) -> None:
    """Apply one explicit Gateway-side fault without changing CoreIoT state."""

    action = event["action"]
    LOG.warning(
        "fault injection iteration=%s action=%s zone=%s value=%s",
        iteration,
        action,
        event.get("zoneId", "SITE"),
        event.get("value", "NONE"),
    )
    if action == "SET_TANK_LEVEL_PCT":
        model.site.tank_level_pct = float(event["value"])
    elif action == "SET_FLOW_FAULT":
        model.zone_runtime[str(event["zoneId"])].flow_fault = True
    elif action == "CLEAR_FLOW_FAULT":
        model.clear_flow_fault(str(event["zoneId"]))
    elif action == "DISCONNECT_CLOUD":
        if client is None:
            raise RuntimeError("DISCONNECT_CLOUD requires live CoreIoT transport")
        client.pause_transport()
        model.set_cloud_connected(False)
    elif action == "RECONNECT_CLOUD":
        if client is None:
            raise RuntimeError("RECONNECT_CLOUD requires live CoreIoT transport")
        client.resume_transport()


def run(
    config_path: Path,
    ticks: int | None,
    interval: float | None,
    offline: bool,
    control_authority: str | None,
) -> None:
    config = load_config(config_path)
    if config.get("transportMode") != "GATEWAY":
        raise ValueError("simulator_v23.py requires transportMode=GATEWAY")
    runtime_profile = str(config.get("runtimeProfile", "SIM_TWO_FIELD")).upper()
    if runtime_profile != "SIM_TWO_FIELD":
        raise ValueError("simulator_v23.py only runs SIM_TWO_FIELD; use gateway_runtime.py for HIL/HARDWARE")
    model = SimulationModelV23.from_config(config)
    if interval is not None:
        model.interval_seconds = interval
    authority = (control_authority or config.get("controlAuthority", "LOCAL")).upper()
    if authority not in {"LOCAL", "COREIOT_REQUEST", "MIXED"}:
        raise ValueError("controlAuthority must be LOCAL, COREIOT_REQUEST or MIXED")
    local_control_zones: set[str] | None = None
    zone_authorities: dict[str, str] = {}
    if authority == "MIXED":
        raw_authorities = config.get("controlAuthorityByZone")
        if not isinstance(raw_authorities, dict):
            raise ValueError("MIXED controlAuthority requires controlAuthorityByZone")
        unknown_zones = set(raw_authorities) - set(model.zones)
        missing_zones = set(model.zones) - set(raw_authorities)
        if unknown_zones or missing_zones:
            raise ValueError(
                f"controlAuthorityByZone mismatch unknown={sorted(unknown_zones)} missing={sorted(missing_zones)}"
            )
        zone_authorities = {zone_id: str(value).upper() for zone_id, value in raw_authorities.items()}
        invalid = {zone_id: value for zone_id, value in zone_authorities.items() if value not in {"LOCAL", "COREIOT_REQUEST"}}
        if invalid:
            raise ValueError(f"invalid per-zone control authority: {invalid}")
        local_control_zones = {zone_id for zone_id, value in zone_authorities.items() if value == "LOCAL"}

    state_directory = runtime_state_dir(config_path, runtime_profile)
    runtime_schedule_path = state_directory / "schedules.json"
    legacy_schedule_path = config_path.with_suffix(config_path.suffix + ".runtime-schedules.json")
    schedule_restore_path = (
        runtime_schedule_path
        if runtime_schedule_path.exists()
        else legacy_schedule_path
    )
    field_config_store = FieldConfigStore(state_directory / "field-config.json")
    restore_field_configuration(model, field_config_store.load(), utc_ms())
    if schedule_restore_path.exists():
        persisted = json.loads(schedule_restore_path.read_text(encoding="utf-8"))
        now_ms = utc_ms()
        restored: list[dict[str, Any]] = []
        for item in persisted.get("localSchedules", []):
            start_at_ms = item.get("startAtMs")
            if not isinstance(start_at_ms, int):
                continue
            repeat_every = int(item.get("repeatEverySeconds") or 0)
            if start_at_ms < now_ms and repeat_every > 0:
                missed = ((now_ms - start_at_ms) // (repeat_every * 1000)) + 1
                start_at_ms += missed * repeat_every * 1000
            if start_at_ms < now_ms:
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
        if restored:
            config = {**config, "localSchedules": restored}
            LOG.info("restored local schedules count=%s path=%s", len(restored), schedule_restore_path)
    local_scheduler = LocalScheduleRunner.from_config(config, model, utc_ms())
    fault_plan = fault_injections_from_config(config, model)

    def handle_rpc(command: RpcCommand) -> tuple[bool, str, str]:
        if command.method == "SET_LOCAL_SCHEDULE":
            result = local_scheduler.update_from_rpc(model, command, utc_ms())
            if result[0]:
                local_scheduler.persist(runtime_schedule_path)
            return result
        return apply_rpc_with_authority(model, command, authority, zone_authorities)

    def handle_attributes(body: dict[str, Any]) -> None:
        now_ms = utc_ms()
        field_result = apply_field_configuration_attribute(
            model,
            body,
            now_ms,
            field_config_store,
        )
        if field_result is not None:
            accepted, reason = field_result
            log = LOG.info if accepted else LOG.warning
            log(
                "field configuration device=%s accepted=%s result=%s",
                body.get("device"),
                accepted,
                reason,
            )
        result = apply_local_schedule_attribute(local_scheduler, model, body, now_ms)
        if result is None:
            return
        success, state, reason = result
        device = body.get("device")
        if success and reason != "DUPLICATE":
            local_scheduler.persist(runtime_schedule_path)
        log = LOG.info if success else LOG.warning
        log(
            "schedule attribute device=%s accepted=%s state=%s result=%s",
            device,
            success,
            state,
            reason,
        )

    client: GatewayClient | None = None
    if not offline:
        client = GatewayClient(
            settings_from_env(config),
            handle_rpc,
            attribute_handler=handle_attributes,
            connection_handler=model.set_cloud_connected,
            legacy_manual_off_allowed=lambda command: legacy_manual_off_allowed(
                model,
                command,
                authority,
                zone_authorities,
            ),
            command_ledger=PersistentCommandLedger(state_directory / "command-ledger.json"),
            clock_ready=lambda: clock_is_ready(require_ntp=False),
        )
        client.start()
        for device in config["devices"]:
            client.connect_device(device["name"], device["profile"])
        for valve_device in {zone.valve_device for zone in model.zones.values()}:
            client.watch_shared_attributes(valve_device, SHARED_ATTRIBUTE_WATCHES)
    else:
        model.set_cloud_connected(
            False,
            now_ms=utc_ms() - int(model.cloud_loss_timeout_seconds * 1000),
        )

    stopped = False

    def stop_handler(signum: int, frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    iteration = 0
    LOG.info(
        "transport=%s controlAuthority=%s zones=%s",
        "OFFLINE" if offline else "COREIOT",
        authority,
        zone_authorities or "ALL",
    )
    try:
        while not stopped and (ticks is None or iteration < ticks):
            iteration += 1
            for event in fault_plan.get(iteration, []):
                apply_fault_injection(event, model, client, iteration)
            now_ms = utc_ms()
            local_scheduler.dispatch_due(model, now_ms)
            readings = model.tick(
                timestamp=now_ms,
                local_decision=authority == "LOCAL",
                local_control_zones=local_control_zones,
            )
            if client:
                client.publish_telemetry(readings)
                client.publish_gateway_telemetry(
                    model.gateway_health(
                        len(config["devices"]),
                        client.buffer_depth,
                        client.expired_buffer_count,
                    )
                )
            active_ids = [zone_id for zone_id, zone in model.zones.items() if zone.valve_state == "ON"]
            LOG.info(
                "tick=%s devices=%s activeZones=%s active=%s pump=%s mode=%s",
                iteration,
                len(readings),
                len(active_ids),
                active_ids or "NONE",
                model.site.pump_state,
                model.site.system_mode,
            )
            if ticks is None or iteration < ticks:
                time.sleep(model.interval_seconds)
    finally:
        shutdown_ms = utc_ms()
        for zone_id in model.zones:
            model._set_valve(zone_id, "OFF", "GATEWAY_SHUTDOWN", shutdown_ms)
        model._sync_pump_with_transition()
        LOG.info("shutdown finalValveStates=%s pump=%s", {
            zone_id: zone.valve_state for zone_id, zone in model.zones.items()
        }, model.site.pump_state)
        if client:
            client.disconnect_devices([device["name"] for device in config["devices"]])
            client.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartFarm v2.3 edge-authoritative Gateway simulator")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ticks", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--offline", action="store_true", help="Do not connect MQTT; control authority is unaffected")
    parser.add_argument("--control-authority", choices=["LOCAL", "COREIOT_REQUEST", "MIXED"], default=None)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    run(args.config, args.ticks, args.interval, args.offline, args.control_authority)
