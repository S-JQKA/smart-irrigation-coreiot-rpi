import json
import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "implementation" / "gateway"))

from coreiot.gateway_client import GatewayClient, GatewaySettings  # noqa: E402
from coreiot.gateway_protocol import RpcGuard  # noqa: E402


class _FakeMqttClient:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.calls.append(("connect", host, port, keepalive))

    def loop_start(self) -> None:
        self.calls.append("loop_start")

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")


class _FakeReadyEvent:
    def __init__(self, calls: list[object], result: bool = True) -> None:
        self.calls = calls
        self.result = result

    def clear(self) -> None:
        self.calls.append("clear")

    def wait(self, timeout: float) -> bool:
        self.calls.append(("wait", timeout))
        return self.result


class _SuccessfulPublish:
    rc = 0

    def __init__(self) -> None:
        self.wait_timeouts: list[float] = []

    def wait_for_publish(self, timeout: float | None = None) -> None:
        if timeout is not None:
            self.wait_timeouts.append(timeout)

    def is_published(self) -> bool:
        return True


class _ReconnectMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []
        self.subscribed: list[tuple[str, int]] = []

    def publish(self, topic: str, payload: str, qos: int = 0) -> _SuccessfulPublish:
        self.published.append((topic, payload, qos))
        return _SuccessfulPublish()

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append((topic, qos))


class GatewayClientStartTest(unittest.TestCase):
    def make_client(self, ready: bool = True) -> tuple[GatewayClient, list[object]]:
        calls: list[object] = []
        client = GatewayClient.__new__(GatewayClient)
        client.settings = GatewaySettings(host="coreiot.test", access_token="test-token")
        client._client = _FakeMqttClient(calls)
        client._ready_event = _FakeReadyEvent(calls, ready)
        client._connected = ready
        client._connect_rc = 0 if ready else None
        return client, calls

    def test_start_waits_for_connack_before_returning(self) -> None:
        client, calls = self.make_client()

        client.start(timeout_seconds=3)

        self.assertEqual(
            [
                "clear",
                ("connect", "coreiot.test", 1883, 60),
                "loop_start",
                ("wait", 3),
            ],
            calls,
        )

    def test_start_cleans_up_after_timeout(self) -> None:
        client, calls = self.make_client(ready=False)

        with self.assertRaises(TimeoutError):
            client.start(timeout_seconds=0.1)

        self.assertEqual(["loop_stop", "disconnect"], calls[-2:])

    def test_stop_sends_disconnect_before_stopping_network_loop(self) -> None:
        client, calls = self.make_client()

        client.stop()

        self.assertEqual(["disconnect", "loop_stop"], calls[-2:])

    def test_disconnect_devices_waits_for_pubacks_and_clears_registration(self) -> None:
        mqtt_client = _ReconnectMqttClient()
        client = GatewayClient.__new__(GatewayClient)
        client._client = mqtt_client
        client._devices = {"Soil 1": "Soil", "Pump 1": "Pump"}
        client._buffer = deque()
        client._lock = threading.Lock()
        client._connected = True

        acknowledged = client.disconnect_devices(["Soil 1", "Pump 1"], timeout_seconds=1)

        self.assertEqual(2, acknowledged)
        self.assertEqual({}, client._devices)
        self.assertEqual(
            ["v1/gateway/disconnect", "v1/gateway/disconnect"],
            [item[0] for item in mqtt_client.published],
        )
        self.assertTrue(all(item[2] == 1 for item in mqtt_client.published))

    def test_pause_transport_retains_registration_state_and_marks_disconnected(self) -> None:
        client, calls = self.make_client()
        states: list[bool] = []
        client.connection_handler = states.append
        client._devices = {"SI Smart Valve 1": "SI Smart Valve"}

        client.pause_transport()

        self.assertFalse(client.connected)
        self.assertEqual(["disconnect", "loop_stop"], calls[-2:])
        self.assertEqual([False], states)
        self.assertIn("SI Smart Valve 1", client._devices)

    def test_reconnect_reannounces_downstream_devices_before_buffer_flush(self) -> None:
        mqtt_client = _ReconnectMqttClient()
        connection_states: list[bool] = []
        client = GatewayClient.__new__(GatewayClient)
        client._client = mqtt_client
        client._devices = {"SI Smart Valve 1": "SI Smart Valve"}
        client._buffer = deque()
        client._lock = threading.Lock()
        client._ready_event = threading.Event()
        client._connected = False
        client._connect_rc = None
        client.connection_handler = connection_states.append

        client._on_connect(mqtt_client, None, None, 0)

        self.assertEqual([True], connection_states)
        self.assertEqual("v1/gateway/connect", mqtt_client.published[0][0])
        self.assertIn("SI Smart Valve 1", mqtt_client.published[0][1])

    def test_reconnect_drops_expired_buffer_and_replays_fresh_payload(self) -> None:
        mqtt_client = _ReconnectMqttClient()
        client = GatewayClient.__new__(GatewayClient)
        client.settings = GatewaySettings(
            host="coreiot.test",
            access_token="test-token",
            buffer_ttl_seconds=10,
        )
        client._client = mqtt_client
        client._buffer = deque([
            ("v1/gateway/telemetry", "old", 1, 980_000),
            ("v1/gateway/telemetry", "fresh", 1, 995_000),
        ])
        client._lock = threading.Lock()
        client._connected = True
        client._expired_buffer_count = 0

        with patch("coreiot.gateway_client.time.time", return_value=1000):
            client._flush_buffer()

        self.assertEqual([("v1/gateway/telemetry", "fresh", 1)], mqtt_client.published)
        self.assertEqual(1, client.expired_buffer_count)

    def test_shared_attribute_watch_requests_durable_config(self) -> None:
        mqtt_client = _ReconnectMqttClient()
        client = GatewayClient.__new__(GatewayClient)
        client._client = mqtt_client
        client._connected = True
        client._shared_attribute_watches = {}
        client._attribute_request_id = 0
        client._buffer = deque()
        client._lock = threading.Lock()

        client.watch_shared_attributes("SI Smart Valve 1", ["localScheduleConfig"])

        topic, payload, qos = mqtt_client.published[-1]
        self.assertEqual("v1/gateway/attributes/request", topic)
        self.assertEqual(1, qos)
        self.assertEqual(
            {
                "id": 1,
                "device": "SI Smart Valve 1",
                "keys": ["localScheduleConfig"],
                "client": False,
            },
            json.loads(payload),
        )


