import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from anomaly_detector import (  # noqa: E402
    AnomalySettings,
    SmartFarmAnalyticsHook,
    SoilMoistureAnalyticsHook,
    SoilMoistureAnomalyDetector,
    ValveLeakDetector,
)
from analytics_hook import NoOpAnalyticsHook  # noqa: E402
from gateway_runtime import _build_analytics_hook, _merge_analytics_telemetry  # noqa: E402



def readings(values: dict[str, float], timestamp: int = 1) -> dict:
    return {
        device: [
            {
                "ts": timestamp,
                "values": {"moisture": value, "dataQuality": "OK"},
            }
        ]
        for device, value in values.items()
    }


class AnomalySettingsTest(unittest.TestCase):
    def test_camel_case_mapping_and_unknown_keys(self) -> None:
        settings = AnomalySettings.from_mapping(
            {"ewmaAlpha": 0.3, "warmupSamples": 4, "clearSamples": 2}
        )
        self.assertEqual(0.3, settings.ewma_alpha)
        self.assertEqual(4, settings.warmup_samples)
        self.assertEqual(2, settings.clear_samples)
        with self.assertRaisesRegex(ValueError, "unknown analytics settings"):
            AnomalySettings.from_mapping({"mysteryThreshold": 1})

    def test_rejects_invalid_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "ewmaAlpha"):
            AnomalySettings(ewma_alpha=0)._validate()
        with self.assertRaisesRegex(ValueError, "warmupSamples"):
            AnomalySettings(warmup_samples=True)._validate()






