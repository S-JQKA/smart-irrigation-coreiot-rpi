"""Generate the importable CoreIoT v2.3 Minimal package from reviewed v2.2 templates."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "implementation" / "coreiot" / "v2.2"
TARGET = ROOT / "implementation" / "coreiot" / "v2.3-minimal"
RULES = TARGET / "rule_chains"
PROFILES = TARGET / "profiles"
MANIFESTS = TARGET / "manifests"
BASELINE_ZIP = Path(r"C:\Users\voles\Downloads\SI_SF.zip")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rule(filename: str) -> dict[str, Any]:
    return load(SOURCE / "rule_chains" / filename)


def node(chain: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(next(item for item in chain["metadata"]["nodes"] if item["name"] == name))


def set_layout(item: dict[str, Any], x: int, y: int) -> dict[str, Any]:
    item.setdefault("additionalInfo", {})["layoutX"] = x
    item.setdefault("additionalInfo", {})["layoutY"] = y
    return item


def set_script(item: dict[str, Any], script: str) -> dict[str, Any]:
    item["configuration"]["scriptLang"] = "TBEL"
    item["configuration"]["tbelScript"] = script
    item["configuration"]["jsScript"] = script.replace('msg.remove("', 'delete msg.').replace('");', ';')
    return item


def chain(
    name: str,
    description: str,
    nodes: list[dict[str, Any]],
    links: list[tuple[str, str, str]],
    first: str,
) -> dict[str, Any]:
    index = {item["name"]: position for position, item in enumerate(nodes)}
    return {
        "ruleChain": {
            "name": name,
            "type": "CORE",
            "firstRuleNodeId": None,
            "root": False,
            "debugMode": False,
            "configuration": None,
            "additionalInfo": {"description": description},
        },
        "metadata": {
            "version": 5,
            "firstNodeIndex": index[first],
            "nodes": nodes,
            "connections": [
                {"fromIndex": index[source], "toIndex": index[target], "type": relation}
                for source, target, relation in links
            ],
            "ruleChainConnections": None,
        },
    }


def alarm_links(profile_name: str, switch_name: str, count_name: str | None = None) -> list[tuple[str, str, str]]:
    links = [(profile_name, switch_name, "Success"), (profile_name, switch_name, "Failure")]
    for relation in ("Alarm Created", "Alarm Updated", "Alarm Cleared", "Alarm Severity Updated"):
        links.append((profile_name, switch_name, relation))
        if count_name:
            links.append((profile_name, count_name, relation))
    return links


def boolean_alarm(
    alarm_type: str,
    telemetry_key: str,
    severity: str,
    propagate_relation: str | None = None,
) -> dict[str, Any]:
    """Clone the reviewed boolean alarm shape with a deterministic v2.3 ID."""

    pump = load(SOURCE / "profiles" / "sf_pump_controller.json")
    template = next(item for item in pump["profileData"]["alarms"] if item["alarmType"] == "PumpDryRun")
    alarm = copy.deepcopy(template)
    alarm["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"smartfarm-v2.3:{alarm_type}:{telemetry_key}"))
    alarm["alarmType"] = alarm_type
    create_rule = next(iter(alarm["createRules"].values()))
    alarm["createRules"] = {severity: create_rule}
    create_rule["condition"]["condition"][0]["key"]["key"] = telemetry_key
    alarm["clearRule"]["condition"]["condition"][0]["key"]["key"] = telemetry_key
    alarm["propagate"] = propagate_relation is not None
    alarm["propagateRelationTypes"] = [propagate_relation] if propagate_relation else []
    return alarm


def node_offline_alarm(propagate_relation: str) -> dict[str, Any]:
    """Create a platform-activity alarm for one Gateway-managed device profile.

    CoreIoT maintains the server-side ``active`` attribute from Gateway API
    connect/disconnect messages.  A five-minute duration avoids treating short
    MQTT interruptions as a device outage; reconnecting clears the alarm.
    """

    soil = load(SOURCE / "profiles" / "si_soil_moisture_sensor_v2_2.json")
    template = next(item for item in soil["profileData"]["alarms"] if item["alarmType"] == "NodeOffline")
    alarm = copy.deepcopy(template)
    alarm["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"smartfarm-v2.3:NodeOffline:{propagate_relation}"))
    alarm["propagate"] = True
    alarm["propagateRelationTypes"] = [propagate_relation]
    return alarm


def normalize_duration_alarm_specs(profile: dict[str, Any]) -> None:
    """Emit the duration predicate shape accepted by the current CoreIoT tenant.

    The v2.2 source profiles use the newer nested numeric-predicate form. The
    current tenant silently discards that duration value during import. Its
    exported schema stores the value object directly under ``spec.predicate``.
    """

    for alarm in profile.get("profileData", {}).get("alarms", []):
        for rule in (alarm.get("createRules") or {}).values():
            spec = (rule.get("condition") or {}).get("spec") or {}
            if spec.get("type") != "DURATION":
                continue
            predicate = spec.get("predicate") or {}
            value = predicate.get("value")
            if isinstance(value, dict):
                spec["predicate"] = copy.deepcopy(value)


def build_count() -> dict[str, Any]:
    source = rule("si_count_alarms_v2_2.json")
    nodes = copy.deepcopy(source["metadata"]["nodes"])
    return chain(
        "SI Count Alarms",
        "Tổng hợp cảnh báo theo từng mức độ.",
        nodes,
        [(nodes[0]["name"], nodes[1]["name"], "Success")],
        nodes[0]["name"],
    )


def build_field() -> dict[str, Any]:
    source = rule("si_field_v2_2.json")
    names = ["SwitchEventType", "Save telemetry", "To Moisture Sensor", "Save Thresholds", "Has Thresholds?", "To Attributes Update"]
    nodes = [node(source, name) for name in names]
    return chain(
        "SI Field",
        "Lưu trạng thái các Field và phân phối các ngưỡng cấu hình.",
        nodes,
        [
            ("SwitchEventType", "Save telemetry", "Post telemetry"),
            ("SwitchEventType", "Has Thresholds?", "Attributes Updated"),
            ("Has Thresholds?", "To Attributes Update", "True"),
            ("To Attributes Update", "To Moisture Sensor", "Success"),
            ("To Moisture Sensor", "Save Thresholds", "Success"),
        ],
        "SwitchEventType",
    )


def build_recommendation() -> dict[str, Any]:
    source = rule("si_field_multivar_control_v2_2.json")
    fetch = set_layout(node(source, "Fetch v2.2 Field Context"), 80, 220)
    fetch["name"] = "Fetch Recommendation Context"
    fetch["configuration"]["serverAttributeNames"] = [
        "criticalMoisture", "minMoistureThreshold", "targetMoisture",
        "maxMoistureThreshold", "floodMoistureThreshold", "controlMode",
    ]
    fetch["configuration"]["latestTsKeyNames"] = [
        "avgMoisture", "airTemp", "airHumidity", "vpd", "lightLux", "actualValveState",
    ]
    evaluate = set_layout(node(source, "Evaluate SRS-aligned Field Decision"), 340, 220)
    evaluate["name"] = "Evaluate Multivariable Recommendation"
    script = '''var moisture = msg.avgMoisture;
if (moisture == null && metadata.avgMoisture != null) moisture = JSON.parse(metadata.avgMoisture).value;
var airTemp = metadata.airTemp != null ? JSON.parse(metadata.airTemp).value : null;
var airHumidity = metadata.airHumidity != null ? JSON.parse(metadata.airHumidity).value : null;
var vpd = metadata.vpd != null ? JSON.parse(metadata.vpd).value : null;
var lightLux = metadata.lightLux != null ? JSON.parse(metadata.lightLux).value : null;
var actualState = metadata.actualValveState != null ? JSON.parse(metadata.actualValveState).value : "OFF";
var critical = metadata.ss_criticalMoisture != null ? parseDouble(metadata.ss_criticalMoisture) : 15;
var minimum = metadata.ss_minMoistureThreshold != null ? parseDouble(metadata.ss_minMoistureThreshold) : 30;
var target = metadata.ss_targetMoisture != null ? parseDouble(metadata.ss_targetMoisture) : 55;
var maximum = metadata.ss_maxMoistureThreshold != null ? parseDouble(metadata.ss_maxMoistureThreshold) : 70;
var flood = metadata.ss_floodMoistureThreshold != null ? parseDouble(metadata.ss_floodMoistureThreshold) : 85;
var controlMode = metadata.ss_controlMode != null ? metadata.ss_controlMode : "DISABLED";
var hot = airTemp != null && airTemp > 28;
var highVpd = vpd != null && vpd > 1.5;
var highLight = lightLux != null && lightLux > 20000;
var stress = hot || highVpd || highLight;
var recommendation = "NONE";
var reason = "NO_CHANGE";
var priority = 0;
if (moisture == null) {
    reason = "NO_VALID_MOISTURE";
} else if (actualState == "ON" && moisture >= flood) {
    recommendation = "TURN_OFF_REQUEST";
    reason = "WATERLOGGING_RISK";
} else if (actualState == "ON" && moisture >= maximum) {
    recommendation = "TURN_OFF_REQUEST";
    reason = "MAX_MOISTURE_REACHED";
} else if (actualState == "ON" && moisture >= target) {
    recommendation = "TURN_OFF_REQUEST";
    reason = "TARGET_MOISTURE_REACHED";
} else if (controlMode == "AUTO" && actualState != "ON" && moisture < critical) {
    recommendation = "TURN_ON_REQUEST";
    reason = "BELOW_CRITICAL_MOISTURE";
    priority = 100;
} else if (controlMode == "AUTO" && actualState != "ON" && moisture < minimum) {
    recommendation = "TURN_ON_REQUEST";
    reason = stress ? "BELOW_MIN_WITH_ENV_STRESS" : "BELOW_MIN_MOISTURE";
    priority = stress ? 80 : 60;
} else if (controlMode != "AUTO") {
    reason = "AUTO_CONTROL_DISABLED";
}
msg = {
    irrigationRecommendation: recommendation,
    recommendationReason: reason,
    irrigationPriority: priority,
    environmentStress: stress,
    observedAirTemp: airTemp,
    observedAirHumidity: airHumidity,
    observedVpd: vpd,
    observedLightLux: lightLux,
    recommendationAt: Date.now()
};
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};'''
    set_script(evaluate, script)
    save = set_layout(node(source, "Save Field Decision"), 650, 220)
    save["name"] = "Save Recommendation Only"
    return chain(
        "SI Field Recommendation",
        "Khuyến nghị tưới dựa trên các thông số thu thập, không trực tiếp điều khiển van.",
        [fetch, evaluate, save],
        [
            ("Fetch Recommendation Context", "Evaluate Multivariable Recommendation", "Success"),
            ("Evaluate Multivariable Recommendation", "Save Recommendation Only", "Success"),
        ],
        "Fetch Recommendation Context",
    )


def build_soil() -> dict[str, Any]:
    source = rule("si_soil_moisture_v2_2.json")
    names = [
        "Cloud Defensive Check - Soil", "Device Profile Node", "Message Type Switch",
        "Save Timeseries", "Has Moisture?", "To Field Asset", "Aggregate Latest Moisture", "Count Alarms",
    ]
    nodes = [node(source, name) for name in names]
    aggregate = next(item for item in nodes if item["name"] == "Aggregate Latest Moisture")
    aggregate["name"] = "Aggregate One Field Average"
    aggregate["configuration"]["deduplicationInSec"] = 5
    mapping = aggregate["configuration"]["aggMappings"][0]
    mapping["target"] = "avgMoisture"
    mapping["filter"] = {"serverAttributeNames": [], "scriptLang": "TBEL", "tbelFilterFunction": "return true;"}
    save_field = set_layout(node(source, "Save Timeseries"), 1640, 180)
    save_field["name"] = "Save Field Average"
    recommendation = set_layout(node(source, "To Field Rule Chain"), 1880, 180)
    recommendation["name"] = "To Field Recommendation"
    recommendation["additionalInfo"]["description"] = "Sau khi import, liên kết tới SI Field Recommendation."
    nodes.extend([save_field, recommendation])
    links = [
        ("Cloud Defensive Check - Soil", "Device Profile Node", "Success"),
        ("Message Type Switch", "Save Timeseries", "Post telemetry"),
        ("Save Timeseries", "Has Moisture?", "Success"),
        ("Has Moisture?", "To Field Asset", "True"),
        ("To Field Asset", "Aggregate One Field Average", "Success"),
        ("Aggregate One Field Average", "Save Field Average", "Success"),
        ("Save Field Average", "To Field Recommendation", "Success"),
    ] + alarm_links("Device Profile Node", "Message Type Switch", "Count Alarms")
    return chain(
        "SI Soil Moisture",
        "Lưu độ ẩm đất và cập nhật độ ẩm trung bình của các Field.",
        nodes,
        links,
        "Cloud Defensive Check - Soil",
    )


def build_environment() -> dict[str, Any]:
    source = rule("si_environment_v2_2.json")
    names = [
        "Device Profile Alarms", "Message Type Switch", "Cloud Defensive Check + Calculate VPD",
        "Save Environment Timeseries", "Save Client Attributes", "To Field Asset", "Count Alarms",
    ]
    nodes = [node(source, name) for name in names]
    save_field = set_layout(node(source, "Save Environment Timeseries"), 1010, 180)
    save_field["name"] = "Save Field Environment Context"
    nodes.append(save_field)
    links = [
        ("Message Type Switch", "Cloud Defensive Check + Calculate VPD", "Post telemetry"),
        ("Message Type Switch", "Save Client Attributes", "Post attributes"),
        ("Cloud Defensive Check + Calculate VPD", "Save Environment Timeseries", "Success"),
        ("Save Environment Timeseries", "To Field Asset", "Success"),
        ("To Field Asset", "Save Field Environment Context", "Success"),
    ] + alarm_links("Device Profile Alarms", "Message Type Switch", "Count Alarms")
    return chain(
        "SI Environment",
        "Lưu nhiệt độ, độ ẩm không khí, ánh sáng và chỉ số VPD.",
        nodes,
        links,
        "Device Profile Alarms",
    )


def build_water() -> dict[str, Any]:
    source = rule("si_water_meter_v2_2.json")
    multivar = rule("si_field_multivar_control_v2_2.json")
    names = [
        "Cloud Defensive Check - Water", "Device Profile Node", "Message Type Switch", "Save Timeseries",
        "Calculate Delta", "To Field", "Count Alarms",
    ]
    nodes = [node(source, name) for name in names]
    fetch = set_layout(node(multivar, "Fetch v2.2 Field Context"), 1210, 190)
    fetch["name"] = "Fetch Pulses Per Liter"
    fetch["configuration"]["serverAttributeNames"] = ["pulsesPerLiter"]
    fetch["configuration"]["latestTsKeyNames"] = []
    convert = set_layout(node(multivar, "Evaluate SRS-aligned Field Decision"), 1450, 190)
    convert["name"] = "Convert Pulse Delta to Liters"
    set_script(convert, '''var pulseDelta = msg.waterConsumption != null ? parseDouble(msg.waterConsumption) : 0;
var pulsesPerLiter = metadata.ss_pulsesPerLiter != null ? parseDouble(metadata.ss_pulsesPerLiter) : 450;
if (pulsesPerLiter <= 0) pulsesPerLiter = 450;
msg.pulseDelta = pulseDelta;
msg.waterConsumption = pulseDelta / pulsesPerLiter;
msg.waterConsumptionLiters = msg.waterConsumption;
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''')
    save_field = set_layout(node(source, "Save Timeseries"), 1930, 190)
    save_field["name"] = "Save Field Water Liters"
    nodes.extend([fetch, convert, save_field])
    links = [
        ("Cloud Defensive Check - Water", "Device Profile Node", "Success"),
        ("Message Type Switch", "Save Timeseries", "Post telemetry"),
        ("Save Timeseries", "Calculate Delta", "Success"),
        ("Calculate Delta", "Fetch Pulses Per Liter", "Success"),
        ("Fetch Pulses Per Liter", "Convert Pulse Delta to Liters", "Success"),
        ("Convert Pulse Delta to Liters", "To Field", "Success"),
        ("To Field", "Save Field Water Liters", "Success"),
    ] + alarm_links("Device Profile Node", "Message Type Switch", "Count Alarms")
    return chain(
        "SI Water Meter",
        "Lưu lưu lượng tưới, lượng nước sử dụng và cảnh báo bất thường tưới.",
        nodes,
        links,
        "Cloud Defensive Check - Water",
    )


def build_valve() -> dict[str, Any]:
    source = rule("si_smart_valve_v2_2.json")
    names = [
        "Normalize Valve State + ACK", "Device Profile Node", "Message Type Switch", "Save Timeseries",
        "RPC Call Request", "Has Actual Valve State?", "To Related Field - Actual State",
        "Normalize Field Actual Valve State", "Save Field Actual Valve State", "Count Alarms",
    ]
    nodes = [node(source, name) for name in names]
    has_field_update = next(item for item in nodes if item["name"] == "Has Actual Valve State?")
    has_field_update["name"] = "Has Field State or Task?"
    set_script(has_field_update, '''return msg.actualValveState != null || msg.valveState != null || msg.irrigationTask != null || msg.gatewayScheduleId != null || msg.waterConsumptionLiters != null;''')
    normalize_field = next(item for item in nodes if item["name"] == "Normalize Field Actual Valve State")
    set_script(normalize_field, '''var newMsg = {};
var actual = msg.actualValveState != null ? msg.actualValveState : msg.valveState;
if (actual != null) {
    var ack = msg.lastAck != null ? msg.lastAck : "NONE";
    newMsg.actualValveState = actual;
    newMsg.irrigationState = actual;
    newMsg.commandStatus = msg.commandStatus != null ? msg.commandStatus : ack;
    newMsg.valveLastCommandId = msg.lastCommandId != null ? msg.lastCommandId : "";
    newMsg.valveLastAck = ack;
    newMsg.valveSafetyBlockReason = msg.safetyBlockReason != null ? msg.safetyBlockReason : "NONE";
    newMsg.decisionReason = msg.decisionReason != null ? msg.decisionReason : "NONE";
    newMsg.irrigationPriority = msg.irrigationPriority != null ? msg.irrigationPriority : 0;
}
if (msg.irrigationTask != null) {
    newMsg.irrigationTask = msg.irrigationTask;
}
if (msg.gatewayScheduleId != null) {
    newMsg.gatewayScheduleId = msg.gatewayScheduleId;
    newMsg.gatewayScheduleSource = msg.gatewayScheduleSource;
    newMsg.gatewayScheduleEnabled = msg.gatewayScheduleEnabled;
    newMsg.gatewayScheduleStatus = msg.gatewayScheduleStatus;
    newMsg.gatewayScheduleNextRunTs = msg.gatewayScheduleNextRunTs;
    newMsg.gatewayScheduleDurationSec = msg.gatewayScheduleDurationSec;
    newMsg.gatewayScheduleRepeatEverySec = msg.gatewayScheduleRepeatEverySec;
    newMsg.gatewayScheduleLastRunTs = msg.gatewayScheduleLastRunTs;
    newMsg.gatewayScheduleLastResultReason = msg.gatewayScheduleLastResultReason;
    newMsg.gatewayScheduleConfigCommandId = msg.gatewayScheduleConfigCommandId;
    newMsg.gatewayScheduleConfigAck = msg.gatewayScheduleConfigAck;
    newMsg.gatewayScheduleConfigReason = msg.gatewayScheduleConfigReason;
}
if (msg.waterConsumptionLiters != null) {
    newMsg.waterConsumptionLiters = msg.waterConsumptionLiters;
    newMsg.cycleWaterLiters = msg.cycleWaterLiters;
    newMsg.dailyWaterLiters = msg.dailyWaterLiters;
}
return {msg: newMsg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''')
    links = [
        ("Normalize Valve State + ACK", "Device Profile Node", "Success"),
        ("Message Type Switch", "Save Timeseries", "Post telemetry"),
        ("Message Type Switch", "RPC Call Request", "RPC Request to Device"),
        ("Save Timeseries", "Has Field State or Task?", "Success"),
        ("Has Field State or Task?", "To Related Field - Actual State", "True"),
        ("To Related Field - Actual State", "Normalize Field Actual Valve State", "Success"),
        ("Normalize Field Actual Valve State", "Save Field Actual Valve State", "Success"),
    ] + alarm_links("Device Profile Node", "Message Type Switch", "Count Alarms")
    return chain(
        "SI Smart Valve",
        "Chuyển lệnh tưới thủ công tới Gateway và đồng bộ trạng thái tưới lên Field.",
        nodes,
        links,
        "Normalize Valve State + ACK",
    )


def build_profiles() -> dict[str, dict[str, Any]]:
    selections = {
        "si_soil_moisture_sensor_v2_2.json": ("SI Soil Moisture Sensor", {"High Moisture Level", "Low Moisture Level", "Low Battery", "WaterloggingRisk"}, "Cảm biến độ ẩm đất theo từng Field."),
        "si_smart_valve_v2_2.json": ("SI Smart Valve", {"Low Battery"}, "Van tưới theo Field, nhận lệnh qua Gateway và báo trạng thái."),
        "si_water_meter_v2_2.json": ("SI Water Meter", {"Low Battery", "ZoneFlowLow"}, "Đồng hồ nước theo Field, ghi lưu lượng và tổng lượng nước."),
        "sf_env_sensor_cluster.json": ("SF Env Sensor", {"HighTemperature", "Low Battery"}, "Cảm biến nhiệt độ, độ ẩm không khí, ánh sáng và VPD theo Field."),
        "sf_pump_controller.json": ("SF Pump Controller", {"PumpDryRun"}, "Bơm chính, theo dõi trạng thái, thời gian chạy và cảnh báo an toàn."),
        "sf_manifold_controller.json": ("SF Manifold Controller", {"TankLow"}, "Cụm phân phối nước, theo dõi bồn chứa, lưu lượng và trạng thái điều khiển."),
        "sf_gateway.json": ("SF Gateway", set(), "Gateway SmartFarm, quản lý kết nối thiết bị và trạng thái hệ thống."),
    }
    result: dict[str, dict[str, Any]] = {}
    offline_relations = {
        "si_soil_moisture_sensor_v2_2.json": "FieldToMoistureSensor",
        "si_smart_valve_v2_2.json": "FieldToSmartValve",
        "si_water_meter_v2_2.json": "FieldToWaterMeter",
        "sf_env_sensor_cluster.json": "FieldToEnvSensor",
        "sf_pump_controller.json": "SiteToPump",
        "sf_manifold_controller.json": "SiteToManifold",
        "sf_gateway.json": "SiteToGateway",
    }
    for filename, (name, keep, description) in selections.items():
        profile = load(SOURCE / "profiles" / filename)
        profile["name"] = name
        profile["description"] = description
        profile["defaultRuleChainId"] = None
        profile["defaultDashboardId"] = None
        profile["defaultEdgeRuleChainId"] = None
        profile["profileData"]["alarms"] = [
            alarm for alarm in profile["profileData"].get("alarms", []) if alarm["alarmType"] in keep
        ]
        profile["profileData"]["alarms"].append(node_offline_alarm(offline_relations[filename]))
        if filename == "si_smart_valve_v2_2.json":
            profile["profileData"]["alarms"].extend([
                boolean_alarm("IrrigationWatchdog", "irrigationWatchdog", "CRITICAL", "FieldToSmartValve"),
                boolean_alarm("WaterQuotaExceeded", "waterQuotaExceeded", "MAJOR", "FieldToSmartValve"),
                boolean_alarm("FieldDataInvalid", "fieldDataInvalid", "MAJOR", "FieldToSmartValve"),
            ])
        if filename == "sf_gateway.json":
            profile["profileData"]["alarms"].append(
                boolean_alarm("GatewayDegraded", "gatewayDegraded", "MAJOR")
            )
        normalize_duration_alarm_specs(profile)
        result[filename.replace("v2_2", "v2_3_minimal")] = profile
    for filename in ("si_field_asset_profile_v2_2.json", "sf_site_asset_profile.json"):
        profile = load(SOURCE / "profiles" / filename)
        if filename == "si_field_asset_profile_v2_2.json":
            profile["name"] = "SI Field"
            profile["description"] = "Khu vực canh tác, liên kết cảm biến và trạng thái tưới."
        else:
            profile["name"] = "SF Site"
            profile["description"] = "Khu SmartFarm tổng, liên kết Field, Gateway, bơm và cụm phân phối."
        profile["defaultRuleChainId"] = None
        result[filename.replace("v2_2", "v2_3_minimal")] = profile
    return result


def main() -> None:
    artifacts = {
        "si_count_alarms_v2_3_minimal.json": build_count(),
        "si_field_v2_3_minimal.json": build_field(),
        "si_field_recommendation_v2_3_minimal.json": build_recommendation(),
        "si_soil_moisture_v2_3_minimal.json": build_soil(),
        "si_environment_v2_3_minimal.json": build_environment(),
        "si_water_meter_v2_3_minimal.json": build_water(),
        "si_smart_valve_v2_3_minimal.json": build_valve(),
    }
    for filename, artifact in artifacts.items():
        write(RULES / filename, artifact)
    for filename, artifact in build_profiles().items():
        write(PROFILES / filename, artifact)

    baseline = {
        "capturedAt": "2026-08-07",
        "source": str(BASELINE_ZIP),
        "sizeBytes": BASELINE_ZIP.stat().st_size if BASELINE_ZIP.exists() else None,
        "sha256": hashlib.sha256(BASELINE_ZIP.read_bytes()).hexdigest().upper() if BASELINE_ZIP.exists() else None,
        "policy": "READ_ONLY_BASELINE_DO_NOT_IMPORT_OVER_V23",
    }
    write(MANIFESTS / "baseline_si_sf_2026_08_07.json", baseline)
    write(MANIFESTS / "migration_manifest.json", {
        "schemaVersion": "2.3-minimal",
        "rootRuleChain": "KEEP_EXISTING_TENANT_ROOT",
        "importOrder": [
            "SI Count Alarms", "SI Field Recommendation", "SI Field",
            "SI Soil Moisture", "SI Environment", "SI Water Meter", "SI Smart Valve",
        ],
        "postImportBindings": [
            {"node": "To Field Recommendation", "target": "SI Field Recommendation"},
            {"node": "Count Alarms", "target": "SI Count Alarms"},
        ],
        "activation": "Bind both existing Fields after Task Sync v2.3.2 import; do not create a synthetic Field 3.",
        "controlOwnership": "Gateway-local schedules are authoritative. CoreIoT recommendation and task sync never send automatic RPC.",
    })
    print(f"generated ruleChains={len(artifacts)} profiles={len(build_profiles())} target={TARGET}")


if __name__ == "__main__":
    main()
