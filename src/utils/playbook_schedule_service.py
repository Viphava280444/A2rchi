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
                # Supports the is_scheduled EXISTS in the sidebar conversation list
                # (chat_app list_conversations), which correlates on conversation_id.
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_playbook_schedule_runs_conversation "
                    "ON playbook_schedule_runs(conversation_id) "
                    "WHERE conversation_id IS NOT NULL"
                )
            conn.commit()
        finally:
            self._release_connection(conn)

    # ---------------------------------------------------------------- validation

    def validate_cron_timezone(self, cron: str, timezone_name: str) -> None:
        """Cron + timezone subset of validate() — shared with the preview path."""
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

    def preview_next_runs(self, cron: str, timezone_name: str, count: int = 3,
                          after_utc: Optional[datetime] = None) -> List[datetime]:
        """Next `count` fire instants (UTC) for a candidate cron + timezone.

        Uses the same validation and compute_next_run() arithmetic as real
        scheduling, so a UI preview can never disagree with the worker.
        """
        self.validate_cron_timezone(cron, timezone_name)
        after = after_utc or datetime.now(timezone.utc)
        instants: List[datetime] = []
        for _ in range(count):
            after = compute_next_run(cron, timezone_name, after)
            instants.append(after)
        return instants

    def validate(self, name: str, cron: str, timezone_name: str, mode: str,
                 recipients: List[str]) -> None:
        if not name or not isinstance(name, str) or len(name) > 100:
            raise ScheduleValidationError("Schedule name must be 1-100 characters")
        if mode not in MODES:
            raise ScheduleValidationError(f"mode must be one of {MODES}")
        self.validate_cron_timezone(cron, timezone_name)
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

    # ---------------------------------------------------------------- claiming

    def claim_due_schedules(self, now_utc: datetime,
                            catchup_window_minutes: int):
        """Atomically claim due schedules. Advancing next_run_at IS the claim:
        the UPDATE is guarded on the previously-read next_run_at, so a second
        scheduler instance (or overlapping poll) claims nothing. Overdue cron
        fires beyond the catch-up window are advanced without executing."""
        claimed = []
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._SCHEDULE_COLS} FROM playbook_schedules "
                    "WHERE enabled AND (next_run_at <= %s OR manual_run_requested)",
                    (now_utc,),
                )
                rows = cursor.fetchall()
                for row in rows:
                    sched = self._row_to_schedule(row)
                    trigger = "manual" if sched.manual_run_requested else "cron"
                    next_run = compute_next_run(sched.cron, sched.timezone, now_utc)
                    cursor.execute(
                        f"""
                        UPDATE playbook_schedules
                           SET next_run_at = %s, manual_run_requested = FALSE,
                               last_run_at = %s, updated_at = NOW()
                         WHERE id = %s AND enabled AND next_run_at = %s
                        RETURNING {self._SCHEDULE_COLS}
                        """,
                        (next_run, now_utc, sched.id, sched.next_run_at),
                    )
                    won = cursor.fetchone()
                    if won is None:
                        continue  # lost the optimistic race or edited meanwhile
                    overdue = now_utc - sched.next_run_at
                    if trigger == "cron" and overdue > timedelta(minutes=catchup_window_minutes):
                        logger.warning(
                            "Schedule %s missed its fire time by %s (> catch-up window); "
                            "skipping to next occurrence", sched.name, overdue,
                        )
                        continue
                    claimed.append((self._row_to_schedule(won), trigger))
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    # ---------------------------------------------------------------- run rows

    def start_run(self, schedule: PlaybookSchedule, trigger: str) -> int:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO playbook_schedule_runs
                        (schedule_id, schedule_name, playbook_name, owner_id,
                         trigger, status, recipients)
                    VALUES (%s, %s, %s, %s, %s, 'running', %s)
                    RETURNING id
                    """,
                    (schedule.id, schedule.name, self._playbook_name_snapshot(schedule),
                     schedule.owner_id, trigger, json.dumps(schedule.recipients)),
                )
                (run_id,) = cursor.fetchone()
            conn.commit()
            return run_id
        finally:
            self._release_connection(conn)

    @staticmethod
    def _playbook_name_snapshot(schedule: PlaybookSchedule) -> str:
        # The runner resolves the live playbook; at insert time we only know the
        # id, so snapshot "id:<n>" and let finalize_run leave it (the runner
        # passes the resolved name via update when it has one).
        return getattr(schedule, "playbook_name", None) or f"id:{schedule.playbook_id}"

    def has_recent_running_run(self, schedule_id: int, timeout_seconds: int) -> bool:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM playbook_schedule_runs "
                    "WHERE schedule_id = %s AND status = 'running' "
                    "AND started_at > NOW() - (%s * INTERVAL '1 second') LIMIT 1",
                    (schedule_id, timeout_seconds),
                )
                return cursor.fetchone() is not None
        finally:
            self._release_connection(conn)

    def finalize_run(self, run_id: int, *, status: str, verdict_notify=None,
                     email_sent: bool = False, email_error=None,
                     conversation_id=None, error=None, playbook_name=None) -> None:
        if status not in RUN_STATUSES:
            raise ScheduleValidationError(f"Unknown run status: {status!r}")
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE playbook_schedule_runs
                       SET status = %s, verdict_notify = %s, email_sent = %s,
                           email_error = %s, conversation_id = %s, error = %s,
                           playbook_name = COALESCE(%s, playbook_name),
                           finished_at = NOW()
                     WHERE id = %s
                    """,
                    (status, verdict_notify, email_sent, email_error,
                     conversation_id, error, playbook_name, run_id),
                )
            conn.commit()
        finally:
            self._release_connection(conn)

    def record_skipped_overlap(self, schedule: PlaybookSchedule, trigger: str) -> None:
        run_id = self.start_run(schedule, trigger)
        self.finalize_run(run_id, status="skipped_overlap",
                          error="previous run of this schedule still in progress")

    # ---------------------------------------------------------------- failures

    def bump_failures(self, schedule_id: int, max_consecutive: int):
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE playbook_schedules
                       SET consecutive_failures = consecutive_failures + 1,
                           enabled = CASE WHEN consecutive_failures + 1 >= %s
                                          THEN FALSE ELSE enabled END,
                           updated_at = NOW()
                     WHERE id = %s
                    RETURNING consecutive_failures, enabled
                    """,
                    (max_consecutive, schedule_id),
                )
                row = cursor.fetchone()
            conn.commit()
            if row is None:
                return (0, False)
            count, still_enabled = row
            return (count, count >= max_consecutive and not still_enabled)
        finally:
            self._release_connection(conn)

    def reset_failures(self, schedule_id: int) -> None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE playbook_schedules SET consecutive_failures = 0, "
                    "updated_at = NOW() WHERE id = %s",
                    (schedule_id,),
                )
            conn.commit()
        finally:
            self._release_connection(conn)

    # ---------------------------------------------------------------- hygiene

    def sweep_stale_runs(self, timeout_seconds: int) -> int:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE playbook_schedule_runs SET status = 'failed', "
                    "error = 'stale: scheduler restarted mid-run', finished_at = NOW() "
                    "WHERE status = 'running' "
                    "AND started_at < NOW() - (%s * INTERVAL '1 second')",
                    (timeout_seconds,),
                )
                swept = cursor.rowcount
            conn.commit()
            return swept
        finally:
            self._release_connection(conn)

    def list_runs(self, owner_id: str, schedule_id: int, limit: int = 50) -> List[ScheduleRun]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, schedule_id, schedule_name, playbook_name, owner_id,
                           trigger, status, verdict_notify, email_sent, email_error,
                           recipients, conversation_id, error, started_at, finished_at
                      FROM playbook_schedule_runs
                     WHERE schedule_id = %s AND owner_id = %s
                     ORDER BY started_at DESC LIMIT %s
                    """,
                    (schedule_id, owner_id, min(int(limit), 200)),
                )
                out = []
                for r in cursor.fetchall():
                    recipients = json.loads(r[10]) if isinstance(r[10], str) else r[10]
                    out.append(ScheduleRun(
                        id=r[0], schedule_id=r[1], schedule_name=r[2], playbook_name=r[3],
                        owner_id=r[4], trigger=r[5], status=r[6], verdict_notify=r[7],
                        email_sent=r[8], email_error=r[9], recipients=recipients,
                        conversation_id=r[11], error=r[12], started_at=r[13], finished_at=r[14],
                    ))
                return out
        finally:
            self._release_connection(conn)
