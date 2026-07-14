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
