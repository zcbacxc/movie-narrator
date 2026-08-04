# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cron-driven scheduled job submission (v0.9.3).

Runs a background thread that periodically checks persisted schedules
and submits their ``TaskRequest`` template to the task queue whenever
the next run time has arrived.

The cron parser in this module is deliberately small: it supports the
standard five fields (minute, hour, day-of-month, month, day-of-week)
with ``*``, ``*/n``, ``a,b,c`` lists and ``a-b`` ranges. No third-party
dependency is introduced.

Typical usage::

    from movie_narrator.cloud import JobScheduler, ScheduleRequest

    scheduler = JobScheduler(queue=queue, poll_interval=15.0)
    scheduler.register_schedule("*/5 * * * *", TaskRequest(movie_name="Forrest Gump"))
    scheduler.start()
    ...
    scheduler.stop()
"""

from __future__ import annotations

import logging
import threading
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import TaskRequest
from .storage import JsonModelStore

logger = logging.getLogger(__name__)

#: Maximum days scanned forward when computing the next run time.
_MAX_SCAN_DAYS = 367

#: Cron field bounds: ``(lower, upper)`` for each of the five fields.
_FIELD_BOUNDS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day-of-month": (1, 31),
    "month": (1, 12),
    "day-of-week": (0, 6),  # 0 = Sunday, matching standard cron
}

#: Total cap on persisted run records across all schedules.
_MAX_PERSISTED_RUNS = 1000


class ScheduleError(Exception):
    """Raised when a cron expression is invalid.

    Includes the offending expression and a human-readable reason, so a
    ``POST /schedules`` with a bad ``cron`` string can answer ``400``.
    """


class ScheduleRequest(BaseModel):
    """A scheduled job: a task template fired on a cron schedule.

    ``next_run_at`` is maintained by :class:`JobScheduler`; it is the
    only field that changes after creation.
    """

    schedule_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    cron: str
    task_request: TaskRequest
    enabled: bool = True
    next_run_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: _utc_now_iso())


class ScheduleRun(BaseModel):
    """Record of a single scheduled trigger."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    schedule_id: str
    run_at: str
    task_id: Optional[str] = None
    status: str = "submitted"  # submitted | failed
    error: Optional[str] = None


def _utc_now_iso() -> str:
    """
    Returns:
        The current UTC time in ISO-8601 format.
    """
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, assuming UTC when no tz is present."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Cron parser ────────────────────────────────────────────


class CronField:
    """One field of a cron expression.

    Args:
        wildcard: True when the field matches every value (``*``).
        values: Explicitly allowed values (lists and ranges).
    """

    __slots__ = ("wildcard", "values")

    def __init__(self, wildcard: bool, values: frozenset[int]) -> None:
        self.wildcard = wildcard
        self.values = values

    def matches(self, value: int) -> bool:
        """
        Returns:
            True if ``value`` is allowed by this field.
        """
        return self.wildcard or value in self.values


def _parse_cron_field(expr: str, lo: int, hi: int, name: str) -> CronField:
    """Parse one cron field into a set of allowed values.

    Supported syntax per comma-separated segment: ``*``, ``*/n``,
    a single number ``a``, a range ``a-b`` and a stepped range ``a-b/n``.

    Raises:
        ScheduleError: if the field is syntactically invalid or contains
            a value outside ``[lo, hi]``.
    """
    values: set[int] = set()
    wildcard = False
    for segment in expr.split(","):
        segment = segment.strip()
        if not segment:
            raise ScheduleError(f"empty segment in cron field {name!r}")
        if segment == "*":
            wildcard = True
            continue
        if segment.startswith("*/"):
            step_str = segment[2:]
            if not step_str.isdigit() or int(step_str) <= 0:
                raise ScheduleError(f"invalid step {step_str!r} in cron field {name!r}")
            values.update(range(lo, hi + 1, int(step_str)))
            continue
        if "-" in segment:
            range_part = segment
            step = 1
            if "/" in segment:
                range_part, step_str = segment.split("/", 1)
                if not step_str.isdigit() or int(step_str) <= 0:
                    raise ScheduleError(f"invalid step {step_str!r} in cron field {name!r}")
                step = int(step_str)
            if "-" not in range_part:
                raise ScheduleError(f"invalid range {segment!r} in cron field {name!r}")
            start_s, end_s = range_part.split("-", 1)
            if not start_s.isdigit() or not end_s.isdigit():
                raise ScheduleError(f"invalid range {segment!r} in cron field {name!r}")
            start, end = int(start_s), int(end_s)
            if start > end:
                raise ScheduleError(f"range {segment!r} is reversed in cron field {name!r}")
            _check_bounds(start, lo, hi, name)
            _check_bounds(end, lo, hi, name)
            values.update(range(start, end + 1, step))
            continue
        if not segment.isdigit():
            raise ScheduleError(f"invalid token {segment!r} in cron field {name!r}")
        value = int(segment)
        _check_bounds(value, lo, hi, name)
        values.add(value)
    if not values and not wildcard:
        raise ScheduleError(f"cron field {name!r} is empty")
    return CronField(wildcard=wildcard, values=frozenset(values))


