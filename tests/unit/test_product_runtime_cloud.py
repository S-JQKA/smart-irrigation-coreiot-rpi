"""Run the product loop with real control logic and unacknowledged MQTT sends."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gateway_runtime
from configuration import load_config
from coreiot.gateway_protocol import TOPIC_TELEMETRY
from hardware_adapter import PeerStatus
from irrigation_controller import IrrigationController, effective_field_config
from test_hardware_controller import RecordingAdapter, CONFIG
from test_nonblocking_telemetry import make_client


class ProductCloudIntegrationTests(unittest.TestCase):
    def test_loop_applies_queued_config_publishes_feedback_and_survives_missing_puback(self):
        now = int(time.time() * 1000)
        config = load_config(CONFIG)
        baseline = IrrigationController.from_config(config)
        client, info = make_client(capacity=100)
        client._devices = {}
        client._shared_attribute_watches = {}
        client._attribute_request_id = 0
        client.stop = Mock()
        client.disconnect_devices = Mock(return_value=16)
        adapter = RecordingAdapter()
        adapter.query_state = lambda: {"zones": adapter.states.copy(), "pump": "OFF",
                                       "confirmedZones": list(adapter.states)}
        adapter.poll = lambda: [PeerStatus("central", "CENTRAL", True, now, False)]
        adapter.stop = Mock(side_effect=lambda: adapter.all_off("stop"))
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)

            def factory(settings, rpc_handler, **kwargs):
                def start():
                    for zone_id, zone in baseline.zones.items():
                        values = {**effective_field_config(baseline, zone_id), "controlMode": "MANUAL"}
                        kwargs["attribute_handler"]({"device": zone.valve_device, "data": values})
                    self.assertFalse((state_dir / "field-config.json").exists(),
                                     "MQTT callback must only enqueue configuration")
                client.start = start
                return client

            with patch.object(gateway_runtime, "GatewayClient", side_effect=factory), \
                 patch.object(gateway_runtime, "UartEspNowAdapter", return_value=adapter), \
                 patch.object(gateway_runtime, "runtime_state_dir", return_value=state_dir), \
                 patch.object(gateway_runtime, "clock_is_ready", return_value=True), \
                 patch.object(gateway_runtime, "settings_from_env", return_value=client.settings), \
                 patch.object(gateway_runtime.signal, "signal"), \
                 patch.dict("os.environ", {"SMARTFARM_SERIAL_PORT": "TEST"}):
                gateway_runtime.run(CONFIG, ticks=2, interval=0)

            payloads = [json.loads(call.args[1]) for call in client._client.publish.call_args_list
                        if call.args[0] == TOPIC_TELEMETRY]
            for zone_id, zone in baseline.zones.items():
                samples = [payload[zone.valve_device][0] for payload in payloads if zone.valve_device in payload]
                self.assertEqual(2, len(samples))
                for sample in samples:
                    self.assertEqual("APPLIED", sample["values"]["fieldConfigStatus"])
                    self.assertEqual("MANUAL", sample["values"]["fieldConfigEffective"]["controlMode"])
                    self.assertGreater(sample["values"]["fieldConfigReceivedAt"], 0)
            self.assertTrue((state_dir / "field-config.json").exists())
        info.wait_for_publish.assert_not_called()
        self.assertEqual(0, client.telemetry_puback_count)
        adapter.stop.assert_called_once()
        self.assertTrue(all(state == "OFF" for state in adapter.states.values()))


if __name__ == "__main__":
    unittest.main()
