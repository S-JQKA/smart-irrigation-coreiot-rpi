"""Configuration feedback uses the product controller, with no HIL imports."""
import tempfile
import unittest
from pathlib import Path

from test_hardware_controller import model_ready, NOW
from field_configuration import FieldConfigurationFeedback
from irrigation_controller import effective_field_config, restore_field_configuration
from runtime_state import FieldConfigStore


class FieldFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FieldConfigStore(Path(self.directory.name) / "fields.json")
        self.model, _ = model_ready()
        self.feedback = FieldConfigurationFeedback(self.model, self.store)
        self.device = self.model.zones["field-1"].valve_device

    def telemetry(self, timestamp=NOW):
        readings = self.model.tick(timestamp)
        self.feedback.annotate(readings)
        return readings[self.device][0]["values"]

    def test_apply_manual_isolated_feedback_and_restart(self):
        other = effective_field_config(self.model, "field-2")
        values = {**effective_field_config(self.model, "field-1"), "controlMode": "MANUAL"}
        self.assertEqual((True, "CONFIG_APPLIED"), self.feedback.apply(
            {"device": self.device, "data": values}, NOW))
        telemetry = self.telemetry()
        self.assertEqual("APPLIED", telemetry["fieldConfigStatus"])
        self.assertEqual(values, telemetry["fieldConfigRequested"])
        self.assertEqual(values, telemetry["fieldConfigEffective"])
        self.assertEqual(NOW, telemetry["fieldConfigAppliedAt"])
        self.assertEqual(other, effective_field_config(self.model, "field-2"))
        self.assertEqual(0, self.feedback.status["field-2"]["fieldConfigReceivedAt"])
        restored, _ = model_ready()
        restore_field_configuration(restored, self.store.load(), NOW + 1)
        self.assertEqual(values, effective_field_config(restored, "field-1"))
        # Restoring a cache is not a fresh cloud acknowledgement.
        self.assertEqual("LOCAL_ONLY", FieldConfigurationFeedback(restored, self.store)
                         .status["field-1"]["fieldConfigStatus"])

    def test_rejected_update_keeps_effective_config_and_last_applied_time(self):
        values = effective_field_config(self.model, "field-1")
        self.feedback.apply({"device": self.device, "shared": values}, NOW)
        rejected = {**values, "targetMoisture": 0}
        self.assertEqual((False, "INVALID_THRESHOLD_ORDER"), self.feedback.apply(
            {"device": self.device, "data": rejected}, NOW + 20))
        telemetry = self.telemetry(NOW + 20)
        self.assertEqual("REJECTED", telemetry["fieldConfigStatus"])
        self.assertEqual(rejected, telemetry["fieldConfigRequested"])
        self.assertEqual(values, telemetry["fieldConfigEffective"])
        self.assertEqual(NOW, telemetry["fieldConfigAppliedAt"])
        self.assertEqual(NOW + 20, telemetry["fieldConfigReceivedAt"])

    def test_partial_update_and_invalid_quotas(self):
        self.assertTrue(self.feedback.apply({"device": self.device, "data": {
            "controlMode": "MANUAL"}}, NOW)[0])
        previous = effective_field_config(self.model, "field-1")
        for update in ({"maxWaterPerDay": 0}, {"maxDurationSec": 1.5},
                       {"maxWaterPerDay": previous["maxWaterPerCycle"] / 2}):
            self.assertFalse(self.feedback.apply({"device": self.device, "data": update}, NOW)[0])
            self.assertEqual(previous, effective_field_config(self.model, "field-1"))
        self.assertGreater(self.feedback.status["field-1"]["fieldConfigReceivedAt"], NOW)

    def test_initial_incomplete_and_unrelated_attributes(self):
        self.model.requires_validated_configuration = True
        self.model.zone_runtime["field-1"].config_validated = False
        self.feedback = FieldConfigurationFeedback(self.model, self.store)
        self.assertEqual("WAITING", self.feedback.status["field-1"]["fieldConfigStatus"])
        self.assertEqual((False, "INCOMPLETE_INITIAL_CONFIG"), self.feedback.apply(
            {"device": self.device, "data": {"controlMode": "MANUAL"}}, NOW))
        before = self.feedback.status["field-1"].copy()
        self.assertIsNone(self.feedback.apply({"device": "unknown", "data": {"controlMode": "AUTO"}}, NOW))
        self.assertIsNone(self.feedback.apply({"device": self.device, "data": {"localScheduleConfig": {}}}, NOW))
        self.assertEqual(before, self.feedback.status["field-1"])


if __name__ == "__main__":
    unittest.main()