def _check_bounds(value: int, lo: int, hi: int, name: str) -> None:
    """Validate a numeric cron field value against its bounds."""
    if value < lo or value > hi:
        raise ScheduleError(f"value {value} out of range {lo}-{hi} in cron field {name!r}")


class CronExpression:
    """A parsed standard five-field cron expression.

    The day-of-month / day-of-week interaction follows classic cron:
    when *both* fields are restricted, a day matches when **either** one
    matches; when only one is restricted, the other is treated as ``*``.
    """

    def __init__(
        self,
        minute: CronField,
        hour: CronField,
        day_of_month: CronField,
        month: CronField,
        day_of_week: CronField,
    ) -> None:
        self.minute = minute
        self.hour = hour
        self.day_of_month = day_of_month
        self.month = month
        self.day_of_week = day_of_week

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        """Parse a ``"minute hour dom month dow"`` cron expression.

        Raises:
            ScheduleError: for expressions without exactly five fields
                or with any invalid field.
        """
        parts = expression.split()
        if len(parts) != 5:
            raise ScheduleError(
                f"cron expression {expression!r} must have 5 fields "
                "(minute hour day-of-month month day-of-week), "
                f"got {len(parts)}"
            )
        return cls(
            minute=_parse_cron_field(parts[0], *_FIELD_BOUNDS["minute"], "minute"),
            hour=_parse_cron_field(parts[1], *_FIELD_BOUNDS["hour"], "hour"),
            day_of_month=_parse_cron_field(
                parts[2], *_FIELD_BOUNDS["day-of-month"], "day-of-month"
            ),
            month=_parse_cron_field(parts[3], *_FIELD_BOUNDS["month"], "month"),
            day_of_week=_parse_cron_field(parts[4], *_FIELD_BOUNDS["day-of-week"], "day-of-week"),
        )

    def _day_matches(self, date: date_type) -> bool:
        """Check the month / day-of-month / day-of-week fields."""
        if not self.month.matches(date.month):
            return False
        dom_ok = self.day_of_month.matches(date.day)
        # cron day-of-week: 0=Sunday … 6=Saturday; Python: Monday=0 …
        dow = (date.weekday() + 1) % 7
        dow_ok = self.day_of_week.matches(dow)
        if not self.day_of_month.wildcard and not self.day_of_week.wildcard:
            return dom_ok or dow_ok
        if not self.day_of_month.wildcard:
            return dom_ok
        if not self.day_of_week.wildcard:
            return dow_ok
        return True

    def matches(self, dt: datetime) -> bool:
        """
        Returns:
            True if ``dt`` falls on a scheduled instant.
        """
        return (
            self.minute.matches(dt.minute)
            and self.hour.matches(dt.hour)
            and self._day_matches(dt.date())
        )

    def next_after(self, dt: datetime) -> datetime:
        """
        Returns:
            The next scheduled time strictly after ``dt``.

            Times are compared at minute resolution. Raises ``ScheduleError``
            when no matching instant exists within :data:`_MAX_SCAN_DAYS`
            (e.g. a ``Feb 30`` expression).
        """
        for offset in range(_MAX_SCAN_DAYS):
            date = dt.date() + timedelta(days=offset)
            if not self._day_matches(date):
                continue
            hours = range(24) if self.hour.wildcard else sorted(self.hour.values)
            minutes = range(60) if self.minute.wildcard else sorted(self.minute.values)
            for hour in hours:
                for minute in minutes:
                    candidate = datetime(
                        date.year,
                        date.month,
                        date.day,
                        hour,
                        minute,
                        tzinfo=dt.tzinfo,
                    )
                    if candidate > dt:
                        return candidate
        raise ScheduleError(f"no next run time for cron expression within {_MAX_SCAN_DAYS} days")


# ── JobScheduler ───────────────────────────────────────────


