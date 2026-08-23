import json
import sys
import tempfile
import queue
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
TEST_TEMP_DIR = ROOT / "archive" / "temp"
TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(GATEWAY_DIR))

from analytics_hook import AnalyticsResult, NoOpAnalyticsHook, safe_evaluate  # noqa: E402
from coreiot.gateway_protocol import RpcCommand, RpcGuard  # noqa: E402
from hardware_adapter import ActuatorAck, FakeAdapter, HardwareAdapter, PeerStatus, SensorSample  # noqa: E402
from runtime_state import FieldConfigStore, PersistentCommandLedger, clock_is_ready  # noqa: E402
from simulator_v23 import (  # noqa: E402
    LocalSchedule,
    LocalScheduleRunner,
    SimulationModelV23,
    apply_field_configuration_attribute,
)
from uart_espnow_adapter import UartEspNowAdapter, decode_frame, encode_frame  # noqa: E402


def load_model() -> SimulationModelV23:
    config = json.loads(
        (GATEWAY_DIR / "config" / "devices.v23.example.json").read_text(encoding="utf-8")
    )
    return SimulationModelV23.from_config(config)


class TimeoutAdapter(HardwareAdapter):
    def __init__(self) -> None:
        super().__init__("HIL_FIELD1_3BOARD")

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def poll(self):
        return []

    def set_zone(self, zone_id, state, command_id, lease_ms=15_000):
        del state, lease_ms
        return ActuatorAck(
            command_id=command_id,
            zone_id=zone_id,
            accepted=False,
            valve_output="OFF",
            pump_output="OFF",
            reason="ACK_TIMEOUT",
            sequence=1,
            received_at_ms=1_800_000_000_000,
        )

    def all_off(self, command_id):
        return [self.set_zone("field-1", "OFF", command_id)]

    def query_state(self):
        return {"zones": {"field-1": "OFF"}, "pump": "OFF"}


