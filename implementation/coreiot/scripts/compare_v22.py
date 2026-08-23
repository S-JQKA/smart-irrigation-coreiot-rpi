#!/usr/bin/env python3
"""Create a reviewable baseline-to-v2.2 structural diff without tenant secrets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CORE = Path(__file__).resolve().parents[1]
BASE = CORE / "baseline" / "2026-08-03" / "exports"
V22 = CORE / "v2.2"

PAIRS = {
    "rule_chain:field": (BASE / "si_field_rule_chain.json", V22 / "rule_chains" / "si_field_v2_2.json"),
    "rule_chain:soil": (BASE / "si_soil_moisture_rule_chain.json", V22 / "rule_chains" / "si_soil_moisture_v2_2.json"),
    "rule_chain:water": (BASE / "si_water_meter_rule_chain.json", V22 / "rule_chains" / "si_water_meter_v2_2.json"),
    "rule_chain:valve": (BASE / "si_smart_valve_rule_chain.json", V22 / "rule_chains" / "si_smart_valve_v2_2.json"),
    "rule_chain:alarm_count": (BASE / "si_count_alarms.json", V22 / "rule_chains" / "si_count_alarms_v2_2.json"),
    "profile:soil": (BASE / "si_soil_moisture_device_profile.json", V22 / "profiles" / "si_soil_moisture_sensor_v2_2.json"),
    "profile:water": (BASE / "si_water_meter_device_profile.json", V22 / "profiles" / "si_water_meter_v2_2.json"),
    "profile:valve": (BASE / "si_smart_valve_device_profile.json", V22 / "profiles" / "si_smart_valve_v2_2.json"),
    "profile:field": (BASE / "si_field_asset_profile.json", V22 / "profiles" / "si_field_asset_profile_v2_2.json"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def stats(path: Path) -> dict[str, Any]:
    data = load(path)
    if "metadata" in data and "ruleChain" in data:
        return {
            "name": data["ruleChain"].get("name"),
            "nodes": len(data["metadata"].get("nodes") or []),
            "connections": len(data["metadata"].get("connections") or []),
        }
    if "profileData" in data:
        return {"name": data.get("name"), "alarms": len((data.get("profileData") or {}).get("alarms") or [])}
    if "configuration" in data:
        config = data["configuration"]
        return {
            "name": data.get("title") or data.get("name"),
            "states": len(config.get("states") or {}),
            "widgets": len(config.get("widgets") or {}),
            "aliases": len(config.get("entityAliases") or {}),
        }
    return {"name": data.get("name")}


def main() -> None:
    output = {
        "baselineDate": "2026-08-03",
        "comparison": [],
        "added": {
            "activeRuleChains": ["SI Field Multivar Control v2.2", "SI Environment v2.2"],
            "experimentalRuleChainsDoNotImport": ["SI Pump Control v2.2", "SI Safety v2.2"],
            "profiles": ["SF Env Sensor Cluster", "SF Pump Controller", "SF Manifold Controller", "SF Gateway", "SF Site"],
        },
        "removedFromActiveTarget": ["SI Field generator subsystem (two generators plus supporting nodes)"],
        "deferred": ["Dashboard v2.2 JSON; rebuild later from tenant-native widgets"],
    }
    for component, (baseline, target) in PAIRS.items():
        output["comparison"].append({
            "component": component,
            "baselinePath": baseline.relative_to(CORE.parent.parent).as_posix(),
            "targetPath": target.relative_to(CORE.parent.parent).as_posix(),
            "baselineSha256": sha256(baseline),
            "targetSha256": sha256(target),
            "changed": sha256(baseline) != sha256(target),
            "baselineStats": stats(baseline),
            "targetStats": stats(target),
        })
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