class SoilMoistureAnomalyDetectorTest(unittest.TestCase):
    def test_gradual_normal_series_does_not_raise_anomaly(self) -> None:
        detector = SoilMoistureAnomalyDetector({"soil-1": "field-1"})
        result = None
        for index in range(20):
            result = detector.evaluate(readings({"soil-1": 40 + index * 0.08}, index))
        self.assertIsNotNone(result)
        self.assertFalse(result.assessments["soil-1"].active)
        self.assertEqual("GOOD", result.assessments["soil-1"].quality)

    def test_ewma_zscore_detects_spike_after_warmup(self) -> None:
        detector = SoilMoistureAnomalyDetector({"soil-1": "field-1"})
        for index in range(10):
            detector.evaluate(readings({"soil-1": 40 + (index % 2) * 0.1}, index))
        result = detector.evaluate(readings({"soil-1": 78.0}, 11))
        assessment = result.assessments["soil-1"]
        self.assertTrue(assessment.active)
        self.assertEqual("SPIKE", assessment.anomaly_type)
        self.assertGreaterEqual(assessment.score, 3.0)

    def test_cross_sensor_outlier_uses_field_peer_median(self) -> None:
        settings = AnomalySettings(z_score_threshold=1000)
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices, settings)
        for index in range(settings.warmup_samples):
            detector.evaluate(
                readings(
                    {"soil-1": 31, "soil-2": 32, "soil-3": 30, "soil-4": 31},
                    index,
                )
            )
        result = detector.evaluate(
            readings(
                {"soil-1": 31, "soil-2": 32, "soil-3": 30, "soil-4": 84},
                settings.warmup_samples,
            )
        )
        self.assertEqual("CROSS_SENSOR_OUTLIER", result.assessments["soil-4"].anomaly_type)
        self.assertFalse(result.assessments["soil-1"].active)

    def test_stuck_requires_peer_movement(self) -> None:
        settings = AnomalySettings(
            z_score_threshold=1000,
            warmup_samples=3,
            stuck_window=5,
            peer_deviation_threshold=100,
        )
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices, settings)
        result = None
        for index in range(7):
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": 40.0,
                        "soil-2": 40.0 + index * 0.25,
                        "soil-3": 40.2 + index * 0.25,
                        "soil-4": 39.8 + index * 0.25,
                    },
                    index,
                )
            )
        self.assertIsNotNone(result)
        self.assertEqual("STUCK", result.assessments["soil-1"].anomaly_type)

    def test_shared_measurement_floor_is_not_a_stuck_sensor(self) -> None:
        settings = AnomalySettings(
            z_score_threshold=1000,
            warmup_samples=3,
            stuck_window=5,
            peer_deviation_threshold=100,
        )
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices, settings)
        result = None
        for index in range(8):
            peer_value = max(0.0, 0.8 - index * 0.14)
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": 0.0,
                        "soil-2": peer_value,
                        "soil-3": min(1.0, peer_value + 0.1),
                        "soil-4": min(1.0, peer_value + 0.2),
                    },
                    index,
                )
            )
        self.assertFalse(result.assessments["soil-1"].active)

    def test_boundary_value_still_flags_when_peers_are_far_away(self) -> None:
        settings = AnomalySettings(
            z_score_threshold=1000,
            warmup_samples=3,
            stuck_window=5,
            peer_deviation_threshold=100,
        )
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices, settings)
        result = None
        for index in range(7):
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": 0.0,
                        "soil-2": 40.0 + index * 0.2,
                        "soil-3": 40.2 + index * 0.2,
                        "soil-4": 39.8 + index * 0.2,
                    },
                    index,
                )
            )
        self.assertEqual("STUCK", result.assessments["soil-1"].anomaly_type)

    def test_peer_relative_drift_is_detected_and_eventually_clears(self) -> None:
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices)
        for index in range(12):
            baseline = 40 + index * 0.1
            detector.evaluate(
                readings(
                    {
                        "soil-1": baseline - 0.3,
                        "soil-2": baseline - 0.1,
                        "soil-3": baseline + 0.1,
                        "soil-4": baseline + 0.3,
                    },
                    index,
                )
            )
        result = None
        for index in range(12):
            baseline = 41.2 + index * 0.1
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": baseline - 0.3,
                        "soil-2": baseline - 0.1,
                        "soil-3": baseline + 0.1,
                        "soil-4": baseline + 0.3 + index * 0.5,
                    },
                    12 + index,
                )
            )
        self.assertIsNotNone(result)
        self.assertEqual("DRIFT", result.assessments["soil-4"].anomaly_type)

        for index in range(20):
            baseline = 43 + index * 0.05
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": baseline - 0.3,
                        "soil-2": baseline - 0.1,
                        "soil-3": baseline + 0.1,
                        "soil-4": baseline + 0.3,
                    },
                    24 + index,
                )
            )
        self.assertFalse(result.assessments["soil-4"].active)

    def test_common_field_trend_is_not_sensor_drift(self) -> None:
        devices = {f"soil-{index}": "field-1" for index in range(1, 5)}
        detector = SoilMoistureAnomalyDetector(devices)
        result = None
        for index in range(30):
            baseline = 35 + index * 0.4
            result = detector.evaluate(
                readings(
                    {
                        "soil-1": baseline - 0.3,
                        "soil-2": baseline - 0.1,
                        "soil-3": baseline + 0.1,
                        "soil-4": baseline + 0.3,
                    },
                    index,
                )
            )
        self.assertFalse(any(item.active for item in result.assessments.values()))

    def test_alarm_clears_only_after_configured_normal_streak(self) -> None:
        settings = AnomalySettings(clear_samples=3)
        detector = SoilMoistureAnomalyDetector({"soil-1": "field-1"}, settings)
        for index in range(10):
            detector.evaluate(readings({"soil-1": 40 + (index % 2) * 0.1}, index))
        self.assertTrue(detector.evaluate(readings({"soil-1": 78}, 11)).active)
        first = detector.evaluate(readings({"soil-1": 40.0}, 12))
        second = detector.evaluate(readings({"soil-1": 40.1}, 13))
        third = detector.evaluate(readings({"soil-1": 40.0}, 14))
        self.assertTrue(first.assessments["soil-1"].active)
        self.assertTrue(second.assessments["soil-1"].active)
        self.assertFalse(third.assessments["soil-1"].active)

    def test_missing_and_non_finite_values_are_not_scored(self) -> None:
        detector = SoilMoistureAnomalyDetector({"soil-1": "field-1"})
        result = detector.evaluate(
            {"soil-1": [{"values": {"moisture": float("nan")}}]}
        )
        self.assertEqual({}, result.assessments)

    def test_invalid_stale_and_future_samples_do_not_change_detector_state(self) -> None:
        settings = AnomalySettings(warmup_samples=2, max_sample_age_seconds=10)
        detector = SoilMoistureAnomalyDetector({"soil-1": "field-1"}, settings)
        invalid = readings({"soil-1": 99}, 100_000)
        invalid["soil-1"][0]["values"]["dataQuality"] = "INVALID"
        self.assertEqual({}, detector.evaluate(invalid, 100_000).assessments)
        self.assertEqual(
            {},
            detector.evaluate(readings({"soil-1": 99}, 80_000), 100_000).assessments,
        )
        self.assertEqual(
            {},
            detector.evaluate(readings({"soil-1": 99}, 110_000), 100_000).assessments,
        )
        first_valid = detector.evaluate(readings({"soil-1": 40}, 100_000), 100_000)
        self.assertEqual("WARMING_UP", first_valid.assessments["soil-1"].quality)
        self.assertEqual("WARMING_UP 1/2", first_valid.assessments["soil-1"].reason)

    def test_warmup_telemetry_does_not_publish_false_alarm_state(self) -> None:
        detector = SoilMoistureAnomalyDetector(
            {"soil-1": "field-1"},
            AnomalySettings(warmup_samples=2),
        )
        batch = detector.evaluate(readings({"soil-1": 40}, 1), 1)
        telemetry = batch.telemetry["soil-1"]
        self.assertEqual("WARMING_UP", telemetry["anomalyQuality"])
        self.assertNotIn("anomalyActive", telemetry)
        self.assertNotIn("anomalyType", telemetry)


