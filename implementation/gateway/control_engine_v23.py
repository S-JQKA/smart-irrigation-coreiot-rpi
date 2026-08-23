"""Pure SRS-aligned irrigation recommendation logic for the Pi gateway.

This module has no MQTT or hardware dependencies.  It deliberately separates
per-field recommendation from multi-zone scheduling and actuator execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IrrigationThresholds:
    critical_moisture: float = 15.0
    min_moisture: float = 30.0
    target_moisture: float = 55.0
    max_moisture: float = 70.0
    flood_moisture: float = 85.0
    max_water_per_cycle_liters: float = 1000.0
    max_water_per_day_liters: float = 5000.0
    max_duration_seconds: float = 600.0
    hot_temperature_c: float = 28.0
    high_vpd_kpa: float = 1.5
    high_light_lux: float = 20000.0
    start_debounce_samples: int = 2
    flood_debounce_samples: int = 3

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "IrrigationThresholds":
        return cls(
            critical_moisture=float(values.get("criticalMoisture", 15)),
            min_moisture=float(values.get("minMoistureThreshold", 30)),
            target_moisture=float(values.get("targetMoisture", 55)),
            max_moisture=float(values.get("maxMoistureThreshold", 70)),
            flood_moisture=float(values.get("floodMoistureThreshold", 85)),
            max_water_per_cycle_liters=float(values.get("maxWaterPerCycle", 1000)),
            max_water_per_day_liters=float(values.get("maxWaterPerDay", 5000)),
            max_duration_seconds=float(values.get("maxDurationSec", 600)),
            hot_temperature_c=float(values.get("hotTemperatureC", 28)),
            high_vpd_kpa=float(values.get("highVpdKpa", 1.5)),
            high_light_lux=float(values.get("highLightLux", 20000)),
            start_debounce_samples=max(1, int(values.get("startDebounceSamples", 2))),
            flood_debounce_samples=max(1, int(values.get("floodDebounceSamples", 3))),
        )


@dataclass(frozen=True)
class FieldControlInput:
    zone_id: str
    avg_moisture: float | None
    air_temp: float | None
    air_humidity: float | None
    vpd: float | None
    light_lux: float | None
    actual_valve_state: str
    control_mode: str = "AUTO"
    system_mode: str = "NORMAL"
    data_quality: str = "OK"
    tank_low: bool = False
    cycle_water_liters: float = 0.0
    daily_water_liters: float = 0.0
    irrigation_runtime_seconds: float = 0.0
    below_min_samples: int = 0
    above_flood_samples: int = 0
    seconds_since_last_irrigation: float = 0.0
    manual_off_latched: bool = False
    manual_on_expired: bool = False
    flow_rate: float = 0.0
    zero_flow_seconds: float = 0.0
    flow_grace_seconds: float = 15.0
    flow_fault_latched: bool = False


@dataclass(frozen=True)
class FieldDecision:
    action: str
    reason: str
    priority: int
    environment_stress: bool
    stress_factors: tuple[str, ...]

    @property
    def requests_start(self) -> bool:
        return self.action == "REQUEST_START"


def _stress_factors(inputs: FieldControlInput, limits: IrrigationThresholds) -> tuple[str, ...]:
    factors: list[str] = []
    if inputs.air_temp is not None and inputs.air_temp > limits.hot_temperature_c:
        factors.append("HIGH_TEMPERATURE")
    if inputs.vpd is not None and inputs.vpd > limits.high_vpd_kpa:
        factors.append("HIGH_VPD")
    if inputs.light_lux is not None and inputs.light_lux > limits.high_light_lux:
        factors.append("HIGH_LIGHT")
    return tuple(factors)


def evaluate_field(inputs: FieldControlInput, limits: IrrigationThresholds) -> FieldDecision:
    """Evaluate one field; scheduling and actuator commands happen elsewhere."""

    is_on = inputs.actual_valve_state == "ON"
    factors = _stress_factors(inputs, limits)

    if inputs.tank_low:
        return FieldDecision("STOP" if is_on else "BLOCK", "TANK_LOW", 0, bool(factors), factors)
    if inputs.system_mode == "SAFE-IDLE":
        return FieldDecision("STOP" if is_on else "BLOCK", "SYSTEM_SAFE_IDLE", 0, bool(factors), factors)
    if inputs.data_quality != "OK" or inputs.avg_moisture is None:
        return FieldDecision("STOP" if is_on else "BLOCK", "FIELD_DATA_INVALID", 0, bool(factors), factors)
    if inputs.flow_fault_latched or (is_on and inputs.zero_flow_seconds >= inputs.flow_grace_seconds):
        return FieldDecision("STOP" if is_on else "BLOCK", "ZONE_FLOW_LOW", 0, bool(factors), factors)
    if inputs.above_flood_samples >= limits.flood_debounce_samples:
        return FieldDecision("STOP" if is_on else "BLOCK", "WATERLOGGING_RISK", 0, bool(factors), factors)
    if inputs.avg_moisture >= limits.max_moisture:
        return FieldDecision("STOP" if is_on else "BLOCK", "MAX_MOISTURE_AUTO_LOCK", 0, bool(factors), factors)
    if inputs.cycle_water_liters >= limits.max_water_per_cycle_liters:
        return FieldDecision("STOP" if is_on else "BLOCK", "CYCLE_QUOTA_REACHED", 0, bool(factors), factors)
    if inputs.daily_water_liters >= limits.max_water_per_day_liters:
        return FieldDecision("STOP" if is_on else "BLOCK", "DAILY_QUOTA_REACHED", 0, bool(factors), factors)
    if inputs.irrigation_runtime_seconds >= limits.max_duration_seconds:
        return FieldDecision("STOP" if is_on else "BLOCK", "MAX_DURATION_REACHED", 0, bool(factors), factors)
    if inputs.control_mode == "DISABLED":
        return FieldDecision("STOP" if is_on else "BLOCK", "CONTROL_DISABLED", 0, bool(factors), factors)
    if inputs.manual_off_latched:
        return FieldDecision("STOP" if is_on else "BLOCK", "MANUAL_OFF_LATCHED", 0, bool(factors), factors)
    if is_on and inputs.manual_on_expired:
        return FieldDecision("STOP", "MANUAL_ON_TTL_EXPIRED", 0, bool(factors), factors)
    if is_on and inputs.avg_moisture >= limits.target_moisture:
        return FieldDecision("STOP", "TARGET_MOISTURE_REACHED", 0, bool(factors), factors)
    if is_on:
        return FieldDecision("KEEP_ON", "KEEP_IRRIGATING", 0, bool(factors), factors)
    if inputs.control_mode != "AUTO":
        return FieldDecision("KEEP_OFF", "MANUAL_MODE_NO_AUTO_START", 0, bool(factors), factors)

    if inputs.avg_moisture < limits.critical_moisture:
        return FieldDecision("REQUEST_START", "BELOW_CRITICAL_MOISTURE", 100, bool(factors), factors)
    if inputs.avg_moisture >= limits.min_moisture:
        return FieldDecision("KEEP_OFF", "MOISTURE_ABOVE_MIN", 0, bool(factors), factors)
    if inputs.below_min_samples < limits.start_debounce_samples:
        return FieldDecision("KEEP_OFF", "WAITING_START_DEBOUNCE", 0, bool(factors), factors)

    moisture_span = max(1.0, limits.min_moisture - limits.critical_moisture)
    dryness = min(1.0, max(0.0, (limits.min_moisture - inputs.avg_moisture) / moisture_span))
    priority = 60 + round(dryness * 15)
    priority += 10 if "HIGH_TEMPERATURE" in factors else 0
    priority += 10 if "HIGH_VPD" in factors else 0
    priority += 5 if "HIGH_LIGHT" in factors else 0
    priority += min(5, int(inputs.seconds_since_last_irrigation // 3600))
    priority = min(99, priority)
    reason = "BELOW_MIN_WITH_ENV_STRESS" if factors else "BELOW_MIN_MOISTURE"
    return FieldDecision("REQUEST_START", reason, priority, bool(factors), factors)
