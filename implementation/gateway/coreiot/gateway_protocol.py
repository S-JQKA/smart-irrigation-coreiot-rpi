"""Pure Gateway API payload encoding and RPC validation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from runtime_state import PersistentCommandLedger


TOPIC_CONNECT = "v1/gateway/connect"
TOPIC_DISCONNECT = "v1/gateway/disconnect"
TOPIC_TELEMETRY = "v1/gateway/telemetry"
TOPIC_DEVICE_TELEMETRY = "v1/devices/me/telemetry"
TOPIC_ATTRIBUTES = "v1/gateway/attributes"
TOPIC_ATTRIBUTES_REQUEST = "v1/gateway/attributes/request"
TOPIC_ATTRIBUTES_RESPONSE = "v1/gateway/attributes/response"
TOPIC_RPC = "v1/gateway/rpc"


@dataclass(frozen=True)
class RpcCommand:
    device: str
    request_id: int
    method: str
    params: dict[str, Any]

    @property
    def command_id(self) -> str:
        value = self.params.get("commandId")
        return str(value) if value else f"gateway-rpc-{self.request_id}"

    @property
    def has_explicit_command_id(self) -> bool:
        value = self.params.get("commandId")
        return isinstance(value, str) and bool(value.strip())


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode_connect(device: str, profile: str | None = None) -> str:
    payload: dict[str, Any] = {"device": device}
    if profile:
        payload["type"] = profile
    return compact_json(payload)


def encode_disconnect(device: str) -> str:
    return compact_json({"device": device})


def encode_telemetry(readings: dict[str, list[dict[str, Any]]]) -> str:
    if not readings:
        raise ValueError("at least one downstream reading is required")
    for device, samples in readings.items():
        if not device or not samples:
            raise ValueError("each downstream device needs a non-empty name and sample list")
        for sample in samples:
            if "ts" in sample and "values" not in sample:
                raise ValueError("timestamped samples require a values object")
    return compact_json(readings)
    

def encode_device_telemetry(sample: dict[str, Any]) -> str:
    if not sample:
        raise ValueError("gateway telemetry sample is required")
    if "ts" in sample and "values" not in sample:
        raise ValueError("timestamped gateway telemetry requires a values object")
    return compact_json(sample)

def encode_attributes(attributes: dict[str, dict[str, Any]]) -> str:
    if not attributes:
        raise ValueError("at least one downstream attribute map is required")
    return compact_json(attributes)


def encode_attribute_request(
    request_id: int,
    device: str,
    keys: list[str] | tuple[str, ...],
    *,
    client: bool = False,
) -> str:
    """Encode a Gateway API attribute request for one downstream device."""

    if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 1:
        raise ValueError("attribute request id must be a positive integer")
    if not isinstance(device, str) or not device.strip():
        raise ValueError("attribute request requires a downstream device name")
    normalized_keys = [key.strip() for key in keys if isinstance(key, str) and key.strip()]
    if not normalized_keys or len(normalized_keys) != len(keys):
        raise ValueError("attribute request requires non-empty string keys")
    return compact_json(
        {
            "id": request_id,
            "device": device,
            "keys": normalized_keys,
            "client": client,
        }
    )


def parse_rpc(payload: bytes | str) -> RpcCommand:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    body = json.loads(payload)
    device = body.get("device")
    data = body.get("data") or {}
    request_id = data.get("id")
    method = data.get("method")
    params = data.get("params") or {}
    if not isinstance(device, str) or not device:
        raise ValueError("RPC is missing downstream device")
    if not isinstance(request_id, int):
        raise ValueError("RPC is missing numeric request id")
    if method not in {"TURN_ON", "TURN_OFF", "SET_LOCAL_SCHEDULE"}:
        raise ValueError(f"RPC method is not allowed: {method}")
    if not isinstance(params, dict):
        raise ValueError("RPC params must be an object")
    return RpcCommand(device=device, request_id=request_id, method=method, params=params)


def normalize_legacy_manual_off(command: RpcCommand, now_ms: int | None = None) -> RpcCommand:
    """Add safe metadata only for a parameter-less fail-safe OFF widget request.

    Some CoreIoT/ThingsBoard forks expose the Command button but drop its
    configured params.  OFF remains safe to accept with a request-correlated
    ID; ON must never use this compatibility path.
    """

    if command.has_explicit_command_id or command.method != "TURN_OFF" or command.params:
        return command
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return RpcCommand(
        device=command.device,
        request_id=command.request_id,
        method=command.method,
        params={
            "commandId": f"legacy-widget-off-{command.request_id}",
            "source": "MANUAL",
            "requestedAt": now_ms,
            "ttlSeconds": 30,
            "latchSeconds": 30,
            "reason": "LEGACY_WIDGET_FAIL_SAFE_OFF",
        },
    )


def encode_rpc_reply(
    command: RpcCommand,
    success: bool,
    state: str,
    reason: str,
    detail: str | None = None,
) -> str:
    if state not in {"ON", "OFF"}:
        raise ValueError("RPC reply state must be ON or OFF")
    allowed_reasons = {"EXECUTED", "SAFETY_BLOCK", "EXPIRED", "DUPLICATE", "INVALID_COMMAND"}
    if reason not in allowed_reasons:
        raise ValueError(f"unsupported RPC reply reason: {reason}")
    data: dict[str, Any] = {
        "success": success,
        "state": state,
        "reason": reason,
        "commandId": command.command_id,
    }
    if detail:
        data["detail"] = detail
    return compact_json(
        {
            "device": command.device,
            "id": command.request_id,
            "data": data,
        }
    )


def requested_run_duration_seconds(command: RpcCommand) -> float | None:
    """Return the bounded run duration, including the deprecated manual alias."""

    value = command.params.get("runDurationSeconds")
    if value is None and command.params.get("source") == "MANUAL":
        value = command.params.get("manualTtlSeconds")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


class RpcGuard:
    """Reject malformed, expired and duplicate commands with optional persistence."""

    def __init__(
        self,
        capacity: int = 1024,
        ledger: PersistentCommandLedger | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._ledger = ledger or PersistentCommandLedger(capacity=capacity)

    def evaluate(
        self,
        command: RpcCommand,
        now_ms: int | None = None,
        require_metadata: bool = False,
        ignore_timestamp: bool = False,
    ) -> str:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        command_id = command.command_id
        if command_id in self._ledger:
            return "DUPLICATE"
        requested_at = command.params.get("requestedAt")
        ttl_seconds = command.params.get("ttlSeconds", 300)
        if require_metadata:
            source = command.params.get("source")
            numeric_requested_at = isinstance(requested_at, (int, float)) and not isinstance(requested_at, bool)
            numeric_ttl = isinstance(ttl_seconds, (int, float)) and not isinstance(ttl_seconds, bool)
            if source not in {"AUTO", "MANUAL", "SCHEDULER", "SAFETY"}:
                return "INVALID_COMMAND"
            if not numeric_requested_at or not numeric_ttl:
                return "INVALID_COMMAND"
            if ttl_seconds < 1 or ttl_seconds > 3600:
                return "INVALID_COMMAND"
            if not ignore_timestamp and requested_at > now_ms + 30_000:
                return "INVALID_COMMAND"
            if source in {"MANUAL", "SCHEDULER"} and command.method == "TURN_ON":
                duration = requested_run_duration_seconds(command)
                maximum = 300 if source == "MANUAL" else 3600
                if duration is None or duration < 1 or duration > maximum:
                    return "INVALID_COMMAND"
            if command.method == "SET_LOCAL_SCHEDULE" and source != "MANUAL":
                return "INVALID_COMMAND"
        if (
            not ignore_timestamp
            and isinstance(requested_at, (int, float))
            and isinstance(ttl_seconds, (int, float))
        ):
            if now_ms > int(requested_at) + int(ttl_seconds * 1000):
                return "EXPIRED"
        self._ledger.record(command_id)
        return "ACCEPT"
