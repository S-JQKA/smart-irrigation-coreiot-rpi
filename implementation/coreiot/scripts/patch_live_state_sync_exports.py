#!/usr/bin/env python3
"""Patch live CoreIoT exports for ACK-owned Field/Valve state reconciliation."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import generate_v22_artifacts as gen


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def node_index(chain: dict[str, Any], name: str) -> int:
    return next(i for i, node in enumerate(chain["metadata"]["nodes"]) if node["name"] == name)


def clone_named(chain: dict[str, Any], name: str, new_name: str, x: int, y: int) -> dict[str, Any]:
    node = copy.deepcopy(chain["metadata"]["nodes"][node_index(chain, name)])
    node["name"] = new_name
    node.setdefault("additionalInfo", {})["layoutX"] = x
    node.setdefault("additionalInfo", {})["layoutY"] = y
    return node


def patch_field(chain: dict[str, Any]) -> dict[str, Any]:
    chain = copy.deepcopy(chain)
    names = [node["name"] for node in chain["metadata"]["nodes"]]
    required = {"SwitchEventType", "Save telemetry", "To Multivar Control"}
    if not required.issubset(names):
        raise ValueError("SI Field export does not match the expected live chain")
    remove_names = {
        "To Irrigation Start", "IsMsgFromWaterMeter", "Start Irrigation Using Volume",
        "Start Irrigation", "To Turn On RPC call", "calculateIrrigationWaterConsumption",
        "Fetch Task", "Should Turn Off?", "Stop Irrigation", "To Turn Off RPC call",
        "To stopped state", "Update state", "Fetch Irrigation State", "Is Irrigation On?",
        "Ignore",
    }
    removed = {i for i, name in enumerate(names) if name in remove_names}
    # Both legacy originator nodes and both legacy save nodes share names.
    removed.update(i for i, name in enumerate(names) if name in {"To Smart Valve"})
    for i, name in enumerate(names):
        if name == "Save Timeseries" and 2 <= i <= 19:
            removed.add(i)
    gen.remap_without_nodes(chain, removed)
    chain["ruleChain"]["name"] = "SI Field v2.2.2 State Sync"
    multivar_node = chain["metadata"]["nodes"][node_index(chain, "To Multivar Control")]
    multivar_node["configuration"]["ruleChainId"] = None
    multivar_node.setdefault("additionalInfo", {})["description"] = (
        "POST-IMPORT BINDING REQUIRED: SI Field Multivar Control v2.2.2 State Sync"
    )
    chain["ruleChain"].setdefault("additionalInfo", {})["description"] = (
        "SmartFarm v2.2 Field telemetry and attribute routing. Legacy direct Start/Stop RPC paths are removed; "
        "SI Field Multivar Control is the sole cloud actuator command authority."
    )
    return chain


def back_to_field(source: dict[str, Any], name: str, x: int, y: int) -> dict[str, Any]:
    node = copy.deepcopy(source)
    node["name"] = name
    node.setdefault("additionalInfo", {})["layoutX"] = x
    node.setdefault("additionalInfo", {})["layoutY"] = y
    node["configuration"]["relationsQuery"]["direction"] = "TO"
    node["configuration"]["relationsQuery"]["filters"] = [
        {"relationType": "FieldToSmartValve", "entityTypes": ["ASSET"]}
    ]
    return node


def patch_multivar(chain: dict[str, Any]) -> dict[str, Any]:
    chain = copy.deepcopy(chain)
    nodes = chain["metadata"]["nodes"]
    names = {node["name"] for node in nodes}
    if "Normalize Field State from RPC ACK" in names:
        raise ValueError("Multivar export is already patched")
    chain["ruleChain"]["name"] = "SI Field Multivar Control v2.2.2 State Sync"
    fetch = nodes[node_index(chain, "Fetch Field Context")]
    fetch["name"] = "Fetch v2.2 Field Context"
    fetch["configuration"] = copy.deepcopy(gen.FIELD_FETCH_CONFIG)
    evaluate = nodes[node_index(chain, "Evaluate SRS-aligned Field Decision")]
    gen.set_tbel_and_js(evaluate, gen.EVALUATE_DECISION_SCRIPT)
    build_rpc = nodes[node_index(chain, "Build Guarded RPC")]
    gen.set_tbel_and_js(build_rpc, gen.BUILD_RPC_SCRIPT)

    to_valve = nodes[node_index(chain, "To Related Smart Valve")]
    transform_template = nodes[node_index(chain, "Normalize RPC ACK")]
    save_template = nodes[node_index(chain, "Save Valve RPC ACK")]
    send_index = node_index(chain, "Send Two-way RPC")

    ack_to_field = back_to_field(to_valve, "Back to Related Field - ACK", 1740, 440)
    normalize_ack = copy.deepcopy(transform_template)
    normalize_ack["name"] = "Normalize Field State from RPC ACK"
    normalize_ack.setdefault("additionalInfo", {}).update({"layoutX": 1970, "layoutY": 440})
    gen.set_tbel_and_js(
        normalize_ack,
        '''var response = msg;
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
    save_ack = copy.deepcopy(save_template)
    save_ack["name"] = "Save Field State from RPC ACK"
    save_ack.setdefault("additionalInfo", {}).update({"layoutX": 2210, "layoutY": 440})

    failure_to_field = back_to_field(to_valve, "Back to Related Field - Failure", 1740, 565)
    normalize_failure = copy.deepcopy(transform_template)
    normalize_failure["name"] = "Normalize Field RPC Failure"
    normalize_failure.setdefault("additionalInfo", {}).update({"layoutX": 1970, "layoutY": 565})
    gen.set_tbel_and_js(
        normalize_failure,
        '''msg = {
    commandStatus: "FAILED",
    lastCommandId: metadata.commandId,
    lastAck: "REJECTED",
    valveSafetyBlockReason: "RPC_FAILURE"
};
return {msg: msg, metadata: metadata, msgType: "POST_TELEMETRY_REQUEST"};''',
    )
    save_failure = copy.deepcopy(save_template)
    save_failure["name"] = "Save Field RPC Failure"
    save_failure.setdefault("additionalInfo", {}).update({"layoutX": 2210, "layoutY": 565})

    start = len(nodes)
    nodes.extend([ack_to_field, normalize_ack, save_ack, failure_to_field, normalize_failure, save_failure])
    chain["metadata"]["connections"].extend(
        [
            {"fromIndex": send_index, "toIndex": start, "type": "Success"},
            {"fromIndex": start, "toIndex": start + 1, "type": "Success"},
            {"fromIndex": start + 1, "toIndex": start + 2, "type": "Success"},
            {"fromIndex": send_index, "toIndex": start + 3, "type": "Failure"},
            {"fromIndex": start + 3, "toIndex": start + 4, "type": "Success"},
            {"fromIndex": start + 4, "toIndex": start + 5, "type": "Success"},
        ]
    )
    chain["ruleChain"].setdefault("additionalInfo", {})["description"] = (
        "SRS-aligned cloud field decision with ACK-owned state transitions. This is the sole cloud actuator "
        "command authority; actual Field state is reconciled from Smart Valve ACK/telemetry."
    )
    return chain


def patch_valve(chain: dict[str, Any], multivar: dict[str, Any]) -> dict[str, Any]:
    chain = copy.deepcopy(chain)
    if any(node["name"] == "Normalize Field Actual Valve State" for node in chain["metadata"]["nodes"]):
        raise ValueError("Smart Valve export is already patched")
    chain["ruleChain"]["name"] = "SI Smart Valve v2.2.2 State Sync"
    has_state = clone_named(multivar, "Has TURN_ON/TURN_OFF?", "Has Actual Valve State?", 360, 430)
    gen.set_tbel_and_js(
        has_state,
        'return msgType == "POST_TELEMETRY_REQUEST" && (msg.valveState == "ON" || msg.valveState == "OFF");',
    )
    to_field = back_to_field(
        multivar["metadata"]["nodes"][node_index(multivar, "To Related Smart Valve")],
        "To Related Field - Actual State",
        590,
        430,
    )
    normalize = clone_named(
        multivar, "Normalize RPC ACK", "Normalize Field Actual Valve State", 830, 430
    )
    gen.set_tbel_and_js(
        normalize,
        '''var blockReason = msg.safetyBlockReason != null ? msg.safetyBlockReason : "NONE";
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
    save = clone_named(multivar, "Save Valve RPC ACK", "Save Field Actual Valve State", 1070, 430)
    start = len(chain["metadata"]["nodes"])
    chain["metadata"]["nodes"].extend([has_state, to_field, normalize, save])
    chain["metadata"]["connections"].extend(
        [
            {"fromIndex": chain["metadata"]["firstNodeIndex"], "toIndex": start, "type": "Success"},
            {"fromIndex": start, "toIndex": start + 1, "type": "True"},
            {"fromIndex": start + 1, "toIndex": start + 2, "type": "Success"},
            {"fromIndex": start + 2, "toIndex": start + 3, "type": "Success"},
        ]
    )
    chain["ruleChain"].setdefault("additionalInfo", {})["description"] = (
        "Valve telemetry and two-way RPC ACK with actual state reconciliation back to the owning Field."
    )
    return chain


def validate(chain: dict[str, Any]) -> None:
    count = len(chain["metadata"]["nodes"])
    first = chain["metadata"]["firstNodeIndex"]
    if not 0 <= first < count:
        raise ValueError("invalid firstNodeIndex")
    for connection in chain["metadata"]["connections"]:
        if not 0 <= connection["fromIndex"] < count or not 0 <= connection["toIndex"] < count:
            raise ValueError(f"invalid connection: {connection}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--multivar", type=Path, required=True)
    parser.add_argument("--field", type=Path, required=True)
    parser.add_argument("--valve", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_multivar = load(args.multivar)
    fixed = {
        "si_field_multivar_control_fixed.json": patch_multivar(source_multivar),
        "si_field_fixed.json": patch_field(load(args.field)),
        "si_smart_valve_fixed.json": patch_valve(load(args.valve), source_multivar),
    }
    for name, chain in fixed.items():
        validate(chain)
        write(args.output / name, chain)
        print(f"wrote={args.output / name} nodes={len(chain['metadata']['nodes'])}")


if __name__ == "__main__":
    main()
