"""Lightweight advisory anomaly detection for soil-moisture telemetry.

The detector is intentionally independent from the irrigation control engine.
It may label telemetry and raise advisory alarms through CoreIoT, but its output
must never be used as an actuator command.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from analytics_hook import AnalyticsResult, NoOpAnalyticsHook


@dataclass(frozen=True)
class AnomalySettings:
    ewma_alpha: float = 0.2
    z_score_threshold: float = 3.0
    warmup_samples: int = 8
    minimum_stddev: float = 0.25
    stuck_window: int = 10
    stuck_epsilon: float = 0.05
    stuck_peer_movement: float = 0.5
    stuck_boundary_margin: float = 1.0
    peer_deviation_threshold: float = 12.0
    peer_min_sensors: int = 3
    drift_window: int = 12
    drift_min_change: float = 4.0
    drift_consistency_ratio: float = 0.75
    leak_min_pulse_delta: float = 1.0
    leak_flow_threshold: float = 0.05
    leak_consecutive_samples: int = 2
    leak_clear_samples: int = 3
    clear_samples: int = 5
    max_sample_age_seconds: float = 60.0
    max_future_skew_seconds: float = 5.0
    require_trusted_hydraulics: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "AnomalySettings":
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("analytics settings must be an object")
        aliases = {
            "ewmaAlpha": "ewma_alpha",
            "zScoreThreshold": "z_score_threshold",
            "warmupSamples": "warmup_samples",
            "minimumStddev": "minimum_stddev",
            "stuckWindow": "stuck_window",
            "stuckEpsilon": "stuck_epsilon",
            "stuckPeerMovement": "stuck_peer_movement",
            "stuckBoundaryMargin": "stuck_boundary_margin",
            "peerDeviationThreshold": "peer_deviation_threshold",
            "peerMinSensors": "peer_min_sensors",
            "driftWindow": "drift_window",
            "driftMinChange": "drift_min_change",
            "driftConsistencyRatio": "drift_consistency_ratio",
            "leakMinPulseDelta": "leak_min_pulse_delta",
            "leakFlowThreshold": "leak_flow_threshold",
            "leakConsecutiveSamples": "leak_consecutive_samples",
            "leakClearSamples": "leak_clear_samples",
            "clearSamples": "clear_samples",
            "maxSampleAgeSeconds": "max_sample_age_seconds",
            "maxFutureSkewSeconds": "max_future_skew_seconds",
            "requireTrustedHydraulics": "require_trusted_hydraulics",
        }
        unknown = set(raw) - set(aliases)
        if unknown:
            raise ValueError(f"unknown analytics settings: {sorted(unknown)}")
        values = {aliases[key]: value for key, value in raw.items()}
        settings = cls(**values)
        settings._validate()
        return settings

    def _validate(self) -> None:
        numeric_positive = {
            "zScoreThreshold": self.z_score_threshold,
            "minimumStddev": self.minimum_stddev,
            "stuckEpsilon": self.stuck_epsilon,
            "stuckPeerMovement": self.stuck_peer_movement,
            "stuckBoundaryMargin": self.stuck_boundary_margin,
            "peerDeviationThreshold": self.peer_deviation_threshold,
            "driftMinChange": self.drift_min_change,
            "leakMinPulseDelta": self.leak_min_pulse_delta,
            "leakFlowThreshold": self.leak_flow_threshold,
            "maxSampleAgeSeconds": self.max_sample_age_seconds,
            "maxFutureSkewSeconds": self.max_future_skew_seconds,
        }
        for name, value in numeric_positive.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.ewma_alpha <= 1:
            raise ValueError("ewmaAlpha must be in (0, 1]")
        integers = {
            "warmupSamples": self.warmup_samples,
            "stuckWindow": self.stuck_window,
            "peerMinSensors": self.peer_min_sensors,
            "driftWindow": self.drift_window,
            "leakConsecutiveSamples": self.leak_consecutive_samples,
            "leakClearSamples": self.leak_clear_samples,
            "clearSamples": self.clear_samples,
        }
        for name, value in integers.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.peer_min_sensors < 3:
            raise ValueError("peerMinSensors must be at least 3")
        if not 0 < self.drift_consistency_ratio <= 1:
            raise ValueError("driftConsistencyRatio must be in (0, 1]")
        if self.stuck_boundary_margin >= 50:
            raise ValueError("stuckBoundaryMargin must be less than 50")
        if not isinstance(self.require_trusted_hydraulics, bool):
            raise ValueError("requireTrustedHydraulics must be boolean")


_ACCEPTABLE_DATA_QUALITIES = {"OK", "VALID", "GOOD"}


def _trusted_latest_values(
    samples: Any,
    keys: tuple[str, ...],
    settings: AnomalySettings,
    reference_ts_ms: int | float | None,
) -> Mapping[str, Any] | None:
    """Return the newest relevant sample only when quality and time are trustworthy."""

    if not isinstance(samples, list) or not samples:
        return None
    for sample in reversed(samples):
        if not isinstance(sample, Mapping):
            continue
        values = sample.get("values", sample)
        if not isinstance(values, Mapping) or not any(key in values for key in keys):
            continue

        quality = values.get("dataQuality")
        if quality is not None and str(quality).upper() not in _ACCEPTABLE_DATA_QUALITIES:
            return None

        if reference_ts_ms is not None:
            sample_ts = sample.get("ts")
            if (
                isinstance(reference_ts_ms, bool)
                or not isinstance(reference_ts_ms, (int, float))
                or not math.isfinite(float(reference_ts_ms))
                or isinstance(sample_ts, bool)
                or not isinstance(sample_ts, (int, float))
                or not math.isfinite(float(sample_ts))
            ):
                return None
            age_ms = float(reference_ts_ms) - float(sample_ts)
            if age_ms > settings.max_sample_age_seconds * 1000:
                return None
            if age_ms < -settings.max_future_skew_seconds * 1000:
                return None
        return values
    return None


@dataclass(frozen=True)
class AnomalyAssessment:
    device: str
    zone_id: str
    active: bool
    anomaly_type: str = "NONE"
    score: float = 0.0
    quality: str = "GOOD"
    reason: str = "NORMAL"
    ready: bool = True

    def telemetry(self) -> dict[str, Any]:
        telemetry = {
            "anomalySensor": self.device,
            "anomalyZoneId": self.zone_id,
            "anomalyReason": self.reason,
            "anomalyMethod": "EWMA_ZSCORE",
            "anomalyQuality": self.quality,
        }
        if self.ready:
            telemetry.update(
                {
                    "anomalyActive": self.active,
                    "anomalyType": self.anomaly_type,
                    "anomalyScore": round(self.score, 3),
                }
            )
        return telemetry


@dataclass(frozen=True)
class DetectionBatch:
    assessments: dict[str, AnomalyAssessment]

    @property
    def active(self) -> tuple[AnomalyAssessment, ...]:
        return tuple(item for item in self.assessments.values() if item.active)

    @property
    def telemetry(self) -> dict[str, dict[str, Any]]:
        return {device: item.telemetry() for device, item in self.assessments.items()}

    @property
    def warming_up(self) -> bool:
        return any(not item.ready for item in self.assessments.values())


@dataclass
class _SensorState:
    history: deque[float]
    peer_residual_history: deque[float]
    samples: int = 0
    ewma_mean: float | None = None
    ewma_variance: float = 0.0
    active_type: str | None = None
    active_score: float = 0.0
    active_reason: str = ""
    normal_streak: int = 0


@dataclass(frozen=True)
class _Candidate:
    anomaly_type: str
    score: float
    reason: str
    priority: int


@dataclass(frozen=True)
class ValveLeakAssessment:
    device: str
    zone_id: str
    active: bool
    score: float = 0.0
    pulse_delta: float = 0.0
    reason: str = "NORMAL"
    quality: str = "GOOD"
    ready: bool = True

    def telemetry(self) -> dict[str, Any]:
        telemetry = {
            "valveLeakReason": self.reason,
            "valveLeakZoneId": self.zone_id,
            "valveLeakMethod": "VALVE_FLOW_CROSS_SIGNAL",
            "valveLeakQuality": self.quality,
        }
        if self.ready:
            telemetry.update(
                {
                    "valveLeakActive": self.active,
                    "valveLeakScore": round(self.score, 3),
                    "valveLeakPulseDelta": round(self.pulse_delta, 3),
                }
            )
        return telemetry


@dataclass
class _LeakState:
    last_pulse_counter: float | None = None
    evidence_streak: int = 0
    active: bool = False
    active_score: float = 0.0
    active_pulse_delta: float = 0.0
    active_reason: str = ""
    normal_streak: int = 0
    samples: int = 0


class SoilMoistureAnomalyDetector:
    """Stateful EWMA/z-score and peer-consistency detector."""

    def __init__(
        self,
        device_zones: Mapping[str, str],
        settings: AnomalySettings | None = None,
    ) -> None:
        if not device_zones:
            raise ValueError("at least one soil-moisture device is required")
        self.device_zones = dict(device_zones)
        self.settings = settings or AnomalySettings()
        self.settings._validate()
        history_size = max(
            self.settings.warmup_samples,
            self.settings.stuck_window,
            self.settings.drift_window,
        )
        self._states = {
            device: _SensorState(
                deque(maxlen=history_size),
                deque(maxlen=self.settings.drift_window),
            )
            for device in self.device_zones
        }

    def evaluate(
        self,
        readings: Mapping[str, Any],
        reference_ts_ms: int | float | None = None,
    ) -> DetectionBatch:
        values = self._extract_moisture(readings, reference_ts_ms)
        candidates: dict[str, _Candidate] = {}

        for device, value in values.items():
            candidate = self._update_ewma(device, value)
            if candidate is not None:
                candidates[device] = candidate

        self._add_peer_outliers(values, candidates)
        self._add_drift_sensors(values, candidates)
        self._add_stuck_sensors(values, candidates)

        assessments = {
            device: self._resolve(device, candidates.get(device)) for device in values
        }
        return DetectionBatch(assessments)

    def _extract_moisture(
        self,
        readings: Mapping[str, Any],
        reference_ts_ms: int | float | None,
    ) -> dict[str, float]:
        extracted: dict[str, float] = {}
        for device in self.device_zones:
            values = _trusted_latest_values(
                readings.get(device),
                ("moisture",),
                self.settings,
                reference_ts_ms,
            )
            if values is None:
                continue
            moisture = values.get("moisture")
            if (
                isinstance(moisture, bool)
                or not isinstance(moisture, (int, float))
                or not math.isfinite(float(moisture))
            ):
                continue
            extracted[device] = float(moisture)
        return extracted

    def _update_ewma(self, device: str, value: float) -> _Candidate | None:
        state = self._states[device]
        candidate = None
        previous_mean = state.ewma_mean
        if previous_mean is not None and state.samples >= self.settings.warmup_samples:
            deviation = abs(value - previous_mean)
            standard_deviation = max(
                math.sqrt(max(0.0, state.ewma_variance)),
                self.settings.minimum_stddev,
            )
            score = deviation / standard_deviation
            if score >= self.settings.z_score_threshold:
                candidate = _Candidate(
                    "SPIKE",
                    score,
                    f"moisture {value:.2f}% deviates from EWMA {previous_mean:.2f}%",
                    30,
                )

        if previous_mean is None:
            state.ewma_mean = value
            state.ewma_variance = 0.0
        else:
            residual = value - previous_mean
            alpha = self.settings.ewma_alpha
            state.ewma_mean = previous_mean + alpha * residual
            state.ewma_variance = (1 - alpha) * (
                state.ewma_variance + alpha * residual * residual
            )
        state.samples += 1
        state.history.append(value)
        return candidate

    def _add_peer_outliers(
        self,
        values: Mapping[str, float],
        candidates: dict[str, _Candidate],
    ) -> None:
        for zone_id in set(self.device_zones.values()):
            peers = {
                device: value
                for device, value in values.items()
                if self.device_zones[device] == zone_id
            }
            if len(peers) < self.settings.peer_min_sensors:
                continue
            median = statistics.median(peers.values())
            for device, value in peers.items():
                deviation = abs(value - median)
                if deviation < self.settings.peer_deviation_threshold:
                    continue
                candidate = _Candidate(
                    "CROSS_SENSOR_OUTLIER",
                    deviation / self.settings.peer_deviation_threshold,
                    f"moisture {value:.2f}% differs from peer median {median:.2f}%",
                    20,
                )
                self._keep_higher_priority(candidates, device, candidate)

    def _add_drift_sensors(
        self,
        values: Mapping[str, float],
        candidates: dict[str, _Candidate],
    ) -> None:
        window = self.settings.drift_window
        for zone_id in set(self.device_zones.values()):
            peers = {
                device: value
                for device, value in values.items()
                if self.device_zones[device] == zone_id
            }
            if len(peers) < self.settings.peer_min_sensors:
                continue
            for device, value in peers.items():
                other_values = [peer_value for peer, peer_value in peers.items() if peer != device]
                residual = value - statistics.median(other_values)
                history = self._states[device].peer_residual_history
                history.append(residual)
                if len(history) < window:
                    continue
                recent = tuple(history)[-window:]
                change = recent[-1] - recent[0]
                if abs(change) < self.settings.drift_min_change:
                    continue
                direction = 1 if change > 0 else -1
                steps = [later - earlier for earlier, later in zip(recent, recent[1:])]
                consistent_steps = sum(1 for step in steps if step * direction > 0)
                consistency = consistent_steps / len(steps)
                if consistency < self.settings.drift_consistency_ratio:
                    continue
                candidate = _Candidate(
                    "DRIFT",
                    abs(change) / self.settings.drift_min_change,
                    (
                        f"peer-relative moisture changed {change:+.2f}% over "
                        f"{window} samples (consistency {consistency:.2f})"
                    ),
                    15,
                )
                self._keep_higher_priority(candidates, device, candidate)

    def _add_stuck_sensors(
        self,
        values: Mapping[str, float],
        candidates: dict[str, _Candidate],
    ) -> None:
        window = self.settings.stuck_window
        for device in values:
            value = values[device]
            state = self._states[device]
            if len(state.history) < window:
                continue
            recent = tuple(state.history)[-window:]
            if max(recent) - min(recent) > self.settings.stuck_epsilon:
                continue
            zone_id = self.device_zones[device]
            peer_current_values = [
                peer_value
                for peer, peer_value in values.items()
                if peer != device and self.device_zones[peer] == zone_id
            ]
            if peer_current_values:
                peer_current = statistics.median(peer_current_values)
                margin = self.settings.stuck_boundary_margin
                if value <= margin and peer_current <= margin:
                    continue
                if value >= 100 - margin and peer_current >= 100 - margin:
                    continue
            peer_movements = []
            for peer, peer_state in self._states.items():
                if peer == device or self.device_zones[peer] != zone_id:
                    continue
                if len(peer_state.history) < window:
                    continue
                peer_recent = tuple(peer_state.history)[-window:]
                peer_movements.append(abs(peer_recent[-1] - peer_recent[0]))
            if not peer_movements:
                continue
            peer_movement = statistics.median(peer_movements)
            if peer_movement < self.settings.stuck_peer_movement:
                continue
            candidate = _Candidate(
                "STUCK",
                peer_movement / self.settings.stuck_peer_movement,
                (
                    f"moisture stayed within {self.settings.stuck_epsilon:.2f}% "
                    f"while peers moved {peer_movement:.2f}%"
                ),
                10,
            )
            self._keep_higher_priority(candidates, device, candidate)

    @staticmethod
    def _keep_higher_priority(
        candidates: dict[str, _Candidate],
        device: str,
        candidate: _Candidate,
    ) -> None:
        current = candidates.get(device)
        if current is None or candidate.priority > current.priority:
            candidates[device] = candidate

    def _resolve(self, device: str, candidate: _Candidate | None) -> AnomalyAssessment:
        state = self._states[device]
        zone_id = self.device_zones[device]
        if state.samples <= self.settings.warmup_samples:
            return AnomalyAssessment(
                device,
                zone_id,
                False,
                quality="WARMING_UP",
                reason=f"WARMING_UP {state.samples}/{self.settings.warmup_samples}",
                ready=False,
            )
        if candidate is not None:
            state.active_type = candidate.anomaly_type
            state.active_score = candidate.score
            state.active_reason = candidate.reason
            state.normal_streak = 0
            return AnomalyAssessment(
                device,
                zone_id,
                True,
                candidate.anomaly_type,
                candidate.score,
                "SUSPECT",
                candidate.reason,
            )

        if state.active_type is not None:
            state.normal_streak += 1
            if state.normal_streak < self.settings.clear_samples:
                return AnomalyAssessment(
                    device,
                    zone_id,
                    True,
                    state.active_type,
                    state.active_score,
                    "SUSPECT",
                    f"recovering {state.normal_streak}/{self.settings.clear_samples}",
                )
            state.active_type = None
            state.active_score = 0.0
            state.active_reason = ""
            state.normal_streak = 0

        return AnomalyAssessment(device, zone_id, False)


class ValveLeakDetector:
    """Detect water movement while the authoritative valve state is OFF."""

    def __init__(
        self,
        zone_devices: Mapping[str, tuple[str, str]],
        settings: AnomalySettings | None = None,
    ) -> None:
        self.zone_devices = dict(zone_devices)
        self.settings = settings or AnomalySettings()
        self.settings._validate()
        self._states = {zone_id: _LeakState() for zone_id in self.zone_devices}

    def evaluate(
        self,
        readings: Mapping[str, Any],
        reference_ts_ms: int | float | None = None,
    ) -> dict[str, ValveLeakAssessment]:
        assessments: dict[str, ValveLeakAssessment] = {}
        for zone_id, (valve_device, meter_device) in self.zone_devices.items():
            valve_values = _trusted_latest_values(
                readings.get(valve_device),
                ("actualValveState", "valveState"),
                self.settings,
                reference_ts_ms,
            )
            meter_values = _trusted_latest_values(
                readings.get(meter_device),
                ("pulseCounter", "flowRate"),
                self.settings,
                reference_ts_ms,
            )
            if valve_values is None or meter_values is None:
                continue
            if (
                self.settings.require_trusted_hydraulics
                and meter_values.get("hydraulicAnalyticsTrusted") is not True
            ):
                continue
            valve_state = valve_values.get("actualValveState", valve_values.get("valveState"))
            pulse_counter = self._number(meter_values.get("pulseCounter"))
            flow_rate = self._number(meter_values.get("flowRate"))
            if valve_state not in {"ON", "OFF"} or (
                pulse_counter is None and flow_rate is None
            ):
                continue
            assessments[meter_device] = self._evaluate_zone(
                zone_id,
                meter_device,
                valve_state,
                pulse_counter,
                flow_rate,
            )
        return assessments

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    def _evaluate_zone(
        self,
        zone_id: str,
        meter_device: str,
        valve_state: str,
        pulse_counter: float | None,
        flow_rate: float | None,
    ) -> ValveLeakAssessment:
        state = self._states[zone_id]
        state.samples += 1
        previous = state.last_pulse_counter
        if pulse_counter is not None:
            state.last_pulse_counter = pulse_counter
        pulse_delta = (
            0.0
            if pulse_counter is None or previous is None or pulse_counter < previous
            else pulse_counter - previous
        )
        pulse_evidence = pulse_delta >= self.settings.leak_min_pulse_delta
        flow_evidence = (
            flow_rate is not None and flow_rate >= self.settings.leak_flow_threshold
        )
        evidence = valve_state == "OFF" and (pulse_evidence or flow_evidence)

        if evidence:
            state.evidence_streak += 1
            state.normal_streak = 0
            score = max(
                pulse_delta / self.settings.leak_min_pulse_delta,
                0.0 if flow_rate is None else flow_rate / self.settings.leak_flow_threshold,
            )
            reason = (
                f"valve OFF but pulse delta={pulse_delta:.2f} "
                f"and flowRate={'NA' if flow_rate is None else f'{flow_rate:.3f}'}"
            )
            if state.evidence_streak >= self.settings.leak_consecutive_samples:
                state.active = True
                state.active_score = score
                state.active_pulse_delta = pulse_delta
                state.active_reason = reason
        else:
            state.evidence_streak = 0
            if state.active:
                state.normal_streak += 1
                if state.normal_streak >= self.settings.leak_clear_samples:
                    state.active = False
                    state.active_score = 0.0
                    state.active_pulse_delta = 0.0
                    state.active_reason = ""
                    state.normal_streak = 0

        if state.active:
            reason = state.active_reason
            if not evidence:
                reason = f"recovering {state.normal_streak}/{self.settings.leak_clear_samples}"
            return ValveLeakAssessment(
                meter_device,
                zone_id,
                True,
                state.active_score,
                state.active_pulse_delta,
                reason,
            )
        if state.samples < self.settings.leak_consecutive_samples:
            return ValveLeakAssessment(
                meter_device,
                zone_id,
                False,
                reason=(
                    f"WARMING_UP {state.samples}/"
                    f"{self.settings.leak_consecutive_samples}"
                ),
                quality="WARMING_UP",
                ready=False,
            )
        return ValveLeakAssessment(meter_device, zone_id, False)


class SoilMoistureAnalyticsHook:
    """Adapt the detector to the Gateway advisory analytics protocol."""

    def __init__(
        self,
        device_zones: Mapping[str, str],
        settings: AnomalySettings | None = None,
    ) -> None:
        self.detector = SoilMoistureAnomalyDetector(device_zones, settings)

    def evaluate(self, snapshot: dict[str, Any]) -> AnalyticsResult:
        readings = snapshot.get("readings", {})
        if not isinstance(readings, Mapping):
            return AnalyticsResult(reason="ANALYTICS_INVALID_SNAPSHOT")
        batch = self.detector.evaluate(readings, snapshot.get("ts"))
        active = batch.active
        if not active:
            reason = "ANALYTICS_WARMING_UP" if batch.warming_up else "ANALYTICS_OK"
            if not batch.assessments:
                reason = "ANALYTICS_NO_TRUSTED_INPUT"
            return AnalyticsResult(reason=reason, telemetry=batch.telemetry)
        types = ",".join(sorted({item.anomaly_type for item in active}))
        return AnalyticsResult(
            recommendation="INSPECT_SENSOR",
            score=max(item.score for item in active),
            reason=f"ANOMALY_ACTIVE:{types}",
            telemetry=batch.telemetry,
        )


class SmartFarmAnalyticsHook:
    """Combine soil-sensor and valve/flow advisory anomaly detectors."""

    def __init__(
        self,
        device_zones: Mapping[str, str],
        zone_devices: Mapping[str, tuple[str, str]],
        settings: AnomalySettings | None = None,
    ) -> None:
        self.soil_detector = SoilMoistureAnomalyDetector(device_zones, settings)
        self.leak_detector = ValveLeakDetector(zone_devices, settings)

    def evaluate(self, snapshot: dict[str, Any]) -> AnalyticsResult:
        readings = snapshot.get("readings", {})
        if not isinstance(readings, Mapping):
            return AnalyticsResult(reason="ANALYTICS_INVALID_SNAPSHOT")
        reference_ts_ms = snapshot.get("ts")
        soil_batch = self.soil_detector.evaluate(readings, reference_ts_ms)
        leak_assessments = self.leak_detector.evaluate(readings, reference_ts_ms)
        telemetry = dict(soil_batch.telemetry)
        telemetry.update(
            {device: item.telemetry() for device, item in leak_assessments.items()}
        )
        soil_active = soil_batch.active
        leak_active = tuple(item for item in leak_assessments.values() if item.active)
        if not soil_active and not leak_active:
            warming_up = soil_batch.warming_up or any(
                not item.ready for item in leak_assessments.values()
            )
            reason = "ANALYTICS_WARMING_UP" if warming_up else "ANALYTICS_OK"
            if not soil_batch.assessments and not leak_assessments:
                reason = "ANALYTICS_NO_TRUSTED_INPUT"
            return AnalyticsResult(reason=reason, telemetry=telemetry)
        active_types = {item.anomaly_type for item in soil_active}
        if leak_active:
            active_types.add("VALVE_LEAK")
        scores = [item.score for item in soil_active] + [item.score for item in leak_active]
        return AnalyticsResult(
            recommendation="INSPECT_IRRIGATION" if leak_active else "INSPECT_SENSOR",
            score=max(scores),
            reason=f"ANOMALY_ACTIVE:{','.join(sorted(active_types))}",
            telemetry=telemetry,
        )


def build_analytics_hook(
    config: Mapping[str, Any],
    mapped_devices: set[str] | tuple[str, ...],
):
    """Build the optional advisory hook shared by SIM and hardware runtimes."""

    analytics_config = config.get("analytics", {})
    if analytics_config is None:
        analytics_config = {}
    if not isinstance(analytics_config, Mapping):
        raise ValueError("analytics configuration must be an object")
    enabled = analytics_config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("analytics.enabled must be boolean")
    if not enabled:
        return NoOpAnalyticsHook()
    method = str(analytics_config.get("method", "EWMA_ZSCORE")).upper()
    if method != "EWMA_ZSCORE":
        raise ValueError("analytics.method must be EWMA_ZSCORE")
    mapped = set(mapped_devices)
    raw_devices = config.get("devices", [])
    if not isinstance(raw_devices, list):
        raise ValueError("devices configuration must be an array")
    device_zones = {
        str(item["name"]): str(item["zoneId"])
        for item in raw_devices
        if isinstance(item, Mapping)
        and item.get("name") in mapped
        and item.get("profile") == "SI Soil Moisture Sensor"
        and item.get("zoneId")
    }
    if not device_zones:
        raise ValueError("enabled analytics requires mapped soil-moisture devices")
    devices_by_zone: dict[str, dict[str, str]] = {}
    for item in raw_devices:
        if (
            not isinstance(item, Mapping)
            or item.get("name") not in mapped
            or not item.get("zoneId")
        ):
            continue
        profile = item.get("profile")
        if profile == "SI Smart Valve":
            devices_by_zone.setdefault(str(item["zoneId"]), {})["valve"] = str(
                item["name"]
            )
        elif profile == "SI Water Meter":
            devices_by_zone.setdefault(str(item["zoneId"]), {})["meter"] = str(
                item["name"]
            )
    zone_devices = {
        zone_id: (devices["valve"], devices["meter"])
        for zone_id, devices in devices_by_zone.items()
        if "valve" in devices and "meter" in devices
    }
    settings = AnomalySettings.from_mapping(analytics_config.get("settings"))
    return SmartFarmAnalyticsHook(device_zones, zone_devices, settings)


def merge_analytics_telemetry(
    readings: dict[str, list[dict[str, Any]]],
    telemetry: Mapping[str, Mapping[str, Any]],
) -> None:
    """Attach prior off-loop results without creating data for offline devices."""

    for device, analytics_values in telemetry.items():
        samples = readings.get(device)
        if not samples or not isinstance(analytics_values, Mapping):
            continue
        sample = samples[-1]
        if not isinstance(sample, dict):
            continue
        values = sample.get("values")
        if not isinstance(values, dict):
            continue
        values.update(analytics_values)