class GatewayClientRpcPolicyTest(unittest.TestCase):
    def make_client(self, legacy_policy=None):
        handled = []
        replies = []
        client = GatewayClient.__new__(GatewayClient)
        client.guard = RpcGuard()
        client.attribute_handler = None
        client.legacy_manual_off_allowed = legacy_policy

        def handle(command):
            handled.append(command)
            return True, "OFF", "EXECUTED"

        def reply(command, success, state, reason):
            replies.append((command, success, state, reason))

        client.rpc_handler = handle
        client.send_rpc_reply = reply
        return client, handled, replies

    def deliver(self, client: GatewayClient, body: dict) -> None:
        message = SimpleNamespace(
            topic="v1/gateway/rpc",
            payload=json.dumps(body).encode("utf-8"),
        )
        client._on_message(None, None, message)

    def test_parameterless_turn_off_is_rejected_by_default(self) -> None:
        client, handled, replies = self.make_client()

        self.deliver(
            client,
            {
                "device": "SI Smart Valve 2",
                "data": {"id": 2, "method": "TURN_OFF", "params": {}},
            },
        )

        self.assertEqual([], handled)
        self.assertEqual(1, len(replies))
        self.assertFalse(replies[0][1])
        self.assertEqual("INVALID_COMMAND", replies[0][3])

    def test_parameterless_turn_off_uses_fallback_only_when_policy_allows(self) -> None:
        client, handled, replies = self.make_client(lambda command: True)

        self.deliver(
            client,
            {
                "device": "SI Smart Valve 2",
                "data": {"id": 3, "method": "TURN_OFF", "params": {}},
            },
        )

        self.assertEqual(1, len(handled))
        self.assertEqual("legacy-widget-off-3", handled[0].command_id)
        self.assertEqual("MANUAL", handled[0].params["source"])
        self.assertEqual(30, handled[0].params["latchSeconds"])
        self.assertTrue(replies[0][1])
        self.assertEqual("EXECUTED", replies[0][3])

    def test_clock_gate_rejects_timestamped_on_but_keeps_off_available(self) -> None:
        client, handled, replies = self.make_client()
        detailed_replies = []
        client.clock_ready = lambda: False
        client.send_rpc_reply = lambda *args: detailed_replies.append(args)

        self.deliver(
            client,
            {
                "device": "SI Smart Valve 1",
                "data": {
                    "id": 4,
                    "method": "TURN_ON",
                    "params": {
                        "commandId": "clock-on-4",
                        "source": "MANUAL",
                        "requestedAt": 1_800_000_000_000,
                        "ttlSeconds": 30,
                        "runDurationSeconds": 60,
                    },
                },
            },
        )
        self.assertEqual([], handled)
        self.assertEqual("CLOCK_NOT_READY", detailed_replies[0][4])

        self.deliver(
            client,
            {
                "device": "SI Smart Valve 1",
                "data": {
                    "id": 5,
                    "method": "TURN_OFF",
                    "params": {
                        "commandId": "clock-off-5",
                        "source": "MANUAL",
                        "requestedAt": int(time.time() * 1000),
                        "ttlSeconds": 30,
                    },
                },
            },
        )
        self.assertEqual(1, len(handled))


if __name__ == "__main__":
    unittest.main()
