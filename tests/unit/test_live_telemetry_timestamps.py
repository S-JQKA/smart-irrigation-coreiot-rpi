import sys
import unittest
import json
import threading
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'implementation/gateway'))
from coreiot.gateway_client import GatewayClient, GatewaySettings
from coreiot.gateway_protocol import TOPIC_TELEMETRY


class LiveTimestampTests(unittest.TestCase):
    def setUp(self):
        self.client = GatewayClient.__new__(GatewayClient)
        self.client._publish_or_buffer = Mock()

    @patch('coreiot.gateway_client.time.time', return_value=1788930000)
    def test_future_in_mixed_batch_rejects_all_before_buffer(self, _):
        with self.assertRaisesRegex(ValueError, 'FUTURE_TELEMETRY_TIMESTAMP'):
            self.client.publish_telemetry({'a': [{'ts': 1788930000000, 'values': {'x': 1}}],
                                           'b': [{'ts': 1800000130000, 'values': {'x': 2}}]})
        self.client._publish_or_buffer.assert_not_called()

    @patch('coreiot.gateway_client.time.time', return_value=1788930000)
    def test_gateway_future_and_malformed_timestamp_rejected(self, _):
        for ts in [1800000130000, True, float('nan'), '1788930000000', -1]:
            with self.subTest(ts=ts), self.assertRaises(ValueError):
                self.client.publish_gateway_telemetry({'ts': ts, 'values': {'x': 1}})
        self.client._publish_or_buffer.assert_not_called()

    @patch('coreiot.gateway_client.time.time', return_value=1788930000)
    def test_past_and_clock_skew_and_server_time_remain_supported(self, _):
        self.client.publish_telemetry({'a': [{'ts': 1700000000000, 'values': {'x': 1}},
                                           {'ts': 1788930030000, 'values': {'x': 2}}, {'x': 3}]})
        self.client._publish_or_buffer.assert_called_once()

    @patch('coreiot.gateway_client.time.time', return_value=1788930000)
    def test_replay_rechecks_after_clock_moves_backwards(self, _):
        self.client._connected = True
        self.client._lock = threading.Lock()
        self.client.settings = GatewaySettings('test', 'test')
        self.client._client = Mock()
        payload = json.dumps({'a': [{'ts': 1800000130000, 'values': {'x': 1}}]})
        self.client._buffer = deque([(TOPIC_TELEMETRY, payload, 1, 1788930000000)])
        self.client._flush_buffer()
        self.client._client.publish.assert_not_called()
        self.assertFalse(self.client._buffer)


if __name__ == '__main__':
    unittest.main()
