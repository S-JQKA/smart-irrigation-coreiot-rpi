"""Static checks for the SmartFarm CoreIoT import package.

These checks validate graph structure and ownership boundaries; they do not
claim that a live tenant import has been executed.
"""

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "implementation" / "coreiot"
RULES = TARGET / "rule_chains"
PROFILES = TARGET / "profiles"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def reachable_node_indexes(chain: dict) -> set[int]:
    edges: dict[int, list[int]] = {}
    for connection in chain["metadata"]["connections"]:
        edges.setdefault(connection["fromIndex"], []).append(connection["toIndex"])
    pending = [chain["metadata"]["firstNodeIndex"]]
    reached: set[int] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(edges.get(current, []))
    return reached


class CoreIoTArtifactStaticChecks(unittest.TestCase):
    def test_gateway_contract_labels_runtime_profiles_and_manual_duration(self) -> None:
        contract = load(TARGET / "manifests" / "data_contract.json")
        self.assertEqual(
            {
                "SIM_TWO_FIELD",
                "HIL_FIELD1_3BOARD",
                "HIL_TWO_FIELD_4BOARD",
                "HARDWARE_TWO_FIELD",
            },
            set(contract["runtimeProfiles"]),
        )
        self.assertIn("integer:1..2", contract["gatewayConfiguration"]["maxConcurrentZones"])
        self.assertIn("runDurationSeconds", contract["manualRpc"]["turnOnRequired"])
        self.assertEqual("forbidden", contract["manualRpc"]["directPumpOn"])
        self.assertIn("runtimeMode", contract["gatewayTelemetry"])
        self.assertEqual(
            "boolean",
            contract["soilSensorAnalyticsTelemetry"]["anomalyActive"],
        )
        self.assertIn(
            "advisory telemetry only",
            " ".join(contract["invariants"]),
        )


    def test_has_seven_small_active_chains(self) -> None:
        paths = list(RULES.glob("*.json"))
        self.assertEqual(7, len(paths))
        for path in paths:
            chain = load(path)
            self.assertLessEqual(len(chain["metadata"]["nodes"]), 12, path.name)

    def test_all_nodes_are_reachable(self) -> None:
        for path in RULES.glob("*.json"):
            chain = load(path)
            self.assertEqual(
                set(range(len(chain["metadata"]["nodes"]))),
                reachable_node_indexes(chain),
                path.name,
            )

    def test_recommendation_has_no_rpc_or_actual_state_write(self) -> None:
        path = RULES / "si_field_recommendation.json"
        chain = load(path)
        nodes = chain["metadata"]["nodes"]
        self.assertEqual(3, len(nodes))
        self.assertFalse(any("rpc" in item["type"].lower() for item in nodes))
        script = next(item for item in nodes if item["name"] == "Evaluate Multivariable Recommendation")["configuration"]["tbelScript"]
        self.assertIn("airTemp", script)
        self.assertIn("airHumidity", script)
        self.assertIn("vpd", script)
        self.assertIn("lightLux", script)
        self.assertIn("irrigationRecommendation", script)
        self.assertNotIn("commandStatus", script)
        self.assertNotIn("actualValveState:", script)

    def test_only_soil_average_triggers_recommendation(self) -> None:
        soil = load(RULES / "si_soil_moisture.json")
        soil_names = [item["name"] for item in soil["metadata"]["nodes"]]
        self.assertEqual(1, sum("Aggregate" in name for name in soil_names))
        self.assertEqual(1, soil_names.count("To Field Recommendation"))
        self.assertNotIn("Aggregate Avg", soil_names)
        for filename in ("si_environment.json", "si_water_meter.json", "si_field.json"):
            text = json.dumps(load(RULES / filename))
            self.assertNotIn("To Field Recommendation", text, filename)

    def test_water_chain_converts_pulses_to_liters_once(self) -> None:
        water = load(RULES / "si_water_meter.json")
        names = [item["name"] for item in water["metadata"]["nodes"]]
        self.assertEqual(1, names.count("Calculate Delta"))
        convert = next(item for item in water["metadata"]["nodes"] if item["name"] == "Convert Pulse Delta to Liters")
        script = convert["configuration"]["tbelScript"]
        self.assertIn("pulsesPerLiter", script)
        self.assertIn("pulseDelta / pulsesPerLiter", script)

    def test_only_valve_path_forwards_explicit_rpc(self) -> None:
        owners = []
        for path in RULES.glob("*.json"):
            chain = load(path)
            if any("TbSendRPCRequestNode" in item["type"] for item in chain["metadata"]["nodes"]):
                owners.append(path.name)
        self.assertEqual(["si_smart_valve.json"], sorted(owners))
        field = load(RULES / "si_field.json")
        field_text = json.dumps(field)
        self.assertNotIn("START_IRRIGATION", field_text)
        self.assertNotIn("TbSendRPCRequestNode", field_text)

    def test_gateway_task_and_schedule_mirror_are_present(self) -> None:
        valve = load(RULES / "si_smart_valve.json")
        task_filter = next(
            item for item in valve["metadata"]["nodes"] if item["name"] == "Has Field State or Task?"
        )
        self.assertIn("irrigationTask", task_filter["configuration"]["tbelScript"])
        self.assertIn("gatewayScheduleId", task_filter["configuration"]["tbelScript"])
        normalize = next(
            item for item in valve["metadata"]["nodes"] if item["name"] == "Normalize Field Actual Valve State"
        )
        self.assertIn("newMsg.irrigationTask", normalize["configuration"]["tbelScript"])
        self.assertIn("newMsg.gatewayScheduleStatus", normalize["configuration"]["tbelScript"])
        self.assertEqual("SI Smart Valve", valve["ruleChain"]["name"])

    def test_profiles_only_enable_alarms_with_runtime_sources(self) -> None:
        expected = {
            "si_soil_moisture_sensor.json": {"High Moisture Level", "Low Moisture Level", "Low Battery", "WaterloggingRisk", "NodeOffline", "SensorAnomaly"},
            "si_smart_valve.json": {"Low Battery", "IrrigationWatchdog", "WaterQuotaExceeded", "FieldDataInvalid", "NodeOffline"},
            "si_water_meter.json": {"Low Battery", "ZoneFlowLow", "ValveLeak", "NodeOffline"},
            "sf_env_sensor_cluster.json": {"HighTemperature", "Low Battery", "NodeOffline"},
            "sf_pump_controller.json": {"PumpDryRun", "NodeOffline"},
            "sf_manifold_controller.json": {"TankLow", "NodeOffline"},
            "sf_gateway.json": {"GatewayDegraded", "NodeOffline"},
        }
        for filename, alarm_types in expected.items():
            profile = load(PROFILES / filename)
            actual = {item["alarmType"] for item in profile["profileData"].get("alarms", [])}
            self.assertEqual(alarm_types, actual, filename)

    def test_node_offline_uses_platform_activity_and_expected_relation(self) -> None:
        expected_relations = {
            "si_soil_moisture_sensor.json": "FieldToMoistureSensor",
            "si_smart_valve.json": "FieldToSmartValve",
            "si_water_meter.json": "FieldToWaterMeter",
            "sf_env_sensor_cluster.json": "FieldToEnvSensor",
            "sf_pump_controller.json": "SiteToPump",
            "sf_manifold_controller.json": "SiteToManifold",
            "sf_gateway.json": "SiteToGateway",
        }
        for filename, relation in expected_relations.items():
            profile = load(PROFILES / filename)
            alarm = next(item for item in profile["profileData"]["alarms"] if item["alarmType"] == "NodeOffline")
            create_rule = alarm["createRules"]["MAJOR"]
            create_condition = create_rule["condition"]["condition"][0]
            self.assertEqual("ATTRIBUTE", create_condition["key"]["type"], filename)
            self.assertEqual("active", create_condition["key"]["key"], filename)
            self.assertFalse(create_condition["predicate"]["value"]["defaultValue"], filename)
            self.assertEqual(300, create_rule["condition"]["spec"]["predicate"]["defaultValue"], filename)
            clear_condition = alarm["clearRule"]["condition"]["condition"][0]
            self.assertTrue(clear_condition["predicate"]["value"]["defaultValue"], filename)
            self.assertTrue(alarm["propagate"], filename)
            self.assertEqual([relation], alarm["propagateRelationTypes"], filename)

    def test_operational_alarm_predicates_use_gateway_runtime_keys(self) -> None:
        expected = {
            ("si_smart_valve.json", "IrrigationWatchdog"): "irrigationWatchdog",
            ("si_smart_valve.json", "WaterQuotaExceeded"): "waterQuotaExceeded",
            ("si_smart_valve.json", "FieldDataInvalid"): "fieldDataInvalid",
            ("si_water_meter.json", "ZoneFlowLow"): "zoneFlowLow",
            ("si_water_meter.json", "ValveLeak"): "valveLeakActive",
            ("sf_pump_controller.json", "PumpDryRun"): "pumpDryRun",
            ("sf_gateway.json", "GatewayDegraded"): "gatewayDegraded",
            ("si_soil_moisture_sensor.json", "SensorAnomaly"): "anomalyActive",
        }
        for (filename, alarm_type), telemetry_key in expected.items():
            profile = load(PROFILES / filename)
            alarm = next(item for item in profile["profileData"]["alarms"] if item["alarmType"] == alarm_type)
            create_rule = next(iter(alarm["createRules"].values()))
            actual_key = create_rule["condition"]["condition"][0]["key"]["key"]
            self.assertEqual(telemetry_key, actual_key, f"{filename}:{alarm_type}")

    def test_sensor_anomaly_alarm_is_advisory_and_propagates_to_field(self) -> None:
        profile = load(PROFILES / "si_soil_moisture_sensor.json")
        alarm = next(
            item for item in profile["profileData"]["alarms"]
            if item["alarmType"] == "SensorAnomaly"
        )
        self.assertEqual({"MAJOR"}, set(alarm["createRules"]))
        create_rule = alarm["createRules"]["MAJOR"]
        create_condition = create_rule["condition"]["condition"][0]
        self.assertEqual("TIME_SERIES", create_condition["key"]["type"])
        self.assertEqual("anomalyActive", create_condition["key"]["key"])
        self.assertTrue(create_condition["predicate"]["value"]["defaultValue"])
        self.assertEqual({"type": "SIMPLE"}, create_rule["condition"]["spec"])
        self.assertIn("${anomalyReason}", create_rule["alarmDetails"])
        clear_condition = alarm["clearRule"]["condition"]["condition"][0]
        self.assertFalse(clear_condition["predicate"]["value"]["defaultValue"])
        self.assertTrue(alarm["propagate"])
        self.assertEqual(["FieldToMoistureSensor"], alarm["propagateRelationTypes"])

    def test_valve_leak_alarm_is_advisory_and_propagates_to_field(self) -> None:
        profile = load(PROFILES / "si_water_meter.json")
        alarm = next(
            item for item in profile["profileData"]["alarms"]
            if item["alarmType"] == "ValveLeak"
        )
        self.assertEqual({"MAJOR"}, set(alarm["createRules"]))
        create_rule = alarm["createRules"]["MAJOR"]
        create_condition = create_rule["condition"]["condition"][0]
        self.assertEqual("TIME_SERIES", create_condition["key"]["type"])
        self.assertEqual("valveLeakActive", create_condition["key"]["key"])
        self.assertTrue(create_condition["predicate"]["value"]["defaultValue"])
        self.assertEqual({"type": "SIMPLE"}, create_rule["condition"]["spec"])
        self.assertIn("${valveLeakReason}", create_rule["alarmDetails"])
        clear_condition = alarm["clearRule"]["condition"]["condition"][0]
        self.assertFalse(clear_condition["predicate"]["value"]["defaultValue"])
        self.assertTrue(alarm["propagate"])
        self.assertEqual(["FieldToWaterMeter"], alarm["propagateRelationTypes"])

    def test_duration_alarm_specs_use_tenant_compatible_shape(self) -> None:
        expected_seconds = {
            ("sf_pump_controller.json", "PumpDryRun"): 15,
            ("sf_gateway.json", "GatewayDegraded"): 15,
            ("sf_manifold_controller.json", "TankLow"): 15,
            ("si_smart_valve.json", "Low Battery"): 10,
            ("si_smart_valve.json", "IrrigationWatchdog"): 15,
            ("si_smart_valve.json", "WaterQuotaExceeded"): 15,
            ("si_smart_valve.json", "FieldDataInvalid"): 15,
            ("si_water_meter.json", "Low Battery"): 10,
            ("si_water_meter.json", "ZoneFlowLow"): 15,
            ("sf_env_sensor_cluster.json", "HighTemperature"): 300,
            ("sf_env_sensor_cluster.json", "Low Battery"): 10,
            ("si_soil_moisture_sensor.json", "High Moisture Level"): 15,
            ("si_soil_moisture_sensor.json", "Low Moisture Level"): 15,
            ("si_soil_moisture_sensor.json", "Low Battery"): 10,
            ("si_soil_moisture_sensor.json", "WaterloggingRisk"): 60,
        }
        for filename in (
            "sf_pump_controller.json",
            "sf_gateway.json",
            "sf_manifold_controller.json",
            "si_smart_valve.json",
            "si_water_meter.json",
            "sf_env_sensor_cluster.json",
            "si_soil_moisture_sensor.json",
        ):
            expected_seconds[(filename, "NodeOffline")] = 300
        for (filename, alarm_type), seconds in expected_seconds.items():
            profile = load(PROFILES / filename)
            alarm = next(item for item in profile["profileData"]["alarms"] if item["alarmType"] == alarm_type)
            create_rule = next(iter(alarm["createRules"].values()))
            spec = create_rule["condition"]["spec"]
            self.assertEqual("DURATION", spec["type"], f"{filename}:{alarm_type}")
            self.assertEqual("SECONDS", spec["unit"], f"{filename}:{alarm_type}")
            self.assertEqual(seconds, spec["predicate"]["defaultValue"], f"{filename}:{alarm_type}")
            self.assertNotIn("type", spec["predicate"], f"{filename}:{alarm_type}")
            self.assertNotIn("operation", spec["predicate"], f"{filename}:{alarm_type}")
            self.assertNotIn("value", spec["predicate"], f"{filename}:{alarm_type}")

    def test_release_artifact_integrity(self) -> None:
        manifest = load(TARGET / "manifests" / "artifact_checksums.json")
        actual = {path.relative_to(TARGET).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for folder in ("profiles", "rule_chains", "dashboard_actions")
                  for path in (TARGET / folder).rglob("*") if path.is_file()}
        self.assertEqual(manifest["sha256"], actual)


if __name__ == "__main__":
    unittest.main()
