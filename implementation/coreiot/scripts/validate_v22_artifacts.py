#!/usr/bin/env python3
"""Static validation for generated CoreIoT v2.2 artifacts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "v2.2"
SECRET_KEY = re.compile(r"(token|secret|password|credentialsValue)", re.IGNORECASE)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nonempty_secrets(value: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if SECRET_KEY.search(key) and bool(child):
                hits.append(path)
            hits.extend(nonempty_secrets(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(nonempty_secrets(child, f"{prefix}[{index}]"))
    return hits


def validate_rule_chain(path: Path, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata = data.get("metadata") or {}
    nodes = metadata.get("nodes") or []
    connections = metadata.get("connections") or []
    name = (data.get("ruleChain") or {}).get("name")
    first = metadata.get("firstNodeIndex")
    if not name:
        errors.append("missing rule-chain name")
    if not nodes:
        errors.append("rule chain has no nodes")
    if not isinstance(first, int) or first < 0 or first >= len(nodes):
        errors.append(f"invalid firstNodeIndex={first}")
    for index, connection in enumerate(connections):
        for endpoint in ("fromIndex", "toIndex"):
            value = connection.get(endpoint)
            if not isinstance(value, int) or value < 0 or value >= len(nodes):
                errors.append(f"connection[{index}] invalid {endpoint}={value}")
    if str(name).startswith("EXPERIMENTAL -"):
        if "DO NOT IMPORT" not in str((data.get("ruleChain") or {}).get("additionalInfo", {}).get("description", "")):
            errors.append("experimental rule chain must be marked DO NOT IMPORT")
        return errors, warnings
    if name == "SI Field v2.2":
        generators = [node.get("name") for node in nodes if "Generator" in str(node.get("name"))]
        if generators:
            errors.append(f"generator nodes remain: {generators}")
        present = {str(node.get("name")) for node in nodes}
        if "To Multivar Control" not in present:
            errors.append("SI Field must delegate exactly once to SI Field Multivar Control")
        if any("Evaluate" in node_name and "Multivar" in node_name for node_name in present):
            errors.append("multivariable decision must not be duplicated inside SI Field")
    if name == "SI Field Multivar Control v2.2":
        scripts = json.dumps(nodes, ensure_ascii=False)
        required = (
            "criticalMoisture", "minMoistureThreshold", "targetMoisture",
            "maxMoistureThreshold", "floodMoistureThreshold", "maxWaterPerCycle",
            "maxWaterPerDay", "maxDurationSec", "airTemp", "vpd", "lightLux",
            "controlMode", "systemMode", "tankLowSwitch", "irrigationPriority",
            "TURN_ON", "TURN_OFF", "ttlSeconds",
        )
        for key in required:
            if key not in scripts:
                errors.append(f"multivar decision missing {key}")
        if "effectiveMin" in scripts or "BELOW_ENV_ADJUSTED_MIN" in scripts:
            errors.append("ad-hoc effectiveMin algorithm from the pre-review draft remains")
    expected_quality_node = {
        "SI Soil Moisture v2.2": "Cloud Defensive Check - Soil",
        "SI Water Meter v2.2": "Cloud Defensive Check - Water",
        "SI Smart Valve v2.2": "Normalize Valve State + ACK",
        "SI Environment v2.2": "Cloud Defensive Check + Calculate VPD",
    }.get(name)
    if expected_quality_node and expected_quality_node not in {str(node.get("name")) for node in nodes}:
        errors.append(f"missing quality node {expected_quality_node}")
    if name == "SI Environment v2.2":
        present = {str(node.get("name")) for node in nodes}
        if not {"To Field Asset", "To Field Rule Chain"}.issubset(present):
            errors.append("environment telemetry is not routed to Field")
        if any("RPC" in node_name for node_name in present):
            errors.append("environment chain must not contain actuator RPC nodes")
    if name == "SI Count Alarms v2.2":
        mappings = ((nodes[0].get("configuration") or {}).get("alarmsCountMappings") or []) if nodes else []
        targets = {mapping.get("target") for mapping in mappings}
        if targets != {"criticalAlarmsCount", "majorAlarmsCount", "warningAlarmsCount"}:
            errors.append(f"alarm counter targets incorrect: {sorted(str(target) for target in targets)}")
    for node in nodes:
        if str(node.get("type", "")).endswith("TbDeviceProfileNode"):
            node_config = node.get("configuration") or {}
            if not node_config.get("persistAlarmRulesState") or not node_config.get("fetchAlarmRulesStateOnStart"):
                errors.append(f"Device Profile Node must persist/fetch debounce state in {name}")
    for index, node in enumerate(nodes):
        if str(node.get("type", "")).endswith("TbRuleChainInputNode"):
            warnings.append(f"node[{index}] {node.get('name')} requires post-import binding by exact v2.2 name")
    return errors, warnings


def validate_profile(path: Path, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not data.get("name"):
        errors.append("missing profile name")
    alarms = (data.get("profileData") or {}).get("alarms") or []
    alarm_names = {alarm.get("alarmType") for alarm in alarms}
    profile_name = data.get("name")
    expected_by_profile = {
        "SI Soil Moisture Sensor v2.2": {"Low Battery", "Low Moisture Level", "High Moisture Level", "WaterloggingRisk", "NodeOffline"},
        "SI Smart Valve v2.2": {"Low Battery", "ValveLeak", "NodeOffline"},
        "SI Water Meter v2.2": {"Low Battery", "ZoneFlowLow", "NodeOffline"},
        "SF Env Sensor Cluster": {"Low Battery", "HighTemperature", "NodeOffline"},
        "SF Pump Controller": {"PumpDryRun", "IrrigationTimeout", "NodeOffline"},
        "SF Manifold Controller": {"TankLow", "ZoneFlowLow", "ValveLeak", "WaterloggingRisk", "NodeOffline"},
        "SF Gateway": {"NodeOffline"},
    }
    missing_alarms = expected_by_profile.get(profile_name, set()) - alarm_names
    if missing_alarms:
        errors.append(f"profile {profile_name} missing alarms: {sorted(missing_alarms)}")
    for alarm in alarms:
        if not alarm.get("alarmType"):
            errors.append("alarm missing alarmType")
        if not alarm.get("createRules"):
            errors.append(f"alarm {alarm.get('alarmType')} has no createRules")
        if not alarm.get("clearRule"):
            errors.append(f"alarm {alarm.get('alarmType')} has no clearRule")
        for severity, create_rule in (alarm.get("createRules") or {}).items():
            spec = ((create_rule.get("condition") or {}).get("spec") or {})
            if spec.get("type") not in {"DURATION", "REPEATING"}:
                errors.append(f"alarm {alarm.get('alarmType')} severity {severity} has no debounce")
        if alarm.get("alarmType") == "Low Battery":
            create_rule = next(iter((alarm.get("createRules") or {}).values()), {})
            create_filters = ((create_rule.get("condition") or {}).get("condition") or [])
            clear_filters = (((alarm.get("clearRule") or {}).get("condition") or {}).get("condition") or [])
            if not create_filters or create_filters[0]["predicate"]["value"].get("defaultValue") != 30:
                errors.append("Low Battery create threshold must be 30")
            if not clear_filters or clear_filters[0]["predicate"]["value"].get("defaultValue") != 35:
                errors.append("Low Battery clear threshold must be 35")
    if data.get("defaultRuleChainId") is not None:
        warnings.append("defaultRuleChainId should be rebound by name after import")
    return errors, warnings


def validate_dashboard(path: Path, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    config = data.get("configuration") or {}
    states = config.get("states") or {}
    widgets = config.get("widgets") or {}
    aliases = config.get("entityAliases") or {}
    required_states = {"default", "field_detail", "control", "safety", "gateway", "alarm", "scheduler"}
    missing_states = sorted(required_states - set(states))
    if missing_states:
        errors.append(f"missing dashboard states: {missing_states}")
    obsolete_states = {"setup_field_polygon", "setup_sensor_location", "moisture_sensor_details"} & set(states)
    if obsolete_states:
        errors.append(f"obsolete template states remain: {sorted(obsolete_states)}")
    referenced: set[str] = set()
    for state_id, state in states.items():
        state_widgets = (((state.get("layouts") or {}).get("main") or {}).get("widgets") or {})
        if not state_widgets:
            errors.append(f"state {state_id} has no widgets")
        referenced.update(state_widgets)
    missing_widgets = sorted(referenced - set(widgets))
    if missing_widgets:
        errors.append(f"states reference missing widgets: {missing_widgets}")
    unreferenced = sorted(set(widgets) - referenced)
    if unreferenced:
        warnings.append(f"unreferenced widgets: {unreferenced}")
    alias_ids = set(aliases)
    for widget_id, widget in widgets.items():
        widget_config = widget.get("config") or {}
        sources = list(widget_config.get("datasources") or [])
        alarm_source = widget_config.get("alarmSource")
        if isinstance(alarm_source, dict):
            sources.append(alarm_source)
        for source in sources:
            entity_alias_id = source.get("entityAliasId")
            if entity_alias_id and entity_alias_id not in alias_ids:
                errors.append(f"widget {widget_id} references missing alias {entity_alias_id}")
    actions_text = json.dumps(widgets, ensure_ascii=False)
    for required in ("TURN_ON", "TURN_OFF", "commandId", "ttlSeconds", "lastAck", "SAFETY_BLOCK"):
        if required not in actions_text:
            errors.append(f"dashboard control/feedback contract missing {required}")
    return errors, warnings


def main() -> int:
    migration = load(TARGET / "manifests" / "migration_manifest.json")
    active_rule_files = set(migration.get("activeRuleChains") or [])
    experimental_rule_files = {
        item.get("file") for item in migration.get("experimentalRuleChains") or []
    }
    dashboard_deferred = (migration.get("dashboard") or {}).get("status") == "DEFERRED_REFERENCE_ONLY"
    files = sorted(TARGET.rglob("*.json"))
    errors: list[str] = []
    warnings: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT)
        try:
            data = load(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{relative}: invalid JSON: {exc}")
            continue
        for hit in nonempty_secrets(data):
            errors.append(f"{relative}: non-empty secret-like field {hit}")
        if "ruleChain" in data and "metadata" in data:
            local_errors, local_warnings = validate_rule_chain(path, data)
        elif "profileData" in data:
            local_errors, local_warnings = validate_profile(path, data)
        elif "configuration" in data and "states" in (data.get("configuration") or {}) and not dashboard_deferred:
            local_errors, local_warnings = validate_dashboard(path, data)
        else:
            local_errors, local_warnings = [], []
        errors.extend(f"{relative}: {message}" for message in local_errors)
        warnings.extend(f"{relative}: {message}" for message in local_warnings)
    present_rule_files = {path.name for path in (TARGET / "rule_chains").glob("*.json")}
    if present_rule_files != active_rule_files | experimental_rule_files:
        errors.append("migration manifest does not classify every rule-chain JSON")
    print(
        f"json={len(files)} activeRuleChains={len(active_rule_files)} "
        f"experimentalRuleChains={len(experimental_rule_files)} profiles={len(list((TARGET / 'profiles').glob('*.json')))} "
        f"dashboard={'DEFERRED' if dashboard_deferred else 'ACTIVE'} errors={len(errors)} warnings={len(warnings)}"
    )
    for warning in warnings:
        print(f"WARN {warning}")
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
