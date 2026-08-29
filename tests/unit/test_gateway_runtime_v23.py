import json
import sys
import tempfile
import queue
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
TEST_TEMP_DIR = ROOT / "archive" / "temp"
TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(GATEWAY_DIR))

from analytics_hook import (  # noqa: E402
    AdvisoryAnalyticsRunner,
    AnalyticsResult,
    NoOpAnalyticsHook,
    safe_evaluate,
)
from coreiot.gateway_protocol import RpcCommand, RpcGuard  # noqa: E402
from hardware_adapter import ActuatorAck, FakeAdapter, HardwareAdapter, PeerStatus, SensorSample  # noqa: E402
from gateway_runtime import (  # noqa: E402
    _enforce_hardware_zone_modes,
    _load_schedule_runner,
    _validate_runtime_mapping,
)
from runtime_state import FieldConfigStore, PersistentCommandLedger, clock_is_ready  # noqa: E402
from simulator_v23 import (  # noqa: E402
    LocalSchedule,
    LocalScheduleRunner,
    SimulationModelV23,
    apply_field_configuration_attribute,
    effective_field_config,
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
        self.calls = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def poll(self):
        return []

    def set_zone(self, zone_id, state, command_id, lease_ms=15_000):
        del lease_ms
        self.calls.append((zone_id, state, command_id))
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
    def test_uart_adapter_stop_uses_a_fresh_all_off_command_id(self) -> None:
        class CloseOnlySerial:
            def close(self):
                pass

        command_ids = []

        class RecordingAdapter(UartEspNowAdapter):
            def all_off(self, command_id):
                command_ids.append(command_id)
                return []

        for _ in range(2):
            adapter = RecordingAdapter(
                "TEST",
                ["field-1", "field-2"],
                serial_factory=lambda *args, **kwargs: None,
            )
            adapter._running = True
            adapter._serial = CloseOnlySerial()
            adapter.stop()

        self.assertEqual(2, len(command_ids))
        self.assertEqual(2, len(set(command_ids)))
        self.assertTrue(all(item.startswith("gateway-adapter-stop-") for item in command_ids))

    def test_hil_profile_maps_only_field1_and_cannot_restore_field2_auto(self) -> None:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.hil-field1.json").read_text(encoding="utf-8")
        )
        self.assertEqual("HIL_FIELD1_3BOARD", config["runtimeProfile"])
        self.assertEqual(["field-1"], config["runtime"]["hardwareZones"])
        self.assertNotIn("SI Smart Valve 2", config["runtime"]["mappedDevices"])
        _validate_runtime_mapping(config, config["runtime"], config["runtimeProfile"])
        model = SimulationModelV23.from_config(config)
        model.zones["field-2"].control_mode = "AUTO"
        _enforce_hardware_zone_modes(model, tuple(config["runtime"]["hardwareZones"]))
        self.assertEqual("DISABLED", model.zones["field-2"].control_mode)

    def test_final_profile_requires_complete_single_owner_mapping(self) -> None:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.hardware-two-field.example.json")
            .read_text(encoding="utf-8")
        )
        mapped, zones, sensor_nodes, owners = _validate_runtime_mapping(
            config, config["runtime"], config["runtimeProfile"]
        )
        self.assertEqual(16, len(mapped))
        self.assertEqual({"field-1", "field-2"}, set(zones))
        self.assertEqual(2, len(sensor_nodes))
        self.assertEqual(set(mapped), {device for devices in owners.values() for device in devices})

        config["runtime"]["peerDeviceMap"]["central"].append("SI Soil Moisture 1")
        with self.assertRaisesRegex(ValueError, "multiple physical owners"):
            _validate_runtime_mapping(config, config["runtime"], config["runtimeProfile"])

    def test_four_board_hil_maps_both_fields_and_allows_concurrency_one_or_two(self) -> None:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.hil-two-field-4board.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual("HIL_TWO_FIELD_4BOARD", config["runtimeProfile"])
        self.assertEqual(2, config["simulation"]["maxConcurrentZones"])
        self.assertEqual(2, SimulationModelV23.from_config(config).site.max_concurrent_zones)
        mapped, zones, sensor_nodes, owners = _validate_runtime_mapping(
            config, config["runtime"], config["runtimeProfile"]
        )
        self.assertEqual(16, len(mapped))
        self.assertEqual({"field-1", "field-2"}, set(zones))
        self.assertEqual(
            {"sensor-field-1": "field-1", "sensor-field-2": "field-2"},
            sensor_nodes,
        )
        self.assertEqual(set(mapped), {device for devices in owners.values() for device in devices})
        adapter = UartEspNowAdapter(
            "TEST",
            zones,
            runtime_profile=config["runtimeProfile"],
            sensor_node_zones=sensor_nodes,
            serial_factory=lambda *args, **kwargs: None,
        )
        self.assertEqual(("SYNTHETIC", "LED", "HIL"), (
            adapter.sensor_data_origin,
            adapter.actuator_backend,
            adapter.evidence_class,
        ))

        config["simulation"]["maxConcurrentZones"] = 1
        _validate_runtime_mapping(config, config["runtime"], config["runtimeProfile"])
        config["simulation"]["maxConcurrentZones"] = 3
        with self.assertRaisesRegex(ValueError, "must be 1 or 2"):
            _validate_runtime_mapping(config, config["runtime"], config["runtimeProfile"])

    def test_one_field_hil_rejects_concurrency_two(self) -> None:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.hil-field1.json").read_text(encoding="utf-8")
        )
        config["simulation"]["maxConcurrentZones"] = 2
        with self.assertRaisesRegex(ValueError, "exceeds mapped hardware Fields"):
            _validate_runtime_mapping(config, config["runtime"], config["runtimeProfile"])

    def test_final_profile_starts_unverified(self) -> None:
        adapter = UartEspNowAdapter(
            "TEST",
            ["field-1", "field-2"],
            runtime_profile="HARDWARE_TWO_FIELD",
            serial_factory=lambda *args, **kwargs: None,
        )
        self.assertEqual("HARDWARE-UNVERIFIED", adapter.evidence_class)

    def test_sensor_node_mapping_and_reboot_sequence_reset(self) -> None:
        adapter = UartEspNowAdapter(
            "TEST",
            ["field-1"],
            serial_factory=lambda *args, **kwargs: None,
            sensor_node_zones={"sensor-field-1": "field-1"},
        )

        def frame(node_id, sequence):
            return decode_frame(encode_frame({
                "type": "SENSOR",
                "sequence": sequence,
                "nodeId": node_id,
                "zoneId": "field-1",
                "soilCentiPct": [2500],
            }))

        with self.assertRaisesRegex(ValueError, "not mapped"):
            adapter._handle_frame(frame("wrong-node", 1))
        adapter._handle_frame(frame("sensor-field-1", 10))
        self.assertEqual(10, adapter.poll()[0].sequence)
        adapter._handle_frame(frame("sensor-field-1", 1))
        self.assertEqual([], adapter.poll())
        adapter._last_sensor_seen_ms["sensor-field-1"] = 0
        adapter._handle_frame(frame("sensor-field-1", 1))
        self.assertEqual(1, adapter.poll()[0].sequence)

    def test_four_soil_sensor_frame_fits_espnow_v1_limit(self) -> None:
        frame = encode_frame({
            "type": "SENSOR",
            "sequence": 99_999,
            "nodeId": "sensor-field-2",
            "zoneId": "field-2",
            "soilMoisture": [22.1, 22.3, 22.5, 22.7],
            "airTemp": 29,
            "airHumidity": 60,
            "lightLux": 18_000,
            "flowRateLpm": 1,
            "pulseCounter": 499_995,
        })
        self.assertLessEqual(len(frame.rstrip(b"\n")), 250)
        adapter = UartEspNowAdapter(
            "TEST",
            ["field-2"],
            serial_factory=lambda *args, **kwargs: None,
            sensor_node_zones={"sensor-field-2": "field-2"},
        )
        adapter._handle_frame(decode_frame(frame))
        sample = adapter.poll()[0]
        self.assertEqual((22.1, 22.3, 22.5, 22.7), sample.soil_moisture)
        self.assertEqual((29.0, 60.0, 1.0), (sample.air_temp, sample.air_humidity, sample.flow_rate_lpm))

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

    def test_frozen_uart_protocol_vectors_have_valid_crc(self) -> None:
        vectors = json.loads(
            (ROOT / "implementation" / "firmware" / "smartfarm_hil_v1" / "protocol_v1_vectors.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["SET_ZONE", "ACK", "SENSOR", "PEER"],
            [decode_frame(json.dumps(item))["type"] for item in vectors],
        )
        adapter = UartEspNowAdapter("TEST", ["field-1"], serial_factory=lambda *args, **kwargs: None)
        adapter._handle_frame(vectors[2])
        sample = adapter.poll()[0]
        self.assertEqual((25.1,), sample.soil_moisture)
        self.assertEqual(29.0, sample.air_temp)
        self.assertEqual(1.0, sample.flow_rate_lpm)
        adapter._handle_frame(vectors[3])
        peer = adapter.poll()[0]
        self.assertFalse(peer.tank_low)

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
        model.startup_safe_confirmed = True
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, 1_800_000_000_000, tank_low=False)
        )
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=1,
            received_at_ms=1_800_000_000_000,
            soil_moisture=(20.0,),
            flow_rate_lpm=0.0,
            tank_low=False,
        ))
        apply_field_configuration_attribute(
            model,
            {
                "device": model.zones["field-1"].valve_device,
                "data": {
                    **effective_field_config(model, "field-1"),
                    "controlMode": "AUTO",
                },
            },
            1_800_000_000_000,
        )
        model.tick(timestamp=1_800_000_000_000)
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=2,
            received_at_ms=1_800_000_001_000,
            soil_moisture=(20.0,),
            flow_rate_lpm=0.0,
            tank_low=False,
        ))
        model.tick(timestamp=1_800_000_001_000)
        zone = model.zones["field-1"]
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("REJECTED", zone.last_ack)
        self.assertEqual("ACK_TIMEOUT", zone.decision_reason)

    def test_hardware_off_is_reasserted_even_when_last_known_state_is_off(self) -> None:
        model = load_model()
        adapter = TimeoutAdapter()
        model.attach_hardware_adapter(
            adapter,
            expected_sensors={"field-1": 1},
            min_valid_sensors={"field-1": 1},
        )
        stopped = model._set_valve(
            "field-1",
            "OFF",
            "MANUAL_OFF",
            1_800_000_000_000,
            "force-off-1",
            force_hardware=True,
        )
        self.assertFalse(stopped)
        self.assertEqual(("field-1", "OFF", "force-off-1"), adapter.calls[-1])
        self.assertTrue(model.zone_runtime["field-1"].off_reassert_pending)

    def test_hardware_boot_stays_safe_until_output_and_full_config_are_confirmed(self) -> None:
        model = load_model()
        model.attach_hardware_adapter(
            TimeoutAdapter(),
            expected_sensors={"field-1": 1},
            min_valid_sensors={"field-1": 1},
        )
        model.required_config_zones = {"field-1"}
        now_ms = 1_800_000_000_000
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, now_ms, tank_low=False)
        )
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=1,
            received_at_ms=now_ms,
            soil_moisture=(40.0,),
            flow_rate_lpm=0.0,
        ))
        model.tick(timestamp=now_ms, local_decision=False)
        self.assertEqual("SAFE-IDLE", model.site.system_mode)
        self.assertEqual("STARTUP_NOT_CONFIRMED", model.site.safety_block_reason)

        model.startup_safe_confirmed = True
        model.tick(timestamp=now_ms + 1_000, local_decision=False)
        self.assertEqual("CONFIG_NOT_READY", model.site.safety_block_reason)
        self.assertEqual(
            (False, "INCOMPLETE_INITIAL_CONFIG"),
            apply_field_configuration_attribute(
                model,
                {
                    "device": model.zones["field-1"].valve_device,
                    "data": {"controlMode": "MANUAL"},
                },
                now_ms + 1_000,
            ),
        )
        full_config = effective_field_config(model, "field-1")
        full_config["controlMode"] = "MANUAL"
        self.assertEqual(
            (True, "CONFIG_APPLIED"),
            apply_field_configuration_attribute(
                model,
                {"device": model.zones["field-1"].valve_device, "data": full_config},
                now_ms + 2_000,
            ),
        )
        model.tick(timestamp=now_ms + 2_000, local_decision=False)
        self.assertEqual("NORMAL", model.site.system_mode)

    def test_hardware_debounce_counts_new_samples_not_runtime_ticks(self) -> None:
        model = load_model()
        model.attach_hardware_adapter(
            TimeoutAdapter(),
            expected_sensors={"field-1": 1},
            min_valid_sensors={"field-1": 1},
        )
        model.startup_safe_confirmed = True
        model.zones["field-1"].control_mode = "AUTO"
        now_ms = 1_800_000_000_000
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, now_ms, tank_low=False)
        )
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=1,
            received_at_ms=now_ms,
            soil_moisture=(20.0,),
            flow_rate_lpm=0.0,
            tank_low=False,
        ))
        model.tick(timestamp=now_ms, local_decision=False)
        self.assertEqual(1, model.zone_runtime["field-1"].below_min_samples)
        model.tick(timestamp=now_ms + 1_000, local_decision=False)
        self.assertEqual(1, model.zone_runtime["field-1"].below_min_samples)
        model.ingest_hardware_event(SensorSample(
            node_id="sensor-field-1",
            zone_id="field-1",
            sequence=2,
            received_at_ms=now_ms + 2_000,
            soil_moisture=(20.0,),
            flow_rate_lpm=0.0,
            tank_low=False,
        ))
        model.tick(timestamp=now_ms + 2_000, local_decision=False)
        self.assertEqual(2, model.zone_runtime["field-1"].below_min_samples)

    def test_auto_requires_fresh_debounce_after_tank_fault_clears(self) -> None:
        model = load_model()
        model.attach_hardware_adapter(
            TimeoutAdapter(),
            expected_sensors={"field-1": 1},
            min_valid_sensors={"field-1": 1},
        )
        model.startup_safe_confirmed = True
        model.zones["field-1"].control_mode = "AUTO"
        now_ms = 1_800_000_000_000
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, now_ms, tank_low=False)
        )

        def ingest(sequence, received_at_ms):
            model.ingest_hardware_event(SensorSample(
                node_id="sensor-field-1",
                zone_id="field-1",
                sequence=sequence,
                received_at_ms=received_at_ms,
                soil_moisture=(20.0,),
                flow_rate_lpm=0.0,
            ))

        ingest(1, now_ms)
        model.tick(timestamp=now_ms, local_decision=False)
        ingest(2, now_ms + 1_000)
        model.tick(timestamp=now_ms + 1_000, local_decision=False)
        self.assertEqual(2, model.zone_runtime["field-1"].below_min_samples)

        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, now_ms + 2_000, tank_low=True)
        )
        ingest(3, now_ms + 2_000)
        model.tick(timestamp=now_ms + 2_000, local_decision=False)
        self.assertEqual(0, model.zone_runtime["field-1"].below_min_samples)

        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, now_ms + 3_000, tank_low=False)
        )
        model.tick(timestamp=now_ms + 3_000, local_decision=False)
        self.assertEqual(0, model.zone_runtime["field-1"].below_min_samples)
        ingest(4, now_ms + 4_000)
        model.tick(timestamp=now_ms + 4_000, local_decision=False)
        self.assertEqual(1, model.zone_runtime["field-1"].below_min_samples)


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
    def test_stale_persisted_one_shot_is_reported_not_dispatched(self) -> None:
        model = load_model()
        now_ms = 1_800_000_000_000
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as directory:
            path = Path(directory) / "schedules.json"
            path.write_text(json.dumps({
                "schemaVersion": 1,
                "localSchedules": [{
                    "id": "stale-after-reboot",
                    "zoneId": "field-1",
                    "configId": "stale-config-1",
                    "enabled": True,
                    "startAtMs": now_ms - 60_000,
                    "durationSeconds": 60,
                    "repeatEverySeconds": 0,
                }],
            }), encoding="utf-8")
            runner = _load_schedule_runner({}, path, model, now_ms)
        state = model.zone_runtime["field-1"].local_schedule
        self.assertEqual("STALE_ONE_SHOT_IGNORED", state["lastResultReason"])
        self.assertEqual([], runner.dispatch_due(model, now_ms + 1))

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

    def test_slow_analytics_reports_timeout_without_blocking_control_caller(self) -> None:
        class Slow:
            def evaluate(self, snapshot):
                del snapshot
                time.sleep(0.05)
                return AnalyticsResult(reason="LATE_RESULT")

        runner = AdvisoryAnalyticsRunner(Slow(), timeout_seconds=0.005)
        try:
            runner.submit({"sample": 1})
            time.sleep(0.015)
            started = time.monotonic()
            result = runner.status()
            self.assertLess(time.monotonic() - started, 0.01)
            self.assertEqual("ANALYTICS_TIMEOUT", result.reason)
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
