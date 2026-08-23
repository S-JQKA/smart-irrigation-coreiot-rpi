"""Advisory-only analytics seam; it is not part of the actuator safety loop."""

from __future__ import annotations

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
