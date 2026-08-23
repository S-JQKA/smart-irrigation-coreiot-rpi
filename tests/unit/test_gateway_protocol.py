import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "implementation" / "gateway"))

from coreiot.gateway_protocol import (  # noqa: E402
    RpcGuard,
    encode_attribute_request,
    encode_connect,
    encode_rpc_reply,
    encode_telemetry,
    normalize_legacy_manual_off,
    parse_rpc,
)


class GatewayProtocolTest(unittest.TestCase):
    def test_shared_attribute_request_uses_gateway_api_contract(self) -> None:
        self.assertEqual(
            {
                "id": 3,
                "device": "SI Smart Valve 1",
                "keys": ["localScheduleConfig"],
                "client": False,
            },
            json.loads(
                encode_attribute_request(
                    3,
                    "SI Smart Valve 1",
                    ["localScheduleConfig"],
                )
            ),
        )

    def test_connect_uses_existing_profile(self) -> None:
        self.assertEqual(
            json.loads(encode_connect("SI Soil Moisture 1", "SI Soil Moisture Sensor v2.2")),
            {"device": "SI Soil Moisture 1", "type": "SI Soil Moisture Sensor v2.2"},
        )

    def test_timestamped_telemetry_requires_values(self) -> None:
        with self.assertRaises(ValueError):
            encode_telemetry({"SI Soil Moisture 1": [{"ts": 1, "moisture": 40}]})

    def test_rpc_whitelist_and_reply(self) -> None:
        command = parse_rpc(
            json.dumps(
                {
                    "device": "SI Smart Valve 1",
                    "data": {
                        "id": 7,
                        "method": "TURN_ON",
                        "params": {"commandId": "cmd-7", "requestedAt": 1000, "ttlSeconds": 300},
                    },
                }
            )
        )
        reply = json.loads(encode_rpc_reply(command, True, "ON", "EXECUTED"))
        self.assertEqual(reply["data"]["commandId"], "cmd-7")
        self.assertTrue(reply["data"]["success"])

    def test_rpc_rejects_unknown_method(self) -> None:
        with self.assertRaises(ValueError):
            parse_rpc('{"device":"SI Smart Valve 1","data":{"id":1,"method":"SET_PWM","params":{}}}')

    def test_schedule_configuration_rpc_is_allowed(self) -> None:
        command = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":8,"method":"SET_LOCAL_SCHEDULE",'
            '"params":{"commandId":"cfg-8","source":"MANUAL","requestedAt":1000,'
            '"ttlSeconds":30,"scheduleId":"field-1-ui","enabled":true,'
            '"startAfterSeconds":60,"durationSeconds":30,"repeatEverySeconds":0}}}'
        )
        self.assertEqual("SET_LOCAL_SCHEDULE", command.method)
        self.assertEqual("ACCEPT", RpcGuard().evaluate(command, now_ms=2000, require_metadata=True))

    def test_rpc_without_command_id_can_be_rejected_with_correlated_reply(self) -> None:
        command = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":1,"method":"TURN_ON","params":{}}}'
        )
        self.assertFalse(command.has_explicit_command_id)
        reply = json.loads(encode_rpc_reply(command, False, "OFF", "INVALID_COMMAND"))
        self.assertEqual(1, reply["id"])
        self.assertEqual("INVALID_COMMAND", reply["data"]["reason"])

    def test_guard_rejects_expired_and_duplicate(self) -> None:
        expired = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":1,"method":"TURN_ON","params":{"commandId":"old","requestedAt":1000,"ttlSeconds":1}}}'
        )
        self.assertEqual(RpcGuard().evaluate(expired, now_ms=3000), "EXPIRED")

        current = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":2,"method":"TURN_OFF","params":{"commandId":"same","requestedAt":1000,"ttlSeconds":10}}}'
        )
        guard = RpcGuard()
        self.assertEqual(guard.evaluate(current, now_ms=2000), "ACCEPT")
        self.assertEqual(guard.evaluate(current, now_ms=2000), "DUPLICATE")

    def test_strict_guard_requires_source_timestamp_and_bounded_ttl(self) -> None:
        valid = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":3,"method":"TURN_OFF","params":'
            '{"commandId":"manual-3","source":"MANUAL","requestedAt":1000,"ttlSeconds":30}}}'
        )
        self.assertEqual("ACCEPT", RpcGuard().evaluate(valid, now_ms=2000, require_metadata=True))

        missing_source = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":4,"method":"TURN_OFF","params":'
            '{"commandId":"manual-4","requestedAt":1000,"ttlSeconds":30}}}'
        )
        self.assertEqual(
            "INVALID_COMMAND",
            RpcGuard().evaluate(missing_source, now_ms=2000, require_metadata=True),
        )

        invalid_ttl = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":5,"method":"TURN_ON","params":'
            '{"commandId":"manual-5","source":"MANUAL","requestedAt":1000,"ttlSeconds":0}}}'
        )
        self.assertEqual(
            "INVALID_COMMAND",
            RpcGuard().evaluate(invalid_ttl, now_ms=2000, require_metadata=True),
        )

    def test_scheduler_requires_a_bounded_turn_on_duration(self) -> None:
        valid = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":6,"method":"TURN_ON","params":'
            '{"commandId":"schedule-6","source":"SCHEDULER","requestedAt":1000,'
            '"ttlSeconds":30,"runDurationSeconds":60}}}'
        )
        self.assertEqual("ACCEPT", RpcGuard().evaluate(valid, now_ms=2000, require_metadata=True))

        missing_duration = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":7,"method":"TURN_ON","params":'
            '{"commandId":"schedule-7","source":"SCHEDULER","requestedAt":1000,'
            '"ttlSeconds":30}}}'
        )
        self.assertEqual(
            "INVALID_COMMAND",
            RpcGuard().evaluate(missing_duration, now_ms=2000, require_metadata=True),
        )

    def test_manual_turn_on_requires_duration_and_accepts_deprecated_alias(self) -> None:
        missing = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":9,"method":"TURN_ON","params":'
            '{"commandId":"manual-9","source":"MANUAL","requestedAt":1000,"ttlSeconds":30}}}'
        )
        alias = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":10,"method":"TURN_ON","params":'
            '{"commandId":"manual-10","source":"MANUAL","requestedAt":1000,'
            '"ttlSeconds":30,"manualTtlSeconds":60}}}'
        )
        self.assertEqual(
            "INVALID_COMMAND",
            RpcGuard().evaluate(missing, now_ms=2000, require_metadata=True),
        )
        self.assertEqual(
            "ACCEPT",
            RpcGuard().evaluate(alias, now_ms=2000, require_metadata=True),
        )

    def test_parameterless_turn_off_gets_safe_legacy_widget_metadata(self) -> None:
        command = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":304,"method":"TURN_OFF","params":{}}}'
        )
        normalized = normalize_legacy_manual_off(command, now_ms=1_800_000_000_000)
        self.assertEqual("legacy-widget-off-304", normalized.command_id)
        self.assertEqual("MANUAL", normalized.params["source"])
        self.assertEqual(30, normalized.params["ttlSeconds"])
        self.assertEqual(30, normalized.params["latchSeconds"])
        self.assertEqual(
            "ACCEPT",
            RpcGuard().evaluate(normalized, now_ms=1_800_000_000_001, require_metadata=True),
        )

    def test_parameterless_turn_on_never_uses_legacy_widget_fallback(self) -> None:
        command = parse_rpc(
            '{"device":"SI Smart Valve 1","data":{"id":305,"method":"TURN_ON","params":{}}}'
        )
        normalized = normalize_legacy_manual_off(command, now_ms=1_800_000_000_000)
        self.assertFalse(normalized.has_explicit_command_id)


if __name__ == "__main__":
    unittest.main()
