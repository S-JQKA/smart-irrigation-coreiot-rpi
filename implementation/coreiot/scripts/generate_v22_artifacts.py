#!/usr/bin/env python3
"""Generate reviewable CoreIoT v2.2 artifacts from the 2026-08-03 baseline.

The active package deliberately keeps CoreIoT focused on telemetry, profile
alarms, field orchestration and cloud-side recommendations.  Hard interlocks,
mode management, pump arbitration and the authoritative quality gate remain at
the Raspberry Pi / Central-Manifold layer as specified by SRS v2.2.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "baseline" / "2026-08-03" / "exports"
TARGET = ROOT / "v2.2"
RULES = TARGET / "rule_chains"
PROFILES = TARGET / "profiles"


def load(name: str) -> dict[str, Any]:
    return json.loads((BASE / name).read_text(encoding="utf-8-sig"))


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def set_description(chain: dict[str, Any], text: str) -> None:
    chain["ruleChain"].setdefault("additionalInfo", {})["description"] = text


def remap_without_nodes(chain: dict[str, Any], removed: set[int]) -> None:
    metadata = chain["metadata"]
    mapping: dict[int, int] = {}
    nodes = []
    for old_index, node in enumerate(metadata["nodes"]):
        if old_index in removed:
            continue
        mapping[old_index] = len(nodes)
        nodes.append(node)
    connections = []
    for connection in metadata.get("connections", []):
        if connection["fromIndex"] in mapping and connection["toIndex"] in mapping:
            connection = copy.deepcopy(connection)
            connection["fromIndex"] = mapping[connection["fromIndex"]]
            connection["toIndex"] = mapping[connection["toIndex"]]
            connections.append(connection)
    metadata["nodes"] = nodes
    metadata["connections"] = connections
    metadata["firstNodeIndex"] = mapping.get(metadata.get("firstNodeIndex"), 0)


def cloned_node(source: dict[str, Any], name: str, x: int, y: int) -> dict[str, Any]:
    node = copy.deepcopy(source)
    node["name"] = name
    node.setdefault("additionalInfo", {})["layoutX"] = x
    node.setdefault("additionalInfo", {})["layoutY"] = y
    return node


FIELD_FETCH_CONFIG = {
    "tellFailureIfAbsent": False,
    "clientAttributeNames": [],
    "sharedAttributeNames": [],
    "serverAttributeNames": [
        "criticalMoisture",
        "minMoistureThreshold",
        "targetMoisture",
        "maxMoistureThreshold",
        "floodMoistureThreshold",
        "maxWaterPerCycle",
        "maxWaterPerDay",
        "maxDurationSec",
        "controlMode",
        "safetyState",
        "systemMode",
        "hydraulicGroupId",
    ],
    "latestTsKeyNames": [
        "irrigationTask",
        "irrigationState",
        "actualValveState",
        "requestedIrrigationState",
        "commandStatus",
        "commandRequestedAt",
        "valveSafetyBlockReason",
        "avgMoisture",
        "latestAvgMoisture",
        "currentIrrigationWaterConsumption",
        "waterConsumption",
        "airTemp",
        "vpd",
        "lightLux",
        "tankLowSwitch",
        "irrigationStartedAt",
    ],
    "getLatestValueWithTs": True,
    "fetchTo": "METADATA",
}


EVALUATE_DECISION_SCRIPT = r'''var moisture = msg.latestAvgMoisture != null ? msg.latestAvgMoisture : msg.avgMoisture;
if (moisture == null && metadata.latestAvgMoisture != null) {
    moisture = JSON.parse(metadata.latestAvgMoisture).value;
}
if (moisture == null && metadata.avgMoisture != null) {
    moisture = JSON.parse(metadata.avgMoisture).value;
}
var criticalMoisture = metadata.ss_criticalMoisture != null ? parseDouble(metadata.ss_criticalMoisture) : 20;
var minMoisture = metadata.ss_minMoistureThreshold != null ? parseDouble(metadata.ss_minMoistureThreshold) : 30;
var targetMoisture = metadata.ss_targetMoisture != null ? parseDouble(metadata.ss_targetMoisture) : 55;
var maxMoisture = metadata.ss_maxMoistureThreshold != null ? parseDouble(metadata.ss_maxMoistureThreshold) : 70;
var floodMoisture = metadata.ss_floodMoistureThreshold != null ? parseDouble(metadata.ss_floodMoistureThreshold) : 85;
var maxWater = metadata.ss_maxWaterPerCycle != null ? parseDouble(metadata.ss_maxWaterPerCycle) : 1000;
var maxWaterPerDay = metadata.ss_maxWaterPerDay != null ? parseDouble(metadata.ss_maxWaterPerDay) : 5000;
var maxDurationSec = metadata.ss_maxDurationSec != null ? parseDouble(metadata.ss_maxDurationSec) : 600;
var controlMode = metadata.ss_controlMode != null ? metadata.ss_controlMode : "DISABLED";
var irrigationState = metadata.actualValveState != null
    ? JSON.parse(metadata.actualValveState).value
    : (metadata.irrigationState != null ? JSON.parse(metadata.irrigationState).value : "IDLE");
var commandStatus = metadata.commandStatus != null ? JSON.parse(metadata.commandStatus).value : "IDLE";
var commandRequestedAt = metadata.commandRequestedAt != null ? JSON.parse(metadata.commandRequestedAt).value : null;
var commandPending = commandStatus == "PENDING" && commandRequestedAt != null
    && Date.now() - commandRequestedAt < 65000;
var valveSafetyBlockReason = metadata.valveSafetyBlockReason != null
    ? JSON.parse(metadata.valveSafetyBlockReason).value
    : "NONE";
var configuredSafety = metadata.ss_safetyState != null ? metadata.ss_safetyState : "BLOCKED";
var systemMode = metadata.ss_systemMode != null ? metadata.ss_systemMode : "SAFE-IDLE";
var waterUsed = msg.currentIrrigationWaterConsumption != null ? msg.currentIrrigationWaterConsumption : 0;
if (metadata.currentIrrigationWaterConsumption != null) {
    waterUsed = JSON.parse(metadata.currentIrrigationWaterConsumption).value;
}
var dailyWater = msg.waterConsumption != null ? msg.waterConsumption : 0;
if (metadata.waterConsumption != null) {
    dailyWater = JSON.parse(metadata.waterConsumption).value;
}
var airTemp = msg.airTemp != null ? msg.airTemp : null;
if (airTemp == null && metadata.airTemp != null) airTemp = JSON.parse(metadata.airTemp).value;
var vpd = msg.vpd != null ? msg.vpd : null;
if (vpd == null && metadata.vpd != null) vpd = JSON.parse(metadata.vpd).value;
var lightLux = msg.lightLux != null ? msg.lightLux : null;
if (lightLux == null && metadata.lightLux != null) lightLux = JSON.parse(metadata.lightLux).value;
var tankLow = msg.tankLowSwitch === true;
if (!tankLow && metadata.tankLowSwitch != null) tankLow = JSON.parse(metadata.tankLowSwitch).value === true;
var irrigationStartedAt = metadata.irrigationStartedAt != null ? JSON.parse(metadata.irrigationStartedAt).value : null;
var durationReached = irrigationStartedAt != null && maxDurationSec > 0 && Date.now() - irrigationStartedAt >= maxDurationSec * 1000;
var environmentStress = (airTemp != null && airTemp > 28) || (vpd != null && vpd > 1.5);

var action = "NONE";
var reason = "NO_CHANGE";
var safety = configuredSafety;
var nextState = irrigationState;
var priority = 0;

if (commandPending) {
    reason = "WAITING_RPC_ACK";
} else if (configuredSafety == "BLOCKED" || systemMode == "SAFE-IDLE" || systemMode == "SAFE_IDLE" || tankLow || valveSafetyBlockReason != "NONE") {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = tankLow ? "TANK_LOW" : (valveSafetyBlockReason != "NONE" ? "VALVE_SAFETY_BLOCK" : "SYSTEM_SAFETY_BLOCK");
    safety = "BLOCKED";
    nextState = "BLOCKED";
} else if (controlMode == "DISABLED") {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "CONTROL_DISABLED";
    nextState = "BLOCKED";
} else if (moisture != null && moisture >= floodMoisture) {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "WATERLOGGING_LOCK";
    safety = "BLOCKED";
    nextState = "BLOCKED";
} else if (moisture != null && moisture >= maxMoisture) {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "MAX_MOISTURE_AUTO_LOCK";
    nextState = "BLOCKED";
} else if (irrigationState == "ON" && waterUsed >= maxWater) {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "CYCLE_QUOTA_REACHED";
    nextState = "BLOCKED";
} else if (dailyWater >= maxWaterPerDay) {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "DAILY_QUOTA_REACHED";
    nextState = "BLOCKED";
} else if (durationReached) {
    action = irrigationState == "ON" ? "TURN_OFF" : "NONE";
    reason = "MAX_DURATION_REACHED";
    nextState = "DONE";
} else if (moisture != null && moisture >= targetMoisture && irrigationState == "ON") {
    action = "TURN_OFF";
    reason = "TARGET_MOISTURE_REACHED";
    nextState = "DONE";
} else if (controlMode == "AUTO" && moisture != null && moisture < criticalMoisture && irrigationState != "ON") {
    action = "TURN_ON";
    reason = "BELOW_CRITICAL_MOISTURE";
    priority = 100;
    nextState = "ON";
} else if (controlMode == "AUTO" && moisture != null && moisture < minMoisture && irrigationState != "ON") {
    action = "TURN_ON";
    reason = environmentStress ? "BELOW_MIN_WITH_ENV_STRESS" : "BELOW_MIN_MOISTURE";
    priority = environmentStress ? 80 : 60;
    nextState = "ON";
} else if (controlMode == "MANUAL") {
    reason = "MANUAL_MODE_NO_AUTO_START";
}

msg.controlAction = action;
msg.decisionReason = reason;
msg.safetyState = safety;
msg.requestedIrrigationState = nextState;
if (action != "NONE") {
    msg.commandStatus = "PENDING";
    msg.commandRequestedAt = Date.now();
}
msg.irrigationPriority = priority;
msg.environmentStress = environmentStress;
msg.lightLuxObserved = lightLux;
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};'''


HAS_ACTION_SCRIPT = 'return msg.controlAction == "TURN_ON" || msg.controlAction == "TURN_OFF";'


BUILD_RPC_SCRIPT = r'''var commandId = "smartfarm-" + msg.controlAction + "-" + Date.now();
var commandSource = msg.safetyState == "BLOCKED" ? "SAFETY" : "AUTO";
var params = {
    commandId: commandId,
    source: commandSource,
    requestedAt: Date.now(),
    ttlSeconds: 300,
    reason: msg.decisionReason
};
var rpc = {method: msg.controlAction, params: params};
var rpcMetadata = {
    expirationTime: Date.now() + 300000,
    oneway: false,
    persistent: true,
    commandId: commandId,
    commandAction: msg.controlAction,
    commandReason: msg.decisionReason
};
return {msg: rpc, metadata: rpcMetadata, msgType: "RPC_CALL_FROM_SERVER_TO_DEVICE"};'''


STOP_SCRIPT = r'''var task = metadata.irrigationTask != null ? JSON.parse(metadata.irrigationTask).value : null;
var moisture = metadata.latestAvgMoisture != null ? JSON.parse(metadata.latestAvgMoisture).value : null;
var target = metadata.targetMoisture != null ? JSON.parse(metadata.targetMoisture).value : 55;
var maxWater = metadata.maxWaterPerCycle != null ? JSON.parse(metadata.maxWaterPerCycle).value : 1000;
var used = msg.currentIrrigationWaterConsumption != null ? msg.currentIrrigationWaterConsumption : 0;
if (task == null) { return true; }
var durationReached = task.durationThreshold > 0 && (task.startTs + task.durationThreshold) < Date.now();
var volumeReached = task.consumptionThreshold != null && task.consumptionThreshold > 0 && used >= task.consumptionThreshold;
var targetReached = moisture != null && moisture >= target;
var quotaReached = used >= maxWater;
var maxReached = moisture != null && metadata.maxMoistureThreshold != null && moisture >= JSON.parse(metadata.maxMoistureThreshold).value;
return durationReached || volumeReached || targetReached || quotaReached || maxReached;'''


def set_tbel_and_js(node: dict[str, Any], script: str) -> None:
    node["configuration"]["scriptLang"] = "TBEL"
    node["configuration"]["jsScript"] = script
    node["configuration"]["tbelScript"] = script


def set_scripts(node: dict[str, Any], tbel_script: str, js_script: str) -> None:
    node["configuration"]["scriptLang"] = "TBEL"
    node["configuration"]["tbelScript"] = tbel_script
    node["configuration"]["jsScript"] = js_script


def transform_template(name: str, x: int, y: int) -> dict[str, Any]:
    template = load("si_soil_moisture_rule_chain.json")["metadata"]["nodes"][13]
    node = cloned_node(template, name, x, y)
    node["configuration"] = {"scriptLang": "TBEL", "jsScript": "", "tbelScript": ""}
    return node


def prepend_quality_transform(chain: dict[str, Any], name: str, tbel_script: str, js_script: str) -> None:
    metadata = chain["metadata"]
    original_first = metadata["firstNodeIndex"]
    node = transform_template(name, 70, 150)
    set_scripts(node, tbel_script, js_script)
    new_index = len(metadata["nodes"])
    metadata["nodes"].append(node)
    metadata["connections"].append({"fromIndex": new_index, "toIndex": original_first, "type": "Success"})
    metadata["firstNodeIndex"] = new_index
    profile_node = metadata["nodes"][original_first]
    if str(profile_node.get("type", "")).endswith("TbDeviceProfileNode"):
        profile_node["configuration"]["persistAlarmRulesState"] = True
        profile_node["configuration"]["fetchAlarmRulesStateOnStart"] = True


def preserve_messages_on_profile_outputs(chain: dict[str, Any]) -> None:
    """Keep messages flowing for every device-profile output relation."""
    metadata = chain["metadata"]
    profile_index = next(
        index
        for index, node in enumerate(metadata["nodes"])
        if str(node.get("type", "")).endswith("TbDeviceProfileNode")
    )
    switch_index = next(
        index
        for index, node in enumerate(metadata["nodes"])
        if str(node.get("type", "")).endswith("TbMsgTypeSwitchNode")
    )
    existing = {
        (connection["fromIndex"], connection["toIndex"], connection["type"])
        for connection in metadata["connections"]
    }
    for relation_type in (
        "Failure",
        "Alarm Created",
        "Alarm Updated",
        "Alarm Cleared",
        "Alarm Severity Updated",
    ):
        edge = (profile_index, switch_index, relation_type)
        if edge not in existing:
            metadata["connections"].append(
                {"fromIndex": profile_index, "toIndex": switch_index, "type": relation_type}
            )


def clear_rule_chain_bindings(chain: dict[str, Any]) -> None:
    """Prevent imported staging JSON from silently targeting baseline chain IDs."""
    for node in chain["metadata"].get("nodes", []):
        if str(node.get("type", "")).endswith("TbRuleChainInputNode"):
            node.setdefault("configuration", {})["ruleChainId"] = None
            node.setdefault("additionalInfo", {})["description"] = (
                "POST-IMPORT BINDING REQUIRED: resolve the target by exact v2.2 rule-chain name."
            )


SOIL_QUALITY_TBEL = r'''if (msgType == "POST_TELEMETRY_REQUEST") {
    var value = msg.moisture != null ? parseFloat(msg.moisture) : null;
    if (value == null || value < 0 || value > 100) {
        msg.remove("moisture");
        msg.dataQuality = "OUT_OF_RANGE";
    } else {
        msg.moisture = value;
        msg.dataQuality = "OK";
    }
    msg.active = true;
}
return {msg: msg, metadata: metadata, msgType: msgType};'''

SOIL_QUALITY_JS = SOIL_QUALITY_TBEL.replace('msg.remove("moisture");', 'delete msg.moisture;')

WATER_QUALITY_TBEL = r'''if (msgType == "POST_TELEMETRY_REQUEST") {
    var pulse = msg.pulseCounter != null ? parseFloat(msg.pulseCounter) : null;
    var flow = msg.flowRate != null ? parseFloat(msg.flowRate) : 0;
    var valid = pulse != null && pulse >= 0 && flow >= 0;
    if (!valid) {
        msg.remove("pulseCounter");
        msg.remove("flowRate");
        msg.dataQuality = "OUT_OF_RANGE";
    } else {
        msg.pulseCounter = pulse;
        msg.flowRate = flow;
        msg.dataQuality = "OK";
    }
}
return {msg: msg, metadata: metadata, msgType: msgType};'''

WATER_QUALITY_JS = WATER_QUALITY_TBEL.replace('msg.remove("pulseCounter");', 'delete msg.pulseCounter;').replace('msg.remove("flowRate");', 'delete msg.flowRate;')

VALVE_QUALITY_TBEL = r'''if (msgType == "POST_TELEMETRY_REQUEST") {
    var valid = msg.valveState == null || msg.valveState == "ON" || msg.valveState == "OFF";
    if (!valid) {
        msg.remove("valveState");
        msg.dataQuality = "INVALID_STATE";
    } else {
        msg.dataQuality = "OK";
    }
    if (msg.lastAck == null) msg.lastAck = "NONE";
    if (msg.safetyBlockReason == null) msg.safetyBlockReason = "NONE";
}
return {msg: msg, metadata: metadata, msgType: msgType};'''

VALVE_QUALITY_JS = VALVE_QUALITY_TBEL.replace('msg.remove("valveState");', 'delete msg.valveState;')

PUMP_QUALITY_TBEL = VALVE_QUALITY_TBEL.replace("valveState", "pumpState")
PUMP_QUALITY_JS = VALVE_QUALITY_JS.replace("valveState", "pumpState")

SAFETY_QUALITY_SCRIPT = r'''if (msgType == "POST_TELEMETRY_REQUEST") {
    msg.dataQuality = "OK";
    if (msg.safetyBlockReason == null) msg.safetyBlockReason = "NONE";
}
return {msg: msg, metadata: metadata, msgType: msgType};'''

ENV_QUALITY_TBEL = r'''if (msgType == "POST_TELEMETRY_REQUEST") {
    var temp = msg.airTemp != null ? parseFloat(msg.airTemp) : null;
    var humidity = msg.airHumidity != null ? parseFloat(msg.airHumidity) : null;
    if (temp == null || temp < -20 || temp > 70 || humidity == null || humidity < 0 || humidity > 100) {
        msg.remove("airTemp");
        msg.remove("airHumidity");
        msg.remove("vpd");
        msg.dataQuality = "OUT_OF_RANGE";
    } else {
        msg.airTemp = temp;
        msg.airHumidity = humidity;
        var saturation = 0.6108 * Math.exp((17.27 * temp) / (temp + 237.3));
        msg.vpd = Math.round(saturation * (1 - humidity / 100) * 1000) / 1000;
        msg.dataQuality = "OK";
    }
}
return {msg: msg, metadata: metadata, msgType: msgType};'''

ENV_QUALITY_JS = ENV_QUALITY_TBEL.replace('msg.remove("airTemp");', 'delete msg.airTemp;').replace('msg.remove("airHumidity");', 'delete msg.airHumidity;').replace('msg.remove("vpd");', 'delete msg.vpd;')


def build_field_chain() -> dict[str, Any]:
    chain = load("si_field_rule_chain.json")
    chain["ruleChain"]["name"] = "SI Field v2.2"
    set_description(
        chain,
        "SmartFarm v2.2 Field telemetry and attribute routing. Legacy direct Start/Stop RPC paths are removed; "
        "SI Field Multivar Control v2.2 is the sole cloud actuator command authority.",
    )
    legacy_direct_control = set(range(2, 20)) | {24, 25, 26, 27, 28, 29}
    remap_without_nodes(chain, legacy_direct_control)

    nodes = chain["metadata"]["nodes"]
    connections = chain["metadata"]["connections"]

    multivar_template = load("si_soil_moisture_rule_chain.json")["metadata"]["nodes"][11]
    to_multivar = cloned_node(multivar_template, "To Multivar Control", 720, 565)
    to_multivar["configuration"]["ruleChainId"] = None
    to_multivar["additionalInfo"]["description"] = (
        "POST-IMPORT BINDING REQUIRED: SI Field Multivar Control v2.2"
    )
    multivar_index = len(nodes)
    nodes.append(to_multivar)
    connections.append({"fromIndex": 1, "toIndex": multivar_index, "type": "Success"})
    clear_rule_chain_bindings(chain)
    return chain


def build_multivar_chain() -> dict[str, Any]:
    field_nodes = load("si_field_rule_chain.json")["metadata"]["nodes"]
    fetch = cloned_node(field_nodes[10], "Fetch v2.2 Field Context", 80, 300)
    fetch["configuration"] = copy.deepcopy(FIELD_FETCH_CONFIG)
    evaluate = cloned_node(field_nodes[2], "Evaluate SRS-aligned Field Decision", 330, 300)
    set_tbel_and_js(evaluate, EVALUATE_DECISION_SCRIPT)
    save = cloned_node(field_nodes[1], "Save Field Decision", 590, 300)
    has_action = cloned_node(field_nodes[11], "Has TURN_ON/TURN_OFF?", 830, 300)
    set_tbel_and_js(has_action, HAS_ACTION_SCRIPT)
    to_valve = cloned_node(field_nodes[13], "To Related Smart Valve", 1060, 235)
    build_rpc = cloned_node(field_nodes[8], "Build Guarded RPC", 1290, 235)
    set_tbel_and_js(build_rpc, BUILD_RPC_SCRIPT)
    send_rpc = cloned_node(field_nodes[6], "Send Two-way RPC", 1515, 235)
    ignore = cloned_node(field_nodes[24], "No Actuator Change", 1060, 400)
    ack = cloned_node(field_nodes[2], "Normalize RPC ACK", 1740, 190)
    set_tbel_and_js(
        ack,
        r'''var response = msg;
msg = {
    lastCommandId: metadata.commandId,
    lastAck: response.success == false ? "REJECTED" : "EXECUTED",
    safetyBlockReason: response.reason == "SAFETY_BLOCK" ? "SAFETY_BLOCK" : "NONE"
};
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save_ack = cloned_node(field_nodes[1], "Save Valve RPC ACK", 1970, 190)
    reject = cloned_node(field_nodes[2], "Normalize RPC Failure", 1740, 315)
    set_tbel_and_js(
        reject,
        r'''msg = {lastCommandId: metadata.commandId, lastAck: "REJECTED", safetyBlockReason: "RPC_FAILURE"};
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save_reject = cloned_node(field_nodes[1], "Save Valve RPC Failure", 1970, 315)
    back_to_field_ack = cloned_node(field_nodes[13], "Back to Related Field - ACK", 1740, 440)
    back_to_field_ack["configuration"]["relationsQuery"]["direction"] = "TO"
    back_to_field_ack["configuration"]["relationsQuery"]["filters"] = [
        {"relationType": "FieldToSmartValve", "entityTypes": ["ASSET"]}
    ]
    normalize_field_ack = cloned_node(field_nodes[2], "Normalize Field State from RPC ACK", 1970, 440)
    set_tbel_and_js(
        normalize_field_ack,
        r'''var response = msg;
var action = metadata.commandAction;
var executed = response.success != false;
var actualState = response.state != null ? response.state : (action == "TURN_ON" ? "ON" : "OFF");
var newMsg = {};
newMsg.actualValveState = actualState;
newMsg.irrigationState = actualState;
newMsg.requestedIrrigationState = actualState;
newMsg.commandStatus = executed ? "EXECUTED" : "REJECTED";
newMsg.lastCommandId = response.commandId != null ? response.commandId : metadata.commandId;
newMsg.lastAck = executed ? "EXECUTED" : "REJECTED";
newMsg.valveSafetyBlockReason = response.reason == "SAFETY_BLOCK" ? "SAFETY_BLOCK" : "NONE";
if (executed && action == "TURN_ON") {
    newMsg.currentIrrigationWaterConsumption = 0;
    newMsg.irrigationStartedAt = Date.now();
}
return {msg: newMsg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save_field_ack = cloned_node(field_nodes[1], "Save Field State from RPC ACK", 2210, 440)
    back_to_field_failure = cloned_node(field_nodes[13], "Back to Related Field - Failure", 1740, 565)
    back_to_field_failure["configuration"]["relationsQuery"]["direction"] = "TO"
    back_to_field_failure["configuration"]["relationsQuery"]["filters"] = [
        {"relationType": "FieldToSmartValve", "entityTypes": ["ASSET"]}
    ]
    normalize_field_failure = cloned_node(field_nodes[2], "Normalize Field RPC Failure", 1970, 565)
    set_tbel_and_js(
        normalize_field_failure,
        r'''msg = {
    commandStatus: "FAILED",
    lastCommandId: metadata.commandId,
    lastAck: "REJECTED",
    valveSafetyBlockReason: "RPC_FAILURE"
};
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save_field_failure = cloned_node(field_nodes[1], "Save Field RPC Failure", 2210, 565)
    nodes = [
        fetch, evaluate, save, has_action, to_valve, build_rpc, send_rpc, ignore,
        ack, save_ack, reject, save_reject,
        back_to_field_ack, normalize_field_ack, save_field_ack,
        back_to_field_failure, normalize_field_failure, save_field_failure,
    ]
    return {
        "ruleChain": {
            "name": "SI Field Multivar Control v2.2",
            "type": "CORE",
            "firstRuleNodeId": None,
            "root": False,
            "debugMode": False,
            "configuration": None,
            "additionalInfo": {
                "description": (
                    "SRS-aligned cloud field decision with ACK-owned state transitions. This is the sole cloud "
                    "actuator command authority; actual Field state is reconciled from Smart Valve ACK/telemetry."
                )
            },
        },
        "metadata": {
            "version": 4,
            "firstNodeIndex": 0,
            "nodes": nodes,
            "connections": [
                {"fromIndex": 0, "toIndex": 1, "type": "Success"},
                {"fromIndex": 1, "toIndex": 2, "type": "Success"},
                {"fromIndex": 2, "toIndex": 3, "type": "Success"},
                {"fromIndex": 3, "toIndex": 4, "type": "True"},
                {"fromIndex": 3, "toIndex": 7, "type": "False"},
                {"fromIndex": 4, "toIndex": 5, "type": "Success"},
                {"fromIndex": 5, "toIndex": 6, "type": "Success"},
                {"fromIndex": 6, "toIndex": 8, "type": "Success"},
                {"fromIndex": 8, "toIndex": 9, "type": "Success"},
                {"fromIndex": 6, "toIndex": 10, "type": "Failure"},
                {"fromIndex": 10, "toIndex": 11, "type": "Success"},
                {"fromIndex": 6, "toIndex": 12, "type": "Success"},
                {"fromIndex": 12, "toIndex": 13, "type": "Success"},
                {"fromIndex": 13, "toIndex": 14, "type": "Success"},
                {"fromIndex": 6, "toIndex": 15, "type": "Failure"},
                {"fromIndex": 15, "toIndex": 16, "type": "Success"},
                {"fromIndex": 16, "toIndex": 17, "type": "Success"},
            ],
            "ruleChainConnections": None,
        },
    }


def clone_rule(name: str, new_name: str, description: str) -> dict[str, Any]:
    chain = load(name)
    chain["ruleChain"]["name"] = new_name
    set_description(chain, description)
    return chain


def build_count_chain() -> dict[str, Any]:
    chain = clone_rule("si_count_alarms.json", "SI Count Alarms v2.2", "Separate Critical, Major and Warning counters.")
    chain["metadata"]["nodes"][0]["configuration"]["alarmsCountMappings"] = [
        {
            "target": "criticalAlarmsCount",
            "typesList": [],
            "severityList": ["CRITICAL"],
            "statusList": ["ACTIVE_UNACK", "ACTIVE_ACK"],
        },
        {
            "target": "majorAlarmsCount",
            "typesList": [],
            "severityList": ["MAJOR", "MINOR", "INDETERMINATE"],
            "statusList": ["ACTIVE_UNACK", "ACTIVE_ACK"],
        },
        {
            "target": "warningAlarmsCount",
            "typesList": [],
            "severityList": ["WARNING"],
            "statusList": ["ACTIVE_UNACK", "ACTIVE_ACK"],
        },
    ]
    return chain


def build_environment_chain() -> dict[str, Any]:
    valve_nodes = load("si_smart_valve_rule_chain.json")["metadata"]["nodes"]
    soil_nodes = load("si_soil_moisture_rule_chain.json")["metadata"]["nodes"]
    profile = cloned_node(valve_nodes[6], "Device Profile Alarms", 70, 250)
    profile["configuration"]["persistAlarmRulesState"] = True
    profile["configuration"]["fetchAlarmRulesStateOnStart"] = True
    switch = cloned_node(valve_nodes[2], "Message Type Switch", 300, 250)
    validate = transform_template("Cloud Defensive Check + Calculate VPD", 535, 180)
    set_scripts(validate, ENV_QUALITY_TBEL, ENV_QUALITY_JS)
    save = cloned_node(valve_nodes[0], "Save Environment Timeseries", 770, 180)
    save_attributes = cloned_node(valve_nodes[1], "Save Client Attributes", 535, 360)
    to_field = cloned_node(soil_nodes[8], "To Field Asset", 1010, 180)
    to_field["configuration"]["relationsQuery"]["filters"][0]["relationType"] = "FieldToEnvSensor"
    field_chain = cloned_node(soil_nodes[11], "To Field Rule Chain", 1245, 180)
    field_chain["configuration"]["ruleChainId"] = None
    count = cloned_node(valve_nodes[7], "Count Alarms", 535, 500)
    count["configuration"]["ruleChainId"] = None
    other = cloned_node(valve_nodes[4], "Log Unsupported Message", 770, 420)
    nodes = [profile, switch, validate, save, save_attributes, to_field, field_chain, count, other]
    chain = {
        "ruleChain": {
            "name": "SI Environment v2.2",
            "type": "CORE",
            "firstRuleNodeId": None,
            "root": False,
            "debugMode": False,
            "configuration": None,
            "additionalInfo": {
                "description": (
                    "Minimal environment telemetry chain: defensive cloud validation, VPD calculation, "
                    "profile alarms and routing to the related Field. No actuator RPC belongs here."
                )
            },
        },
        "metadata": {
            "version": 4,
            "firstNodeIndex": 0,
            "nodes": nodes,
            "connections": [
                {"fromIndex": 0, "toIndex": 1, "type": "Success"},
                {"fromIndex": 0, "toIndex": 7, "type": "Alarm Created"},
                {"fromIndex": 0, "toIndex": 7, "type": "Alarm Updated"},
                {"fromIndex": 0, "toIndex": 7, "type": "Alarm Cleared"},
                {"fromIndex": 0, "toIndex": 7, "type": "Alarm Severity Updated"},
                {"fromIndex": 1, "toIndex": 2, "type": "Post telemetry"},
                {"fromIndex": 1, "toIndex": 4, "type": "Post attributes"},
                {"fromIndex": 1, "toIndex": 7, "type": "Alarm Acknowledged"},
                {"fromIndex": 1, "toIndex": 7, "type": "Alarm Cleared"},
                {"fromIndex": 1, "toIndex": 8, "type": "Other"},
                {"fromIndex": 2, "toIndex": 3, "type": "Success"},
                {"fromIndex": 3, "toIndex": 5, "type": "Success"},
                {"fromIndex": 5, "toIndex": 6, "type": "Success"},
            ],
            "ruleChainConnections": None,
        },
    }
    preserve_messages_on_profile_outputs(chain)
    clear_rule_chain_bindings(chain)
    return chain


def add_valve_field_reconciliation(chain: dict[str, Any]) -> None:
    """Propagate actual valve state back to the owning Field on every telemetry report."""
    metadata = chain["metadata"]
    field_nodes = load("si_field_rule_chain.json")["metadata"]["nodes"]
    has_state = cloned_node(field_nodes[11], "Has Actual Valve State?", 360, 430)
    set_tbel_and_js(
        has_state,
        'return msgType == "POST_TELEMETRY_REQUEST" && (msg.valveState == "ON" || msg.valveState == "OFF");',
    )
    to_field = cloned_node(field_nodes[13], "To Related Field - Actual State", 590, 430)
    to_field["configuration"]["relationsQuery"]["direction"] = "TO"
    to_field["configuration"]["relationsQuery"]["filters"] = [
        {"relationType": "FieldToSmartValve", "entityTypes": ["ASSET"]}
    ]
    normalize = cloned_node(field_nodes[2], "Normalize Field Actual Valve State", 830, 430)
    set_tbel_and_js(
        normalize,
        r'''var blockReason = msg.safetyBlockReason != null ? msg.safetyBlockReason : "NONE";
var ack = msg.lastAck != null ? msg.lastAck : "NONE";
var newMsg = {};
newMsg.actualValveState = msg.valveState;
newMsg.irrigationState = msg.valveState;
newMsg.commandStatus = ack == "EXECUTED" ? "EXECUTED" : "SYNCED";
newMsg.valveLastCommandId = msg.lastCommandId != null ? msg.lastCommandId : "";
newMsg.valveLastAck = ack;
newMsg.valveSafetyBlockReason = blockReason;
return {msg: newMsg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save = cloned_node(field_nodes[1], "Save Field Actual Valve State", 1070, 430)
    start = len(metadata["nodes"])
    metadata["nodes"].extend([has_state, to_field, normalize, save])
    metadata["connections"].extend(
        [
            {"fromIndex": metadata["firstNodeIndex"], "toIndex": start, "type": "Success"},
            {"fromIndex": start, "toIndex": start + 1, "type": "True"},
            {"fromIndex": start + 1, "toIndex": start + 2, "type": "Success"},
            {"fromIndex": start + 2, "toIndex": start + 3, "type": "Success"},
        ]
    )


def rule_variants(field_chain: dict[str, Any]) -> dict[str, dict[str, Any]]:
    soil = clone_rule("si_soil_moisture_rule_chain.json", "SI Soil Moisture v2.2", "Baseline aggregation plus a defensive cloud check; the authoritative quality gate remains at the gateway.")
    water = clone_rule("si_water_meter_rule_chain.json", "SI Water Meter v2.2", "Baseline pulse delta plus a defensive cloud check; branch safety remains at the gateway/controller.")
    valve = clone_rule("si_smart_valve_rule_chain.json", "SI Smart Valve v2.2", "Valve telemetry, two-way RPC ACK and safety rejection contract.")
    pump = clone_rule("si_smart_valve_rule_chain.json", "EXPERIMENTAL - SI Pump Control v2.2", "DO NOT IMPORT. Review artifact only; pump arbitration belongs to the edge scheduler/controller.")
    safety = clone_rule("si_smart_valve_rule_chain.json", "EXPERIMENTAL - SI Safety v2.2", "DO NOT IMPORT. Hard interlock, watchdog, dry-run and mode management belong to Pi/Central-Manifold.")
    prepend_quality_transform(soil, "Cloud Defensive Check - Soil", SOIL_QUALITY_TBEL, SOIL_QUALITY_JS)
    prepend_quality_transform(water, "Cloud Defensive Check - Water", WATER_QUALITY_TBEL, WATER_QUALITY_JS)
    prepend_quality_transform(valve, "Normalize Valve State + ACK", VALVE_QUALITY_TBEL, VALVE_QUALITY_JS)
    add_valve_field_reconciliation(valve)
    prepend_quality_transform(pump, "Experimental Pump Normalization", PUMP_QUALITY_TBEL, PUMP_QUALITY_JS)
    prepend_quality_transform(safety, "Experimental Safety Normalization", SAFETY_QUALITY_SCRIPT, SAFETY_QUALITY_SCRIPT)
    for active_chain in (soil, water, valve):
        preserve_messages_on_profile_outputs(active_chain)
        clear_rule_chain_bindings(active_chain)
    return {
        "si_field_v2_2.json": field_chain,
        "si_field_multivar_control_v2_2.json": build_multivar_chain(),
        "si_count_alarms_v2_2.json": build_count_chain(),
        "si_soil_moisture_v2_2.json": soil,
        "si_water_meter_v2_2.json": water,
        "si_smart_valve_v2_2.json": valve,
        "si_environment_v2_2.json": build_environment_chain(),
        "si_pump_control_v2_2.json": pump,
        "si_safety_v2_2.json": safety,
    }


def alarm_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "smartfarm-coreiot-v2.2:" + name))


def numeric_condition(key: str, operation: str, default: float, attribute: str | None = None) -> dict[str, Any]:
    dynamic = None
    if attribute:
        dynamic = {
            "sourceType": "CURRENT_DEVICE",
            "sourceAttribute": attribute,
            "inherit": False,
            "resolvedValue": None,
        }
    return {
        "key": {"type": "TIME_SERIES", "key": key},
        "valueType": "NUMERIC",
        "value": None,
        "predicate": {
            "type": "NUMERIC",
            "operation": operation,
            "value": {"defaultValue": default, "userValue": None, "dynamicValue": dynamic},
        },
    }


def boolean_condition(key: str, expected: bool) -> dict[str, Any]:
    return {
        "key": {"type": "TIME_SERIES", "key": key},
        "valueType": "BOOLEAN",
        "value": None,
        "predicate": {
            "type": "BOOLEAN",
            "operation": "EQUAL",
            "value": {"defaultValue": expected, "userValue": None, "dynamicValue": None},
        },
    }


def attribute_boolean_condition(key: str, expected: bool) -> dict[str, Any]:
    condition = boolean_condition(key, expected)
    condition["key"]["type"] = "ATTRIBUTE"
    return condition


def alarm_rule(
    alarm_type: str,
    severity: str,
    create_conditions: list[dict[str, Any]],
    clear_conditions: list[dict[str, Any]],
    relation: str | None = None,
    debounce_seconds: int = 15,
) -> dict[str, Any]:
    def rule(conditions: list[dict[str, Any]], debounce: bool) -> dict[str, Any]:
        spec: dict[str, Any] = {"type": "SIMPLE"}
        if debounce:
            spec = {
                "type": "DURATION",
                "unit": "SECONDS",
                "predicate": {
                    "type": "NUMERIC",
                    "operation": "GREATER_OR_EQUAL",
                    "value": {"defaultValue": debounce_seconds, "userValue": None, "dynamicValue": None},
                },
            }
        return {
            "condition": {"condition": conditions, "spec": spec},
            "schedule": None,
            "alarmDetails": None,
            "dashboardId": None,
        }

    return {
        "id": alarm_id(alarm_type),
        "alarmType": alarm_type,
        "createRules": {severity: rule(create_conditions, True)},
        "clearRule": rule(clear_conditions, False),
        "propagate": relation is not None,
        "propagateToOwner": False,
        "propagateToOwnerHierarchy": False,
        "propagateToTenant": False,
        "propagateRelationTypes": [relation] if relation else [],
    }


def standardize_low_battery(profile: dict[str, Any]) -> None:
    for alarm in profile.get("profileData", {}).get("alarms", []):
        if alarm.get("alarmType") != "Low Battery":
            continue
        create = alarm["createRules"]["WARNING"]["condition"]["condition"][0]
        create["predicate"]["operation"] = "LESS_OR_EQUAL"
        create["predicate"]["value"]["defaultValue"] = 30
        create["predicate"]["value"]["dynamicValue"]["sourceAttribute"] = "lowBatteryThreshold"
        clear = alarm["clearRule"]["condition"]["condition"][0]
        clear["predicate"]["operation"] = "GREATER_OR_EQUAL"
        clear["predicate"]["value"]["defaultValue"] = 35
        clear["predicate"]["value"]["dynamicValue"]["sourceAttribute"] = "lowBatteryClearThreshold"
        alarm["id"] = alarm_id(profile["name"] + ":Low Battery")
        alarm["createRules"]["WARNING"]["condition"]["spec"] = {
            "type": "DURATION",
            "unit": "SECONDS",
            "predicate": {
                "type": "NUMERIC",
                "operation": "GREATER_OR_EQUAL",
                "value": {"defaultValue": 10, "userValue": None, "dynamicValue": None},
            },
        }


def add_node_offline(profile: dict[str, Any], relation: str | None = None) -> None:
    alarms = profile.setdefault("profileData", {}).setdefault("alarms", [])
    if any(alarm.get("alarmType") == "NodeOffline" for alarm in alarms):
        return
    alarms.append(
        alarm_rule(
            "NodeOffline",
            "MAJOR",
            [attribute_boolean_condition("active", False)],
            [attribute_boolean_condition("active", True)],
            relation,
            debounce_seconds=300,
        )
    )


def ensure_alarm_debounce(profile: dict[str, Any], seconds: int = 15) -> None:
    for alarm in profile.get("profileData", {}).get("alarms", []):
        for create_rule in alarm.get("createRules", {}).values():
            condition = create_rule.get("condition") or {}
            spec = condition.get("spec") or {}
            if spec.get("type") == "SIMPLE":
                condition["spec"] = {
                    "type": "DURATION",
                    "unit": "SECONDS",
                    "predicate": {
                        "type": "NUMERIC",
                        "operation": "GREATER_OR_EQUAL",
                        "value": {"defaultValue": seconds, "userValue": None, "dynamicValue": None},
                    },
                }


def clean_profile(profile: dict[str, Any], name: str) -> dict[str, Any]:
    profile = copy.deepcopy(profile)
    profile["name"] = name
    profile["defaultRuleChainId"] = None
    profile["defaultDashboardId"] = None
    profile["defaultEdgeRuleChainId"] = None
    profile["firmwareId"] = None
    profile["softwareId"] = None
    profile["provisionDeviceKey"] = None
    return profile


def build_profiles() -> dict[str, dict[str, Any]]:
    soil = clean_profile(load("si_soil_moisture_device_profile.json"), "SI Soil Moisture Sensor v2.2")
    standardize_low_battery(soil)
    soil["profileData"]["alarms"].append(
        alarm_rule(
            "WaterloggingRisk",
            "CRITICAL",
            [numeric_condition("moisture", "GREATER_OR_EQUAL", 85, "floodMoistureThreshold")],
            [numeric_condition("moisture", "LESS", 70, "maxMoistureThreshold")],
            "FieldToMoistureSensor",
            debounce_seconds=60,
        )
    )
    add_node_offline(soil, "FieldToMoistureSensor")

    valve = clean_profile(load("si_smart_valve_device_profile.json"), "SI Smart Valve v2.2")
    standardize_low_battery(valve)
    valve["profileData"]["alarms"].append(
        alarm_rule("ValveLeak", "MAJOR", [boolean_condition("valveLeak", True)], [boolean_condition("valveLeak", False)], "FieldToSmartValve")
    )
    add_node_offline(valve, "FieldToSmartValve")
    water = clean_profile(load("si_water_meter_device_profile.json"), "SI Water Meter v2.2")
    standardize_low_battery(water)
    water["profileData"]["alarms"].append(
        alarm_rule("ZoneFlowLow", "MAJOR", [boolean_condition("zoneFlowLow", True)], [boolean_condition("zoneFlowLow", False)], "FieldToWaterMeter")
    )
    add_node_offline(water, "FieldToWaterMeter")

    env = clean_profile(load("si_soil_moisture_device_profile.json"), "SF Env Sensor Cluster")
    env["description"] = "Per-field environment telemetry: airTemp, airHumidity, lightLux, vpd, battery and rssi. Tank telemetry belongs to the site manifold."
    low_battery = next(a for a in env["profileData"]["alarms"] if a["alarmType"] == "Low Battery")
    env["profileData"]["alarms"] = [
        alarm_rule(
            "HighTemperature",
            "MAJOR",
            [numeric_condition("airTemp", "GREATER_OR_EQUAL", 35)],
            [numeric_condition("airTemp", "LESS_OR_EQUAL", 33)],
            "FieldToEnvSensor",
            debounce_seconds=300,
        ),
        low_battery,
    ]
    standardize_low_battery(env)
    add_node_offline(env, "FieldToEnvSensor")

    pump = clean_profile(load("si_smart_valve_device_profile.json"), "SF Pump Controller")
    pump["description"] = "Main pump state, runtime, two-way RPC ACK and safety flags."
    pump["profileData"]["alarms"] = [
        alarm_rule("PumpDryRun", "CRITICAL", [boolean_condition("pumpDryRun", True)], [boolean_condition("pumpDryRun", False)]),
        alarm_rule("IrrigationTimeout", "MAJOR", [boolean_condition("irrigationTimeout", True)], [boolean_condition("irrigationTimeout", False)]),
    ]
    add_node_offline(pump, "SiteToPump")

    manifold = clean_profile(load("si_smart_valve_device_profile.json"), "SF Manifold Controller")
    manifold["description"] = "Tank, branch-flow and controller safety telemetry."
    manifold["profileData"]["alarms"] = [
        alarm_rule("TankLow", "CRITICAL", [boolean_condition("tankLowSwitch", True)], [boolean_condition("tankLowSwitch", False)]),
        alarm_rule("ZoneFlowLow", "MAJOR", [boolean_condition("zoneFlowLow", True)], [boolean_condition("zoneFlowLow", False)]),
        alarm_rule("ValveLeak", "MAJOR", [boolean_condition("valveLeak", True)], [boolean_condition("valveLeak", False)]),
        alarm_rule("WaterloggingRisk", "CRITICAL", [boolean_condition("waterloggingRisk", True)], [boolean_condition("waterloggingRisk", False)]),
    ]
    add_node_offline(manifold, "SiteToManifold")

    gateway = clean_profile(load("si_smart_valve_device_profile.json"), "SF Gateway")
    gateway["description"] = "CoreIoT Gateway API endpoint and health telemetry."
    gateway["profileData"]["alarms"] = []
    add_node_offline(gateway, "SiteToGateway")

    field_asset = load("si_field_asset_profile.json")
    field_asset["name"] = "SI Field v2.2"
    field_asset["description"] = "Smart Irrigation Field with multivariable control and safety status."
    field_asset["defaultRuleChainId"] = None
    field_asset["defaultDashboardId"] = None
    field_asset["defaultEdgeRuleChainId"] = None

    site_asset = copy.deepcopy(field_asset)
    site_asset["name"] = "SF Site"
    site_asset["description"] = "Top-level SmartFarm site for fields, gateway, pump and manifold relations."

    for profile in (soil, valve, water, env, pump, manifold, gateway):
        ensure_alarm_debounce(profile)

    return {
        "si_soil_moisture_sensor_v2_2.json": soil,
        "si_smart_valve_v2_2.json": valve,
        "si_water_meter_v2_2.json": water,
        "sf_env_sensor_cluster.json": env,
        "sf_pump_controller.json": pump,
        "sf_manifold_controller.json": manifold,
        "sf_gateway.json": gateway,
        "si_field_asset_profile_v2_2.json": field_asset,
        "sf_site_asset_profile.json": site_asset,
    }


def main() -> None:
    field_chain = build_field_chain()
    for filename, artifact in rule_variants(field_chain).items():
        write(RULES / filename, artifact)
    for filename, artifact in build_profiles().items():
        write(PROFILES / filename, artifact)
    print(
        f"rule_chains={len(list(RULES.glob('*.json')))} "
        f"(active=7 experimental=2) profiles={len(list(PROFILES.glob('*.json')))} dashboard=DEFERRED"
    )


if __name__ == "__main__":
    main()
