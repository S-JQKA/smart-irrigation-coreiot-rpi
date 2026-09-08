"""Hardware input and control regression tests; no local_dev dependency."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "implementation/gateway"))
from configuration import load_config
from irrigation_controller import (
    IrrigationController,
    LocalScheduleRunner,
    apply_field_configuration_attribute,
)
from hardware_adapter import (
    ActuatorAck,
    FlowSample,
    HardwareAdapter,
    PeerStatus,
    SensorSample,
)
from uart_espnow_adapter import UartEspNowAdapter, encode_frame, decode_frame
from gateway_runtime import _validate_runtime_mapping
from coreiot.gateway_protocol import RpcCommand

NOW = 1_800_000_000_000
CONFIG = ROOT / "implementation/gateway/config/hardware.example.json"


class RecordingAdapter(HardwareAdapter):
    def __init__(self, reject=False):
        super().__init__("HARDWARE_TWO_FIELD")
        self.states = {"field-1": "OFF", "field-2": "OFF"}
        self.calls = []
        self.reject = reject

    def start(self):
        pass

    def stop(self):
        pass

    def poll(self):
        return []

    def query_state(self):
        return {
            "zones": self.states.copy(),
            "pump": "ON" if "ON" in self.states.values() else "OFF",
        }

    def set_zone(self, zone_id, state, command_id, lease_ms=15000):
        self.calls.append((zone_id, state))
        if not self.reject:
            self.states[zone_id] = state
        return ActuatorAck(
            command_id,
            zone_id,
            not self.reject,
            self.states[zone_id],
            self.query_state()["pump"],
            "ACK_TIMEOUT" if self.reject else "EXECUTED",
            len(self.calls),
            NOW,
        )

    def all_off(self, command_id):
        return [self.set_zone(z, "OFF", command_id) for z in self.states]


def model_ready(concurrency=1, reject=False):
    config = load_config(CONFIG)
    config["control"]["maxConcurrentZones"] = concurrency
    model = IrrigationController.from_config(config)
    adapter = RecordingAdapter(reject)
    model.attach_hardware_adapter(
        adapter,
        expected_sensors={"field-1": 4, "field-2": 4},
        min_valid_sensors={"field-1": 3, "field-2": 3},
    )
    model.startup_safe_confirmed = True
    model.ingest_hardware_event(PeerStatus("central", "CENTRAL", True, NOW, False))
    for zone in model.zones:
        model.zone_runtime[zone].config_validated = True
        model.zones[zone].control_mode = "AUTO"
        model.ingest_hardware_event(FlowSample(zone, 1, NOW, 0, 2.0, "boot1"))
        sensor(model, zone)
    return model, adapter


def sensor(
    model,
    zone="field-1",
    soil=(20, 21, 20, 21),
    sequence=1,
    timestamp=NOW,
    environment=True,
):
    model.ingest_hardware_event(
        SensorSample(
            "sensor-" + zone,
            zone,
            sequence,
            timestamp,
            soil,
            29 if environment else None,
            60 if environment else None,
            18000 if environment else None,
        )
    )


class HardwareControllerTests(unittest.TestCase):
    def test_no_fabricated_observations_before_samples(self):
        model = IrrigationController.from_config(load_config(CONFIG))
        points = model.tick(NOW)
        self.assertNotIn(
            "tankLevelPct", points[model.site.manifold_device][0]["values"]
        )
        self.assertNotIn(
            "tankLowSwitch", points[model.site.manifold_device][0]["values"]
        )
        for zone in model.zones.values():
            self.assertNotIn("moisture", points[zone.soil_devices[0]][0]["values"])
            self.assertNotIn("airTemp", points[zone.env_device][0]["values"])
            self.assertNotIn("flowRate", points[zone.water_meter_device][0]["values"])
            self.assertEqual("OFF", zone.valve_state)

    def test_no_adapter_never_acknowledges_on(self):
        model = IrrigationController.from_config(load_config(CONFIG))
        self.assertFalse(model._set_valve("field-1", "ON", "TEST", NOW))
        self.assertEqual("OFF", model.zones["field-1"].valve_state)

    def test_full_configuration_required_before_first_enable(self):
        model = IrrigationController.from_config(load_config(CONFIG))
        result = apply_field_configuration_attribute(
            model,
            {
                "device": model.zones["field-1"].valve_device,
                "data": {"controlMode": "AUTO"},
            },
            NOW,
        )
        self.assertFalse(result[0])

    def test_three_agreeing_probes_exclude_outlier(self):
        model, _ = model_ready()
        sensor(model, soil=(20, 21, 20, 90))
        self.assertAlmostEqual(61 / 3, model.zones["field-1"].moisture)
        self.assertEqual(
            (True, True, True, False),
            model.zone_runtime["field-1"].soil_sample_consensus,
        )

    def test_failed_probe_does_not_shift_identity(self):
        model, _ = model_ready()
        sensor(model, soil=(None, 20, 21, 22))
        points = model.tick(NOW)
        zone = model.zones["field-1"]
        self.assertNotIn("moisture", points[zone.soil_devices[0]][0]["values"])
        self.assertEqual("INVALID", points[zone.soil_devices[0]][0]["values"]["dataQuality"])
        self.assertEqual("UNKNOWN", points[zone.soil_devices[0]][0]["values"]["sensorConsensus"])
        self.assertEqual(20, points[zone.soil_devices[1]][0]["values"]["moisture"])

    def test_insufficient_consensus_blocks_auto(self):
        model, _ = model_ready()
        sensor(model, soil=(10, 10, 90, 90))
        model.tick(NOW)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual(0, model.zone_runtime["field-1"].below_min_samples)

    def test_missing_environment_blocks_auto(self):
        model, _ = model_ready()
        sensor(model, environment=False)
        model.tick(NOW)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertNotIn(
            "airTemp", model.tick(NOW)[model.zones["field-1"].env_device][0]["values"]
        )

    def test_auto_uses_fresh_samples_and_respects_one_slot(self):
        model, adapter = model_ready()
        model.tick(NOW)
        model.tick(NOW + 1000)
        self.assertFalse(adapter.calls)
        for zone in model.zones:
            sensor(model, zone, sequence=2, timestamp=NOW + 2000)
        model.tick(NOW + 2000)
        self.assertEqual(1, sum(z.valve_state == "ON" for z in model.zones.values()))

    def test_two_slots_and_tank_low_stop_both(self):
        model, _ = model_ready(concurrency=2)
        model.tick(NOW)
        for zone in model.zones:
            sensor(model, zone, sequence=2, timestamp=NOW + 2000)
        model.tick(NOW + 2000)
        self.assertEqual(2, sum(z.valve_state == "ON" for z in model.zones.values()))
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, NOW + 3000, True)
        )
        model.tick(NOW + 3000)
        self.assertTrue(all(z.valve_state == "OFF" for z in model.zones.values()))
        self.assertEqual("OFF", model.site.pump_state)

    def test_ack_timeout_cannot_create_on_state(self):
        model, _ = model_ready(reject=True)
        self.assertFalse(model._set_valve("field-1", "ON", "TEST", NOW))
        self.assertEqual("OFF", model.zones["field-1"].valve_state)

    def test_flow_staleness_stops_irrigation_even_with_fresh_soil(self):
        model, _ = model_ready()
        model._set_valve("field-1", "ON", "TEST", NOW)
        sensor(model, sequence=10, timestamp=NOW + 31000)
        model.ingest_hardware_event(
            PeerStatus("central", "CENTRAL", True, NOW + 31000, False)
        )
        model.tick(NOW + 31000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)

    def test_flow_counter_reboot_and_duplicate_do_not_inflate_water(self):
        model, _ = model_ready()
        model._set_valve("field-1", "ON", "TEST", NOW)
        event = FlowSample("field-1", 2, NOW + 2000, 450, 2.0, "boot1")
        model.ingest_hardware_event(event)
        model.ingest_hardware_event(event)
        model.ingest_hardware_event(
            FlowSample("field-1", 1, NOW + 4000, 0, 2.0, "boot2")
        )
        model.ingest_hardware_event(
            FlowSample("field-1", 2, NOW + 6000, 225, 2.0, "boot2")
        )
        self.assertEqual(1.5, model.zones["field-1"].water_used)

    def test_flow_calibration_is_per_branch(self):
        model, _ = model_ready()
        model.pulses_per_liter_by_zone["field-2"] = 900
        for zone in model.zones:
            model.ingest_hardware_event(
                FlowSample(zone, 2, NOW + 2000, 450, 2.0, "boot1")
            )
        self.assertEqual(1, model.zone_runtime["field-1"].total_water_liters)
        self.assertEqual(0.5, model.zone_runtime["field-2"].total_water_liters)

    def test_invalid_calibration_rejected(self):
        config = load_config(CONFIG)
        config["control"]["pulsesPerLiterByZone"]["field-1"] = float("nan")
        with self.assertRaises(ValueError):
            IrrigationController.from_config(config)

    def test_water_meter_mapping_owned_by_central(self):
        config = load_config(CONFIG)
        _, _, _, owners = _validate_runtime_mapping(
            config, config["runtime"], "HARDWARE_TWO_FIELD"
        )
        self.assertIn("SI Water Meter 1", owners["central"])
        self.assertNotIn("SI Water Meter 1", owners["sensor-field-1"])


class PhysicalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.adapter = UartEspNowAdapter(
            "UNOPENED",
            ["field-1", "field-2"],
            sensor_node_zones={
                "sensor-field-1": "field-1",
                "sensor-field-2": "field-2",
            },
        )
        self.sample = {
            "version": 1,
            "type": "SENSOR",
            "origin": "P",
            "boot": "abcdef12",
            "nodeId": "sensor-field-1",
            "zoneId": "field-1",
            "sequence": 1,
            "soil": [2000, None, 2200, 2100],
            "tC": 2900,
            "rh": 6000,
            "lightLux": 18000,
        }

    def test_physical_sensor_wire_frame_and_missing_probe(self):
        self.adapter._handle_frame(decode_frame(encode_frame(self.sample)))
        sample = self.adapter.poll()[0]
        self.assertEqual((20, None, 22, 21), sample.soil_moisture)
        self.assertEqual(29, sample.air_temp)

    def test_worst_case_sensor_fits_250_bytes(self):
        self.sample.update(
            sequence=4294967295, soil=[10000] * 4, tC=-1000, rh=10000, lightLux=54612
        )
        self.assertLessEqual(len(encode_frame(self.sample).strip()), 250)

    def test_hil_frame_rejected_by_product(self):
        self.sample.pop("origin")
        with self.assertRaises(ValueError):
            self.adapter._handle_frame(self.sample)

    def test_flow_cannot_be_injected_by_sensor(self):
        self.sample["pulseCounter"] = 1
        with self.assertRaises(ValueError):
            self.adapter._handle_frame(self.sample)

    def test_reboot_accepts_new_sequence(self):
        self.sample["sequence"] = 99
        self.adapter._handle_frame(self.sample)
        self.sample.update(sequence=1, boot="newboot")
        self.adapter._handle_frame(self.sample)
        self.assertEqual(2, len(self.adapter.poll()))

    def test_duplicate_sequence_dropped(self):
        self.adapter._handle_frame(self.sample)
        self.adapter._handle_frame(self.sample)
        self.assertEqual(1, len(self.adapter.poll()))

    def test_corrupt_frame_rejected(self):
        wire = json.loads(encode_frame(self.sample))
        wire["rh"] = 9000
        with self.assertRaises(ValueError):
            decode_frame(json.dumps(wire))

    def test_flow_requires_central_identity(self):
        flow = {
            "version": 1,
            "type": "FLOW",
            "origin": "P",
            "boot": "1",
            "zoneId": "field-1",
            "sequence": 1,
            "pulseCounter": 0,
            "flowMilliLpm": 0,
            "nodeId": "sensor-field-1",
        }
        with self.assertRaises(ValueError):
            self.adapter._handle_frame(flow)
        flow["nodeId"] = "central"
        self.adapter._handle_frame(decode_frame(encode_frame(flow)))
        self.assertIsInstance(self.adapter.poll()[0], FlowSample)


if __name__ == "__main__":
    unittest.main()
