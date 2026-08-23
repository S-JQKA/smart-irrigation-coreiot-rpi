"""Refactor the Smart Irrigation dashboard into the v2.2 staging dashboard."""

from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "implementation" / "coreiot" / "baseline" / "2026-08-03" / "exports" / "irrigation_management_dashboard.json"
TARGET = ROOT / "implementation" / "coreiot" / "v2.2" / "dashboards" / "irrigation_management_v2_2_staging.json"
MANIFEST = ROOT / "implementation" / "coreiot" / "v2.2" / "manifests" / "dashboard_v2_2_manifest.json"
NAMESPACE = uuid.UUID("b7e2d805-36ef-4cc9-9d43-a0f3895854b0")


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def data_key(name: str, kind: str = "timeseries", label: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "type": kind,
        "label": label or name,
        "color": "#5d5aec",
        "settings": {},
        "_hash": round(int(stable_id(name).replace("-", "")[:10], 16) / 16**10, 12),
    }


def alias(alias_name: str, filter_config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    alias_id = stable_id(f"alias:{alias_name}")
    return alias_id, {"id": alias_id, "alias": alias_name, "filter": filter_config}


def device_type_filter(*profiles: str) -> dict[str, Any]:
    return {"type": "deviceType", "resolveMultiple": True, "deviceTypes": list(profiles)}


def relation_device_filter(*profiles: str) -> dict[str, Any]:
    return {
        "type": "deviceSearchQuery",
        "resolveMultiple": True,
        "rootStateEntity": True,
        "stateEntityParamName": None,
        "defaultStateEntity": None,
        "rootEntity": None,
        "direction": "FROM",
        "maxLevel": 1,
        "fetchLastLevelOnly": False,
        "relationType": None,
        "deviceTypes": list(profiles),
    }


CARD_CSS = """
.sf-card { height: 100%; padding: 18px; box-sizing: border-box; color: #17223b; }
.sf-title { font-weight: 700; font-size: 18px; color: #3133a8; margin-bottom: 12px; }
.sf-row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-bottom: 1px solid #eef0f6; }
.sf-key { color: #697386; }
.sf-value { font-weight: 600; text-align: right; }
.sf-empty { color: #9aa1ae; }
.sf-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.sf-button { border: 0; border-radius: 6px; padding: 9px 14px; cursor: pointer; color: #fff; background: #5d5aec; }
.sf-button.stop { background: #c62828; }
.sf-button.mode { background: #3949ab; }
""".strip()


def card_function(title: str, keys: list[tuple[str, str]]) -> str:
    spec = json.dumps(keys, ensure_ascii=False)
    safe_title = json.dumps(title, ensure_ascii=False)
    return f"""var row = data && data.length ? data[0] : {{}};
var spec = {spec};
var html = '<div class="sf-card"><div class="sf-title">' + {safe_title} + '</div>';
for (var i = 0; i < spec.length; i++) {{
  var key = spec[i][0];
  var label = spec[i][1];
  var value = row[key];
  if (value === undefined || value === null || value === '') value = '<span class="sf-empty">--</span>';
  html += '<div class="sf-row"><span class="sf-key">' + label + '</span><span class="sf-value">' + value + '</span></div>';
}}
return html + '</div>';"""


def make_card(template: dict[str, Any], name: str, title: str, alias_id: str, keys: list[tuple[str, str, str]]) -> tuple[str, dict[str, Any]]:
    widget_id = stable_id(f"widget:{name}")
    widget = copy.deepcopy(template)
    widget["id"] = widget_id
    widget["config"]["title"] = title
    widget["config"]["actions"] = {}
    widget["config"]["datasources"] = [{
        "type": "entity",
        "name": None,
        "entityAliasId": alias_id,
        "filterId": None,
        "dataKeys": [data_key(key, kind, label) for key, label, kind in keys],
    }]
    widget["config"]["settings"] = {
        "useMarkdownTextFunction": True,
        "markdownTextFunction": card_function(title, [(key, label) for key, label, _ in keys]),
        "markdownCss": CARD_CSS,
    }
    return widget_id, widget


def action(action_id: str, name: str, custom_function: str) -> dict[str, Any]:
    return {
        "name": name,
        "icon": "bolt",
        "useShowWidgetActionFunction": False,
        "showWidgetActionFunction": "return true;",
        "type": "customPretty",
        "customHtml": "",
        "customCss": "",
        "customFunction": custom_function,
        "customResources": [],
        "openInSeparateDialog": False,
        "openInPopover": False,
        "id": stable_id(f"action:{action_id}"),
    }


def rpc_action(method: str) -> str:
    return f"""let injector = widgetContext.$scope.$injector;
let rpcService = injector.get(widgetContext.servicesMap.get('rpcService'));
let now = Date.now();
let request = {{
  method: '{method}',
  params: {{commandId: entityName + '-' + now, source: 'MANUAL', requestedAt: now, ttlSeconds: 300, reason: 'DASHBOARD_MANUAL'}},
  timeout: 5000,
  persistent: true
}};
rpcService.sendRpcRequest(entityId.id, request).subscribe(
  function() {{ widgetContext.updateAliases(); }},
  function(error) {{ console.warn('RPC failed', error); }}
);"""


def attribute_action(mode: str) -> str:
    return f"""let injector = widgetContext.$scope.$injector;
let attributeService = injector.get(widgetContext.servicesMap.get('attributeService'));
attributeService.saveEntityAttributes(entityId, 'SERVER_SCOPE', [{{key: 'controlMode', value: '{mode}'}}]).subscribe(
  function() {{ widgetContext.updateAliases(); }},
  function(error) {{ console.warn('Attribute update failed', error); }}
);"""


def make_control_card(template: dict[str, Any], name: str, title: str, alias_id: str, control_kind: str) -> tuple[str, dict[str, Any]]:
    widget_id, widget = make_card(
        template,
        name,
        title,
        alias_id,
        [("valveState" if control_kind == "valve" else "pumpState" if control_kind == "pump" else "controlMode", "Current state", "timeseries" if control_kind != "mode" else "attribute"),
         ("lastAck", "Last ACK", "timeseries"),
         ("safetyBlockReason", "Safety block", "timeseries")],
    )
    if control_kind in {"valve", "pump"}:
        buttons = '<div class="sf-actions"><button id="turn-on" class="sf-button">TURN ON</button><button id="turn-off" class="sf-button stop">TURN OFF</button></div>'
        actions = [action(f"{name}:on", "turn-on", rpc_action("TURN_ON")), action(f"{name}:off", "turn-off", rpc_action("TURN_OFF"))]
    else:
        buttons = '<div class="sf-actions"><button id="mode-auto" class="sf-button mode">AUTO</button><button id="mode-manual" class="sf-button mode">MANUAL</button><button id="mode-disabled" class="sf-button stop">DISABLED</button></div>'
        actions = [action(f"{name}:{mode}", f"mode-{mode.lower()}", attribute_action(mode)) for mode in ("AUTO", "MANUAL", "DISABLED")]
    widget["config"]["settings"]["markdownTextFunction"] = widget["config"]["settings"]["markdownTextFunction"].replace("return html + '</div>';", f"html += {json.dumps(buttons)}; return html + '</div>';" )
    widget["config"]["actions"] = {"elementClick": actions}
    return widget_id, widget


def navigation_card(template: dict[str, Any], states: list[tuple[str, str]]) -> tuple[str, dict[str, Any]]:
    widget_id = stable_id("widget:navigation")
    widget = copy.deepcopy(template)
    widget["id"] = widget_id
    widget["config"]["title"] = "SmartFarm v2.2 navigation"
    widget["config"]["datasources"] = []
    buttons = "".join(f'<button id="nav-{state_id}" class="sf-button">{label}</button>' for state_id, label in states)
    widget["config"]["settings"] = {
        "useMarkdownTextFunction": True,
        "markdownTextFunction": f"return '<div class=\"sf-card sf-actions\">{buttons}</div>';",
        "markdownCss": CARD_CSS,
    }
    widget["config"]["actions"] = {
        "elementClick": [{
            "name": f"nav-{state_id}",
            "icon": "arrow_forward",
            "useShowWidgetActionFunction": False,
            "showWidgetActionFunction": "return true;",
            "type": "openDashboardState",
            "targetDashboardStateId": state_id,
            "setEntityId": False,
            "stateEntityParamName": None,
            "openRightLayout": False,
            "popoverPreferredPlacement": "bottom",
            "popoverHideOnClickOutside": True,
            "popoverHideDashboardToolbar": True,
            "popoverStyle": {},
            "openInSeparateDialog": False,
            "openInPopover": False,
            "id": stable_id(f"nav:{state_id}"),
        } for state_id, _ in states]
    }
    return widget_id, widget


def layout(widget_ids: list[str], columns: int = 24) -> dict[str, Any]:
    placements: dict[str, Any] = {}
    for index, widget_id in enumerate(widget_ids):
        if index == 0:
            placements[widget_id] = {"sizeX": columns, "sizeY": 2, "row": 0, "col": 0, "mobileOrder": 0, "mobileHeight": 2}
            continue
        slot = index - 1
        placements[widget_id] = {
            "sizeX": columns // 2,
            "sizeY": 6,
            "row": 2 + (slot // 2) * 6,
            "col": (slot % 2) * (columns // 2),
            "mobileOrder": index,
            "mobileHeight": 5,
        }
    return {
        "main": {
            "widgets": placements,
            "gridSettings": {
                "backgroundColor": "#f6f8fc",
                "columns": columns,
                "margin": 10,
                "backgroundSizeMode": "100%",
                "autoFillHeight": True,
                "backgroundImageUrl": None,
                "mobileAutoFillHeight": False,
                "mobileRowHeight": 70,
                "outerMargin": True,
                "layoutType": "default",
            },
        }
    }


def main() -> None:
    if "--experimental" not in sys.argv:
        print(
            "dashboard=DEFERRED; build it manually from the tenant widget library after entity aliases and telemetry are stable. "
            "Use --experimental only to refresh the non-importable layout reference."
        )
        return
    dashboard = json.loads(SOURCE.read_text(encoding="utf-8"))
    config = dashboard["configuration"]
    old_widgets = config["widgets"]
    markdown_template = old_widgets["aaccdb0d-72ff-9f2c-e4ae-55077480a3e6"]
    alarm_template = old_widgets["2dcc4fce-85e4-e67d-4467-7bd38e579c5c"]
    scheduler_template = old_widgets["e18122c8-7973-5206-792a-cfa7f924a7b0"]

    aliases: dict[str, Any] = {}
    alias_ids: dict[str, str] = {}
    definitions = [
        ("Fields", {"type": "assetType", "resolveMultiple": True, "assetNameFilter": "", "assetTypes": ["SI Field v2.2", "SI Field"]}),
        ("Current field", {"type": "stateEntity", "resolveMultiple": False, "stateEntityParamName": None, "defaultStateEntity": None}),
        ("SmartFarm Site", {"type": "assetType", "resolveMultiple": True, "assetNameFilter": "SmartFarm Demo Site", "assetTypes": ["SF Site"]}),
        ("Gateway", device_type_filter("SF Gateway")),
        ("Pump", device_type_filter("SF Pump Controller")),
        ("Manifold", device_type_filter("SF Manifold Controller")),
        ("Environment Sensors", device_type_filter("SF Env Sensor Cluster")),
        ("Valves", device_type_filter("SI Smart Valve v2.2")),
        ("Water Meters", device_type_filter("SI Water Meter v2.2")),
        ("Soil Sensors", device_type_filter("SI Soil Moisture Sensor v2.2")),
        ("Current Field Valves", relation_device_filter("SI Smart Valve v2.2")),
        ("Current Field Environment", relation_device_filter("SF Env Sensor Cluster")),
        ("Irrigation schedule", {"type": "schedulerEvent", "resolveMultiple": True, "originatorStateEntity": True, "stateEntityParamName": None, "defaultStateEntity": None, "originator": None, "eventType": "START_IRRIGATION"}),
    ]
    for name, filter_config in definitions:
        alias_id, value = alias(name, filter_config)
        aliases[alias_id] = value
        alias_ids[name] = alias_id

    states_spec = [
        ("default", "Tổng quan"),
        ("field_detail", "Chi tiết Field"),
        ("control", "Điều khiển"),
        ("safety", "An toàn"),
        ("gateway", "Gateway"),
        ("alarm", "Alarm"),
        ("scheduler", "Scheduler"),
    ]
    widgets: dict[str, Any] = {}
    nav_id, nav_widget = navigation_card(markdown_template, states_spec)
    widgets[nav_id] = nav_widget

    def add_card(name: str, title: str, alias_name: str, keys: list[tuple[str, str, str]]) -> str:
        widget_id, widget = make_card(markdown_template, name, title, alias_ids[alias_name], keys)
        widgets[widget_id] = widget
        return widget_id

    overview_ids = [nav_id,
        add_card("overview-fields", "Fields", "Fields", [("avgMoisture", "Average moisture", "timeseries"), ("irrigationState", "Irrigation", "timeseries"), ("safetyState", "Safety", "timeseries")]),
        add_card("overview-gateway", "Gateway", "Gateway", [("gatewayMode", "Mode", "timeseries"), ("connectedDeviceCount", "Connected devices", "timeseries"), ("bufferDepth", "Buffer", "timeseries"), ("lastSyncTs", "Last sync", "timeseries")]),
        add_card("overview-pump", "Main pump", "Pump", [("pumpState", "Pump", "timeseries"), ("runtimeSec", "Runtime", "timeseries"), ("lastAck", "Last ACK", "timeseries")]),
        add_card("overview-system", "System safety", "Manifold", [("controllerState", "System mode", "timeseries"), ("tankLowSwitch", "Tank low", "timeseries"), ("activeZoneCount", "Active zones", "timeseries")]),
        add_card("overview-alarms", "Alarm counters", "Fields", [("criticalAlarmsCount", "Critical", "attribute"), ("majorAlarmsCount", "Major", "attribute"), ("warningAlarmsCount", "Warning", "attribute")]),
    ]

    field_ids = [nav_id,
        add_card("field-moisture", "Field moisture", "Current field", [("avgMoisture", "Average", "timeseries"), ("latestAvgMoisture", "Latest", "timeseries"), ("decisionReason", "Decision", "timeseries")]),
        add_card("field-water", "Water consumption", "Current field", [("waterConsumption", "Daily", "timeseries"), ("currentIrrigationWaterConsumption", "Current cycle", "timeseries"), ("irrigationState", "State", "timeseries")]),
        add_card("field-environment", "Environment", "Current Field Environment", [("airTemp", "Temperature", "timeseries"), ("airHumidity", "Humidity", "timeseries"), ("lightLux", "Light", "timeseries"), ("vpd", "VPD", "timeseries")]),
        add_card("field-thresholds", "Control thresholds", "Current field", [("controlMode", "Control mode", "attribute"), ("minMoistureThreshold", "Minimum", "attribute"), ("targetMoisture", "Target", "attribute"), ("maxWaterPerCycle", "Cycle quota", "attribute")]),
    ]

    mode_id, mode_widget = make_control_card(markdown_template, "field-mode-control", "Field control mode", alias_ids["Current field"], "mode")
    valve_id, valve_widget = make_control_card(markdown_template, "valve-rpc-control", "Valve RPC with ACK", alias_ids["Current Field Valves"], "valve")
    pump_id, pump_widget = make_control_card(markdown_template, "pump-rpc-control", "Pump RPC with ACK", alias_ids["Pump"], "pump")
    widgets.update({mode_id: mode_widget, valve_id: valve_widget, pump_id: pump_widget})
    control_ids = [nav_id, mode_id, valve_id, pump_id,
        add_card("control-contract", "Command contract", "Current Field Valves", [("lastCommandId", "Command ID", "timeseries"), ("lastAck", "ACK", "timeseries"), ("safetyBlockReason", "SAFETY_BLOCK reason", "timeseries")])]

    alarm_id = stable_id("widget:alarm-table")
    alarm_widget = copy.deepcopy(alarm_template)
    alarm_widget["id"] = alarm_id
    alarm_widget["config"]["title"] = "SmartFarm alarms — create/clear timeline"
    alarm_widget["config"]["alarmSource"]["entityAliasId"] = alias_ids["SmartFarm Site"]
    alarm_widget["config"]["actions"] = {}
    widgets[alarm_id] = alarm_widget
    safety_ids = [nav_id,
        add_card("safety-manifold", "Tank and manifold", "Manifold", [("tankLowSwitch", "Tank low", "timeseries"), ("activeZoneCount", "Active zones", "timeseries"), ("controllerState", "Controller", "timeseries")]),
        add_card("safety-valves", "Valve safety", "Valves", [("valveState", "State", "timeseries"), ("safetyBlockReason", "Block reason", "timeseries"), ("lastAck", "ACK", "timeseries")]),
        add_card("safety-pump", "Pump safety", "Pump", [("pumpState", "State", "timeseries"), ("safetyBlockReason", "Block reason", "timeseries"), ("lastAck", "ACK", "timeseries")]),
        alarm_id,
    ]
    gateway_ids = [nav_id,
        add_card("gateway-health", "Gateway health", "Gateway", [("gatewayMode", "Transport", "timeseries"), ("systemMode", "System mode", "timeseries"), ("connectedDeviceCount", "Downstream", "timeseries"), ("bufferDepth", "Buffer", "timeseries"), ("lastSyncTs", "Last sync", "timeseries")]),
        add_card("gateway-quality", "Downstream quality", "Soil Sensors", [("active", "Active", "timeseries"), ("dataQuality", "Quality", "timeseries"), ("battery", "Battery", "timeseries")]),
    ]
    alarm_ids = [nav_id, alarm_id, add_card("alarm-counts", "Alarm counts", "Fields", [("criticalAlarmsCount", "Critical", "attribute"), ("majorAlarmsCount", "Major", "attribute"), ("warningAlarmsCount", "Warning", "attribute")])]

    scheduler_id = stable_id("widget:scheduler-table")
    scheduler_widget = copy.deepcopy(scheduler_template)
    scheduler_widget["id"] = scheduler_id
    scheduler_widget["config"]["title"] = "Irrigation schedules"
    for datasource in scheduler_widget["config"].get("datasources", []):
        datasource["entityAliasId"] = alias_ids["Irrigation schedule"]
    widgets[scheduler_id] = scheduler_widget
    scheduler_ids = [nav_id, scheduler_id,
        add_card("scheduler-policy", "Stop policy", "Current field", [("maxDurationSec", "Duration limit", "attribute"), ("maxWaterPerCycle", "Volume limit", "attribute"), ("targetMoisture", "Target moisture", "attribute"), ("decisionReason", "First reached", "timeseries")])]

    state_widget_ids = {
        "default": overview_ids,
        "field_detail": field_ids,
        "control": control_ids,
        "safety": safety_ids,
        "gateway": gateway_ids,
        "alarm": alarm_ids,
        "scheduler": scheduler_ids,
    }
    config["widgets"] = widgets
    config["entityAliases"] = aliases
    config["states"] = {
        state_id: {"name": label, "root": state_id == "default", "layouts": layout(state_widget_ids[state_id])}
        for state_id, label in states_spec
    }
    config["description"] = "SmartFarm CoreIoT v2.2 staging dashboard. Seven focused states, Gateway health, RPC metadata/ACK, safety, alarms and scheduler."
    dashboard["title"] = "Irrigation Management v2.2 [STAGING]"
    dashboard["name"] = dashboard["title"]
    TARGET.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "dashboard": dashboard["title"],
        "status": "DEFERRED_REFERENCE_ONLY",
        "importReady": False,
        "decision": "Do not import this JSON; rebuild later with tenant-native widgets.",
        "states": [{"id": state_id, "name": label, "widgets": len(state_widget_ids[state_id])} for state_id, label in states_spec],
        "aliases": list(alias_ids),
        "controlActions": {
            "fieldMode": ["AUTO", "MANUAL", "DISABLED"],
            "valveRpc": ["TURN_ON", "TURN_OFF"],
            "pumpRpc": ["TURN_ON", "TURN_OFF"],
            "rpcMetadata": ["commandId", "source", "requestedAt", "ttlSeconds", "reason"],
            "twoWayAck": True,
        },
        "postImportChecks": [
            "Resolve every alias without datasource errors",
            "Open all seven states from the navigation strip",
            "Verify mode action updates SERVER_SCOPE controlMode",
            "Verify TURN_ON/TURN_OFF reaches Gateway API and renders lastAck",
            "Verify alarm source includes Site descendants or rebind to Fields when tenant relation traversal differs",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"dashboard={TARGET}")
    print(f"states={len(config['states'])} widgets={len(widgets)} aliases={len(aliases)}")


if __name__ == "__main__":
    main()
