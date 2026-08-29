"""Advisory-only analytics seam; it is not part of the actuator safety loop."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AnalyticsResult:
    recommendation: str = "NONE"
    score: float | None = None
    reason: str = "ANALYTICS_DISABLED"


class AnalyticsHook(Protocol):
    def evaluate(self, snapshot: dict[str, Any]) -> AnalyticsResult:
        ...


class NoOpAnalyticsHook:
    """Default implementation: deterministic, dependency-free and side-effect free."""

    def evaluate(self, snapshot: dict[str, Any]) -> AnalyticsResult:
        del snapshot
        return AnalyticsResult()


def safe_evaluate(hook: AnalyticsHook, snapshot: dict[str, Any]) -> AnalyticsResult:
    """Isolate optional analytics failures from the control loop."""

    try:
        result = hook.evaluate(dict(snapshot))
    except Exception:  # noqa: BLE001
        return AnalyticsResult(reason="ANALYTICS_ERROR")
    return result if isinstance(result, AnalyticsResult) else AnalyticsResult(reason="ANALYTICS_INVALID_RESULT")


class AdvisoryAnalyticsRunner:
    """Run optional analytics off-loop with one replaceable pending snapshot."""

    def __init__(self, hook: AnalyticsHook, timeout_seconds: float = 0.1) -> None:
        if timeout_seconds <= 0:
            raise ValueError("analytics timeout must be positive")
        self.hook = hook
        self.timeout_seconds = timeout_seconds
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._active_since: float | None = None
        self._latest = AnalyticsResult(reason="ANALYTICS_PENDING")
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            name="smartfarm-analytics",
            daemon=True,
        )
        self._thread.start()

    def submit(self, snapshot: dict[str, Any]) -> None:
        candidate = dict(snapshot)
        try:
            self._queue.put_nowait(candidate)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(candidate)
            except queue.Full:
                pass

    def status(self) -> AnalyticsResult:
        with self._lock:
            if (
                self._active_since is not None
                and time.monotonic() - self._active_since > self.timeout_seconds
            ):
                return AnalyticsResult(reason="ANALYTICS_TIMEOUT")
            return self._latest

    def close(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=min(0.2, self.timeout_seconds))

    def _worker(self) -> None:
        while not self._stopped.is_set():
            try:
                snapshot = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            with self._lock:
                self._active_since = time.monotonic()
            result = safe_evaluate(self.hook, snapshot)
            with self._lock:
                self._latest = result
                self._active_since = None
