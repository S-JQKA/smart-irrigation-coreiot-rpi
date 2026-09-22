"""SmartFarm irrigation controller, scheduler and configuration admission."""

from __future__ import annotations
import json
import logging
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from control_engine import (
    FieldControlInput,
    FieldDecision,
    IrrigationThresholds,
    evaluate_field,
)
from hardware_adapter import (
    ActuatorAck,
    HardwareAdapter,
    PeerStatus,
    SensorSample,
    FlowSample,
)
from coreiot.gateway_protocol import RpcCommand, requested_run_duration_seconds
from controller_state import ControllerState
from configuration import utc_ms
from scheduler import LocalSchedule, LocalScheduleRunner
from runtime_state import (
    AtomicJsonFile,
    FieldConfigStore,
)

LOG = logging.getLogger("smartfarm.gateway.controller")
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
    "SYSTEM_SAFE_IDLE",
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
    soil_samples: tuple[float | None, ...] = ()
    control_soil_samples: tuple[float, ...] = ()
    soil_sample_consensus: tuple[bool, ...] = ()
    sensor_node_id: str = ""
    expected_sensors: int = 4
    min_valid_sensors: int = 3
    sensor_agreement_tolerance_pct: float = 15.0
    last_sensor_sequence: int = -1
    last_debounce_sequence: int = -1
    off_reassert_pending: bool = False
    off_reassert_last_ms: int = 0
    flow_state_known: bool = False
    last_flow_update_ms: int = 0
    flow_boot_id: str = ""
    last_flow_sequence: int = -1
    raw_pulse_counter: int | None = None
    total_water_liters: float = 0.0
    environment_valid: bool = False
    config_validated: bool = False






LOCAL_SCHEDULE_ATTRIBUTE = "localScheduleConfig"
FIELD_CORE_ATTRIBUTES = (
    "controlMode",
    "criticalMoisture",
    "minMoistureThreshold",
    "targetMoisture",
    "maxMoistureThreshold",
    "floodMoistureThreshold",
)
FIELD_QUOTA_ATTRIBUTES = ("maxWaterPerCycle", "maxWaterPerDay", "maxDurationSec")
FIELD_CONFIGURATION_ATTRIBUTES = (*FIELD_CORE_ATTRIBUTES, *FIELD_QUOTA_ATTRIBUTES)
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


def effective_field_config(
    model: "IrrigationController", zone_id: str
) -> dict[str, Any]:
    values = _threshold_mapping(model._limits_for(zone_id))
    return {
        "controlMode": model.zones[zone_id].control_mode,
        **{
            key: values[key]
            for key in FIELD_CONFIGURATION_ATTRIBUTES
            if key != "controlMode"
        },
    }


def apply_field_configuration_attribute(
    model: "IrrigationController",
    body: dict[str, Any],
    now_ms: int,
    store: FieldConfigStore | None = None,
) -> tuple[bool, str] | None:
    """Atomically validate and apply one Field's cloud-owned soft configuration."""
    device = body.get("device")
    if not isinstance(device, str) or not device:
        return None
    target = next(
        (zone for zone in model.zones.values() if zone.valve_device == device), None
    )
    if target is None:
        return None
    updates = {
        key: value
        for key, value in _shared_attribute_values(body).items()
        if key in FIELD_CONFIGURATION_ATTRIBUTES
    }
    if not updates:
        return None
    runtime = model.zone_runtime[target.zone_id]
    if (
        model.requires_validated_configuration
        and (not runtime.config_validated)
        and not set(FIELD_CORE_ATTRIBUTES).issubset(updates)
    ):
        return (False, "INCOMPLETE_INITIAL_CONFIG")
    current = effective_field_config(model, target.zone_id)
    candidate = {**current, **updates}
    mode = candidate.get("controlMode")
    if not isinstance(mode, str) or mode.upper() not in {"AUTO", "MANUAL", "DISABLED"}:
        return (False, "INVALID_CONTROL_MODE")
    mode = mode.upper()
    candidate["controlMode"] = mode
    numeric_keys = [
        key for key in FIELD_CORE_ATTRIBUTES if key != "controlMode"
    ]
    if any(
        (
            not isinstance(candidate.get(key), (int, float))
            or isinstance(candidate.get(key), bool)
            or (not 0 <= float(candidate[key]) <= 100)
            for key in numeric_keys
        )
    ):
        return (False, "INVALID_THRESHOLD_RANGE")
    ordered = [
        float(candidate["criticalMoisture"]),
        float(candidate["minMoistureThreshold"]),
        float(candidate["targetMoisture"]),
        float(candidate["maxMoistureThreshold"]),
        float(candidate["floodMoistureThreshold"]),
    ]
    if any((left >= right for left, right in zip(ordered, ordered[1:]))):
        return (False, "INVALID_THRESHOLD_ORDER")
    if any(not isinstance(candidate[k], (int, float)) or isinstance(candidate[k], bool)
           or not math.isfinite(candidate[k]) or candidate[k] <= 0
           for k in FIELD_QUOTA_ATTRIBUTES):
        return (False, "INVALID_QUOTA_RANGE")
    if candidate["maxWaterPerDay"] < candidate["maxWaterPerCycle"]:
        return (False, "DAILY_QUOTA_BELOW_CYCLE")
    if not float(candidate["maxDurationSec"]).is_integer():
        return (False, "DURATION_MUST_BE_INTEGER_SECONDS")
    previous_mode = target.control_mode
    combined = {**_threshold_mapping(model._limits_for(target.zone_id)), **candidate}
    model.zone_limits[target.zone_id] = IrrigationThresholds.from_mapping(combined)
    target.control_mode = mode
    runtime.config_validated = True
    if mode == "DISABLED" and target.valve_state == "ON":
        model._set_valve(target.zone_id, "OFF", "CONTROL_DISABLED", now_ms)
        model._sync_pump_with_transition()
    elif (
        previous_mode == "AUTO"
        and mode == "MANUAL"
        and (target.valve_state == "ON")
        and (not runtime.active_request_source)
    ):
        model._set_valve(target.zone_id, "OFF", "CONTROL_MODE_CHANGED", now_ms)
        model._sync_pump_with_transition()
    elif previous_mode == "MANUAL" and mode == "AUTO":
        runtime.below_min_samples = 0
    if store is not None:
        store.save(
            {
                zone_id: effective_field_config(model, zone_id)
                for zone_id in model.zones
                if model.zone_runtime[zone_id].config_validated
            }
        )
    return (True, "CONFIG_APPLIED")


