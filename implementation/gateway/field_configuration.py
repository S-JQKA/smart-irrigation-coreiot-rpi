"""Product Field configuration feedback consumed by the CoreIoT widget."""

from copy import deepcopy
from typing import Any

from irrigation_controller import (
    FIELD_CONFIGURATION_ATTRIBUTES,
    IrrigationController,
    _shared_attribute_values,
    apply_field_configuration_attribute,
    effective_field_config,
)
from runtime_state import FieldConfigStore


class FieldConfigurationFeedback:
    def __init__(self, model: IrrigationController, store: FieldConfigStore):
        self.model = model
        self.store = store
        self.status = {
            zone_id: {
                "fieldConfigStatus": "LOCAL_ONLY" if model.zone_runtime[zone_id].config_validated else "WAITING",
                "fieldConfigReason": "WAITING_FOR_CLOUD_CONFIG",
                "fieldConfigRequested": {},
                "fieldConfigReceivedAt": 0,
                "fieldConfigAppliedAt": 0,
            }
            for zone_id in model.zones
        }

    def apply(self, body: dict[str, Any], now_ms: int) -> tuple[bool, str] | None:
        zone_id = next((key for key, zone in self.model.zones.items()
                        if zone.valve_device == body.get("device")), None)
        if zone_id is None:
            return None
        values = {key: value for key, value in _shared_attribute_values(body).items()
                  if key in FIELD_CONFIGURATION_ATTRIBUTES}
        if not values:
            return None
        status = self.status[zone_id]
        # Monotonic receipt identifiers let a UI distinguish consecutive saves
        # even if they arrive within the same millisecond.
        received = max(now_ms, status["fieldConfigReceivedAt"] + 1)
        result = apply_field_configuration_attribute(self.model, body, now_ms, self.store)
        accepted, reason = result
        status.update(fieldConfigStatus="APPLIED" if accepted else "REJECTED",
                      fieldConfigReason=reason,
                      fieldConfigRequested=deepcopy(values),
                      fieldConfigReceivedAt=received)
        if accepted:
            status["fieldConfigAppliedAt"] = now_ms
        return result

    def annotate(self, readings: dict[str, list[dict[str, Any]]]) -> None:
        for zone_id, zone in self.model.zones.items():
            for sample in readings.get(zone.valve_device, [])[:1]:
                sample["values"].update(deepcopy(self.status[zone_id]),
                    fieldConfigEffective=effective_field_config(self.model, zone_id))
