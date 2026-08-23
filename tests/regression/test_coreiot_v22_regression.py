"""Static review checks for the local CoreIoT v2.2 correction package.

These checks are intentionally named SC (static check), not TC.  Passing them
does not claim CoreIoT tenant execution or SRS acceptance-test completion.
"""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from coreiot.gateway_protocol import RpcCommand, RpcGuard  # noqa: E402
from simulator_v22 import SimulationModel  # noqa: E402


CORE = ROOT / "implementation" / "coreiot"
V22 = CORE / "v2.2"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nodes(name: str):
    return load(V22 / "rule_chains" / name)["metadata"]["nodes"]


def alarm(profile_name: str, alarm_type: str):
    profile = load(V22 / "profiles" / profile_name)
    return next(item for item in profile["profileData"]["alarms"] if item["alarmType"] == alarm_type)


def debounce_seconds(alarm_config: dict) -> int:
    rule = next(iter(alarm_config["createRules"].values()))
    return rule["condition"]["spec"]["predicate"]["value"]["defaultValue"]


class CoreIoTV22StaticReviewChecks(unittest.TestCase):
    def test_SC01_raw_archive_contains_11_exports(self):
        exports = list((CORE / "baseline" / "2026-08-03" / "exports").glob("*.json"))
        self.assertEqual(11, len(exports))

    def test_SC02_audit_keeps_generators_out_of_target(self):
        inventory = load(CORE / "baseline" / "2026-08-03" / "inventory.json")
        text = json.dumps(inventory)
        self.assertIn("REMOVE", text)
        self.assertIn("Generator", text)

    def test_SC03_manifest_classifies_seven_active_and_two_experimental_chains(self):
        migration = load(V22 / "manifests" / "migration_manifest.json")
        self.assertEqual(7, len(migration["activeRuleChains"]))
        self.assertEqual(2, len(migration["experimentalRuleChains"]))
        self.assertEqual(9, len(list((V22 / "rule_chains").glob("*.json"))))

    def test_SC04_pump_and_safety_chains_are_do_not_import(self):
        migration = load(V22 / "manifests" / "migration_manifest.json")
        self.assertEqual(
            {"si_pump_control_v2_2.json", "si_safety_v2_2.json"},
            {item["file"] for item in migration["experimentalRuleChains"] if item["status"] == "DO_NOT_IMPORT"},
        )

    def test_SC05_target_has_nine_profiles(self):
        self.assertEqual(9, len(list((V22 / "profiles").glob("*.json"))))

    def test_SC06_field_preserves_core_and_delegates_multivar_once(self):
        field_nodes = nodes("si_field_v2_2.json")
        names = [node["name"] for node in field_nodes]
        self.assertFalse(any("Generator" in name for name in names))
        self.assertEqual(1, names.count("To Multivar Control"))
        self.assertFalse(any("Evaluate" in name and "Multivar" in name for name in names))
        self.assertFalse(any(node["type"] == "org.thingsboard.rule.engine.rpc.TbSendRPCRequestNode" for node in field_nodes))

    def test_SC07_multivar_uses_srs_inputs_without_ad_hoc_effective_min(self):
        text = json.dumps(nodes("si_field_multivar_control_v2_2.json"))
        for key in (
            "criticalMoisture", "minMoistureThreshold", "targetMoisture", "maxMoistureThreshold",
            "floodMoistureThreshold", "maxWaterPerCycle", "maxWaterPerDay", "maxDurationSec",
            "airTemp", "vpd", "lightLux", "tankLowSwitch", "systemMode", "irrigationPriority",
        ):
            self.assertIn(key, text)
        self.assertNotIn("effectiveMin", text)
        self.assertNotIn("BELOW_ENV_ADJUSTED_MIN", text)

    def test_SC08_multivar_defaults_fail_safe_and_stops_at_max_quota_duration(self):
        text = json.dumps(nodes("si_field_multivar_control_v2_2.json"))
        for value in (
            "DISABLED", "BLOCKED", "SAFE-IDLE", "MAX_MOISTURE_AUTO_LOCK",
            "CYCLE_QUOTA_REACHED", "DAILY_QUOTA_REACHED", "MAX_DURATION_REACHED", "TURN_OFF",
        ):
            self.assertIn(value, text)
        for key in (
            "ss_criticalMoisture", "ss_minMoistureThreshold", "ss_targetMoisture",
            "ss_maxMoistureThreshold", "ss_floodMoistureThreshold", "ss_maxWaterPerCycle",
            "ss_maxWaterPerDay", "ss_maxDurationSec", "ss_controlMode", "ss_safetyState",
            "ss_systemMode",
        ):
            self.assertIn(key, text)
        self.assertNotIn("metadata.controlMode", text)
        self.assertNotIn("metadata.safetyState", text)
        self.assertNotIn("metadata.systemMode", text)
        self.assertIn('irrigationState == \\"ON\\" && waterUsed >= maxWater', text)

    def test_SC09_rpc_is_two_way_and_carries_command_metadata(self):
        text = json.dumps(nodes("si_field_multivar_control_v2_2.json"))
        for key in ("commandId", "source", "requestedAt", "ttlSeconds", "reason", "oneway"):
            self.assertIn(key, text)
        self.assertIn("SAFETY", text)

    def test_SC10_rpc_ack_is_owned_by_multivar_not_duplicated_in_field(self):
        multivar_names = {node["name"] for node in nodes("si_field_multivar_control_v2_2.json")}
        field_names = {node["name"] for node in nodes("si_field_v2_2.json")}
        self.assertTrue({
            "Normalize RPC ACK", "Save Valve RPC ACK", "Normalize RPC Failure",
            "Back to Related Field - ACK", "Normalize Field State from RPC ACK",
            "Save Field State from RPC ACK",
        }.issubset(multivar_names))
        self.assertNotIn("Normalize RPC ACK", field_names)

    def test_SC10b_field_state_is_ack_owned_not_optimistic(self):
        multivar_nodes = nodes("si_field_multivar_control_v2_2.json")
        evaluate = next(node for node in multivar_nodes if node["name"] == "Evaluate SRS-aligned Field Decision")
        build_rpc = next(node for node in multivar_nodes if node["name"] == "Build Guarded RPC")
        evaluate_text = json.dumps(evaluate)
        rpc_text = json.dumps(build_rpc)
        self.assertNotIn("msg.irrigationState = nextState", evaluate_text)
        self.assertIn("requestedIrrigationState", evaluate_text)
        self.assertIn("WAITING_RPC_ACK", evaluate_text)
        self.assertIn("commandAction", rpc_text)

    def test_SC10c_valve_telemetry_reconciles_actual_state_to_field(self):
        valve_names = {node["name"] for node in nodes("si_smart_valve_v2_2.json")}
        self.assertTrue({
            "Has Actual Valve State?", "To Related Field - Actual State",
            "Normalize Field Actual Valve State", "Save Field Actual Valve State",
        }.issubset(valve_names))
        valve_text = json.dumps(nodes("si_smart_valve_v2_2.json"))
        self.assertIn("actualValveState", valve_text)
        self.assertIn("FieldToSmartValve", valve_text)
        self.assertIn("newMsg.commandStatus", valve_text)

    def test_SC11_soil_keeps_aggregation_and_only_adds_cloud_defense(self):
        data = load(V22 / "rule_chains" / "si_soil_moisture_v2_2.json")
        first = data["metadata"]["nodes"][data["metadata"]["firstNodeIndex"]]
        names = {node["name"] for node in data["metadata"]["nodes"]}
        self.assertEqual("Cloud Defensive Check - Soil", first["name"])
        self.assertTrue({"Aggregate Avg", "Aggregate Latest Moisture"}.issubset(names))

        node_names = [node["name"] for node in data["metadata"]["nodes"]]
        profile_index = node_names.index("Device Profile Node")
        switch_index = node_names.index("Message Type Switch")
        connections = {
            (item["fromIndex"], item["toIndex"], item["type"])
            for item in data["metadata"]["connections"]
        }
        for relation_type in (
            "Success",
            "Failure",
            "Alarm Created",
            "Alarm Updated",
            "Alarm Cleared",
            "Alarm Severity Updated",
        ):
            self.assertIn((profile_index, switch_index, relation_type), connections)

    def test_SC12_water_keeps_delta_and_only_adds_cloud_defense(self):
        water_nodes = nodes("si_water_meter_v2_2.json")
        names = {node["name"] for node in water_nodes}
        self.assertTrue({"Calculate Delta", "Cloud Defensive Check - Water"}.issubset(names))
        self.assertIn("flowRate", json.dumps(water_nodes))

    def test_SC13_environment_is_minimal_vpd_route_without_rpc(self):
        env_nodes = nodes("si_environment_v2_2.json")
        names = {node["name"] for node in env_nodes}
        self.assertTrue({"Cloud Defensive Check + Calculate VPD", "To Field Asset", "To Field Rule Chain"}.issubset(names))
        self.assertFalse(any("RPC" in name for name in names))
        self.assertIn("Math.exp", json.dumps(env_nodes))

    def test_SC14_alarm_catalog_contains_required_types(self):
        profiles = [load(path) for path in (V22 / "profiles").glob("*.json") if "profileData" in load(path)]
        names = {item["alarmType"] for profile in profiles for item in profile["profileData"].get("alarms", [])}
        required = {"HighTemperature", "TankLow", "WaterloggingRisk", "NodeOffline", "PumpDryRun", "ZoneFlowLow", "ValveLeak", "IrrigationTimeout"}
        self.assertTrue(required.issubset(names))

    def test_SC15_alarm_timing_matches_reviewed_srs_values(self):
        self.assertEqual(300, debounce_seconds(alarm("sf_env_sensor_cluster.json", "HighTemperature")))
        self.assertEqual(300, debounce_seconds(alarm("sf_gateway.json", "NodeOffline")))
        low_battery = alarm("si_soil_moisture_sensor_v2_2.json", "Low Battery")
        self.assertEqual(10, debounce_seconds(low_battery))
        create = next(iter(low_battery["createRules"].values()))["condition"]["condition"][0]
        clear = low_battery["clearRule"]["condition"]["condition"][0]
        self.assertEqual(30, create["predicate"]["value"]["defaultValue"])
        self.assertEqual(35, clear["predicate"]["value"]["defaultValue"])

    def test_SC16_dashboard_is_deferred_reference_only(self):
        migration = load(V22 / "manifests" / "migration_manifest.json")
        dashboard_manifest = load(V22 / "manifests" / "dashboard_v2_2_manifest.json")
        self.assertEqual("DEFERRED_REFERENCE_ONLY", migration["dashboard"]["status"])
        self.assertFalse(dashboard_manifest["importReady"])

    def test_SC17_contract_assigns_tank_to_manifold_and_uses_safe_idle(self):
        contract = load(V22 / "manifests" / "data_contract_v2.2.json")
        telemetry = contract["telemetry"]
        self.assertNotIn("tankLevelPct", telemetry["SF Env Sensor Cluster"])
        self.assertIn("tankLevelPct", telemetry["SF Manifold Controller"])
        self.assertIn("systemMode", telemetry["SF Manifold Controller"])
        self.assertIn("SAFE-IDLE", json.dumps(contract))
        self.assertNotIn("SAFE_IDLE", json.dumps(contract))

    def test_SC18_gateway_contract_uses_gateway_topics_and_no_credentials(self):
        contract = load(V22 / "manifests" / "data_contract_v2.2.json")
        text = json.dumps(contract)
        for topic in ("v1/gateway/connect", "v1/gateway/telemetry", "v1/gateway/attributes", "v1/gateway/rpc"):
            self.assertIn(topic, text)
        self.assertNotIn("accessToken", text)

    def test_SC19_simulator_emits_16_children_and_tank_has_one_owner(self):
        config = load(GATEWAY_DIR / "config" / "devices.example.json")
        model = SimulationModel.from_config(config)
        payload = model.tick(timestamp=1_800_000_000_000)
        self.assertEqual(16, len(payload))
        self.assertEqual(8, sum(len(zone.soil_devices) for zone in model.zones.values()))
        for zone in model.zones.values():
            self.assertNotIn("tankLevelPct", payload[zone.env_device][0]["values"])
        manifold = payload[model.site.manifold_device][0]["values"]
        self.assertIn("tankLevelPct", manifold)
        self.assertEqual("ONLINE", manifold["controllerState"])

    def test_SC20_simulator_enforces_concurrency_and_safety_guard(self):
        config = load(GATEWAY_DIR / "config" / "devices.example.json")
        config["simulation"]["startMoisture"] = {"field-1": 20, "field-2": 20}
        config["simulation"]["maxConcurrentZones"] = 1
        model = SimulationModel.from_config(config)
        model.tick(timestamp=1_800_000_000_000)
        self.assertEqual(1, sum(zone.valve_state == "ON" for zone in model.zones.values()))
        queued = next(zone for zone in model.zones.values() if zone.valve_state == "OFF")
        self.assertEqual("WAITING_FOR_SCHEDULER_SLOT", queued.decision_reason)
        model.site.tank_low_switch = True
        command = RpcCommand(queued.valve_device, 1, "TURN_ON", {"commandId": "sc20", "requestedAt": 1_800_000_000_000, "ttlSeconds": 300})
        self.assertEqual((False, "OFF", "SAFETY_BLOCK"), model.apply_rpc(command))
        guard = RpcGuard()
        self.assertEqual("ACCEPT", guard.evaluate(command, now_ms=1_800_000_001_000))
        self.assertEqual("DUPLICATE", guard.evaluate(command, now_ms=1_800_000_001_001))


if __name__ == "__main__":
    unittest.main()