def restore_field_configuration(
    model: "IrrigationController", stored: dict[str, dict[str, Any]], now_ms: int
) -> None:
    """Restore previously validated values before the cloud reconnects."""
    for zone_id, values in stored.items():
        zone = model.zones.get(zone_id)
        if zone is None or not isinstance(values, dict):
            continue
        apply_field_configuration_attribute(
            model, {"device": zone.valve_device, "data": values}, now_ms
        )


def apply_local_schedule_attribute(
    runner: LocalScheduleRunner,
    model: "IrrigationController",
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
            return (False, "OFF", "INVALID_COMMAND")
    if not isinstance(config, dict) or config.get("schemaVersion") != 1:
        return (False, "OFF", "INVALID_COMMAND")
    config_id = config.get("configId")
    if not isinstance(config_id, str) or not config_id.strip():
        return (False, "OFF", "INVALID_COMMAND")
    target = next(
        (zone for zone in model.zones.values() if zone.valve_device == device), None
    )
    if target is None:
        return (False, "OFF", "INVALID_COMMAND")
    current = model.zone_runtime[target.zone_id].local_schedule or {}
    if current.get("configCommandId") == config_id:
        return (True, target.valve_state, "DUPLICATE")
    return runner.update_from_config(model, device, config, config_id, now_ms)


class IrrigationController(ControllerState):

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "IrrigationController":
        control = config.get("control", {})
        raw_concurrency = (
            control.get("maxConcurrentZones", 1) if isinstance(control, dict) else 1
        )
        if (
            not isinstance(raw_concurrency, int)
            or isinstance(raw_concurrency, bool)
            or raw_concurrency not in {1, 2}
        ):
            raise ValueError("control.maxConcurrentZones must be 1 or 2")
        base = ControllerState.from_config(config)
        model = cls(
            zones=base.zones,
            site=base.site,
            thresholds=base.thresholds,
            interval_seconds=base.interval_seconds,
        )
        settings = control
        model.limits = IrrigationThresholds.from_mapping(settings.get("thresholds", {}))
        base_thresholds = settings.get("thresholds", {})
        model.zone_limits = {
            zone_id: IrrigationThresholds.from_mapping({**base_thresholds, **values})
            for zone_id, values in settings.get("thresholdsByZone", {}).items()
            if zone_id in model.zones and isinstance(values, dict)
        }
        model.pulses_per_liter = max(1.0, float(settings.get("pulsesPerLiter", 450)))
        model.manual_off_latch_seconds = max(
            0, int(settings.get("manualOffLatchSeconds", 300))
        )
        model.manual_on_max_seconds = max(
            1, int(settings.get("manualOnMaxSeconds", 300))
        )
        model.flow_grace_seconds = max(1.0, float(settings.get("flowGraceSeconds", 15)))
        model.minimum_flow_rate = max(0.0, float(settings.get("minimumFlowRate", 0.1)))
        model.sensor_stale_seconds = max(
            1.0, float(settings.get("sensorStaleSeconds", 30))
        )
        model.cloud_loss_timeout_seconds = max(
            1.0, float(settings.get("cloudLossTimeoutSeconds", 60))
        )
        model.zone_runtime = {
            zone_id: ZoneRuntime(air_temp=0.0, air_humidity=0.0, light_lux=0.0, vpd=0.0)
            for zone_id in model.zones
        }
        for zone_id, mode in settings.get("controlModes", {}).items():
            normalized = str(mode).upper()
            if zone_id not in model.zones or normalized not in {
                "AUTO",
                "MANUAL",
                "DISABLED",
            }:
                raise ValueError(f"invalid control mode mapping: {zone_id}={mode}")
            model.zones[zone_id].control_mode = normalized
        model._pump_transition_sequence = 0
        model.hardware_adapter: HardwareAdapter | None = None
        model.requires_validated_configuration = True
        model.controller_state = "OFFLINE"
        model.controller_last_seen_ms = 0
        model.runtime_mode = "HARDWARE_TWO_FIELD"
        model.sensor_data_origin = "PHYSICAL"
        model.actuator_backend = "RELAY"
        model.evidence_class = "HARDWARE-UNVERIFIED"
        model.cloud_connected = True
        model.cloud_disconnected_since_ms = 0
        model.cloud_reconnect_count = 0
        model.startup_safe_confirmed = False
        model.tank_state_known = False
        model.required_config_zones: set[str] = set(model.zones)
        model.pulses_per_liter_by_zone = {}
        model.minimum_flow_rate_by_zone = {}
        for key, target, default, allow_zero in (
            (
                "pulsesPerLiterByZone",
                model.pulses_per_liter_by_zone,
                model.pulses_per_liter,
                False,
            ),
            (
                "minimumFlowRateByZone",
                model.minimum_flow_rate_by_zone,
                model.minimum_flow_rate,
                True,
            ),
        ):
            values = settings.get(key, {})
            if not isinstance(values, dict) or set(values) - set(model.zones):
                raise ValueError(f"{key} must map known Fields")
            for zone_id in model.zones:
                value = values.get(zone_id, default)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                    or (value == 0 and not allow_zero)
                ):
                    raise ValueError(f"invalid {key}[{zone_id}]")
                target[zone_id] = float(value)
        for runtime in model.zone_runtime.values():
            runtime.config_validated = False
        return model

    def attach_hardware_adapter(
        self,
        adapter: HardwareAdapter,
        *,
        expected_sensors: dict[str, int],
        min_valid_sensors: dict[str, int],
        sensor_agreement_tolerance_pct: dict[str, float] | None = None,
    ) -> None:
        """Attach physical inputs and require Central acknowledgements."""
        if adapter.runtime_profile != "HARDWARE_TWO_FIELD":
            raise ValueError("product controller requires a physical hardware adapter")
        self.hardware_adapter = adapter
        self.requires_validated_configuration = True
        self.runtime_mode = adapter.runtime_profile
        self.sensor_data_origin = adapter.sensor_data_origin
        self.actuator_backend = adapter.actuator_backend
        self.evidence_class = adapter.evidence_class
        self.controller_state = "OFFLINE"
        self.controller_last_seen_ms = 0
        self.tank_state_known = False
        self.startup_safe_confirmed = False
        self.site.pump_state = "OFF"
        self.site.system_mode = "SAFE-IDLE"
        agreement_tolerances = sensor_agreement_tolerance_pct or {}
        for zone_id, zone in self.zones.items():
            zone.valve_state = "OFF"
            zone.control_mode = "DISABLED"
            runtime = self.zone_runtime[zone_id]
            runtime.data_quality = "INVALID"
            runtime.config_validated = False
            runtime.last_sensor_update_ms = 0
            runtime.soil_samples = ()
            runtime.control_soil_samples = ()
            runtime.soil_sample_consensus = ()
            runtime.expected_sensors = max(1, int(expected_sensors.get(zone_id, 4)))
            runtime.min_valid_sensors = max(
                1,
                min(
                    runtime.expected_sensors,
                    int(min_valid_sensors.get(zone_id, runtime.expected_sensors)),
                ),
            )
            raw_tolerance = agreement_tolerances.get(zone_id, 15.0)
            if (
                isinstance(raw_tolerance, bool)
                or not isinstance(raw_tolerance, (int, float))
                or (not math.isfinite(float(raw_tolerance)))
                or (not 0 < float(raw_tolerance) <= 100)
            ):
                raise ValueError(
                    f"sensorAgreementTolerancePct[{zone_id}] must be in (0, 100]"
                )
            runtime.sensor_agreement_tolerance_pct = float(raw_tolerance)

    def ingest_hardware_event(
        self, event: SensorSample | PeerStatus | ActuatorAck
    ) -> None:
        """Apply validated adapter events without synthesizing physical state."""
        if isinstance(event, SensorSample):
            zone = self.zones.get(event.zone_id)
            if zone is None:
                return
            runtime = self.zone_runtime[event.zone_id]
            samples = tuple(event.soil_moisture[: runtime.expected_sensors])
            valid = tuple(
                float(v)
                for v in samples
                if isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and 0 <= v <= 100
            )
            peer_median = statistics.median(valid) if valid else 0.0
            consensus = tuple(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and 0 <= v <= 100
                and abs(v - peer_median) <= runtime.sensor_agreement_tolerance_pct
                for v in samples
            )
            consistent = tuple(
                float(v) for v, agrees in zip(samples, consensus) if agrees
            )
            runtime.sensor_node_id = event.node_id
            runtime.soil_samples = samples
            runtime.control_soil_samples = consistent
            runtime.soil_sample_consensus = consensus
            runtime.environment_valid = all(
                v is not None and math.isfinite(v)
                for v in (event.air_temp, event.air_humidity, event.light_lux)
            )
            runtime.last_sensor_sequence = event.sequence
            runtime.last_sensor_update_ms = event.received_at_ms
            if event.air_temp is not None:
                runtime.air_temp = event.air_temp
            if event.air_humidity is not None:
                runtime.air_humidity = event.air_humidity
            if event.light_lux is not None:
                runtime.light_lux = event.light_lux
            if event.air_temp is not None and event.air_humidity is not None:
                runtime.vpd = self._calculate_vpd(event.air_temp, event.air_humidity)
            if len(consistent) >= runtime.min_valid_sensors:
                zone.moisture = sum(consistent) / len(consistent)
                runtime.data_quality = "OK" if runtime.environment_valid else "INVALID"
            else:
                runtime.data_quality = (
                    "SENSOR_INCONSISTENT"
                    if len(valid) >= runtime.min_valid_sensors
                    else "INVALID"
                )
            return
        if isinstance(event, FlowSample):
            if event.zone_id not in self.zones:
                return
            zone = self.zones[event.zone_id]
            runtime = self.zone_runtime[event.zone_id]
            if (
                event.boot_id == runtime.flow_boot_id
                and event.sequence <= runtime.last_flow_sequence
            ):
                return
            # Establish a baseline after a Central restart; never count an old
            # absolute counter as water delivered in the current cycle.
            delta = 0
            if (
                event.boot_id == runtime.flow_boot_id
                and runtime.raw_pulse_counter is not None
            ):
                delta = max(0, event.pulse_counter - runtime.raw_pulse_counter)
            runtime.flow_boot_id = event.boot_id
            runtime.last_flow_sequence = event.sequence
            runtime.raw_pulse_counter = event.pulse_counter
            runtime.last_flow_update_ms = event.received_at_ms
            runtime.last_flow_rate = event.flow_rate_lpm
            runtime.flow_state_known = True
            zone.pulse_counter += delta
            liters = delta / self.pulses_per_liter_by_zone[event.zone_id]
            runtime.total_water_liters += liters
            if zone.valve_state == "ON":
                zone.water_used += liters
                runtime.daily_water_liters += liters
            return
        if isinstance(event, PeerStatus) and event.role.upper() == "CENTRAL":
            self.controller_state = "ONLINE" if event.online else "OFFLINE"
            self.controller_last_seen_ms = event.received_at_ms
            if event.tank_low is not None:
                self.site.tank_low_switch = event.tank_low
                self.tank_state_known = True
            if not event.online:
                self.site.system_mode = "DEGRADED"
            return
        if isinstance(event, ActuatorAck):
            if event.reason == "ACK_TIMEOUT":
                self.controller_state = "OFFLINE"
                self.site.system_mode = "SAFE-IDLE"
                self.site.safety_block_reason = "CONTROLLER_OFFLINE"
            else:
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
        elif not self.startup_safe_confirmed:
            self.site.system_mode = "SAFE-IDLE"
            self.site.safety_block_reason = "STARTUP_NOT_CONFIRMED"
        elif self.required_config_zones and any(
            (
                not self.zone_runtime[zone_id].config_validated
                for zone_id in self.required_config_zones
            )
        ):
            self.site.system_mode = "SAFE-IDLE"
            self.site.safety_block_reason = "CONFIG_NOT_READY"
        elif self.controller_state != "ONLINE":
            self.site.system_mode = "SAFE-IDLE"
            self.site.safety_block_reason = "CONTROLLER_OFFLINE"
        elif self.cloud_connected:
            self.site.system_mode = "NORMAL"
        elif (
            self.cloud_disconnected_since_ms
            and now_ms - self.cloud_disconnected_since_ms
            >= self.cloud_loss_timeout_seconds * 1000
        ):
            self.site.system_mode = "DEGRADED"
        else:
            self.site.system_mode = "NORMAL"
        if (
            not self.site.tank_low_switch
            and self.site.safety_block_reason == "TANK_LOW"
        ):
            self.site.safety_block_reason = "NONE"
        if (
            self.controller_state == "ONLINE"
            and self.site.safety_block_reason == "CONTROLLER_OFFLINE"
        ):
            self.site.safety_block_reason = "NONE"
        if (
            self.startup_safe_confirmed
            and all(
                (
                    self.zone_runtime[zone_id].config_validated
                    for zone_id in self.required_config_zones
                )
            )
            and (
                self.site.safety_block_reason
                in {"STARTUP_NOT_CONFIRMED", "CONFIG_NOT_READY"}
            )
        ):
            self.site.safety_block_reason = "NONE"

    def _refresh_sensor_health(self, now_ms: int) -> None:
        for runtime in self.zone_runtime.values():
            if runtime.last_sensor_update_ms == 0:
                runtime.data_quality = "INVALID"
            elif (
                now_ms - runtime.last_sensor_update_ms
                > self.sensor_stale_seconds * 1000
            ):
                runtime.data_quality = "STALE"
        if (
            self.controller_last_seen_ms
            and now_ms - self.controller_last_seen_ms > self.sensor_stale_seconds * 1000
        ):
            self.controller_state = "OFFLINE"

    def _reset_daily_counters(self, now_ms: int) -> None:
        epoch_day = now_ms // 86400000
        for runtime in self.zone_runtime.values():
            if runtime.daily_epoch_day == -1:
                runtime.daily_epoch_day = epoch_day
            elif runtime.daily_epoch_day != epoch_day:
                runtime.daily_epoch_day = epoch_day
                runtime.daily_water_liters = 0.0

    def _update_debounce_counters(self) -> None:
        for zone_id, zone in self.zones.items():
            runtime = self.zone_runtime[zone_id]
            if runtime.data_quality != "OK":
                runtime.below_min_samples = 0
                runtime.above_flood_samples = 0
                continue
            if runtime.last_sensor_sequence == runtime.last_debounce_sequence:
                continue
            runtime.last_debounce_sequence = runtime.last_sensor_sequence
            limits = self._limits_for(zone_id)
            runtime.below_min_samples = (
                runtime.below_min_samples + 1
                if zone.moisture < limits.min_moisture
                else 0
            )
            runtime.above_flood_samples = (
                runtime.above_flood_samples + 1
                if zone.moisture >= limits.flood_moisture
                else 0
            )

    @staticmethod
    def _calculate_vpd(air_temp: float, air_humidity: float) -> float:
        saturation = 0.6108 * math.exp(17.27 * air_temp / (air_temp + 237.3))
        return saturation * (1 - air_humidity / 100)

    def _field_input(self, zone_id: str, now_ms: int) -> FieldControlInput:
        zone = self.zones[zone_id]
        runtime = self.zone_runtime[zone_id]
        seconds_since_last = 0.0
        if runtime.last_irrigation_ended_ms:
            seconds_since_last = max(
                0.0, (now_ms - runtime.last_irrigation_ended_ms) / 1000
            )
        stale = bool(
            runtime.last_sensor_update_ms
            and now_ms - runtime.last_sensor_update_ms
            > self.sensor_stale_seconds * 1000
        )
        data_quality = "STALE" if stale else runtime.data_quality
        if (
            not self.tank_state_known
            or not runtime.flow_state_known
            or not runtime.environment_valid
        ):
            data_quality = "INVALID"
        elif now_ms - runtime.last_flow_update_ms > self.sensor_stale_seconds * 1000:
            data_quality = "STALE"
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
            manual_on_expired=bool(
                runtime.manual_on_until_ms and now_ms >= runtime.manual_on_until_ms
            ),
            flow_rate=runtime.last_flow_rate,
            zero_flow_seconds=runtime.zero_flow_seconds,
            flow_grace_seconds=self.flow_grace_seconds,
            flow_fault_latched=runtime.flow_fault_latched,
        )

    def _evaluate_zone(self, zone_id: str, now_ms: int) -> FieldDecision:
        decision = evaluate_field(
            self._field_input(zone_id, now_ms), self._limits_for(zone_id)
        )
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

    def _execute_without_adapter(
        self, zone_id: str, state: str, command_id: str
    ) -> bool:
        """A physical runtime cannot execute an output without an adapter."""
        return False

    def _set_valve(
        self,
        zone_id: str,
        state: str,
        reason: str,
        now_ms: int,
        command_id: str | None = None,
        *,
        force_hardware: bool = False,
    ) -> bool:
        zone = self.zones[zone_id]
        runtime = self.zone_runtime[zone_id]
        generated_transition = zone.valve_state != state
        if generated_transition:
            runtime.transition_sequence += 1
        effective_command_id = (
            command_id or f"local-{zone_id}-{runtime.transition_sequence}"
        )
        issue_hardware_command = self.hardware_adapter is not None and (
            generated_transition or force_hardware
        )
        if issue_hardware_command:
            ack = self.hardware_adapter.set_zone(
                zone_id, state, effective_command_id, lease_ms=15000
            )
            self.ingest_hardware_event(ack)
            zone.last_command_id = effective_command_id
            if not ack.accepted:
                if state == "OFF" or ack.reason == "ACK_TIMEOUT":
                    runtime.off_reassert_pending = True
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
            if state == "OFF":
                runtime.off_reassert_pending = False
                runtime.off_reassert_last_ms = now_ms
            else:
                runtime.off_reassert_pending = False
            zone.last_ack = "EXECUTED"
            self.site.last_command_id = effective_command_id
            self.site.last_ack = "EXECUTED"
            self.site.safety_block_reason = "NONE"
        elif generated_transition:
            if not self._execute_without_adapter(zone_id, state, effective_command_id):
                zone.last_ack = "REJECTED"
                zone.safety_block_reason = "ADAPTER_NOT_READY"
                return False
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

    def decide(
        self, now_ms: int | None = None, local_zone_ids: set[str] | None = None
    ) -> None:
        now_ms = utc_ms() if now_ms is None else now_ms
        active_before = [
            zone_id for zone_id, zone in self.zones.items() if zone.valve_state == "ON"
        ]
        pump_dry_run = bool(active_before) and all(
            (
                self.zone_runtime[zone_id].zero_flow_seconds >= self.flow_grace_seconds
                for zone_id in active_before
            )
        )
        candidates: list[tuple[str, FieldDecision]] = []
        for zone_id, zone in self.zones.items():
            decision = self._evaluate_zone(zone_id, now_ms)
            self.zone_runtime[zone_id].decision = decision
            if local_zone_ids is not None and zone_id not in local_zone_ids:
                if decision.reason in HARD_STOP_REASONS and decision.action in {
                    "STOP",
                    "BLOCK",
                }:
                    self.zone_runtime[zone_id].below_min_samples = 0
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
                if decision.reason in HARD_STOP_REASONS:
                    self.zone_runtime[zone_id].below_min_samples = 0
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
        active = sum((zone.valve_state == "ON" for zone in self.zones.values()))
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
        """Pump output is updated only by a Central acknowledgement."""
        return

    def clear_flow_fault(self, zone_id: str) -> None:
        runtime = self.zone_runtime[zone_id]
        runtime.flow_fault = False
        runtime.flow_fault_latched = False
        runtime.zero_flow_seconds = 0.0
        zone = self.zones[zone_id]
        if zone.safety_block_reason == "ZONE_FLOW_LOW":
            zone.safety_block_reason = "NONE"
            zone.safety_state = "SAFE"
        if not any((item.flow_fault_latched for item in self.zone_runtime.values())):
            if self.site.safety_block_reason == "PUMP_DRY_RUN":
                self.site.safety_block_reason = "NONE"

    def _enforce_tank_low_immediately(self, now_ms: int) -> None:
        if not self.site.tank_low_switch:
            return
        for zone_id, zone in self.zones.items():
            self.zone_runtime[zone_id].below_min_samples = 0
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
            if decision.reason in HARD_STOP_REASONS and decision.action in {
                "STOP",
                "BLOCK",
            }:
                self.zone_runtime[zone_id].below_min_samples = 0
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
        self, command: RpcCommand, now_ms: int | None = None
    ) -> tuple[bool, str, str]:
        target = next(
            (
                item
                for item in self.zones.values()
                if item.valve_device == command.device
            ),
            None,
        )
        if target is None:
            if command.device != self.site.pump_device:
                return (False, "OFF", "SAFETY_BLOCK")
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
                        force_hardware=True,
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
            return (False, self.site.pump_state, "SAFETY_BLOCK")
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
                return (False, target.valve_state, "INVALID_COMMAND")
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
            latch_seconds = max(
                0,
                int(command.params.get("latchSeconds", self.manual_off_latch_seconds)),
            )
            runtime.manual_off_until_ms = now_ms + latch_seconds * 1000
            stopped = self._set_valve(
                target.zone_id,
                "OFF",
                "MANUAL_OFF",
                now_ms,
                command.command_id,
                force_hardware=True,
            )
            self._sync_pump_with_transition()
            if not stopped:
                return (False, target.valve_state, "SAFETY_BLOCK")
            target.last_command_id = command.command_id
            target.last_ack = "EXECUTED"
            return (True, "OFF", "EXECUTED")
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
            return (False, "ON", "SAFETY_BLOCK")
        inputs = self._field_input(target.zone_id, now_ms)
        blocked = evaluate_field(inputs, self._limits_for(target.zone_id))
        active_other = sum(
            (
                z.valve_state == "ON" and z.zone_id != target.zone_id
                for z in self.zones.values()
            )
        )
        reason = blocked.reason if blocked.reason in HARD_STOP_REASONS else "NONE"
        if reason == "NONE" and active_other >= self.site.max_concurrent_zones:
            reason = "MAX_CONCURRENT_ZONES"
        if reason != "NONE":
            if reason in HARD_STOP_REASONS:
                runtime.below_min_samples = 0
            if source == "SCHEDULER":
                self._scheduled_task(
                    target.zone_id,
                    command,
                    now_ms,
                    requested_duration,
                    "REJECTED",
                    reason,
                )
            target.last_command_id = command.command_id
            target.last_ack = "REJECTED"
            target.safety_state = "BLOCKED"
            target.safety_block_reason = reason
            target.decision_reason = reason
            return (False, "OFF", "SAFETY_BLOCK")
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
            target.zone_id, "ON", start_reason, now_ms, command.command_id
        )
        if not started:
            if source == "SCHEDULER":
                self._finish_scheduled_task(
                    target.zone_id, now_ms, target.decision_reason
                )
            runtime.manual_on_until_ms = 0
            runtime.active_request_source = ""
            return (False, target.valve_state, "SAFETY_BLOCK")
        target.last_command_id = command.command_id
        target.last_ack = "EXECUTED"
        self._sync_pump_with_transition()
        return (True, "ON", "EXECUTED")

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
        self._enforce_tank_low_immediately(now)
        self._refresh_operation_mode(now)
        if local_control_zones is not None:
            self.decide(now, local_control_zones)
        elif local_decision:
            self.decide(now)
        else:
            self.enforce_runtime_safety(now)
        readings: dict[str, list[dict[str, Any]]] = {}
        active_zones = sum((zone.valve_state == "ON" for zone in self.zones.values()))
        for sensor_zone_index, (zone_id, zone) in enumerate(
            self.zones.items(), start=1
        ):
            runtime = self.zone_runtime[zone_id]
            if zone.valve_state == "ON":
                flow_rate = runtime.last_flow_rate
                runtime.irrigation_runtime_seconds += self.interval_seconds
                if flow_rate < self.minimum_flow_rate_by_zone[zone_id]:
                    runtime.zero_flow_seconds += self.interval_seconds
                else:
                    runtime.zero_flow_seconds = 0.0
            else:
                flow_rate = runtime.last_flow_rate
                runtime.zero_flow_seconds = 0.0
            runtime.last_flow_rate = flow_rate
            for sensor_index, device in enumerate(zone.soil_devices, start=1):
                sample_agrees = (
                    runtime.soil_sample_consensus[sensor_index - 1]
                    if sensor_index <= len(runtime.soil_sample_consensus)
                    and runtime.soil_samples[sensor_index - 1] is not None
                    else None
                )
                sensor_values: dict[str, Any] = {
                    "dataQuality": (
                        "INVALID"
                        if sample_agrees is None
                        else "SENSOR_INCONSISTENT"
                        if sample_agrees is False
                        else runtime.data_quality
                    ),
                    "sensorConsensus": (
                        "AGREE"
                        if sample_agrees is True
                        else "OUTLIER" if sample_agrees is False else "UNKNOWN"
                    ),
                }
                if (
                    sensor_index <= len(runtime.soil_samples)
                    and runtime.soil_samples[sensor_index - 1] is not None
                ):
                    sensor_values["moisture"] = round(
                        runtime.soil_samples[sensor_index - 1], 2
                    )
                readings[device] = [{"ts": now, "values": sensor_values}]
            env_values: dict[str, Any] = {"dataQuality": runtime.data_quality}
            if runtime.environment_valid:
                env_values.update(
                    {
                        "airTemp": round(runtime.air_temp, 2),
                        "airHumidity": round(runtime.air_humidity, 2),
                        "lightLux": round(runtime.light_lux, 1),
                        "vpd": round(runtime.vpd, 3),
                    }
                )
            readings[zone.env_device] = [{"ts": now, "values": env_values}]
            decision = runtime.decision or FieldDecision(
                "KEEP_OFF", "INITIALIZED", 0, False, ()
            )
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
                "irrigationWatchdog": zone.safety_block_reason
                == "MAX_DURATION_REACHED",
                "waterQuotaExceeded": zone.safety_block_reason
                in {"CYCLE_QUOTA_REACHED", "DAILY_QUOTA_REACHED"},
                "fieldDataInvalid": zone.safety_block_reason == "FIELD_DATA_INVALID",
            }
            effective_limits = self._limits_for(zone_id)
            valve_values.update(
                {
                    "criticalMoisture": effective_limits.critical_moisture,
                    "minMoistureThreshold": effective_limits.min_moisture,
                    "targetMoisture": effective_limits.target_moisture,
                    "maxMoistureThreshold": effective_limits.max_moisture,
                    "floodMoistureThreshold": effective_limits.flood_moisture,
                    "maxWaterPerCycle": effective_limits.max_water_per_cycle_liters,
                    "maxWaterPerDay": effective_limits.max_water_per_day_liters,
                    "maxDurationSec": effective_limits.max_duration_seconds,
                }
            )
            schedule = runtime.local_schedule
            if schedule is not None:
                valve_values.update(
                    {
                        "gatewayScheduleId": schedule["id"],
                        "gatewayScheduleSource": schedule["source"],
                        "gatewayScheduleEnabled": schedule["enabled"],
                        "gatewayScheduleStatus": schedule["status"],
                        "gatewayScheduleNextRunTs": schedule["nextRunTs"],
                        "gatewayScheduleDurationSec": schedule["durationSeconds"],
                        "gatewayScheduleRepeatEverySec": schedule["repeatEverySeconds"],
                        "gatewayScheduleLastRunTs": schedule["lastRunTs"],
                        "gatewayScheduleLastResultReason": schedule["lastResultReason"],
                        "gatewayScheduleConfigCommandId": schedule.get(
                            "configCommandId", ""
                        ),
                        "gatewayScheduleConfigAck": schedule.get("configAck", "NONE"),
                        "gatewayScheduleConfigReason": schedule.get(
                            "configReason", "NONE"
                        ),
                    }
                )
            valve_values.update(
                {
                    "waterConsumptionLiters": round(runtime.total_water_liters, 3),
                    "cycleWaterLiters": round(zone.water_used, 3),
                    "dailyWaterLiters": round(runtime.daily_water_liters, 3),
                }
            )
            readings[zone.valve_device] = [{"ts": now, "values": valve_values}]
            task = runtime.irrigation_task
            if task and (
                task.get("status") == "RUNNING" or runtime.irrigation_task_dirty
            ):
                task_snapshot = dict(task)
                if task_snapshot.get("status") == "RUNNING":
                    task_snapshot["duration"] = max(
                        0, now - int(task_snapshot["startTs"])
                    )
                    task_snapshot["consumption"] = round(zone.water_used, 3)
                readings[zone.valve_device].append(
                    {
                        "ts": int(task_snapshot["startTs"]),
                        "values": {"irrigationTask": task_snapshot},
                    }
                )
                runtime.irrigation_task_dirty = False
            meter_values: dict[str, Any] = {
                "pulseCounter": zone.pulse_counter,
                "flowRate": flow_rate,
                "waterConsumptionLiters": round(runtime.total_water_liters, 3),
                "cycleWaterLiters": round(zone.water_used, 3),
                "dailyWaterLiters": round(runtime.daily_water_liters, 3),
                "pulsesPerLiter": self.pulses_per_liter_by_zone[zone_id],
                "zeroFlowSeconds": round(runtime.zero_flow_seconds, 1),
                "zoneFlowLow": zone.safety_block_reason == "ZONE_FLOW_LOW",
                "dataQuality": runtime.data_quality,
                "hydraulicAnalyticsTrusted": runtime.flow_state_known
                and now - runtime.last_flow_update_ms
                <= self.sensor_stale_seconds * 1000,
            }
            if not runtime.flow_state_known:
                for key in ("flowRate", "pulseCounter", "waterConsumptionLiters"):
                    meter_values.pop(key, None)
            readings[zone.water_meter_device] = [{"ts": now, "values": meter_values}]
        if self.site.pump_state == "ON":
            self.site.pump_runtime_sec += round(self.interval_seconds)
        self._enforce_tank_low_immediately(now)
        self._refresh_operation_mode(now)
        active_zones = sum((zone.valve_state == "ON" for zone in self.zones.values()))
        for zone in self.zones.values():
            values = readings[zone.valve_device][0]["values"]
            values["valveState"] = zone.valve_state
            values["actualValveState"] = zone.valve_state
            values["decisionReason"] = zone.decision_reason
            values["safetyBlockReason"] = zone.safety_block_reason
        readings[self.site.pump_device] = [
            {
                "ts": now,
                "values": {
                    "pumpState": self.site.pump_state,
                    "runtimeSec": self.site.pump_runtime_sec,
                    "lastCommandId": self.site.last_command_id,
                    "lastAck": self.site.last_ack,
                    "safetyBlockReason": self.site.safety_block_reason,
                    "pumpDryRun": self.site.safety_block_reason == "PUMP_DRY_RUN",
                },
            }
        ]
        readings[self.site.manifold_device] = [
            {
                "ts": now,
                "values": {
                    **(
                        {"tankLowSwitch": self.site.tank_low_switch}
                        if self.tank_state_known
                        else {}
                    ),
                    "activeZoneCount": active_zones,
                    "controllerState": self.controller_state,
                    "systemMode": self.site.system_mode,
                    "cloudConnected": self.cloud_connected,
                    "cloudReconnectCount": self.cloud_reconnect_count,
                    "waterloggingRisk": any(
                        (
                            zone.safety_block_reason == "WATERLOGGING_RISK"
                            for zone in self.zones.values()
                        )
                    ),
                },
            }
        ]
        return readings

    def gateway_health(
        self,
        connected_device_count: int,
        buffer_depth: int,
        expired_buffer_count: int = 0,
    ) -> dict[str, Any]:
        health = super().gateway_health(connected_device_count, buffer_depth)
        health["values"].update(
            {
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
            }
        )
        return health


def effective_rpc_authority(
    model: IrrigationController,
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
    model: IrrigationController,
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
    model: IrrigationController,
    command: RpcCommand,
    authority: str,
    zone_authorities: dict[str, str],
) -> tuple[bool, str, str]:
    """Reject cloud automation on local zones while preserving operator manual/scheduled requests."""
    effective_authority = effective_rpc_authority(
        model, command.device, authority, zone_authorities
    )
    source = command.params.get("source")
    if effective_authority == "LOCAL" and source not in {"MANUAL", "SCHEDULER"}:
        target = next(
            (
                zone
                for zone in model.zones.values()
                if zone.valve_device == command.device
            ),
            None,
        )
        current_state = (
            target.valve_state if target is not None else model.site.pump_state
        )
        LOG.warning(
            "rpc rejected device=%s method=%s source=%s reason=LOCAL_AUTHORITY",
            command.device,
            command.method,
            source,
        )
        return (False, current_state, "SAFETY_BLOCK")
    return model.apply_rpc(command)