class RuntimeStateTest(unittest.TestCase):
    def test_command_ledger_survives_restart_and_stays_bounded(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as directory:
            path = Path(directory) / "commands.json"
            first = PersistentCommandLedger(path, capacity=2)
            first.record("a")
            first.record("b")
            first.record("c")
            restored = PersistentCommandLedger(path, capacity=2)
            self.assertNotIn("a", restored)
            self.assertIn("b", restored)
            self.assertIn("c", restored)

    def test_rpc_guard_uses_persistent_ledger(self) -> None:
        command = RpcCommand(
            device="SI Smart Valve 1",
            request_id=1,
            method="TURN_OFF",
            params={
                "commandId": "persistent-off",
                "source": "MANUAL",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
            },
        )
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as directory:
            path = Path(directory) / "commands.json"
            guard = RpcGuard(ledger=PersistentCommandLedger(path))
            self.assertEqual("ACCEPT", guard.evaluate(command, 1_800_000_001_000, True))
            restarted = RpcGuard(ledger=PersistentCommandLedger(path))
            self.assertEqual("DUPLICATE", restarted.evaluate(command, 1_800_000_002_000, True))

    def test_clock_gate_checks_epoch_and_optional_ntp_file(self) -> None:
        self.assertFalse(clock_is_ready(now_ms=1_000, require_ntp=False))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as directory:
            path = Path(directory) / "synchronized"
            self.assertFalse(clock_is_ready(now_ms=1_800_000_000_000, require_ntp=True, synchronized_file=path))
            path.write_text("", encoding="utf-8")
            self.assertTrue(clock_is_ready(now_ms=1_800_000_000_000, require_ntp=True, synchronized_file=path))


class AdapterContractTest(unittest.TestCase):
    def test_uart_codec_round_trip_and_crc_rejection(self) -> None:
        frame = encode_frame({
            "type": "SET_ZONE",
            "sequence": 7,
            "commandId": "cmd-7",
            "zoneId": "field-1",
            "state": "ON",
            "leaseMs": 15_000,
        })
        decoded = decode_frame(frame)
        self.assertEqual(("cmd-7", "ON"), (decoded["commandId"], decoded["state"]))
        decoded["state"] = "OFF"
        with self.assertRaisesRegex(ValueError, "CRC"):
            decode_frame(json.dumps(decoded))

    def test_fake_adapter_sequences_pump_from_zone_state(self) -> None:
        adapter = FakeAdapter(["field-1", "field-2"])
        adapter.start()
        on = adapter.set_zone("field-1", "ON", "on-1")
        off = adapter.set_zone("field-1", "OFF", "off-1")
        self.assertTrue(on.accepted)
        self.assertEqual(("ON", "ON"), (on.valve_output, on.pump_output))
        self.assertEqual(("OFF", "OFF"), (off.valve_output, off.pump_output))

    def test_uart_adapter_waits_for_crc_valid_ack(self) -> None:
        class LoopSerial:
            def __init__(self, *args, **kwargs):
                del args, kwargs
                self.lines = queue.Queue()

            def reset_input_buffer(self):
                pass

            def write(self, raw):
                command = decode_frame(raw)
                self.lines.put(encode_frame({
                    "type": "ACK",
                    "sequence": command["sequence"],
                    "commandId": command["commandId"],
                    "zoneId": command["zoneId"],
                    "accepted": True,
                    "valveOutput": command["state"],
                    "pumpOutput": "ON" if command["state"] == "ON" else "OFF",
                    "reason": "EXECUTED",
                }))

            def flush(self):
                pass

            def readline(self):
                try:
                    return self.lines.get(timeout=0.05)
                except queue.Empty:
                    return b""

            def close(self):
                pass

        adapter = UartEspNowAdapter(
            "TEST",
            ["field-1"],
            serial_factory=LoopSerial,
            ack_timeout_seconds=0.1,
        )
        adapter.start()
        try:
            ack = adapter.set_zone("field-1", "ON", "uart-on-1")
            self.assertTrue(ack.accepted)
            self.assertEqual(("ON", "ON"), (ack.valve_output, ack.pump_output))
        finally:
            adapter.stop()

    def test_hardware_model_does_not_claim_on_without_ack(self) -> None:
        model = load_model()
        model.attach_hardware_adapter(
            TimeoutAdapter(),
            expected_sensors={"field-1": 1, "field-2": 4},
            min_valid_sensors={"field-1": 1, "field-2": 3},
        )
        model.ingest_hardware_event(PeerStatus("central", "CENTRAL", True, 1_800_000_000_000))
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=1,
            received_at_ms=1_800_000_000_000,
            soil_moisture=(20.0,),
        ))
        apply_field_configuration_attribute(
            model,
            {"device": model.zones["field-1"].valve_device, "data": {"controlMode": "AUTO"}},
            1_800_000_000_000,
        )
        model.tick(timestamp=1_800_000_000_000)
        model.tick(timestamp=1_800_000_001_000)
        zone = model.zones["field-1"]
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("REJECTED", zone.last_ack)
        self.assertEqual("ACK_TIMEOUT", zone.decision_reason)


