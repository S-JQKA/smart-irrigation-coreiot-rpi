"""MQTT Gateway API client for CoreIoT/ThingsBoard-compatible tenants."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .gateway_protocol import (
    TOPIC_DEVICE_TELEMETRY,
    TOPIC_ATTRIBUTES,
    TOPIC_ATTRIBUTES_REQUEST,
    TOPIC_ATTRIBUTES_RESPONSE,
    TOPIC_CONNECT,
    TOPIC_DISCONNECT,
    TOPIC_RPC,
    TOPIC_TELEMETRY,
    RpcCommand,
    RpcGuard,
    encode_attributes,
    encode_attribute_request,
    encode_connect,
    encode_device_telemetry,
    encode_disconnect,
    encode_rpc_reply,
    encode_telemetry,
    normalize_legacy_manual_off,
    parse_rpc,
)
from runtime_state import PersistentCommandLedger


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewaySettings:
    host: str
    access_token: str
    port: int = 1883
    keepalive: int = 60
    tls: bool = False
    client_id: str = "smartfarm-pi-gateway"
    buffer_capacity: int = 500
    buffer_ttl_seconds: int = 900


class GatewayClient:
    def __init__(
        self,
        settings: GatewaySettings,
        rpc_handler: Callable[[RpcCommand], tuple[bool, str, str]],
        attribute_handler: Callable[[dict[str, Any]], None] | None = None,
        connection_handler: Callable[[bool], None] | None = None,
        legacy_manual_off_allowed: Callable[[RpcCommand], bool] | None = None,
        command_ledger: PersistentCommandLedger | None = None,
        clock_ready: Callable[[], bool] | None = None,
        rpc_detail_handler: Callable[[RpcCommand], str | None] | None = None,
        reconnect_snapshot_handler: Callable[[], None] | None = None,
    ) -> None:
        if not settings.host or not settings.access_token:
            raise ValueError("CoreIoT host and gateway access token are required")
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install paho-mqtt from implementation/gateway/requirements.txt") from exc

        self.settings = settings
        self.rpc_handler = rpc_handler
        self.attribute_handler = attribute_handler
        self.connection_handler = connection_handler
        self.legacy_manual_off_allowed = legacy_manual_off_allowed
        self.clock_ready = clock_ready
        self.rpc_detail_handler = rpc_detail_handler
        self.reconnect_snapshot_handler = reconnect_snapshot_handler
        self.guard = RpcGuard(ledger=command_ledger)
        self._devices: dict[str, str | None] = {}
        self._shared_attribute_watches: dict[str, tuple[str, ...]] = {}
        self._attribute_request_id = 0
        self._buffer: deque[tuple[str, str, int, int]] = deque(maxlen=settings.buffer_capacity)
        self._expired_buffer_count = 0
        self._lock = threading.Lock()
        self._connected = False
        self._ready_event = threading.Event()
        self._connect_rc: int | None = None
        self._client = mqtt.Client(client_id=settings.client_id, protocol=mqtt.MQTTv311)
        self._client.username_pw_set(settings.access_token)
        if settings.tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def buffer_depth(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def expired_buffer_count(self) -> int:
        with self._lock:
            return self._expired_buffer_count

    def start(self, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("MQTT connection timeout must be positive")
        self._ready_event.clear()
        self._connect_rc = None
        self._client.connect(self.settings.host, self.settings.port, self.settings.keepalive)
        self._client.loop_start()
        if not self._ready_event.wait(timeout_seconds):
            self._client.loop_stop()
            self._client.disconnect()
            raise TimeoutError(f"CoreIoT MQTT connection timed out after {timeout_seconds:g}s")
        if not self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            raise ConnectionError(f"CoreIoT MQTT connection failed rc={self._connect_rc}")

    def stop(self) -> None:
        # Keep the network loop alive while MQTT sends the DISCONNECT packet.
        # Stopping the loop first can leave the tenant's device-state service
        # waiting for its inactivity timeout instead of observing a clean exit.
        self._client.disconnect()
        self._client.loop_stop()

    def pause_transport(self) -> None:
        """Close the MQTT transport while retaining downstream registrations.

        This is a deliberate live-test hook.  A later ``resume_transport`` uses
        the same client and the same registered-device map, so ``_on_connect``
        exercises the production re-announce and buffered-replay path.
        """

        was_connected = self._connected
        self._client.disconnect()
        self._client.loop_stop()
        self._connected = False
        if was_connected and self.connection_handler:
            self.connection_handler(False)

    def resume_transport(self, timeout_seconds: float = 10.0) -> None:
        """Reconnect after ``pause_transport`` and wait for the broker ACK."""

        self.start(timeout_seconds=timeout_seconds)

    def connect_device(self, device: str, profile: str | None = None) -> None:
        self._devices[device] = profile
        self._publish_or_buffer(TOPIC_CONNECT, encode_connect(device, profile))

    def disconnect_device(self, device: str) -> Any | None:
        self._devices.pop(device, None)
        return self._publish_or_buffer(TOPIC_DISCONNECT, encode_disconnect(device))

    def disconnect_devices(self, devices: list[str] | tuple[str, ...], timeout_seconds: float = 5.0) -> int:
        """Gracefully disconnect downstream devices and wait for broker PUBACKs.

        Gateway API disconnect messages are QoS 1.  The simulator used to stop
        its MQTT loop immediately after enqueueing them, so CoreIoT could keep
        the children Active until the platform inactivity timeout.  A single
        shared deadline bounds shutdown even when the broker is unavailable.
        """

        if timeout_seconds <= 0:
            raise ValueError("disconnect timeout must be positive")
        pending = []
        for device in devices:
            info = self.disconnect_device(device)
            if info is not None:
                pending.append(info)
        deadline = time.monotonic() + timeout_seconds
        acknowledged = 0
        for info in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                info.wait_for_publish(timeout=remaining)
                if not hasattr(info, "is_published") or info.is_published():
                    acknowledged += 1
            except (RuntimeError, ValueError):
                LOG.warning("CoreIoT downstream disconnect PUBACK wait failed", exc_info=True)
                break
        LOG.info(
            "CoreIoT downstream disconnect requested=%s acknowledged=%s",
            len(devices),
            acknowledged,
        )
        return acknowledged

    def publish_telemetry(self, readings: dict[str, list[dict[str, Any]]]) -> None:
        self._publish_or_buffer(TOPIC_TELEMETRY, encode_telemetry(readings))

    def publish_gateway_telemetry(self, sample: dict[str, Any]) -> None:
        self._publish_or_buffer(
            TOPIC_DEVICE_TELEMETRY,
            encode_device_telemetry(sample),
        )

    def publish_attributes(self, attributes: dict[str, dict[str, Any]]) -> None:
        self._publish_or_buffer(TOPIC_ATTRIBUTES, encode_attributes(attributes))

    def watch_shared_attributes(self, device: str, keys: list[str] | tuple[str, ...]) -> None:
        """Receive live updates and re-read durable shared config after reconnect."""

        normalized_keys = tuple(key.strip() for key in keys if isinstance(key, str) and key.strip())
        if not device or not normalized_keys or len(normalized_keys) != len(keys):
            raise ValueError("shared attribute watch requires a device and non-empty keys")
        self._shared_attribute_watches[device] = normalized_keys
        if self._connected:
            self._request_shared_attributes(device, normalized_keys)

    def send_rpc_reply(
        self,
        command: RpcCommand,
        success: bool,
        state: str,
        reason: str,
        detail: str | None = None,
    ) -> None:
        self._publish_or_buffer(
            TOPIC_RPC,
            encode_rpc_reply(command, success, state, reason, detail),
        )

    def _publish_or_buffer(self, topic: str, payload: str, qos: int = 1) -> Any | None:
        if not self._connected:
            self._buffer_message(topic, payload, qos)
            return None
        info = self._client.publish(topic, payload, qos=qos)
        if info.rc != 0:
            self._buffer_message(topic, payload, qos)
            return None
        return info

    def _buffer_message(self, topic: str, payload: str, qos: int) -> None:
        with self._lock:
            self._buffer.append((topic, payload, qos, int(time.time() * 1000)))

    def _flush_buffer(self) -> None:
        while self._connected:
            with self._lock:
                if not self._buffer:
                    return
                topic, payload, qos, buffered_at_ms = self._buffer.popleft()
            if int(time.time() * 1000) - buffered_at_ms > self.settings.buffer_ttl_seconds * 1000:
                with self._lock:
                    self._expired_buffer_count += 1
                continue
            info = self._client.publish(topic, payload, qos=qos)
            if info.rc != 0:
                with self._lock:
                    self._buffer.appendleft((topic, payload, qos, buffered_at_ms))
                return

    def _request_shared_attributes(self, device: str, keys: tuple[str, ...]) -> None:
        self._attribute_request_id += 1
        self._publish_or_buffer(
            TOPIC_ATTRIBUTES_REQUEST,
            encode_attribute_request(self._attribute_request_id, device, keys, client=False),
        )

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int, properties: Any = None) -> None:
        self._connect_rc = rc
        self._connected = rc == 0
        if not self._connected:
            LOG.error("CoreIoT MQTT connect failed rc=%s", rc)
            self._ready_event.set()
            return
        if self.connection_handler:
            self.connection_handler(True)
        client.subscribe(TOPIC_RPC, qos=1)
        client.subscribe(TOPIC_ATTRIBUTES, qos=1)
        client.subscribe(TOPIC_ATTRIBUTES_RESPONSE, qos=1)
        # CoreIoT may discard downstream sessions when the gateway transport
        # drops. Re-announce every registered child before flushing telemetry.
        for device, profile in self._devices.items():
            info = client.publish(TOPIC_CONNECT, encode_connect(device, profile), qos=1)
            if info.rc != 0:
                with self._lock:
                    self._buffer.appendleft(
                        (TOPIC_CONNECT, encode_connect(device, profile), 1, int(time.time() * 1000))
                    )
        for device, keys in getattr(self, "_shared_attribute_watches", {}).items():
            self._request_shared_attributes(device, keys)
        LOG.info(
            "CoreIoT MQTT connected rc=%s reannounced=%s attributeWatches=%s bufferDepth=%s",
            rc,
            len(self._devices),
            len(getattr(self, "_shared_attribute_watches", {})),
            self.buffer_depth,
        )
        reconnect_snapshot_handler = getattr(self, "reconnect_snapshot_handler", None)
        if reconnect_snapshot_handler:
            reconnect_snapshot_handler()
        self._flush_buffer()
        self._ready_event.set()

    def _on_disconnect(self, client: Any, userdata: Any, rc: int, properties: Any = None) -> None:
        self._connected = False
        if self.connection_handler:
            self.connection_handler(False)
        LOG.warning("CoreIoT MQTT disconnected rc=%s", rc)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            if message.topic == TOPIC_RPC:
                command = parse_rpc(message.payload)
                original_had_command_id = command.has_explicit_command_id
                if (
                    not original_had_command_id
                    and self.legacy_manual_off_allowed is not None
                    and self.legacy_manual_off_allowed(command)
                ):
                    command = normalize_legacy_manual_off(command)
                LOG.info(
                    "rpc received device=%s method=%s commandId=%s requestId=%s",
                    command.device,
                    command.method,
                    command.command_id,
                    command.request_id,
                )
                if not original_had_command_id and command.has_explicit_command_id:
                    LOG.warning(
                        "rpc compatibility fallback device=%s method=%s requestId=%s commandId=%s",
                        command.device,
                        command.method,
                        command.request_id,
                        command.command_id,
                    )
                if not command.has_explicit_command_id:
                    LOG.warning(
                        "rpc rejected device=%s method=%s requestId=%s reason=INVALID_COMMAND missing=commandId",
                        command.device,
                        command.method,
                        command.request_id,
                    )
                    self.send_rpc_reply(command, False, "OFF", "INVALID_COMMAND")
                    return
                clock_is_ready = (
                    self.clock_ready()
                    if getattr(self, "clock_ready", None) is not None
                    else True
                )
                if (
                    not clock_is_ready
                    and command.method in {"TURN_ON", "SET_LOCAL_SCHEDULE"}
                ):
                    LOG.warning(
                        "rpc rejected commandId=%s reason=CLOCK_NOT_READY",
                        command.command_id,
                    )
                    self.send_rpc_reply(
                        command,
                        False,
                        "OFF",
                        "INVALID_COMMAND",
                        "CLOCK_NOT_READY",
                    )
                    return
                guard_result = self.guard.evaluate(
                    command,
                    require_metadata=True,
                    # A stale or future OFF is fail-safe.  When the Pi clock is
                    # invalid, retain metadata/dedup checks but do not block the
                    # only cloud action that may reduce physical risk.
                    ignore_timestamp=not clock_is_ready and command.method == "TURN_OFF",
                )
                if guard_result != "ACCEPT":
                    LOG.warning("rpc rejected commandId=%s reason=%s", command.command_id, guard_result)
                    self.send_rpc_reply(command, False, "OFF", guard_result)
                    return
                success, state, reason = self.rpc_handler(command)
                rpc_detail_handler = getattr(self, "rpc_detail_handler", None)
                detail = rpc_detail_handler(command) if rpc_detail_handler else None
                LOG.info(
                    "rpc completed commandId=%s success=%s state=%s reason=%s",
                    command.command_id,
                    success,
                    state,
                    reason,
                )
                if detail:
                    self.send_rpc_reply(command, success, state, reason, detail)
                else:
                    self.send_rpc_reply(command, success, state, reason)
            elif self.attribute_handler:
                import json

                self.attribute_handler(json.loads(message.payload.decode("utf-8")))
        except Exception:  # noqa: BLE001
            LOG.exception("Failed to process CoreIoT message topic=%s", message.topic)
