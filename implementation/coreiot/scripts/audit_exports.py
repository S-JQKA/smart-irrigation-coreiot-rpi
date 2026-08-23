#!/usr/bin/env python3
"""Inventory and classify exported CoreIoT JSON artifacts.

The raw exports are references and rollback inputs. The classifications in this
script describe the v2.2 migration intent; they do not imply that a baseline
artifact must remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_kind(data: dict[str, Any]) -> str:
    if "ruleChain" in data and "metadata" in data:
        return "rule_chain"
    if "profileData" in data:
        return "device_profile"
    if "configuration" in data and "resources" in data:
        return "dashboard"
    if "defaultRuleChainId" in data:
        return "asset_profile"
    return "unknown"


def short_type(node: dict[str, Any]) -> str:
    return str(node.get("type", "")).rsplit(".", 1)[-1]


def classify_rule_node(chain: str, node: dict[str, Any]) -> tuple[str, str]:
    name = str(node.get("name", ""))
    node_type = short_type(node)
    if "Generator" in name or node_type == "TbMsgGeneratorNode":
        return "REMOVE", "Simulator ngoài platform sẽ phát telemetry; tránh dữ liệu trùng."
    if chain == "Root Rule Chain":
        return "KEEP", "Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu."
    if chain == "SI Count Alarms":
        return "MODIFY", "Mở rộng đếm Warning và alarm an toàn v2.2."
    if chain == "SI Field":
        return "MODIFY", "Refactor vào orchestration v2.2 với mode, safety, target và quota."
    if chain == "SI Soil Moisture":
        if node_type in {"TbSimpleAggMsgNode", "TbAggLatestTelemetryNodeV2", "TbChangeOriginatorNode"}:
            return "KEEP", "Tái sử dụng aggregation/relation lõi."
        return "MODIFY", "Bổ sung dataQuality và contract telemetry v2.2."
    if chain == "SI Water Meter":
        if node_type in {"CalculateDeltaNode", "TbChangeOriginatorNode"}:
            return "KEEP", "Tái sử dụng delta và mapping waterConsumption."
        return "MODIFY", "Bổ sung flowRate, quality và safety event."
    if chain == "SI Smart Valve":
        return "MODIFY", "Bổ sung valveState, command ACK và SAFETY_BLOCK."
    return "REVIEW", "Chưa có quy tắc migration chuyên biệt."


def profile_summary(data: dict[str, Any]) -> dict[str, Any]:
    alarms = []
    for alarm in data.get("profileData", {}).get("alarms", []):
        alarms.append(
            {
                "type": alarm.get("alarmType"),
                "severities": list((alarm.get("createRules") or {}).keys()),
                "hasClearRule": alarm.get("clearRule") is not None,
                "propagate": bool(alarm.get("propagate")),
                "relations": alarm.get("propagateRelationTypes") or [],
            }
        )
    return {
        "name": data.get("name"),
        "decision": "MODIFY",
        "alarms": alarms,
        "hasCloudRuleChain": bool(data.get("defaultRuleChainId")),
        "hasEdgeRuleChain": bool(data.get("defaultEdgeRuleChainId")),
    }


def dashboard_summary(data: dict[str, Any]) -> dict[str, Any]:
    config = data.get("configuration") or {}
    states = []
    for state_id, state in (config.get("states") or {}).items():
        states.append(
            {
                "id": state_id,
                "name": state.get("name"),
                "decision": "MODIFY",
                "reason": "Giữ hành vi hữu ích nhưng cho phép gộp/xóa/đổi layout và alias.",
            }
        )
    widgets = []
    for widget_id, widget in (config.get("widgets") or {}).items():
        cfg = widget.get("config") or {}
        keys = []
        for datasource in cfg.get("datasources") or []:
            for key in datasource.get("dataKeys") or []:
                if key.get("name"):
                    keys.append(key["name"])
        widgets.append(
            {
                "id": widget_id,
                "title": cfg.get("title"),
                "type": widget.get("typeFullFqn"),
                "keys": sorted(set(keys)),
                "decision": "MODIFY",
            }
        )
    aliases = [
        {"id": alias_id, "alias": alias.get("alias"), "filterType": (alias.get("filter") or {}).get("type")}
        for alias_id, alias in (config.get("entityAliases") or {}).items()
    ]
    return {
        "name": data.get("name") or data.get("title"),
        "decision": "MODIFY",
        "states": states,
        "widgets": widgets,
        "aliases": aliases,
    }


def build_inventory(export_dir: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(export_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        kind = artifact_kind(data)
        record: dict[str, Any] = {
            "file": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "kind": kind,
        }
        if kind == "rule_chain":
            chain_name = data.get("ruleChain", {}).get("name")
            nodes = []
            for index, node in enumerate(data.get("metadata", {}).get("nodes") or []):
                decision, reason = classify_rule_node(str(chain_name), node)
                nodes.append(
                    {
                        "index": index,
                        "name": node.get("name"),
                        "type": short_type(node),
                        "decision": decision,
                        "reason": reason,
                    }
                )
            record.update(
                {
                    "name": chain_name,
                    "nodes": nodes,
                    "nodeCount": len(nodes),
                    "connectionCount": len(data.get("metadata", {}).get("connections") or []),
                }
            )
        elif kind == "device_profile":
            record.update(profile_summary(data))
        elif kind == "asset_profile":
            record.update(
                {
                    "name": data.get("name"),
                    "decision": "MODIFY",
                    "hasCloudRuleChain": bool(data.get("defaultRuleChainId")),
                    "hasEdgeRuleChain": bool(data.get("defaultEdgeRuleChainId")),
                }
            )
        elif kind == "dashboard":
            record.update(dashboard_summary(data))
        artifacts.append(record)
    return {
        "schemaVersion": "1.0",
        "purpose": "CoreIoT v2.2 reference inventory and migration classification",
        "artifactCount": len(artifacts),
        "artifacts": artifacts,
    }


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# CoreIoT baseline inventory — 2026-08-03",
        "",
        "> Raw exports are rollback references. KEEP/MODIFY/REPLACE/REMOVE applies to the v2.2 target configuration.",
        "",
        "## Artifact summary",
        "",
        "| File | Kind | Name | SHA-256 |",
        "|---|---|---|---|",
    ]
    for artifact in inventory["artifacts"]:
        lines.append(
            f"| `{artifact['file']}` | {artifact['kind']} | {artifact.get('name', '')} | `{artifact['sha256']}` |"
        )
    for artifact in inventory["artifacts"]:
        if artifact["kind"] != "rule_chain":
            continue
        lines.extend(
            [
                "",
                f"## {artifact['name']}",
                "",
                f"Nodes: {artifact['nodeCount']}; connections: {artifact['connectionCount']}.",
                "",
                "| # | Node | Type | Decision | Reason |",
                "|---:|---|---|---|---|",
            ]
        )
        for node in artifact["nodes"]:
            lines.append(
                f"| {node['index']} | {node['name']} | `{node['type']}` | **{node['decision']}** | {node['reason']} |"
            )
    dashboard = next((a for a in inventory["artifacts"] if a["kind"] == "dashboard"), None)
    if dashboard:
        lines.extend(
            [
                "",
                "## Dashboard",
                "",
                f"Reference dashboard contains {len(dashboard['states'])} states, {len(dashboard['widgets'])} widgets and {len(dashboard['aliases'])} aliases. All are marked MODIFY so v2.2 may retain, merge, replace or remove them after review.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()

    inventory = build_inventory(args.export_dir)
    args.json_output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_markdown(inventory), encoding="utf-8")
    print(f"audited={inventory['artifactCount']}")


if __name__ == "__main__":
    main()
