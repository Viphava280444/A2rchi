"""Unit tests for PlaybookScheduleService — mocked psycopg2, no real DB."""
from datetime import datetime, timezone

import pytest

from src.utils.playbook_schedule_service import (
    PlaybookSchedule,
    PlaybookScheduleService,
    ScheduleValidationError,
)


class FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_values=None):
        self.statements = []
        self._fetchone = list(fetchone_values or [])
        self._fetchall = list(fetchall_values or [])

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchall(self):
        return self._fetchall.pop(0) if self._fetchall else []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def make_service(cursor, monkeypatch, limits=None):
    svc = PlaybookScheduleService(pg_config={"dummy": True}, limits=limits)
    conn = FakeConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    return svc, conn


def test_ensure_schema_creates_both_tables_and_indexes(monkeypatch):
    cur = FakeCursor()
    svc, conn = make_service(cur, monkeypatch)

    svc.ensure_schema()

    joined = " ".join(sql for sql, _ in cur.statements)
    assert "CREATE TABLE IF NOT EXISTS playbook_schedules" in joined
    assert "CREATE TABLE IF NOT EXISTS playbook_schedule_runs" in joined
    assert "idx_playbook_schedules_due" in joined
    assert "idx_playbook_schedule_runs_sched" in joined
    assert conn.committed
