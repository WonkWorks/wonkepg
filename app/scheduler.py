"""Persistent anchored source refresh and XMLTV build scheduling."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.full_build import FULL_OUTPUT_XMLTV, build_full_xmltv
from app.source_manager import refresh_sources


CONFIG_DIR = Path("/app/config")
SETTINGS_PATH = CONFIG_DIR / "settings.json"
CHANNELS_PATH = CONFIG_DIR / "channels.json"
SCHEDULE_UNITS = {"hours", "days"}
DEFAULT_SETTINGS = {
    "schedule": {
        "enabled": False,
        "start_time": "02:30",
        "interval": 6,
        "unit": "hours",
        "run_immediately_on_startup": False,
    },
    "last_scheduled_run": None,
    "last_xmltv_build": None,
    "error_handling": {
        "enabled": False,
        "recipient": "",
        "notify_source_refresh": True,
        "notify_build": True,
        "notify_stale_mappings": True,
        "notify_recovery": True,
    },
    "notification_state": {},
}

_SETTINGS_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_settings_unlocked() -> dict:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("settings must be an object")
        schedule = validate_schedule(settings.get("schedule"))
        last_run = settings.get("last_scheduled_run")
        if last_run is not None and not isinstance(last_run, dict):
            last_run = None
        loaded = deepcopy(settings)
        loaded["schedule"] = schedule
        loaded["last_scheduled_run"] = last_run
        last_build = settings.get("last_xmltv_build")
        loaded["last_xmltv_build"] = (
            deepcopy(last_build) if isinstance(last_build, dict) else None
        )
        error_handling = settings.get("error_handling")
        loaded["error_handling"] = (
            deepcopy(error_handling)
            if isinstance(error_handling, dict)
            else deepcopy(DEFAULT_SETTINGS["error_handling"])
        )
        state = settings.get("notification_state")
        loaded["notification_state"] = (
            deepcopy(state) if isinstance(state, dict) else {}
        )
        return loaded
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return deepcopy(DEFAULT_SETTINGS)


def load_settings() -> dict:
    with _SETTINGS_LOCK:
        return _load_settings_unlocked()


def validate_schedule(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("schedule must be an object")

    enabled = value.get("enabled")
    immediate = value.get("run_immediately_on_startup")
    interval = value.get("interval")
    unit = value.get("unit")
    start_time = value.get("start_time", "02:30")

    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if not isinstance(immediate, bool):
        raise ValueError(
            "run_immediately_on_startup must be true or false"
        )
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or interval != int(interval)
        or interval < 1
        or interval > 10000
    ):
        raise ValueError("interval must be a whole number from 1 to 10000")
    if unit not in SCHEDULE_UNITS:
        raise ValueError("unit must be hours or days")
    if (
        not isinstance(start_time, str)
        or len(start_time) != 5
        or start_time[2] != ":"
        or not start_time[:2].isdigit()
        or not start_time[3:].isdigit()
        or int(start_time[:2]) > 23
        or int(start_time[3:]) not in (0, 30)
    ):
        raise ValueError(
            "start_time must be a 30-minute value from 00:00 to 23:30"
        )

    return {
        "enabled": enabled,
        "start_time": start_time,
        "interval": int(interval),
        "unit": unit,
        "run_immediately_on_startup": immediate,
    }


def _write_settings_unlocked(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing_metadata = (
        SETTINGS_PATH.stat() if SETTINGS_PATH.exists() else None
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".settings.", suffix=".tmp", dir=CONFIG_DIR
    )
    if existing_metadata is not None:
        os.fchmod(descriptor, existing_metadata.st_mode & 0o777)
        os.fchown(
            descriptor, existing_metadata.st_uid, existing_metadata.st_gid
        )
    else:
        os.fchmod(descriptor, 0o664)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(settings, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, SETTINGS_PATH)
        directory = os.open(CONFIG_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def save_schedule(value: object) -> dict:
    schedule = validate_schedule(value)
    with _SETTINGS_LOCK:
        settings = _load_settings_unlocked()
        settings["schedule"] = schedule
        _write_settings_unlocked(settings)
        return deepcopy(settings)


def update_settings(mutator: Callable[[dict], None]) -> dict:
    """Atomically mutate app settings while preserving unrelated keys."""
    with _SETTINGS_LOCK:
        settings = _load_settings_unlocked()
        mutator(settings)
        _write_settings_unlocked(settings)
        return deepcopy(settings)


def _record_last_run(last_run: dict) -> None:
    with _SETTINGS_LOCK:
        settings = _load_settings_unlocked()
        settings["last_scheduled_run"] = last_run
        _write_settings_unlocked(settings)


def _compact_build_result(result: dict) -> dict:
    return {
        "channels_with_programmes": result.get(
            "channels_built_with_programmes"
        ),
        "channels_with_no_schedule": result.get(
            "channels_with_no_schedule"
        ),
        "total_programmes": result.get("total_output_programmes"),
        "total_collisions": result.get("total_collisions"),
        "stale_baseline_count": result.get("stale_baseline_count", 0),
        "stale_baseline_mappings": result.get(
            "stale_baseline_mappings", []
        ),
        "stale_epgshare_count": result.get("stale_epgshare_count", 0),
        "stale_epgtalk_count": result.get("stale_epgtalk_count", 0),
        "stale_epgshare_mappings": result.get(
            "stale_epgshare_mappings", []
        ),
        "stale_epgtalk_mappings": result.get(
            "stale_epgtalk_mappings", []
        ),
        "channels_degraded_to_baseline_only": result.get(
            "channels_degraded_to_baseline_only", 0
        ),
        "parse_validation": result.get("parse_validation"),
        "output_path": result.get("output_path"),
    }


def record_build_status(
    result: dict | None,
    trigger: str,
    error: str | None = None,
) -> dict:
    """Persist a compact, non-sensitive build status for the UI."""
    result = result or {}
    validation = result.get("parse_validation") or {}
    status = {
        "finished_at": _utc_now(),
        "trigger": trigger,
        "success": bool(
            result.get("output_written") and validation.get("valid")
        ),
        "channels_with_programmes": result.get(
            "channels_built_with_programmes"
        ),
        "channels_with_no_schedule": result.get(
            "channels_with_no_schedule"
        ),
        "stale_baseline_count": result.get("stale_baseline_count", 0),
        "stale_epgshare_count": result.get("stale_epgshare_count", 0),
        "stale_epgtalk_count": result.get("stale_epgtalk_count", 0),
        "channels_degraded_to_baseline_only": result.get(
            "channels_degraded_to_baseline_only", 0
        ),
        "total_programmes": result.get("total_output_programmes"),
        "total_collisions": result.get("total_collisions"),
        "error": error,
    }

    def mutate(settings: dict) -> None:
        settings["last_xmltv_build"] = status

    update_settings(mutate)
    return status


def _restore_output(contents: bytes | None) -> None:
    if contents is None:
        FULL_OUTPUT_XMLTV.unlink(missing_ok=True)
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".scheduled-output.",
        suffix=".tmp",
        dir=FULL_OUTPUT_XMLTV.parent,
    )
    os.fchmod(descriptor, 0o644)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, FULL_OUTPUT_XMLTV)
        directory = os.open(FULL_OUTPUT_XMLTV.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _scheduled_workflow() -> dict:
    try:
        refresh = refresh_sources()
    except Exception as error:
        return {
            "success": False,
            "stage": "refresh",
            "refresh": None,
            "build": None,
            "error": f"source refresh failed ({type(error).__name__})",
        }

    source_results = refresh.get("sources") or []
    if not source_results or not all(
        item.get("success") for item in source_results
    ):
        return {
            "success": False,
            "stage": "refresh",
            "refresh": refresh,
            "build": None,
            "error": "one or more source refreshes failed",
        }

    previous_output = (
        FULL_OUTPUT_XMLTV.read_bytes()
        if FULL_OUTPUT_XMLTV.is_file()
        else None
    )
    try:
        matrix = json.loads(CHANNELS_PATH.read_text(encoding="utf-8"))
        build = build_full_xmltv(matrix)
    except Exception as error:
        _restore_output(previous_output)
        try:
            record_build_status(
                None,
                "scheduled",
                f"XMLTV build failed ({type(error).__name__})",
            )
        except Exception:
            pass
        return {
            "success": False,
            "stage": "build",
            "refresh": refresh,
            "build": None,
            "error": f"XMLTV build failed ({type(error).__name__})",
        }

    try:
        record_build_status(build, "scheduled")
    except Exception:
        pass
    build_summary = _compact_build_result(build)
    validation = build_summary.get("parse_validation") or {}
    build_succeeded = bool(
        build.get("output_written") and validation.get("valid")
    )
    if not build_succeeded:
        _restore_output(previous_output)
    return {
        "success": build_succeeded,
        "stage": "complete" if build_succeeded else "build",
        "refresh": refresh,
        "build": build_summary,
        "error": None if build_succeeded else "XMLTV build was not written",
    }


class ScheduleRunner:
    """Run one scheduled workflow at a time."""

    def __init__(
        self,
        workflow: Callable[[], dict] = _scheduled_workflow,
        record_result: Callable[[dict], None] = _record_last_run,
    ):
        self._workflow = workflow
        self._record_result = record_result
        self._run_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._run_lock.locked()

    def run(self) -> dict:
        if not self._run_lock.acquire(blocking=False):
            return {
                "started": False,
                "overlap_prevented": True,
                "error": "scheduled run already in progress",
            }

        started_at = _utc_now()
        try:
            try:
                result = self._workflow()
            except Exception as error:
                result = {
                    "success": False,
                    "stage": "internal",
                    "refresh": None,
                    "build": None,
                    "error": (
                        f"scheduled run failed ({type(error).__name__})"
                    ),
                }
            last_run = {
                "started_at": started_at,
                "finished_at": _utc_now(),
                **result,
            }
            self._record_result(last_run)
            try:
                from app.notifications import process_scheduled_result

                process_scheduled_result(last_run)
            except Exception:
                # Notification delivery must never interfere with refresh/build.
                pass
            return {
                "started": True,
                "overlap_prevented": False,
                **last_run,
            }
        finally:
            self._run_lock.release()


def _local_timezone():
    zone_name = os.environ.get("TZ", "").strip()
    if zone_name:
        try:
            return ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo


def _normalize_local_time(value: datetime) -> datetime:
    """Move a nonexistent DST wall time to its real post-transition time."""
    return value.astimezone(timezone.utc).astimezone(value.tzinfo)


def next_run_time(schedule: dict, now: datetime | None = None) -> datetime:
    """Return the first anchored local occurrence strictly after now."""
    schedule = validate_schedule(schedule)
    local_now = now or datetime.now(_local_timezone())
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=_local_timezone())
    hour, minute = (
        int(part) for part in schedule["start_time"].split(":")
    )
    # Wall-clock arithmetic keeps the configured HH:MM stable across restarts
    # and daylight-saving transitions.
    naive_now = local_now.replace(tzinfo=None)
    anchor = datetime.combine(datetime(1970, 1, 1), time(hour, minute))
    step = timedelta(**{schedule["unit"]: schedule["interval"]})
    elapsed = naive_now - anchor
    steps = max(0, elapsed // step + 1)
    candidate = anchor + steps * step
    return _normalize_local_time(candidate.replace(tzinfo=local_now.tzinfo))


class SchedulerService:
    """Background anchored scheduler managed by the application process."""

    def __init__(self, runner: ScheduleRunner | None = None):
        self.runner = runner or ScheduleRunner()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._next_run_at: str | None = None

    @property
    def next_run_at(self) -> str | None:
        with self._state_lock:
            return self._next_run_at

    def _set_next_run_at(self, value: str | None) -> None:
        with self._state_lock:
            self._next_run_at = value

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._loop, name="wonkepg-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._set_next_run_at(None)

    def reschedule(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        initial = load_settings()["schedule"]
        immediate_pending = bool(
            initial["enabled"]
            and initial["run_immediately_on_startup"]
        )

        while not self._stop.is_set():
            self._wake.clear()
            schedule = load_settings()["schedule"]
            if not schedule["enabled"]:
                self._set_next_run_at(None)
                immediate_pending = False
                self._wake.wait()
                continue

            if immediate_pending:
                immediate_pending = False
                self._set_next_run_at(_utc_now())
                self.runner.run()
                continue

            now = datetime.now(_local_timezone())
            deadline = next_run_time(schedule, now)
            delay = max(
                0.0,
                (
                    deadline.astimezone(timezone.utc)
                    - now.astimezone(timezone.utc)
                ).total_seconds(),
            )
            self._set_next_run_at(deadline.isoformat())
            if self._wake.wait(timeout=delay):
                continue
            if self._stop.is_set():
                break
            self.runner.run()

        self._set_next_run_at(None)


scheduler_service = SchedulerService()


def scheduler_status() -> dict:
    settings = load_settings()
    return {
        "schedule": settings["schedule"],
        "last_scheduled_run": settings["last_scheduled_run"],
        "running": scheduler_service.runner.running,
        "next_run_at": scheduler_service.next_run_at,
    }