class JobScheduler:
    """Cron-driven task submission loop.

    Owns the persisted schedule set and a background thread that polls
    for due schedules every ``poll_interval`` seconds. The thread uses a
    ``threading.Event`` so it never busy-waits and wakes immediately on
    :meth:`stop`.

    Args:
        queue: The task queue used to submit jobs (must implement
            ``submit(request) -> task_id``).
        storage_dir: Directory for schedule persistence. Defaults to
            ``~/.mn_tasks`` (``schedules.json`` + ``schedule_runs.json``).
        poll_interval: Seconds between due-check cycles.
        max_runs_per_schedule: Cap on the run records kept per schedule.
    """

    def __init__(
        self,
        *,
        queue,
        storage_dir: Optional[Path] = None,
        poll_interval: float = 15.0,
        max_runs_per_schedule: int = 50,
    ) -> None:
        self._queue = queue
        self._poll_interval = poll_interval
        self._max_runs = max_runs_per_schedule
        base = Path(storage_dir) if storage_dir else Path.home() / ".mn_tasks"
        base.mkdir(parents=True, exist_ok=True)
        self._store = JsonModelStore(
            base, "schedules.json", ScheduleRequest, key_field="schedule_id"
        )
        self._runs_store = JsonModelStore(
            base, "schedule_runs.json", ScheduleRun, key_field="run_id"
        )
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        """Whether the scheduler loop thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ── CRUD ───────────────────────────────────────────────

    def register_schedule(
        self,
        cron: str,
        task_request: TaskRequest,
        *,
        enabled: bool = True,
        schedule_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ScheduleRequest:
        """Create and persist a scheduled job.

        Args:
            cron: A standard five-field cron expression.
            task_request: Template submitted on every trigger.
            enabled: Whether the schedule starts active.
            schedule_id: Explicit ID (else a random one is generated).
            now: Base time for computing ``next_run_at`` (test hook).

        Returns:
            The persisted :class:`ScheduleRequest` with ``next_run_at``
            already populated.

        Raises:
            ScheduleError: if ``cron`` is invalid.
        """
        expression = CronExpression.parse(cron)  # validate eagerly
        schedule = ScheduleRequest(
            schedule_id=schedule_id or uuid4().hex[:12],
            cron=cron,
            task_request=task_request,
            enabled=enabled,
            next_run_at=expression.next_after(now or datetime.now(timezone.utc)).isoformat(),
        )
        self._store.save(schedule)
        return schedule

    def cancel_schedule(self, schedule_id: str) -> bool:
        """Remove a scheduled job. Returns True if it existed."""
        return self._store.delete(schedule_id)

    def get_schedule(self, schedule_id: str) -> Optional[ScheduleRequest]:
        """Fetch one schedule, or None."""
        return self._store.load(schedule_id)

    def list_schedules(self) -> List[ScheduleRequest]:
        """List all schedules, newest first."""
        schedules = self._store.list()
        schedules.sort(key=lambda s: s.created_at, reverse=True)
        return schedules

    def get_runs(self, schedule_id: str) -> List[ScheduleRun]:
        """
        Returns:
            The most recent trigger records for one schedule.
        """
        runs = [r for r in self._runs_store.list() if r.schedule_id == schedule_id]
        runs.sort(key=lambda r: r.run_at, reverse=True)
        return runs[: self._max_runs]

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler loop in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mn-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Job scheduler started (poll interval %.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Stop the scheduler loop and join the thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_loop(self) -> None:
        """Main scheduler loop — never busy-waits."""
        try:
            while not self._stop_event.is_set():
                try:
                    self.check_due()
                except Exception:  # noqa: BLE001 — a bad cycle must not kill the loop
                    logger.exception("Scheduler due-check cycle failed")
                self._stop_event.wait(timeout=self._poll_interval)
        finally:
            logger.info("Job scheduler stopped")

    # ── Due-checking ───────────────────────────────────────

    def check_due(self, now: Optional[datetime] = None) -> List[ScheduleRun]:
        """Submit jobs for every due schedule.

        Returns the list of run records created in this cycle. Exposed
        separately from :meth:`start` so it is testable without threads.

        Args:
            now: Base time for the due check (test hook).
        """
        now = now or datetime.now(timezone.utc)
        schedules = self._store.list()
        runs: List[ScheduleRun] = []
        for schedule in schedules:
            if not schedule.enabled or not schedule.next_run_at:
                continue
            if _parse_dt(schedule.next_run_at) <= now:
                runs.append(self._trigger(schedule, now))
        return runs

    def _trigger(self, schedule: ScheduleRequest, now: datetime) -> ScheduleRun:
        """Submit one schedule's task template and advance ``next_run_at``."""
        task_id: Optional[str] = None
        status = "submitted"
        error: Optional[str] = None
        try:
            task_id = self._queue.submit(schedule.task_request)
        except Exception as e:  # noqa: BLE001 — a failed trigger must not stop the loop
            status = "failed"
            error = str(e)
            logger.warning("Schedule %s: job submission failed: %s", schedule.schedule_id, e)

        try:
            expression = CronExpression.parse(schedule.cron)
            schedule.next_run_at = expression.next_after(now).isoformat()
        except ScheduleError:
            # A schedule that no longer parses cannot fire again — disable
            # it rather than erroring every cycle.
            logger.error(
                "Schedule %s: invalid cron %r — disabling",
                schedule.schedule_id,
                schedule.cron,
            )
            schedule.enabled = False
        self._store.save(schedule)

        run = ScheduleRun(
            schedule_id=schedule.schedule_id,
            run_at=now.isoformat(),
            task_id=task_id,
            status=status,
            error=error,
        )
        self._record_run(run)
        return run

    def _record_run(self, run: ScheduleRun) -> None:
        """Persist a run record, trimming the store to a bounded size."""
        self._runs_store.save(run)
        if self._runs_store.count() <= _MAX_PERSISTED_RUNS:
            return
        all_runs = sorted(self._runs_store.list(), key=lambda r: r.run_at)
        for old in all_runs[: len(all_runs) - _MAX_PERSISTED_RUNS]:
            self._runs_store.delete(old.run_id)
