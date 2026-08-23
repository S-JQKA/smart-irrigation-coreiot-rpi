"""Small, durable runtime-state primitives for the SmartFarm Gateway.

Only safety-relevant control metadata is persisted here.  Telemetry remains a
bounded in-memory MQTT buffer and is deliberately not presented as durable
replay across a Pi power loss.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any


MIN_VALID_EPOCH_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z


class AtomicJsonFile:
    """Read and atomically replace one small JSON state file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self, default: Any) -> Any:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default
        return value

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


class PersistentCommandLedger:
    """Bounded command-ID ledger that survives a fast Gateway restart."""

    def __init__(self, path: Path | None = None, capacity: int = 1024) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.file = AtomicJsonFile(path) if path is not None else None
        self._order: deque[str] = deque()
        self._seen: set[str] = set()
        if self.file is not None:
            payload = self.file.load({})
            values = payload.get("commandIds", []) if isinstance(payload, dict) else []
            if isinstance(values, list):
                for value in values[-capacity:]:
                    if isinstance(value, str) and value and value not in self._seen:
                        self._order.append(value)
                        self._seen.add(value)

    def __contains__(self, command_id: str) -> bool:
        return command_id in self._seen

    def record(self, command_id: str) -> None:
        if not command_id or command_id in self._seen:
            return
        self._order.append(command_id)
        self._seen.add(command_id)
        while len(self._order) > self.capacity:
            self._seen.discard(self._order.popleft())
        if self.file is not None:
            self.file.save({"schemaVersion": 1, "commandIds": list(self._order)})


class FieldConfigStore:
    """Persist only the last complete, validated per-Field configuration."""

    def __init__(self, path: Path) -> None:
        self.file = AtomicJsonFile(path)

    def load(self) -> dict[str, dict[str, Any]]:
        payload = self.file.load({})
        fields = payload.get("fields", {}) if isinstance(payload, dict) else {}
        if not isinstance(fields, dict):
            return {}
        return {
            key: value
            for key, value in fields.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def save(self, fields: dict[str, dict[str, Any]]) -> None:
        self.file.save({"schemaVersion": 1, "fields": fields})


def clock_is_ready(
    now_ms: int | None = None,
    *,
    require_ntp: bool = False,
    synchronized_file: Path = Path("/run/systemd/timesync/synchronized"),
) -> bool:
    """Return whether timestamped commands and schedules are safe to admit."""

    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if now_ms < MIN_VALID_EPOCH_MS:
        return False
    if not require_ntp:
        return True
    # systemd-time-wait-sync creates this boot-scoped stamp file; it may be
    # empty, so existence is the synchronization signal.
    return synchronized_file.is_file()


def runtime_state_dir(config_path: Path, runtime_mode: str) -> Path:
    """Choose a durable Pi path while keeping SIM runs workspace-local."""

    override = os.getenv("SMARTFARM_STATE_DIR")
    if override:
        return Path(override)
    if runtime_mode.upper() == "SIM_TWO_FIELD" or os.name == "nt":
        return config_path.parent / ".runtime" / config_path.stem
    return Path("/var/lib/smartfarm-gateway")