class FieldConfigurationTest(unittest.TestCase):
    def test_valid_update_is_atomic_and_persisted(self) -> None:
        model = load_model()
        device = model.zones["field-1"].valve_device
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as directory:
            store = FieldConfigStore(Path(directory) / "fields.json")
            result = apply_field_configuration_attribute(
                model,
                {"device": device, "data": {
                    "controlMode": "MANUAL",
                    "criticalMoisture": 10,
                    "minMoistureThreshold": 25,
                    "targetMoisture": 50,
                    "maxMoistureThreshold": 70,
                    "floodMoistureThreshold": 90,
                }},
                1_800_000_000_000,
                store,
            )
            self.assertEqual((True, "CONFIG_APPLIED"), result)
            self.assertEqual("MANUAL", model.zones["field-1"].control_mode)
            self.assertEqual(25, model._limits_for("field-1").min_moisture)
            self.assertEqual("MANUAL", store.load()["field-1"]["controlMode"])

    def test_invalid_threshold_order_keeps_previous_values(self) -> None:
        model = load_model()
        device = model.zones["field-1"].valve_device
        previous = model._limits_for("field-1")
        result = apply_field_configuration_attribute(
            model,
            {"device": device, "data": {"targetMoisture": 95}},
            1_800_000_000_000,
        )
        self.assertEqual((False, "INVALID_THRESHOLD_ORDER"), result)
        self.assertEqual(previous, model._limits_for("field-1"))

    def test_auto_to_manual_stops_local_auto_and_manual_to_auto_redebounces(self) -> None:
        model = load_model()
        zone = model.zones["field-1"]
        now_ms = 1_800_000_000_000
        model._set_valve("field-1", "ON", "AUTO_TEST", now_ms)
        result = apply_field_configuration_attribute(
            model,
            {"device": zone.valve_device, "data": {"controlMode": "MANUAL"}},
            now_ms,
        )
        self.assertEqual((True, "CONFIG_APPLIED"), result)
        self.assertEqual("OFF", zone.valve_state)
        model.zone_runtime["field-1"].below_min_samples = 99
        apply_field_configuration_attribute(
            model,
            {"device": zone.valve_device, "data": {"controlMode": "AUTO"}},
            now_ms + 1,
        )
        self.assertEqual(0, model.zone_runtime["field-1"].below_min_samples)


class ManualAndScheduleBehaviorTest(unittest.TestCase):
    def test_manual_run_ignores_target_but_expires_at_duration(self) -> None:
        model = load_model()
        zone = model.zones["field-1"]
        now_ms = 1_800_000_000_000
        zone.moisture = model._limits_for("field-1").target_moisture
        command = RpcCommand(
            device=zone.valve_device,
            request_id=9,
            method="TURN_ON",
            params={
                "commandId": "manual-duration-9",
                "source": "MANUAL",
                "requestedAt": now_ms,
                "ttlSeconds": 30,
                "runDurationSeconds": 60,
            },
        )
        self.assertEqual((True, "ON", "EXECUTED"), model.apply_rpc(command, now_ms))
        model.tick(timestamp=now_ms + 5_000)
        self.assertEqual("ON", zone.valve_state)
        self.assertEqual("MANUAL_DURATION_ACTIVE", zone.decision_reason)
        model.tick(timestamp=now_ms + 61_000)
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("MANUAL_ON_TTL_EXPIRED", zone.decision_reason)

    def test_recurring_schedule_remains_anchored_when_dispatch_is_late(self) -> None:
        model = load_model()
        started = 1_800_000_000_000
        runner = LocalScheduleRunner(
            [LocalSchedule("anchored", "field-1", 10, 5, 60)],
            started,
        )
        result = runner.dispatch_due(model, started + 15_000)
        self.assertEqual([("anchored", True, "EXECUTED")], result)
        self.assertEqual(started + 70_000, runner.next_fire_ms["anchored"])

    def test_schedule_due_while_slot_busy_is_rejected_not_delayed(self) -> None:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.scheduler-demo.json").read_text(encoding="utf-8")
        )
        config["localSchedules"][1]["startAfterSeconds"] = 30
        model = SimulationModelV23.from_config(config)
        started = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, started)
        results = runner.dispatch_due(model, started + 30_000)
        self.assertEqual(("field-1-demo", True, "EXECUTED"), results[0])
        self.assertEqual(("field-2-demo", False, "SAFETY_BLOCK"), results[1])
        state = model.zone_runtime["field-2"].local_schedule
        self.assertEqual("REJECTED", state["status"])
        self.assertEqual("MAX_CONCURRENT_ZONES", state["lastResultReason"])
        self.assertEqual([], runner.dispatch_due(model, started + 90_000))


class AnalyticsIsolationTest(unittest.TestCase):
    def test_noop_and_exception_are_advisory_only(self) -> None:
        self.assertEqual("ANALYTICS_DISABLED", safe_evaluate(NoOpAnalyticsHook(), {}).reason)

        class Broken:
            def evaluate(self, snapshot):
                raise RuntimeError("model failure")

        self.assertEqual(AnalyticsResult(reason="ANALYTICS_ERROR"), safe_evaluate(Broken(), {}))


if __name__ == "__main__":
    unittest.main()
