import json
import sys
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implementation/gateway"))
from coreiot.gateway_client import GatewayClient, GatewaySettings
from coreiot.gateway_protocol import TOPIC_TELEMETRY


def make_client(capacity=4):
    client = GatewayClient.__new__(GatewayClient)
    client.settings = GatewaySettings("test", "test", buffer_capacity=capacity)
    client._connected = True
    client._lock = threading.Lock()
    client._buffer = deque(maxlen=capacity)
    client._pending_telemetry = deque()
    client._telemetry_publish_count = client._telemetry_puback_count = 0
    client._telemetry_delivery_failures = client._expired_buffer_count = 0
    client._last_telemetry_puback_ms = None
    client._client = Mock()
    info = Mock(rc=0)
    info.is_published.return_value = False
    info.wait_for_publish.side_effect = AssertionError("must not wait on PUBACK")
    client._client.publish.return_value = info
    return client, info


class NonblockingTelemetryTests(unittest.TestCase):
    def test_delayed_ack_counts_once_without_waiting(self):
        client, info = make_client()
        client.submit_telemetry({"valve": [{"ts": 1, "values": {"state": "OFF"}}]})
        self.assertEqual(0, client.telemetry_puback_count)
        info.is_published.return_value = True
        client.poll_telemetry_delivery()
        client.poll_telemetry_delivery()
        self.assertEqual(1, client.telemetry_puback_count)
        info.wait_for_publish.assert_not_called()

    def test_missing_ack_expires_without_raising_and_queue_stays_bounded(self):
        client, info = make_client(capacity=2)
        with patch("coreiot.gateway_client.time.monotonic", return_value=10):
            for _ in range(3):
                client.submit_telemetry({"valve": [{"values": {"x": 1}}]})
        self.assertEqual(2, len(client._pending_telemetry))
        self.assertEqual(1, client.telemetry_delivery_failures)
        with patch("coreiot.gateway_client.time.monotonic", return_value=16):
            client.poll_telemetry_delivery()
        self.assertEqual(3, client.telemetry_delivery_failures)
        self.assertFalse(client._pending_telemetry)
        info.wait_for_publish.assert_not_called()

    def test_transport_loss_buffers_and_reconnect_replays_without_wait(self):
        client, info = make_client()
        client._client.publish.side_effect = OSError("lost socket")
        client.submit_telemetry({"valve": [{"ts": 1, "values": {"x": 1}}]})
        self.assertEqual(1, client.buffer_depth)
        client._client.publish.side_effect = None
        client._devices = {}
        client._shared_attribute_watches = {}
        client.connection_handler = None
        client._ready_event = threading.Event()
        client.reconnect_snapshot_handler = lambda: client.submit_telemetry(
            {"valve": [{"values": {"x": 2}}]})
        client._on_connect(client._client, None, {}, 0)
        self.assertTrue(client._ready_event.is_set())
        self.assertEqual(0, client.buffer_depth)
        self.assertEqual(2, len(client._pending_telemetry))
        info.wait_for_publish.assert_not_called()
        info.is_published.return_value = True
        client.poll_telemetry_delivery()
        self.assertEqual(2, client.telemetry_puback_count)

    def test_replay_transport_error_preserves_buffer(self):
        client, _ = make_client()
        payload = json.dumps({"valve": [{"values": {"x": 1}}]})
        client._buffer_message(TOPIC_TELEMETRY, payload, 1)
        client._client.publish.side_effect = OSError("lost socket")
        client._flush_buffer()
        self.assertEqual(1, client.buffer_depth)

    def test_future_batch_is_rejected_before_any_publish(self):
        client, _ = make_client()
        with patch("coreiot.gateway_client.time.time", return_value=100):
            with self.assertRaisesRegex(ValueError, "FUTURE_TELEMETRY"):
                client.submit_telemetry({"a": [{"ts": 1, "values": {}}],
                                         "b": [{"ts": 200_000, "values": {}}]})
        client._client.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
