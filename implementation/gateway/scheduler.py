"""Bounded local schedules, recurrence and persistent schedule state."""
from __future__ import annotations
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING
from coreiot.gateway_protocol import RpcCommand
from runtime_state import AtomicJsonFile
if TYPE_CHECKING:
    from irrigation_controller import IrrigationController
LOG = logging.getLogger('smartfarm.gateway.scheduler')

@dataclass(frozen=True)
class LocalSchedule:
    schedule_id: str
    zone_id: str
    start_after_seconds: int
    duration_seconds: int
    repeat_every_seconds: int | None = None
    config_id: str = ""


class LocalScheduleRunner:
    """Emit bounded scheduler commands from deterministic Gateway configuration."""

    def __init__(self, schedules: list[LocalSchedule], started_ms: int) -> None:
        self.schedules = schedules
        self.next_fire_ms = {
            item.schedule_id: started_ms + item.start_after_seconds * 1000
            for item in schedules
        }
        self.sequence = 0
        self._lock = threading.Lock()

    @classmethod
    def from_config(
        cls, config: dict[str, Any], model: "IrrigationController", started_ms: int
    ) -> "LocalScheduleRunner":
        schedules: list[LocalSchedule] = []
        seen_ids: set[str] = set()
        raw_schedules = config.get("localSchedules", [])
        if not isinstance(raw_schedules, list):
            raise ValueError("localSchedules must be a list")
        for index, raw in enumerate(raw_schedules, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"localSchedules[{index}] must be an object")
            if not raw.get("enabled", True):
                continue
            schedule_id = str(raw.get("id", "")).strip()
            zone_id = str(raw.get("zoneId", "")).strip()
            config_id = str(raw.get("configId", "")).strip()
            if not schedule_id or schedule_id in seen_ids:
                raise ValueError(f"localSchedules[{index}] requires a unique id")
            if zone_id not in model.zones:
                raise ValueError(
                    f"localSchedules[{index}] has unknown zoneId={zone_id}"
                )
            start_after = raw.get("startAfterSeconds", 0)
            duration = raw.get("durationSeconds")
            repeat_every = raw.get("repeatEverySeconds")
            if (
                not isinstance(start_after, int)
                or isinstance(start_after, bool)
                or start_after < 0
            ):
                raise ValueError(
                    f"localSchedules[{index}] startAfterSeconds must be >= 0"
                )
            max_duration = min(
                model.manual_on_max_seconds,
                int(model._limits_for(zone_id).max_duration_seconds),
            )
            if (
                not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration < 1
                or (duration > max_duration)
            ):
                raise ValueError(
                    f"localSchedules[{index}] durationSeconds must be 1..{max_duration}"
                )
            if repeat_every is not None and (
                not isinstance(repeat_every, int)
                or isinstance(repeat_every, bool)
                or repeat_every < duration
            ):
                raise ValueError(
                    f"localSchedules[{index}] repeatEverySeconds must be >= durationSeconds"
                )
            schedules.append(
                LocalSchedule(
                    schedule_id=schedule_id,
                    zone_id=zone_id,
                    start_after_seconds=start_after,
                    duration_seconds=duration,
                    repeat_every_seconds=repeat_every,
                    config_id=config_id,
                )
            )
            model.zone_runtime[zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": True,
                "status": "SCHEDULED",
                "nextRunTs": started_ms + start_after * 1000,
                "durationSeconds": duration,
                "repeatEverySeconds": repeat_every or 0,
                "lastRunTs": 0,
                "lastResultReason": "NONE",
                "configCommandId": config_id,
                "configAck": "ACCEPTED" if config_id else "",
                "configReason": "SCHEDULE_RESTORED" if config_id else "",
            }
            seen_ids.add(schedule_id)
        return cls(schedules, started_ms)

    def update_from_rpc(
        self, model: "IrrigationController", command: RpcCommand, now_ms: int
    ) -> tuple[bool, str, str]:
        """Validate and apply one operator-owned local schedule update."""
        return self.update_from_config(
            model, command.device, command.params, command.command_id, now_ms
        )

    def update_from_config(
        self,
        model: "IrrigationController",
        device: str,
        params: dict[str, Any],
        config_id: str,
        now_ms: int,
    ) -> tuple[bool, str, str]:
        """Validate and apply one durable operator schedule configuration."""
        target = next(
            (zone for zone in model.zones.values() if zone.valve_device == device), None
        )
        if target is None:
            return (False, "OFF", "INVALID_COMMAND")
        if not isinstance(config_id, str) or not config_id.strip():
            return (False, target.valve_state, "INVALID_COMMAND")
        schedule_id = str(params.get("scheduleId", "")).strip()
        enabled = params.get("enabled", True)
        duration = params.get("durationSeconds")
        repeat_every = params.get("repeatEverySeconds", 0)
        start_at_ms = params.get("startAtMs")
        start_after = params.get("startAfterSeconds")
        if not schedule_id or not isinstance(enabled, bool):
            return (False, target.valve_state, "INVALID_COMMAND")
        existing = next(
            (item for item in self.schedules if item.schedule_id == schedule_id), None
        )
        if existing is not None and existing.zone_id != target.zone_id:
            return (False, target.valve_state, "INVALID_COMMAND")
        if not enabled:
            disabled_duration = existing.duration_seconds if existing is not None else 0
            with self._lock:
                self.schedules = [
                    item for item in self.schedules if item.schedule_id != schedule_id
                ]
                self.next_fire_ms.pop(schedule_id, None)
            model.zone_runtime[target.zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": False,
                "status": "CANCELLED",
                "nextRunTs": 0,
                "durationSeconds": disabled_duration,
                "repeatEverySeconds": int(repeat_every or 0),
                "lastRunTs": 0,
                "lastResultReason": "OPERATOR_DISABLED",
                "configCommandId": config_id,
                "configAck": "ACCEPTED",
                "configReason": "SCHEDULE_DISABLED",
            }
            return (True, target.valve_state, "EXECUTED")
        max_duration = min(
            model.manual_on_max_seconds,
            int(model._limits_for(target.zone_id).max_duration_seconds),
        )
        valid_duration = isinstance(duration, int) and (not isinstance(duration, bool))
        valid_repeat = isinstance(repeat_every, int) and (
            not isinstance(repeat_every, bool)
        )
        if not valid_duration or duration < 1 or duration > max_duration:
            return (False, target.valve_state, "INVALID_COMMAND")
        if (
            not valid_repeat
            or repeat_every < 0
            or (repeat_every and repeat_every < duration)
        ):
            return (False, target.valve_state, "INVALID_COMMAND")
        if start_at_ms is not None:
            if not isinstance(start_at_ms, int) or isinstance(start_at_ms, bool):
                return (False, target.valve_state, "INVALID_COMMAND")
            delay_ms = start_at_ms - now_ms
        elif start_after is not None:
            if not isinstance(start_after, int) or isinstance(start_after, bool):
                return (False, target.valve_state, "INVALID_COMMAND")
            delay_ms = start_after * 1000
            start_at_ms = now_ms + delay_ms
        else:
            return (False, target.valve_state, "INVALID_COMMAND")
        config_reason = "SCHEDULE_UPDATED"
        if delay_ms < 0 and repeat_every > 0:
            interval_ms = repeat_every * 1000
            missed = (now_ms - start_at_ms) // interval_ms + 1
            start_at_ms += missed * interval_ms
            delay_ms = start_at_ms - now_ms
            config_reason = "SCHEDULE_ROLLED_FORWARD"
        elif delay_ms < 0:
            with self._lock:
                self.schedules = [
                    item for item in self.schedules if item.schedule_id != schedule_id
                ]
                self.next_fire_ms.pop(schedule_id, None)
            model.zone_runtime[target.zone_id].local_schedule = {
                "id": schedule_id,
                "source": "GATEWAY_LOCAL",
                "enabled": True,
                "status": "COMPLETED",
                "nextRunTs": 0,
                "durationSeconds": duration,
                "repeatEverySeconds": 0,
                "lastRunTs": start_at_ms,
                "lastResultReason": "STALE_ONE_SHOT_IGNORED",
                "configCommandId": config_id,
                "configAck": "ACCEPTED",
                "configReason": "STALE_ONE_SHOT_IGNORED",
            }
            return (True, target.valve_state, "STALE_ONE_SHOT_IGNORED")
        if delay_ms > 7 * 86400000:
            return (False, target.valve_state, "INVALID_COMMAND")
        updated = LocalSchedule(
            schedule_id=schedule_id,
            zone_id=target.zone_id,
            start_after_seconds=max(0, int(delay_ms / 1000)),
            duration_seconds=duration,
            repeat_every_seconds=repeat_every or None,
            config_id=config_id,
        )
        with self._lock:
            self.schedules = [
                item for item in self.schedules if item.schedule_id != schedule_id
            ]
            self.schedules.append(updated)
            self.next_fire_ms[schedule_id] = start_at_ms
        model.zone_runtime[target.zone_id].local_schedule = {
            "id": schedule_id,
            "source": "GATEWAY_LOCAL",
            "enabled": True,
            "status": "SCHEDULED",
            "nextRunTs": start_at_ms,
            "durationSeconds": duration,
            "repeatEverySeconds": repeat_every,
            "lastRunTs": 0,
            "lastResultReason": "NONE",
            "configCommandId": config_id,
            "configAck": "ACCEPTED",
            "configReason": config_reason,
        }
        return (True, target.valve_state, "EXECUTED")

    def persist(self, path: Path) -> None:
        """Atomically persist the active runtime schedules for restart recovery."""
        with self._lock:
            schedules = list(self.schedules)
            next_fire = dict(self.next_fire_ms)
        payload = {
            "schemaVersion": 1,
            "localSchedules": [
                {
                    "id": item.schedule_id,
                    "zoneId": item.zone_id,
                    "enabled": True,
                    "startAtMs": next_fire.get(item.schedule_id),
                    "durationSeconds": item.duration_seconds,
                    "repeatEverySeconds": item.repeat_every_seconds or 0,
                    "configId": item.config_id,
                }
                for item in schedules
            ],
        }
        AtomicJsonFile(path).save(payload)

    def dispatch_due(
        self, model: "IrrigationController", now_ms: int
    ) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        with self._lock:
            schedules = list(self.schedules)
        for schedule in schedules:
            due_ms = self.next_fire_ms.get(schedule.schedule_id)
            if due_ms is None or now_ms < due_ms:
                continue
            self.sequence += 1
            command = RpcCommand(
                device=model.zones[schedule.zone_id].valve_device,
                request_id=-self.sequence,
                method="TURN_ON",
                params={
                    "commandId": f"smartfarm-local-schedule-{schedule.schedule_id}-{self.sequence}",
                    "source": "SCHEDULER",
                    "requestedAt": now_ms,
                    "ttlSeconds": 30,
                    "runDurationSeconds": schedule.duration_seconds,
                    "scheduleId": schedule.schedule_id,
                },
            )
            accepted, _, ack = model.apply_rpc(command, now_ms=now_ms)
            results.append((schedule.schedule_id, accepted, ack))
            LOG.info(
                "local schedule fired id=%s zone=%s duration=%ss accepted=%s ack=%s",
                schedule.schedule_id,
                schedule.zone_id,
                schedule.duration_seconds,
                accepted,
                ack,
            )
            if schedule.repeat_every_seconds is None:
                self.next_fire_ms[schedule.schedule_id] = None
                next_run_ms = 0
            else:
                interval_ms = schedule.repeat_every_seconds * 1000
                next_run_ms = due_ms + interval_ms
                if next_run_ms <= now_ms:
                    missed = (now_ms - next_run_ms) // interval_ms + 1
                    next_run_ms += missed * interval_ms
                self.next_fire_ms[schedule.schedule_id] = next_run_ms
            schedule_state = model.zone_runtime[schedule.zone_id].local_schedule
            if schedule_state is not None:
                detailed_result = (
                    "SCHEDULE_STARTED"
                    if accepted
                    else model.zones[schedule.zone_id].decision_reason or ack
                )
                schedule_state.update(
                    {
                        "status": "RUNNING" if accepted else "REJECTED",
                        "nextRunTs": next_run_ms,
                        "lastRunTs": now_ms,
                        "lastResultReason": detailed_result,
                    }
                )
        return results
