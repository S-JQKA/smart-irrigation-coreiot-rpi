import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from control_engine_v23 import FieldControlInput, IrrigationThresholds, evaluate_field  # noqa: E402
from coreiot.gateway_protocol import RpcCommand  # noqa: E402
from simulator_v23 import (  # noqa: E402
    LocalScheduleRunner,
    SimulationModelV23,
    apply_fault_injection,
    apply_local_schedule_attribute,
    apply_rpc_with_authority,
    effective_rpc_authority,
    fault_injections_from_config,
    legacy_manual_off_allowed,
)


class MultivariableDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = IrrigationThresholds(start_debounce_samples=2)

    def inputs(self, **overrides):
        values = {
            "zone_id": "field-1",
            "avg_moisture": 24.0,
            "air_temp": 31.0,
            "air_humidity": 55.0,
            "vpd": 1.8,
            "light_lux": 24000.0,
            "actual_valve_state": "OFF",
            "below_min_samples": 2,
        }
        values.update(overrides)
        return FieldControlInput(**values)

    def test_environment_changes_priority_but_moisture_remains_primary_gate(self) -> None:
        stressed = evaluate_field(self.inputs(), self.limits)
        mild = evaluate_field(
            self.inputs(air_temp=24, air_humidity=80, vpd=0.6, light_lux=8000),
            self.limits,
        )
        wet = evaluate_field(self.inputs(avg_moisture=40), self.limits)
        self.assertEqual("REQUEST_START", stressed.action)
        self.assertGreater(stressed.priority, mild.priority)
        self.assertEqual(
            ("HIGH_TEMPERATURE", "HIGH_VPD", "HIGH_LIGHT"),
            stressed.stress_factors,
        )
        self.assertEqual("KEEP_OFF", wet.action)

    def test_critical_moisture_bypasses_start_debounce_but_not_hard_safety(self) -> None:
        critical = evaluate_field(self.inputs(avg_moisture=10, below_min_samples=0), self.limits)
        tank_low = evaluate_field(self.inputs(avg_moisture=10, below_min_samples=0, tank_low=True), self.limits)
        self.assertEqual(("REQUEST_START", 100), (critical.action, critical.priority))
        self.assertEqual(("BLOCK", "TANK_LOW"), (tank_low.action, tank_low.reason))

    def test_active_irrigation_stops_at_target(self) -> None:
        decision = evaluate_field(self.inputs(avg_moisture=55, actual_valve_state="ON"), self.limits)
        self.assertEqual(("STOP", "TARGET_MOISTURE_REACHED"), (decision.action, decision.reason))


