"""Postgres-backed store for scheduled playbook runs.

Two tables: playbook_schedules (one row per scheduled job) and
playbook_schedule_runs (audit-grade run history with snapshot columns and no FK
to the schedule, mirroring the playbook_invocations philosophy). DDL here must
stay textually identical to src/cli/templates/init.sql section 15.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from croniter import croniter
from psycopg2 import errors as pg_errors
from zoneinfo import ZoneInfo

from src.utils.logging import get_logger

logger = get_logger(__name__)

MODES = ("digest", "alert")
RUN_STATUSES = ("running", "success", "suppressed", "verdict_unparsed", "failed", "skipped_overlap")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_LIMITS = {
    "max_schedules_per_user": 10,
    "min_interval_minutes": 5,
    "allowed_recipient_domains": [],
}


class ScheduleError(Exception):
    pass


class ScheduleValidationError(ScheduleError):
    pass


class ScheduleNotFoundError(ScheduleError):
    pass


class ScheduleConflictError(ScheduleError):
    pass


@dataclass
class PlaybookSchedule:
    id: int
    owner_id: str
    playbook_id: int
    name: str
    cron: str
    timezone: str
    mode: str
    recipients: List[str]
    subject_prefix: Optional[str]
    extra_instructions: Optional[str]
    enabled: bool
    manual_run_requested: bool
    consecutive_failures: int
    last_run_at: Optional[datetime]
    next_run_at: datetime
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


@dataclass
class ScheduleRun:
    id: int
    schedule_id: Optional[int]
    schedule_name: str
    playbook_name: str
    owner_id: str
    trigger: str
    status: str
    verdict_notify: Optional[bool]
    email_sent: bool
    email_error: Optional[str]
    recipients: Optional[List[str]]
    conversation_id: Optional[int]
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


def compute_next_run(cron: str, timezone_name: str, after_utc: datetime) -> datetime:
    """Next cron occurrence strictly after `after_utc`, computed in the schedule's
    IANA timezone (so '0 7 * * *' means 07:00 local across DST), returned as
    aware UTC.

    The cron arithmetic runs in NAIVE local wall-clock space: croniter's
    day-stepping mishandles 23h/25h DST transition days when given aware
    datetimes (off-by-one-hour on the transition day). Nonexistent/ambiguous
    wall times on transition days resolve via zoneinfo's fold=0 semantics.
    """
    tz = ZoneInfo(timezone_name)
    local_after_naive = after_utc.astimezone(tz).replace(tzinfo=None)
    nxt_naive = croniter(cron, local_after_naive).get_next(datetime)
    nxt_local = nxt_naive.replace(tzinfo=tz)
    return nxt_local.astimezone(timezone.utc)


class PlaybookScheduleService:
    def __init__(self, pg_config: Optional[Dict[str, Any]] = None, *,
                 connection_pool=None, limits: Optional[Dict[str, Any]] = None):
        self._pool = connection_pool
        self._pg_config = pg_config
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}

    def _get_connection(self):
        if self._pool:
            return self._pool.get_connection_direct()
        elif self._pg_config:
            return psycopg2.connect(**self._pg_config)
        raise ValueError("No connection pool or pg_config provided")

    def _release_connection(self, conn) -> None:
        if self._pool:
            self._pool.release_connection(conn)
        else:
            conn.close()

    def ensure_schema(self) -> None:
        """Create scheduler tables on pre-existing databases (idempotent)."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS playbook_schedules (
                        id                   SERIAL PRIMARY KEY,
                        owner_id             VARCHAR(200) NOT NULL,
                        playbook_id          INTEGER NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
                        name                 VARCHAR(100) NOT NULL,
                        cron                 VARCHAR(100) NOT NULL,
                        timezone             VARCHAR(64)  NOT NULL DEFAULT 'UTC',
                        mode                 VARCHAR(10)  NOT NULL CHECK (mode IN ('digest','alert')),
                        recipients           JSONB NOT NULL,
                        subject_prefix       VARCHAR(200),
                        extra_instructions   TEXT,
                        enabled              BOOLEAN NOT NULL DEFAULT TRUE,
                        manual_run_requested BOOLEAN NOT NULL DEFAULT FALSE,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        last_run_at          TIMESTAMPTZ,
                        next_run_at          TIMESTAMPTZ NOT NULL,
                        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (owner_id, name)
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_playbook_schedules_due "
                    "ON playbook_schedules(next_run_at) WHERE enabled"
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS playbook_schedule_runs (
                        id              SERIAL PRIMARY KEY,
                        schedule_id     INTEGER,
                        schedule_name   VARCHAR(100) NOT NULL,
                        playbook_name   VARCHAR(100) NOT NULL,
                        owner_id        VARCHAR(200) NOT NULL,
                        trigger         TEXT NOT NULL DEFAULT 'cron' CHECK (trigger IN ('cron','manual')),
                        status          TEXT NOT NULL DEFAULT 'running'
                                        CHECK (status IN ('running','success','suppressed',
                                                          'verdict_unparsed','failed','skipped_overlap')),
                        verdict_notify  BOOLEAN,
                        email_sent      BOOLEAN NOT NULL DEFAULT FALSE,
                        email_error     TEXT,
                        recipients      JSONB,
                        conversation_id INTEGER,
                        error           TEXT,
                        started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        finished_at     TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_playbook_schedule_runs_sched "
                    "ON playbook_schedule_runs(schedule_id, started_at DESC)"
                )
            conn.commit()
        finally:
            self._release_connection(conn)

    # ---------------------------------------------------------------- validation

    def validate(self, name: str, cron: str, timezone_name: str, mode: str,
                 recipients: List[str]) -> None:
        if not name or not isinstance(name, str) or len(name) > 100:
            raise ScheduleValidationError("Schedule name must be 1-100 characters")
        if mode not in MODES:
            raise ScheduleValidationError(f"mode must be one of {MODES}")
        try:
            ZoneInfo(timezone_name)
        except Exception:
            raise ScheduleValidationError(f"Unknown IANA timezone: {timezone_name!r}")
        try:
            probe = croniter(cron, datetime(2026, 1, 1, tzinfo=timezone.utc))
            first = probe.get_next(datetime)
            second = probe.get_next(datetime)
        except Exception:
            raise ScheduleValidationError(f"Invalid cron expression: {cron!r}")
        floor = int(self.limits["min_interval_minutes"])
        if (second - first) < timedelta(minutes=floor):
            raise ScheduleValidationError(
                f"Schedule fires more often than every {floor} minutes"
            )
        self.validate_recipients(recipients)

    def validate_recipients(self, recipients: List[str]) -> None:
        """Syntax + domain-allowlist check. Called at create/update AND re-called
        by the runner at send time (the allowlist may have tightened since)."""
        if not recipients or not isinstance(recipients, list):
            raise ScheduleValidationError("At least one recipient email is required")
        allowed = [d.lower() for d in self.limits.get("allowed_recipient_domains") or []]
        for addr in recipients:
            if not isinstance(addr, str) or not _EMAIL_RE.match(addr):
                raise ScheduleValidationError(f"Invalid recipient email: {addr!r}")
            if allowed and addr.rsplit("@", 1)[1].lower() not in allowed:
                raise ScheduleValidationError(
                    f"Recipient domain not allowed: {addr!r} (allowed: {allowed})"
                )

    # ---------------------------------------------------------------- row mapping

    @staticmethod
    def _row_to_schedule(row) -> PlaybookSchedule:
        recipients = row[7]
        if isinstance(recipients, str):
            recipients = json.loads(recipients)
        return PlaybookSchedule(
            id=row[0], owner_id=row[1], playbook_id=row[2], name=row[3],
            cron=row[4], timezone=row[5], mode=row[6], recipients=recipients,
            subject_prefix=row[8], extra_instructions=row[9], enabled=row[10],
            manual_run_requested=row[11], consecutive_failures=row[12],
            last_run_at=row[13], next_run_at=row[14], created_at=row[15],
            updated_at=row[16],
        )

    _SCHEDULE_COLS = (
        "id, owner_id, playbook_id, name, cron, timezone, mode, recipients, "
        "subject_prefix, extra_instructions, enabled, manual_run_requested, "
        "consecutive_failures, last_run_at, next_run_at, created_at, updated_at"
    )

    # ---------------------------------------------------------------- CRUD

    def create_schedule(self, owner_id: str, playbook_id: int, name: str, cron: str,
                        timezone_name: str, mode: str, recipients: List[str],
                        subject_prefix: Optional[str] = None,
                        extra_instructions: Optional[str] = None) -> PlaybookSchedule:
        self.validate(name, cron, timezone_name, mode, recipients)
        next_run = compute_next_run(cron, timezone_name, datetime.now(timezone.utc))
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM playbook_schedules WHERE owner_id = %s",
                    (owner_id,),
                )
                (count,) = cursor.fetchone() or (0,)
                if count >= int(self.limits["max_schedules_per_user"]):
                    raise ScheduleValidationError(
                        f"Maximum {self.limits['max_schedules_per_user']} schedules per user"
                    )
                cursor.execute(
                    f"""
                    INSERT INTO playbook_schedules
                        (owner_id, playbook_id, name, cron, timezone, mode, recipients,
                         subject_prefix, extra_instructions, next_run_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {self._SCHEDULE_COLS}
                    """,
                    (owner_id, playbook_id, name, cron, timezone_name, mode,
                     json.dumps(recipients), subject_prefix, extra_instructions, next_run),
                )
                row = cursor.fetchone()
            conn.commit()
            return self._row_to_schedule(row)
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise ScheduleConflictError(f"A schedule named '{name}' already exists")
        finally:
            self._release_connection(conn)

    def list_schedules(self, owner_id: str) -> List[PlaybookSchedule]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._SCHEDULE_COLS} FROM playbook_schedules "
                    "WHERE owner_id = %s ORDER BY name ASC",
                    (owner_id,),
                )
                return [self._row_to_schedule(r) for r in cursor.fetchall()]
        finally:
            self._release_connection(conn)

    def list_all_schedules(self) -> List[PlaybookSchedule]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._SCHEDULE_COLS} FROM playbook_schedules ORDER BY owner_id, name"
                )
                return [self._row_to_schedule(r) for r in cursor.fetchall()]
        finally:
            self._release_connection(conn)

    def get_schedule(self, owner_id: str, schedule_id: int) -> PlaybookSchedule:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._SCHEDULE_COLS} FROM playbook_schedules "
                    "WHERE id = %s AND owner_id = %s",
                    (schedule_id, owner_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ScheduleNotFoundError(f"Schedule {schedule_id} not found")
                return self._row_to_schedule(row)
        finally:
            self._release_connection(conn)

    _UPDATABLE = ("name", "cron", "timezone", "mode", "recipients",
                  "subject_prefix", "extra_instructions", "enabled")

    def update_schedule(self, owner_id: str, schedule_id: int, **fields) -> PlaybookSchedule:
        unknown = set(fields) - set(self._UPDATABLE)
        if unknown:
            raise ScheduleValidationError(f"Unknown fields: {sorted(unknown)}")
        current = self.get_schedule(owner_id, schedule_id)
        merged = {
            "name": fields.get("name", current.name),
            "cron": fields.get("cron", current.cron),
            "timezone": fields.get("timezone", current.timezone),
            "mode": fields.get("mode", current.mode),
            "recipients": fields.get("recipients", current.recipients),
        }
        self.validate(merged["name"], merged["cron"], merged["timezone"],
                      merged["mode"], merged["recipients"])
        sets, params = [], []
        for key in self._UPDATABLE:
            if key in fields:
                value = fields[key]
                if key == "recipients":
                    value = json.dumps(value)
                sets.append(f"{key} = %s")
                params.append(value)
        if "cron" in fields or "timezone" in fields:
            sets.append("next_run_at = %s")
            params.append(compute_next_run(merged["cron"], merged["timezone"],
                                           datetime.now(timezone.utc)))
        if fields.get("enabled") is True:
            sets.append("consecutive_failures = 0")
        sets.append("updated_at = NOW()")
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"UPDATE playbook_schedules SET {', '.join(sets)} "
                    f"WHERE id = %s AND owner_id = %s RETURNING {self._SCHEDULE_COLS}",
                    (*params, schedule_id, owner_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ScheduleNotFoundError(f"Schedule {schedule_id} not found")
            conn.commit()
            return self._row_to_schedule(row)
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise ScheduleConflictError(f"A schedule named '{fields.get('name')}' already exists")
        finally:
            self._release_connection(conn)

    def delete_schedule(self, owner_id: str, schedule_id: int) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM playbook_schedules WHERE id = %s AND owner_id = %s",
                    (schedule_id, owner_id),
                )
                if cursor.rowcount == 0:
                    raise ScheduleNotFoundError(f"Schedule {schedule_id} not found")
            conn.commit()
        finally:
            self._release_connection(conn)

    def request_manual_run(self, owner_id: str, schedule_id: int) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE playbook_schedules SET manual_run_requested = TRUE, "
                    "updated_at = NOW() WHERE id = %s AND owner_id = %s",
                    (schedule_id, owner_id),
                )
                if cursor.rowcount == 0:
                    raise ScheduleNotFoundError(f"Schedule {schedule_id} not found")
            conn.commit()
        finally:
            self._release_connection(conn)