class SoilMoistureAnalyticsHookTest(unittest.TestCase):
    def test_hook_returns_advisory_telemetry_without_commands(self) -> None:
        hook = SoilMoistureAnalyticsHook(
            {"soil-1": "field-1"},
            AnomalySettings(warmup_samples=2),
        )
        hook.evaluate({"readings": readings({"soil-1": 40.0})})
        hook.evaluate({"readings": readings({"soil-1": 40.1})})
        result = hook.evaluate({"readings": readings({"soil-1": 75.0})})
        self.assertEqual("INSPECT_SENSOR", result.recommendation)
        self.assertEqual("SPIKE", result.telemetry["soil-1"]["anomalyType"])
        self.assertNotIn("command", result.telemetry["soil-1"])

    def test_runtime_factory_is_disabled_by_default(self) -> None:
        config = {
            "devices": [
                {
                    "name": "soil-1",
                    "profile": "SI Soil Moisture Sensor",
                    "zoneId": "field-1",
                }
            ]
        }
        self.assertIsInstance(_build_analytics_hook(config, {"soil-1"}), NoOpAnalyticsHook)

    def test_runtime_factory_builds_enabled_detector(self) -> None:
        config = {
            "analytics": {
                "enabled": True,
                "method": "EWMA_ZSCORE",
                "settings": {"warmupSamples": 3},
            },
            "devices": [
                {
                    "name": "soil-1",
                    "profile": "SI Soil Moisture Sensor",
                    "zoneId": "field-1",
                }
            ],
        }
        self.assertIsInstance(
            _build_analytics_hook(config, {"soil-1"}),
            SmartFarmAnalyticsHook,
        )

    def test_merge_preserves_control_data_quality(self) -> None:
        current = {
            "soil-1": [
                {"ts": 1, "values": {"moisture": 40, "dataQuality": "VALID"}}
            ]
        }
        _merge_analytics_telemetry(
            current,
            {
                "soil-1": {"anomalyActive": True, "anomalyQuality": "SUSPECT"},
                "offline-soil": {"anomalyActive": True},
            },
        )
        values = current["soil-1"][0]["values"]
        self.assertEqual("VALID", values["dataQuality"])
        self.assertEqual("SUSPECT", values["anomalyQuality"])
        self.assertNotIn("offline-soil", current)