class GatewayVerticalSliceV23Test(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads((GATEWAY_DIR / "config" / "devices.v23.example.json").read_text(encoding="utf-8"))
        config["simulation"]["startMoisture"]["field-2"] = 48
        self.model = SimulationModelV23.from_config(config)

    def test_control_authority_can_be_local_while_transport_remains_gateway(self) -> None:
        config = json.loads((GATEWAY_DIR / "config" / "devices.v23.example.json").read_text(encoding="utf-8"))
        self.assertEqual("GATEWAY", config["transportMode"])
        self.assertEqual("LOCAL", config["controlAuthority"])

    def test_fault_plan_injects_tank_and_flow_at_gateway(self) -> None:
        config = {
            "simulation": {
                "faultInjections": [
                    {"atIteration": 2, "action": "SET_TANK_LEVEL_PCT", "value": 5},
                    {"atIteration": 3, "action": "SET_FLOW_FAULT", "zoneId": "field-1"},
                ]
            }
        }
        plan = fault_injections_from_config(config, self.model)

        apply_fault_injection(plan[2][0], self.model, None, 2)
        apply_fault_injection(plan[3][0], self.model, None, 3)

        self.assertEqual(5.0, self.model.site.tank_level_pct)
        self.assertTrue(self.model.zone_runtime["field-1"].flow_fault)

    def test_fault_plan_rejects_unknown_zone(self) -> None:
        config = {
            "simulation": {
                "faultInjections": [
                    {"atIteration": 2, "action": "SET_FLOW_FAULT", "zoneId": "field-3"}
                ]
            }
        }
        with self.assertRaisesRegex(ValueError, "unknown zoneId=field-3"):
            fault_injections_from_config(config, self.model)

    def test_vertical_slice_starts_then_stops_with_one_command_per_transition(self) -> None:
        zone = self.model.zones["field-1"]
        first = self.model.tick(timestamp=1_800_000_000_000, local_decision=True)
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("WAITING_START_DEBOUNCE", zone.decision_reason)

        second = self.model.tick(timestamp=1_800_000_005_000, local_decision=True)
        self.assertEqual("ON", zone.valve_state)
        self.assertEqual("ON", self.model.site.pump_state)
        start_command = zone.last_command_id
        self.assertTrue(start_command.startswith("local-field-1-"))
        valve_values = second[zone.valve_device][0]["values"]
        self.assertTrue(valve_values["environmentStress"])
        self.assertIn("HIGH_VPD", valve_values["stressFactors"])

        for tick in range(3, 30):
            self.model.tick(timestamp=1_800_000_000_000 + tick * 5_000, local_decision=True)
            if zone.valve_state == "OFF" and zone.decision_reason == "TARGET_MOISTURE_REACHED":
                break

        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("OFF", self.model.site.pump_state)
        self.assertNotEqual(start_command, zone.last_command_id)
        self.assertEqual(2, self.model.zone_runtime["field-1"].transition_sequence)

    def test_pulses_are_converted_to_liters(self) -> None:
        self.model.tick(timestamp=1_800_000_000_000, local_decision=True)
        payload = self.model.tick(timestamp=1_800_000_005_000, local_decision=True)
        zone = self.model.zones["field-1"]
        meter = payload[zone.water_meter_device][0]["values"]
        self.assertAlmostEqual(
            meter["pulseCounter"] / meter["pulsesPerLiter"],
            meter["waterConsumptionLiters"],
            places=3,
        )

    def test_coreiot_request_mode_does_not_auto_start(self) -> None:
        self.model.tick(timestamp=1_800_000_000_000, local_decision=False)
        self.model.tick(timestamp=1_800_000_005_000, local_decision=False)
        self.assertEqual("OFF", self.model.zones["field-1"].valve_state)
        self.assertEqual("OFF", self.model.site.pump_state)


class GatewayLocalScheduleTest(unittest.TestCase):
    def load_demo(self):
        return json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.scheduler-demo.json").read_text(
                encoding="utf-8"
            )
        )

    def test_local_schedule_starts_once_and_emits_task_history(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        started_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, started_ms)

        self.assertEqual([], runner.dispatch_due(model, started_ms + 29_000))
        self.assertEqual("OFF", model.zones["field-1"].valve_state)

        results = runner.dispatch_due(model, started_ms + 30_000)
        self.assertEqual([("field-1-demo", True, "EXECUTED")], results)
        self.assertEqual("ON", model.zones["field-1"].valve_state)
        self.assertEqual("ON", model.site.pump_state)
        task = model.zone_runtime["field-1"].irrigation_task
        self.assertEqual("RUNNING", task["status"])
        self.assertEqual(60_000, task["durationThreshold"])
        self.assertEqual("field-1-demo", task["scheduleId"])

        payload = model.tick(timestamp=started_ms + 35_000, local_decision=True)
        valve_points = payload[model.zones["field-1"].valve_device]
        self.assertEqual("RUNNING", valve_points[1]["values"]["irrigationTask"]["status"])
        self.assertEqual("RUNNING", valve_points[0]["values"]["gatewayScheduleStatus"])
        self.assertEqual("GATEWAY_LOCAL", valve_points[0]["values"]["gatewayScheduleSource"])
        self.assertEqual([], runner.dispatch_due(model, started_ms + 90_000))

        model.tick(timestamp=started_ms + 91_000, local_decision=True)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("COMPLETED", task["status"])
        self.assertEqual("SCHEDULE_DURATION_REACHED", task["resultReason"])
        self.assertEqual("COMPLETED", model.zone_runtime["field-1"].local_schedule["status"])

    def test_second_field_runs_after_first_schedule_is_complete(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        started_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, started_ms)

        runner.dispatch_due(model, started_ms + 30_000)
        model.tick(timestamp=started_ms + 91_000, local_decision=True)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)

        results = runner.dispatch_due(model, started_ms + 120_000)
        self.assertEqual([("field-2-demo", True, "EXECUTED")], results)
        self.assertEqual("ON", model.zones["field-2"].valve_state)
        self.assertEqual("ON", model.site.pump_state)
        self.assertEqual("RUNNING", model.zone_runtime["field-2"].local_schedule["status"])

    def test_local_schedule_rejects_unknown_zone(self) -> None:
        config = self.load_demo()
        config["localSchedules"][0]["zoneId"] = "field-99"
        model = SimulationModelV23.from_config(config)
        with self.assertRaisesRegex(ValueError, "unknown zoneId=field-99"):
            LocalScheduleRunner.from_config(config, model, 1_800_000_000_000)

    def test_operator_can_update_and_disable_local_schedule(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        valve = model.zones["field-1"].valve_device
        update = RpcCommand(
            device=valve,
            request_id=81,
            method="SET_LOCAL_SCHEDULE",
            params={
                "commandId": "schedule-config-81",
                "source": "MANUAL",
                "requestedAt": now_ms,
                "ttlSeconds": 30,
                "scheduleId": "field-1-ui",
                "enabled": True,
                "startAfterSeconds": 10,
                "durationSeconds": 30,
                "repeatEverySeconds": 0,
            },
        )
        self.assertEqual((True, "OFF", "EXECUTED"), runner.update_from_rpc(model, update, now_ms))
        state = model.zone_runtime["field-1"].local_schedule
        self.assertEqual("ACCEPTED", state["configAck"])
        self.assertEqual(now_ms + 10_000, state["nextRunTs"])
        self.assertEqual([("field-1-ui", True, "EXECUTED")], runner.dispatch_due(model, now_ms + 10_000))

        disable = RpcCommand(
            device=valve,
            request_id=82,
            method="SET_LOCAL_SCHEDULE",
            params={
                "commandId": "schedule-config-82",
                "source": "MANUAL",
                "requestedAt": now_ms,
                "ttlSeconds": 30,
                "scheduleId": "field-1-ui",
                "enabled": False,
            },
        )
        self.assertEqual((True, "ON", "EXECUTED"), runner.update_from_rpc(model, disable, now_ms))
        self.assertEqual("CANCELLED", model.zone_runtime["field-1"].local_schedule["status"])

    def test_operator_schedule_rejects_invalid_duration(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        command = RpcCommand(
            device=model.zones["field-1"].valve_device,
            request_id=83,
            method="SET_LOCAL_SCHEDULE",
            params={
                "commandId": "schedule-config-83",
                "source": "MANUAL",
                "requestedAt": now_ms,
                "ttlSeconds": 30,
                "scheduleId": "field-1-ui",
                "enabled": True,
                "startAfterSeconds": 10,
                "durationSeconds": 99999,
                "repeatEverySeconds": 0,
            },
        )
        self.assertEqual((False, "OFF", "INVALID_COMMAND"), runner.update_from_rpc(model, command, now_ms))

    def test_shared_attribute_applies_and_deduplicates_operator_schedule(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        body = {
            "device": model.zones["field-1"].valve_device,
            "data": {
                "localScheduleConfig": {
                    "schemaVersion": 1,
                    "configId": "dashboard-field-1-1",
                    "scheduleId": "field-1-ui",
                    "enabled": True,
                    "startAtMs": now_ms + 10_000,
                    "durationSeconds": 30,
                    "repeatEverySeconds": 0,
                }
            },
        }

        self.assertEqual(
            (True, "OFF", "EXECUTED"),
            apply_local_schedule_attribute(runner, model, body, now_ms),
        )
        self.assertEqual(
            (True, "OFF", "DUPLICATE"),
            apply_local_schedule_attribute(runner, model, body, now_ms + 1_000),
        )
        self.assertEqual(
            "dashboard-field-1-1",
            model.zone_runtime["field-1"].local_schedule["configCommandId"],
        )

    def test_shared_attribute_request_response_accepts_json_value(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        value = {
            "schemaVersion": 1,
            "configId": "dashboard-field-2-1",
            "scheduleId": "field-2-ui",
            "enabled": True,
            "startAtMs": now_ms + 20_000,
            "durationSeconds": 30,
            "repeatEverySeconds": 0,
        }
        body = {
            "id": 7,
            "device": model.zones["field-2"].valve_device,
            "value": json.dumps(value),
        }

        self.assertEqual(
            (True, "OFF", "EXECUTED"),
            apply_local_schedule_attribute(runner, model, body, now_ms),
        )

    def test_shared_attribute_without_config_is_ignored(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        runner = LocalScheduleRunner.from_config(config, model, 1_800_000_000_000)
        device = model.zones["field-2"].valve_device

        self.assertIsNone(
            apply_local_schedule_attribute(runner, model, {"device": device}, 1_800_000_000_000)
        )
        self.assertIsNone(
            apply_local_schedule_attribute(
                runner,
                model,
                {"device": device, "value": None},
                1_800_000_000_000,
            )
        )

    def test_stale_one_shot_attribute_is_completed_without_firing(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        device = model.zones["field-1"].valve_device
        body = {
            "device": device,
            "value": {
                "schemaVersion": 1,
                "configId": "stale-field-1-1",
                "scheduleId": "field-1-stale",
                "enabled": True,
                "startAtMs": now_ms - 60_000,
                "durationSeconds": 30,
                "repeatEverySeconds": 0,
            },
        }

        self.assertEqual(
            (True, "OFF", "STALE_ONE_SHOT_IGNORED"),
            apply_local_schedule_attribute(runner, model, body, now_ms),
        )
        state = model.zone_runtime["field-1"].local_schedule
        self.assertEqual("COMPLETED", state["status"])
        self.assertEqual("ACCEPTED", state["configAck"])
        self.assertEqual([], runner.dispatch_due(model, now_ms + 1_000))

    def test_stale_recurring_attribute_rolls_forward(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        body = {
            "device": model.zones["field-1"].valve_device,
            "value": {
                "schemaVersion": 1,
                "configId": "recurring-field-1-1",
                "scheduleId": "field-1-recurring",
                "enabled": True,
                "startAtMs": now_ms - 65_000,
                "durationSeconds": 30,
                "repeatEverySeconds": 60,
            },
        }

        self.assertEqual(
            (True, "OFF", "EXECUTED"),
            apply_local_schedule_attribute(runner, model, body, now_ms),
        )
        state = model.zone_runtime["field-1"].local_schedule
        self.assertEqual(now_ms + 55_000, state["nextRunTs"])
        self.assertEqual("SCHEDULE_ROLLED_FORWARD", state["configReason"])

    def test_persisted_config_id_restores_duplicate_detection(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        now_ms = 1_800_000_000_000
        runner = LocalScheduleRunner.from_config(config, model, now_ms)
        device = model.zones["field-1"].valve_device
        value = {
            "schemaVersion": 1,
            "configId": "restart-field-1-1",
            "scheduleId": "field-1-restart",
            "enabled": True,
            "startAtMs": now_ms + 10_000,
            "durationSeconds": 30,
            "repeatEverySeconds": 0,
        }
        body = {"device": device, "value": value}
        self.assertEqual(
            (True, "OFF", "EXECUTED"),
            apply_local_schedule_attribute(runner, model, body, now_ms),
        )

        runtime_path = Path("runtime-schedules.json")
        with patch("simulator_v23.AtomicJsonFile.save") as save:
            runner.persist(runtime_path)
        persisted = save.call_args.args[0]
        saved = next(
            item for item in persisted["localSchedules"] if item["id"] == "field-1-restart"
        )
        self.assertEqual("restart-field-1-1", saved["configId"])

        restart_config = self.load_demo()
        restart_config["localSchedules"] = [{
            "id": saved["id"],
            "zoneId": saved["zoneId"],
            "configId": saved["configId"],
            "enabled": True,
            "startAfterSeconds": 10,
            "durationSeconds": saved["durationSeconds"],
            "repeatEverySeconds": saved["repeatEverySeconds"] or None,
        }]
        restart_model = SimulationModelV23.from_config(restart_config)
        restart_runner = LocalScheduleRunner.from_config(restart_config, restart_model, now_ms)
        self.assertEqual(
            (True, "OFF", "DUPLICATE"),
            apply_local_schedule_attribute(restart_runner, restart_model, body, now_ms + 1_000),
        )

    def test_valve_payload_carries_field_water_totals_for_relation_sync(self) -> None:
        config = self.load_demo()
        model = SimulationModelV23.from_config(config)
        zone = model.zones["field-1"]
        zone.valve_state = "ON"
        model.site.pump_state = "ON"
        payload = model.tick(timestamp=1_800_000_000_000)
        valve = payload[zone.valve_device][0]["values"]
        meter = payload[zone.water_meter_device][0]["values"]
        self.assertEqual(meter["waterConsumptionLiters"], valve["waterConsumptionLiters"])


class GatewayField1PilotTest(unittest.TestCase):
    def load_pilot(self):
        return json.loads((GATEWAY_DIR / "config" / "devices.v23.field1-pilot.json").read_text(encoding="utf-8"))

    def test_pilot_config_mixes_profiles_and_authority_by_zone(self) -> None:
        config = self.load_pilot()
        self.assertEqual("MIXED", config["controlAuthority"])
        self.assertEqual(
            {"field-1": "LOCAL", "field-2": "COREIOT_REQUEST"},
            config["controlAuthorityByZone"],
        )
        by_name = {item["name"]: item["profile"] for item in config["devices"]}
        self.assertEqual("SI Soil Moisture Sensor", by_name["SI Soil Moisture 1"])
        self.assertEqual("SI Soil Moisture Sensor", by_name["SI Soil Moisture 5"])
        self.assertEqual("SF Pump Controller", by_name["SF Main Pump 1"])

    def test_only_field1_runs_local_decision(self) -> None:
        config = self.load_pilot()
        config["simulation"]["startMoisture"] = {"field-1": 20, "field-2": 20}
        model = SimulationModelV23.from_config(config)
        local_zones = {"field-1"}
        model.tick(timestamp=1_800_000_000_000, local_control_zones=local_zones)
        model.tick(timestamp=1_800_000_005_000, local_control_zones=local_zones)
        self.assertEqual("ON", model.zones["field-1"].valve_state)
        self.assertEqual("OFF", model.zones["field-2"].valve_state)
        self.assertEqual("AWAITING_COREIOT_REQUEST", model.zones["field-2"].decision_reason)

    def test_coreiot_controlled_field2_preserves_rpc_state_and_occupies_scheduler_slot(self) -> None:
        config = self.load_pilot()
        config["simulation"]["startMoisture"] = {"field-1": 20, "field-2": 48}
        model = SimulationModelV23.from_config(config)
        field2 = model.zones["field-2"]
        rpc = RpcCommand(
            device=field2.valve_device,
            request_id=23,
            method="TURN_ON",
            params={"commandId": "pilot-field2-on", "requestedAt": 1_800_000_000_000, "ttlSeconds": 300},
        )
        self.assertEqual((True, "ON", "EXECUTED"), model.apply_rpc(rpc))
        field2.moisture = model.limits.target_moisture

        model.tick(timestamp=1_800_000_000_000, local_control_zones={"field-1"})
        model.tick(timestamp=1_800_000_005_000, local_control_zones={"field-1"})

        self.assertEqual("ON", field2.valve_state, "Field 2 target must be handled by its CoreIoT request path")
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("WAITING_FOR_SCHEDULER_SLOT", model.zones["field-1"].decision_reason)
        self.assertEqual("ON", model.site.pump_state)

    def test_manual_off_latch_prevents_local_auto_restart(self) -> None:
        config = self.load_pilot()
        config["simulation"]["startMoisture"] = {"field-1": 20, "field-2": 48}
        model = SimulationModelV23.from_config(config)
        now_ms = int(time.time() * 1000)
        model.tick(timestamp=now_ms, local_control_zones={"field-1"})
        model.tick(timestamp=now_ms + 5_000, local_control_zones={"field-1"})
        field1 = model.zones["field-1"]
        self.assertEqual("ON", field1.valve_state)

        manual_off = RpcCommand(
            device=field1.valve_device,
            request_id=30,
            method="TURN_OFF",
            params={
                "commandId": "manual-field1-off",
                "source": "MANUAL",
                "requestedAt": now_ms + 6_000,
                "ttlSeconds": 30,
                "latchSeconds": 30,
            },
        )
        self.assertEqual((True, "OFF", "EXECUTED"), model.apply_rpc(manual_off))
        self.assertEqual("manual-field1-off", field1.last_command_id)

        model.tick(timestamp=now_ms + 10_000, local_control_zones={"field-1"})
        self.assertEqual("OFF", field1.valve_state)
        self.assertEqual("MANUAL_OFF_LATCHED", field1.decision_reason)

        model.zone_runtime["field-1"].manual_off_until_ms = now_ms + 11_000
        model.tick(timestamp=now_ms + 12_000, local_control_zones={"field-1"})
        self.assertEqual("OFF", field1.valve_state)
        model.tick(timestamp=now_ms + 13_000, local_control_zones={"field-1"})
        self.assertEqual("ON", field1.valve_state)

    def test_manual_on_is_rejected_by_tank_low_hard_interlock(self) -> None:
        model = SimulationModelV23.from_config(self.load_pilot())
        field1 = model.zones["field-1"]
        model.site.tank_low_switch = True
        command = RpcCommand(
            device=field1.valve_device,
            request_id=31,
            method="TURN_ON",
            params={
                "commandId": "manual-field1-on-tank-low",
                "source": "MANUAL",
                "requestedAt": int(time.time() * 1000),
                "ttlSeconds": 30,
                "runDurationSeconds": 60,
            },
        )
        self.assertEqual((False, "OFF", "SAFETY_BLOCK"), model.apply_rpc(command))
        self.assertEqual("TANK_LOW", field1.safety_block_reason)
        self.assertEqual("OFF", model.site.pump_state)


class GatewayRpcAuthorityTest(unittest.TestCase):
    def load_model(self) -> SimulationModelV23:
        config = json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.example.json").read_text(encoding="utf-8")
        )
        return SimulationModelV23.from_config(config)

    def test_mixed_authority_is_resolved_per_valve_and_shared_pump_stays_local(self) -> None:
        model = self.load_model()
        zone_authorities = {"field-1": "LOCAL", "field-2": "COREIOT_REQUEST"}

        self.assertEqual(
            "LOCAL",
            effective_rpc_authority(
                model,
                model.zones["field-1"].valve_device,
                "MIXED",
                zone_authorities,
            ),
        )
        self.assertEqual(
            "COREIOT_REQUEST",
            effective_rpc_authority(
                model,
                model.zones["field-2"].valve_device,
                "MIXED",
                zone_authorities,
            ),
        )
        self.assertEqual(
            "LOCAL",
            effective_rpc_authority(
                model,
                model.site.pump_device,
                "MIXED",
                zone_authorities,
            ),
        )

    def test_legacy_off_fallback_is_disabled_for_local_zone_only(self) -> None:
        model = self.load_model()
        zone_authorities = {"field-1": "LOCAL", "field-2": "COREIOT_REQUEST"}
        field1_off = RpcCommand(
            device=model.zones["field-1"].valve_device,
            request_id=60,
            method="TURN_OFF",
            params={},
        )
        field2_off = RpcCommand(
            device=model.zones["field-2"].valve_device,
            request_id=61,
            method="TURN_OFF",
            params={},
        )

        self.assertFalse(legacy_manual_off_allowed(model, field1_off, "MIXED", zone_authorities))
        self.assertTrue(legacy_manual_off_allowed(model, field2_off, "MIXED", zone_authorities))
        self.assertFalse(legacy_manual_off_allowed(model, field2_off, "LOCAL", {}))

    def test_local_authority_rejects_cloud_auto_without_changing_valve(self) -> None:
        model = self.load_model()
        zone = model.zones["field-2"]
        zone.valve_state = "ON"
        model.site.pump_state = "ON"
        command = RpcCommand(
            device=zone.valve_device,
            request_id=62,
            method="TURN_OFF",
            params={
                "commandId": "legacy-auto-off",
                "source": "AUTO",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
            },
        )

        result = apply_rpc_with_authority(model, command, "LOCAL", {})

        self.assertEqual((False, "ON", "SAFETY_BLOCK"), result)
        self.assertEqual("ON", zone.valve_state)
        self.assertEqual("ON", model.site.pump_state)

    def test_local_authority_accepts_explicit_manual_off(self) -> None:
        model = self.load_model()
        zone = model.zones["field-2"]
        zone.valve_state = "ON"
        model.site.pump_state = "ON"
        command = RpcCommand(
            device=zone.valve_device,
            request_id=63,
            method="TURN_OFF",
            params={
                "commandId": "manual-off",
                "source": "MANUAL",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
            },
        )

        result = apply_rpc_with_authority(model, command, "LOCAL", {})

        self.assertEqual((True, "OFF", "EXECUTED"), result)
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("OFF", model.site.pump_state)

    def test_local_authority_accepts_bounded_scheduler_request(self) -> None:
        model = self.load_model()
        zone = model.zones["field-2"]
        zone.moisture = 40
        command = RpcCommand(
            device=zone.valve_device,
            request_id=64,
            method="TURN_ON",
            params={
                "commandId": "scheduler-field2-64",
                "source": "SCHEDULER",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
                "runDurationSeconds": 60,
            },
        )

        with patch("simulator_v23.utc_ms", return_value=1_800_000_000_000):
            result = apply_rpc_with_authority(model, command, "LOCAL", {})

        self.assertEqual((True, "ON", "EXECUTED"), result)
        self.assertEqual("SCHEDULER", model.zone_runtime["field-2"].active_request_source)


class GatewayTwoFieldV23Test(unittest.TestCase):
    def load_config(self) -> dict:
        return json.loads(
            (GATEWAY_DIR / "config" / "devices.v23.example.json").read_text(encoding="utf-8")
        )

    def load_model(self) -> SimulationModelV23:
        return SimulationModelV23.from_config(self.load_config())

    def test_two_fields_obey_priority_single_slot_and_handoff(self) -> None:
        model = self.load_model()
        self.assertEqual({"field-1", "field-2"}, set(model.zones))
        self.assertEqual(1, model.site.max_concurrent_zones)
        model.zones["field-1"].moisture = 20
        model.zones["field-2"].moisture = 10

        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual("ON", model.zones["field-2"].valve_state, "critical zone must start first")
        model.tick(timestamp=1_800_000_005_000)
        self.assertEqual("WAITING_FOR_SCHEDULER_SLOT", model.zones["field-1"].decision_reason)

        # Releasing one slot must allow the waiting dry zone to start.
        model.zones["field-2"].moisture = model._limits_for("field-2").target_moisture
        model.tick(timestamp=1_800_000_010_000)
        self.assertEqual("OFF", model.zones["field-2"].valve_state)
        self.assertEqual("ON", model.zones["field-1"].valve_state)
        self.assertLessEqual(sum(z.valve_state == "ON" for z in model.zones.values()), 1)

    def test_two_field_payload_contains_existing_16_downstream_devices(self) -> None:
        readings = self.load_model().tick(timestamp=1_800_000_000_000)
        self.assertEqual(16, len(readings))

    def test_each_field_can_override_thresholds(self) -> None:
        model = self.load_model()
        self.assertEqual(55, model._limits_for("field-1").target_moisture)
        self.assertEqual(58, model._limits_for("field-2").target_moisture)

    def test_configurable_concurrency_can_run_both_existing_fields(self) -> None:
        model = self.load_model()
        model.site.max_concurrent_zones = 2
        model.zones["field-1"].moisture = 10
        model.zones["field-2"].moisture = 10
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual({"ON"}, {zone.valve_state for zone in model.zones.values()})
        self.assertEqual("ON", model.site.pump_state)

    def test_concurrency_config_rejects_values_outside_one_or_two(self) -> None:
        config = self.load_config()
        config["simulation"]["maxConcurrentZones"] = 3
        with self.assertRaisesRegex(ValueError, "must be 1 or 2"):
            SimulationModelV23.from_config(config)

    def test_max_duration_forces_valve_and_pump_off(self) -> None:
        model = self.load_model()
        model.limits = IrrigationThresholds(max_duration_seconds=5, start_debounce_samples=1)
        model.zone_limits = {}
        model.zones["field-1"].moisture = 10
        model.zones["field-2"].moisture = 50
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual("ON", model.zones["field-1"].valve_state)
        model.tick(timestamp=1_800_000_005_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("MAX_DURATION_REACHED", model.zones["field-1"].decision_reason)
        self.assertEqual("OFF", model.site.pump_state)

    def test_waterlogging_debounce_locks_zone(self) -> None:
        model = self.load_model()
        model.zone_limits = {}
        for zone in model.zones.values():
            zone.moisture = 50
        flooded = model.zones["field-1"]
        flooded.moisture = model.limits.flood_moisture + 1
        flooded.valve_state = "ON"
        model.site.pump_state = "ON"

        for index in range(model.limits.flood_debounce_samples):
            model.tick(timestamp=1_800_000_000_000 + index * 5_000)

        self.assertEqual("OFF", flooded.valve_state)
        self.assertEqual("WATERLOGGING_RISK", flooded.decision_reason)
        self.assertEqual("BLOCKED", flooded.safety_state)
        self.assertEqual("OFF", model.site.pump_state)

    def test_cloud_loss_enters_degraded_but_local_irrigation_continues(self) -> None:
        model = self.load_model()
        model.set_cloud_connected(False, now_ms=1_800_000_000_000)
        model.tick(timestamp=1_800_000_059_000)
        self.assertEqual("NORMAL", model.site.system_mode)
        model.tick(timestamp=1_800_000_060_000)
        self.assertEqual("DEGRADED", model.site.system_mode)
        self.assertTrue(any(zone.valve_state == "ON" for zone in model.zones.values()))
        health = model.gateway_health(16, 4)["values"]
        self.assertFalse(health["cloudConnected"])
        self.assertEqual("DEGRADED", health["operationMode"])

        model.set_cloud_connected(True, now_ms=1_800_000_065_000)
        self.assertEqual("NORMAL", model.site.system_mode)
        self.assertEqual(1, model.cloud_reconnect_count)

    def test_cycle_daily_and_total_water_are_independent(self) -> None:
        model = self.load_model()
        zone = model.zones["field-1"]
        zone.moisture = 10
        payload = model.tick(timestamp=1_800_000_000_000)
        meter = payload[zone.water_meter_device][0]["values"]
        self.assertGreater(meter["cycleWaterLiters"], 0)
        self.assertEqual(meter["cycleWaterLiters"], meter["dailyWaterLiters"])
        total_before = meter["waterConsumptionLiters"]
        daily_before = meter["dailyWaterLiters"]

        model._set_valve("field-1", "OFF", "TEST_STOP", 1_800_000_001_000)
        model._set_valve("field-1", "ON", "TEST_RESTART", 1_800_000_002_000)
        self.assertEqual(0, zone.water_used)
        self.assertAlmostEqual(daily_before, model.zone_runtime["field-1"].daily_water_liters, places=3)
        self.assertAlmostEqual(total_before, zone.pulse_counter / model.pulses_per_liter, places=3)

    def test_daily_water_resets_on_next_epoch_day(self) -> None:
        model = self.load_model()
        runtime = model.zone_runtime["field-1"]
        runtime.daily_epoch_day = 20_000
        runtime.daily_water_liters = 12.5
        model.zones["field-1"].moisture = 50
        model.tick(timestamp=20_001 * 86_400_000)
        self.assertEqual(0, runtime.daily_water_liters)
        self.assertEqual(20_001, runtime.daily_epoch_day)

    def test_cycle_quota_stops_only_affected_field(self) -> None:
        model = self.load_model()
        model.zone_limits = {}
        model.limits = IrrigationThresholds(
            max_water_per_cycle_liters=0.5,
            start_debounce_samples=1,
        )
        model.zones["field-1"].moisture = 10
        model.zones["field-2"].moisture = 50
        model.tick(timestamp=1_800_000_000_000)
        self.assertGreater(model.zones["field-1"].water_used, 0.5)
        model.tick(timestamp=1_800_000_005_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("CYCLE_QUOTA_REACHED", model.zones["field-1"].decision_reason)

    def test_daily_quota_blocks_new_cycle(self) -> None:
        model = self.load_model()
        model.zone_limits = {}
        model.limits = IrrigationThresholds(
            max_water_per_day_liters=5,
            start_debounce_samples=1,
        )
        model.zone_runtime["field-1"].daily_water_liters = 5
        model.zones["field-1"].moisture = 10
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("DAILY_QUOTA_REACHED", model.zones["field-1"].decision_reason)

    def test_zero_flow_stops_zone_and_pump_after_grace_period(self) -> None:
        model = self.load_model()
        model.zone_limits = {}
        model.zones["field-1"].moisture = 10
        model.zones["field-2"].moisture = 50
        model.zone_runtime["field-1"].flow_fault = True
        for index in range(4):
            model.tick(timestamp=1_800_000_000_000 + index * 5_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("ZONE_FLOW_LOW", model.zones["field-1"].decision_reason)
        self.assertEqual("PUMP_DRY_RUN", model.site.safety_block_reason)
        self.assertEqual("OFF", model.site.pump_state)
        model.tick(timestamp=1_800_000_020_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state, "flow fault must remain latched")
        model.clear_flow_fault("field-1")
        model.tick(timestamp=1_800_000_025_000)
        self.assertEqual("ON", model.zones["field-1"].valve_state)

    def test_flow_fault_isolated_to_one_field_when_other_field_has_flow(self) -> None:
        model = self.load_model()
        model.zone_limits = {}
        model.site.max_concurrent_zones = 2
        for zone in model.zones.values():
            zone.moisture = 10
        model.zone_runtime["field-1"].flow_fault = True
        for index in range(4):
            model.tick(timestamp=1_800_000_000_000 + index * 5_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("ZONE_FLOW_LOW", model.zones["field-1"].decision_reason)
        self.assertEqual("ON", model.zones["field-2"].valve_state)
        self.assertEqual("ON", model.site.pump_state)
        self.assertEqual("NONE", model.site.safety_block_reason)

    def test_stale_sensor_data_blocks_new_irrigation(self) -> None:
        model = self.load_model()
        runtime = model.zone_runtime["field-1"]
        runtime.last_sensor_update_ms = 1_800_000_000_000
        runtime.sensor_fault = True
        model.zones["field-1"].moisture = 10
        model.tick(timestamp=1_800_000_031_000)
        self.assertEqual("OFF", model.zones["field-1"].valve_state)
        self.assertEqual("FIELD_DATA_INVALID", model.zones["field-1"].decision_reason)

    def test_manual_on_ttl_expires_and_returns_control_to_auto(self) -> None:
        model = self.load_model()
        zone = model.zones["field-1"]
        zone.moisture = 40
        zone.valve_state = "ON"
        model.site.pump_state = "ON"
        model.zone_runtime["field-1"].manual_on_until_ms = 1_800_000_005_000
        model.tick(timestamp=1_800_000_006_000)
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("MANUAL_ON_TTL_EXPIRED", zone.decision_reason)

    def test_manual_rpc_ttl_is_bounded_and_applied_only_to_manual_source(self) -> None:
        model = self.load_model()
        zone = model.zones["field-1"]
        zone.moisture = 40
        command = RpcCommand(
            device=zone.valve_device,
            request_id=44,
            method="TURN_ON",
            params={
                "commandId": "manual-field1-ttl",
                "source": "MANUAL",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
                "manualTtlSeconds": 9999,
            },
        )
        with patch("simulator_v23.utc_ms", return_value=1_800_000_000_000):
            self.assertEqual((True, "ON", "EXECUTED"), model.apply_rpc(command))
        self.assertEqual(
            1_800_000_000_000 + model.manual_on_max_seconds * 1000,
            model.zone_runtime["field-1"].manual_on_until_ms,
        )

    def test_scheduler_duration_stops_and_emits_one_task_history_point(self) -> None:
        model = self.load_model()
        zone = model.zones["field-1"]
        zone.moisture = 40
        command = RpcCommand(
            device=zone.valve_device,
            request_id=45,
            method="TURN_ON",
            params={
                "commandId": "scheduler-field1-duration",
                "source": "SCHEDULER",
                "requestedAt": 1_800_000_000_000,
                "ttlSeconds": 30,
                "runDurationSeconds": 10,
            },
        )
        with patch("simulator_v23.utc_ms", return_value=1_800_000_000_000):
            self.assertEqual((True, "ON", "EXECUTED"), model.apply_rpc(command))

        running = model.tick(timestamp=1_800_000_005_000)
        running_task = next(
            sample["values"]["irrigationTask"]
            for sample in running[zone.valve_device]
            if "irrigationTask" in sample["values"]
        )
        self.assertEqual("RUNNING", running_task["status"])
        self.assertGreater(running_task["consumption"], 0)

        completed = model.tick(timestamp=1_800_000_011_000)
        completed_task = next(
            sample["values"]["irrigationTask"]
            for sample in completed[zone.valve_device]
            if "irrigationTask" in sample["values"]
        )
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("SCHEDULE_DURATION_REACHED", zone.decision_reason)
        self.assertEqual("COMPLETED", completed_task["status"])
        self.assertEqual(10_000, completed_task["durationThreshold"])
        self.assertGreaterEqual(completed_task["duration"], completed_task["durationThreshold"])

    def test_control_mode_disabled_and_manual_do_not_auto_start(self) -> None:
        model = self.load_model()
        model.zones["field-1"].control_mode = "DISABLED"
        model.zones["field-2"].control_mode = "MANUAL"
        for zone in model.zones.values():
            zone.moisture = 10
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual("CONTROL_DISABLED", model.zones["field-1"].decision_reason)
        self.assertEqual("MANUAL_MODE_NO_AUTO_START", model.zones["field-2"].decision_reason)
        self.assertEqual("OFF", model.site.pump_state)

    def test_tank_low_forces_both_fields_and_pump_to_safe_idle(self) -> None:
        model = self.load_model()
        model.site.max_concurrent_zones = 2
        for zone in model.zones.values():
            zone.moisture = 10
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual("ON", model.site.pump_state)
        model.site.tank_level_pct = 9
        model.tick(timestamp=1_800_000_005_000)
        self.assertEqual({"OFF"}, {zone.valve_state for zone in model.zones.values()})
        self.assertEqual("OFF", model.site.pump_state)
        self.assertEqual("SAFE-IDLE", model.site.system_mode)
        self.assertEqual("TANK_LOW", model.site.safety_block_reason)

    def test_direct_pump_on_is_always_forbidden(self) -> None:
        model = self.load_model()
        command = RpcCommand(
            device=model.site.pump_device,
            request_id=50,
            method="TURN_ON",
            params={"commandId": "manual-pump-on", "source": "MANUAL"},
        )
        self.assertEqual((False, "OFF", "SAFETY_BLOCK"), model.apply_rpc(command))
        self.assertEqual("DIRECT_PUMP_ON_FORBIDDEN", model.site.safety_block_reason)

    def test_direct_pump_off_closes_all_valves_and_latches_auto_restart(self) -> None:
        model = self.load_model()
        model.site.max_concurrent_zones = 2
        for zone in model.zones.values():
            zone.moisture = 10
        model.tick(timestamp=1_800_000_000_000)
        command = RpcCommand(
            device=model.site.pump_device,
            request_id=51,
            method="TURN_OFF",
            params={"commandId": "manual-pump-off", "source": "MANUAL"},
        )
        with patch("simulator_v23.utc_ms", return_value=1_800_000_001_000):
            self.assertEqual((True, "OFF", "EXECUTED"), model.apply_rpc(command))
        self.assertEqual({"OFF"}, {zone.valve_state for zone in model.zones.values()})
        self.assertEqual("OFF", model.site.pump_state)
        model.tick(timestamp=1_800_000_005_000)
        self.assertEqual({"OFF"}, {zone.valve_state for zone in model.zones.values()})


if __name__ == "__main__":
    unittest.main()
