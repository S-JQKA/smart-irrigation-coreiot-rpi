"""The source distribution must contain a complete widget and its checks."""
import runpy
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SubmissionContentsTests(unittest.TestCase):
    def test_widget_refresh_preserves_metadata_and_input(self):
        module = runpy.run_path(str(ROOT / 'tools/release/update_field_selector_sources.py'))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.json'
            output = Path(directory) / 'nested/output.json'
            original = {'name': 'tenant widget', 'descriptor': {'customFlag': True}}
            source.write_text(json.dumps(original), encoding='utf-8-sig')
            module['refresh_widget'](source, output)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['name'], original['name'])
            self.assertTrue(result['descriptor']['customFlag'])
            self.assertEqual(json.loads(source.read_text(encoding='utf-8-sig')), original)
            for key, filename in [('templateHtml', 'template.html'), ('templateCss', 'style.css'), ('controllerScript', 'controller.js')]:
                self.assertEqual(result['descriptor'][key], (module['SOURCE'] / filename).read_text(encoding='utf-8'))

    def test_invalid_widget_export_does_not_overwrite_output(self):
        module = runpy.run_path(str(ROOT / 'tools/release/update_field_selector_sources.py'))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.json'
            output = Path(directory) / 'output.json'
            source.write_text('{}', encoding='utf-8')
            output.write_text('preserve', encoding='utf-8')
            with self.assertRaises(ValueError):
                module['refresh_widget'](source, output)
            self.assertEqual(output.read_text(encoding='utf-8'), 'preserve')

    def test_contract_contains_widget_feedback_and_transport_metrics(self):
        contract = json.loads((ROOT / 'implementation/coreiot/manifests/data_contract.json').read_text(encoding='utf-8'))
        self.assertEqual(9, len(contract['fieldAttributes']))
        for key in ('fieldConfigStatus', 'fieldConfigReason', 'fieldConfigRequested', 'fieldConfigEffective', 'fieldConfigReceivedAt', 'fieldConfigAppliedAt'):
            self.assertIn(key, contract['fieldTelemetry'])
        for key in ('telemetryPublishCount', 'telemetryPubackCount', 'telemetryDeliveryFailures', 'lastTelemetryPubackMs'):
            self.assertIn(key, contract['gatewayTelemetry'])

    def test_widget_and_feedback_are_included_without_development_files(self):
        builder = runpy.run_path(str(ROOT / "tools/release/build_submission.py"))
        names = {p.relative_to(ROOT).as_posix() for p in builder["source_files"]()}
        for name in ("template.html", "style.css", "controller.js", "README.md"):
            self.assertIn("implementation/coreiot/widgets/field_selector/" + name, names)
        self.assertIn("tests/widget_field_selector.test.cjs", names)
        self.assertIn("implementation/gateway/field_configuration.py", names)
        self.assertFalse(any(name.startswith("local_dev/") or "/.env" in name for name in names))


if __name__ == "__main__":
    unittest.main()