class ValveLeakDetectorTest(unittest.TestCase):
    @staticmethod
    def snapshot(valve_state: str, pulse_counter: float, flow_rate: float) -> dict:
        return {
            "valve-1": [{"values": {"actualValveState": valve_state}}],
            "meter-1": [
                {
                    "values": {
                        "pulseCounter": pulse_counter,
                        "flowRate": flow_rate,
                        "dataQuality": "OK",
                        "hydraulicAnalyticsTrusted": True,
                    }
                }
            ],
        }

    def test_leak_requires_repeated_water_movement_while_valve_off(self) -> None:
        detector = ValveLeakDetector({"field-1": ("valve-1", "meter-1")})
        baseline = detector.evaluate(self.snapshot("OFF", 100, 0))["meter-1"]
        first = detector.evaluate(self.snapshot("OFF", 102, 0.1))["meter-1"]
        second = detector.evaluate(self.snapshot("OFF", 104, 0.1))["meter-1"]
        self.assertFalse(baseline.active)
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertIn("valve OFF", second.reason)

    def test_leak_does_not_fire_when_valve_is_on_and_clears_after_recovery(self) -> None:
        detector = ValveLeakDetector({"field-1": ("valve-1", "meter-1")})
        detector.evaluate(self.snapshot("OFF", 100, 0))
        detector.evaluate(self.snapshot("OFF", 102, 0.1))
        self.assertTrue(detector.evaluate(self.snapshot("OFF", 104, 0.1))["meter-1"].active)
        first = detector.evaluate(self.snapshot("ON", 106, 0.2))["meter-1"]
        second = detector.evaluate(self.snapshot("ON", 108, 0.2))["meter-1"]
        third = detector.evaluate(self.snapshot("ON", 110, 0.2))["meter-1"]
        self.assertTrue(first.active)
        self.assertTrue(second.active)
        self.assertFalse(third.active)

    def test_pulse_counter_reset_is_not_a_leak(self) -> None:
        detector = ValveLeakDetector({"field-1": ("valve-1", "meter-1")})
        detector.evaluate(self.snapshot("OFF", 100, 0))
        result = detector.evaluate(self.snapshot("OFF", 2, 0))["meter-1"]
        self.assertFalse(result.active)

    def test_flow_only_can_detect_leak(self) -> None:
        detector = ValveLeakDetector(
            {"field-1": ("valve-1", "meter-1")},
            AnomalySettings(leak_consecutive_samples=2),
        )
        readings = {
            "valve-1": [{"values": {"actualValveState": "OFF"}}],
            "meter-1": [
                {
                    "values": {
                        "flowRate": 0.2,
                        "dataQuality": "OK",
                        "hydraulicAnalyticsTrusted": True,
                    }
                }
            ],
        }
        detector.evaluate(readings)
        result = detector.evaluate(readings)["meter-1"]
        self.assertTrue(result.active)

    def test_irrigation_task_sample_does_not_hide_valve_state(self) -> None:
        detector = ValveLeakDetector(
            {"field-1": ("valve-1", "meter-1")},
            AnomalySettings(leak_consecutive_samples=1),
        )
        readings = {
            "valve-1": [
                {"values": {"actualValveState": "OFF"}},
                {"values": {"irrigationTask": {"status": "COMPLETED"}}},
            ],
            "meter-1": [
                {
                    "values": {
                        "pulseCounter": 10,
                        "flowRate": 0.2,
                        "dataQuality": "OK",
                        "hydraulicAnalyticsTrusted": True,
                    }
                }
            ],
        }
        result = detector.evaluate(readings)["meter-1"]
        self.assertTrue(result.active)

    def test_combined_hook_publishes_leak_advisory_without_command(self) -> None:
        hook = SmartFarmAnalyticsHook(
            {"soil-1": "field-1"},
            {"field-1": ("valve-1", "meter-1")},
            AnomalySettings(warmup_samples=2),
        )
        for pulse in (100, 102, 104):
            payload = readings({"soil-1": 40})
            payload.update(self.snapshot("OFF", pulse, 0.1 if pulse > 100 else 0))
            result = hook.evaluate({"readings": payload})
        self.assertEqual("INSPECT_IRRIGATION", result.recommendation)
        self.assertTrue(result.telemetry["meter-1"]["valveLeakActive"])
        self.assertNotIn("command", result.telemetry["meter-1"])

    def test_untrusted_or_invalid_hydraulics_do_not_change_alarm_state(self) -> None:
        detector = ValveLeakDetector({"field-1": ("valve-1", "meter-1")})
        untrusted = self.snapshot("OFF", 100, 0.2)
        untrusted["meter-1"][0]["values"]["hydraulicAnalyticsTrusted"] = False
        self.assertEqual({}, detector.evaluate(untrusted))
        invalid = self.snapshot("OFF", 102, 0.2)
        invalid["meter-1"][0]["values"]["dataQuality"] = "STALE"
        self.assertEqual({}, detector.evaluate(invalid))
        first = detector.evaluate(self.snapshot("OFF", 104, 0.2))["meter-1"]
        self.assertEqual("WARMING_UP", first.quality)
        self.assertFalse(first.ready)

    def test_hil_override_allows_controlled_synthetic_leak(self) -> None:
        detector = ValveLeakDetector(
            {"field-1": ("valve-1", "meter-1")},
            AnomalySettings(require_trusted_hydraulics=False),
        )
        first = self.snapshot("OFF", 100, 0)
        second = self.snapshot("OFF", 102, 0.2)
        third = self.snapshot("OFF", 104, 0.2)
        for payload in (first, second, third):
            payload["meter-1"][0]["values"]["hydraulicAnalyticsTrusted"] = False
        detector.evaluate(first)
        detector.evaluate(second)
        self.assertTrue(detector.evaluate(third)["meter-1"].active)

    def test_leak_warmup_omits_false_alarm_state(self) -> None:
        detector = ValveLeakDetector({"field-1": ("valve-1", "meter-1")})
        assessment = detector.evaluate(self.snapshot("OFF", 100, 0))["meter-1"]
        telemetry = assessment.telemetry()
        self.assertEqual("WARMING_UP", telemetry["valveLeakQuality"])
        self.assertNotIn("valveLeakActive", telemetry)


if __name__ == "__main__":
    unittest.main()
