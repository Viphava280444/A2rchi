"""Unit tests for PlaybookScheduleService — mocked psycopg2, no real DB."""
import json
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
        self.rowcount = 1

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


from src.utils.playbook_schedule_service import compute_next_run


UTC = timezone.utc


class TestComputeNextRun:
    def test_daily_7am_zurich_winter(self):
        after = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
        nxt = compute_next_run("0 7 * * *", "Europe/Zurich", after)
        # CET = UTC+1 → 07:00 local == 06:00Z next day
        assert nxt == datetime(2026, 1, 11, 6, 0, tzinfo=UTC)

    def test_daily_7am_zurich_summer_dst(self):
        after = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        nxt = compute_next_run("0 7 * * *", "Europe/Zurich", after)
        # CEST = UTC+2 → 07:00 local == 05:00Z next day
        assert nxt == datetime(2026, 7, 11, 5, 0, tzinfo=UTC)

    def test_result_is_utc_aware(self):
        nxt = compute_next_run("*/30 * * * *", "UTC", datetime(2026, 1, 1, tzinfo=UTC))
        assert nxt.tzinfo is not None and nxt.utcoffset().total_seconds() == 0


class TestValidation:
    def _svc(self, monkeypatch, limits=None, existing_count=0):
        cur = FakeCursor(fetchone_values=[(existing_count,)])
        return make_service(cur, monkeypatch, limits=limits)

    def test_rejects_bad_cron(self, monkeypatch):
        svc, _ = self._svc(monkeypatch)
        with pytest.raises(ScheduleValidationError, match="cron"):
            svc.validate("ok-name", "not a cron", "UTC", "digest", ["a@cern.ch"])

    def test_rejects_bad_timezone(self, monkeypatch):
        svc, _ = self._svc(monkeypatch)
        with pytest.raises(ScheduleValidationError, match="timezone"):
            svc.validate("ok-name", "0 7 * * *", "Mars/Olympus", "digest", ["a@cern.ch"])

    def test_rejects_bad_mode(self, monkeypatch):
        svc, _ = self._svc(monkeypatch)
        with pytest.raises(ScheduleValidationError, match="mode"):
            svc.validate("ok-name", "0 7 * * *", "UTC", "loud", ["a@cern.ch"])

    def test_rejects_empty_and_malformed_recipients(self, monkeypatch):
        svc, _ = self._svc(monkeypatch)
        with pytest.raises(ScheduleValidationError, match="recipient"):
            svc.validate("ok-name", "0 7 * * *", "UTC", "digest", [])
        with pytest.raises(ScheduleValidationError, match="recipient"):
            svc.validate("ok-name", "0 7 * * *", "UTC", "digest", ["not-an-email"])

    def test_enforces_domain_allowlist(self, monkeypatch):
        svc, _ = self._svc(monkeypatch, limits={"allowed_recipient_domains": ["cern.ch"]})
        svc.validate("ok-name", "0 7 * * *", "UTC", "digest", ["ops@cern.ch"])
        with pytest.raises(ScheduleValidationError, match="domain"):
            svc.validate("ok-name", "0 7 * * *", "UTC", "digest", ["x@gmail.com"])

    def test_enforces_min_interval_floor(self, monkeypatch):
        svc, _ = self._svc(monkeypatch, limits={"min_interval_minutes": 5})
        with pytest.raises(ScheduleValidationError, match="often"):
            svc.validate("ok-name", "* * * * *", "UTC", "digest", ["a@cern.ch"])

    def test_rejects_bad_name(self, monkeypatch):
        svc, _ = self._svc(monkeypatch)
        with pytest.raises(ScheduleValidationError, match="name"):
            svc.validate("", "0 7 * * *", "UTC", "digest", ["a@cern.ch"])


class TestCreateSchedule:
    def test_create_inserts_row_and_returns_schedule(self, monkeypatch):
        now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        row = (
            1, "owner-1", 7, "daily-transfers", "0 7 * * *", "Europe/Zurich",
            "digest", json.dumps(["ops@cern.ch"]), None, None, True, False, 0,
            None, datetime(2026, 7, 15, 5, 0, tzinfo=UTC), now, now,
        )
        cur = FakeCursor(fetchone_values=[(0,), row])
        svc, conn = make_service(cur, monkeypatch)

        s = svc.create_schedule(
            "owner-1", 7, "daily-transfers", "0 7 * * *", "Europe/Zurich",
            "digest", ["ops@cern.ch"],
        )

        assert s.name == "daily-transfers"
        assert s.recipients == ["ops@cern.ch"]
        assert conn.committed
        inserted = [sql for sql, _ in cur.statements if sql.startswith("INSERT INTO playbook_schedules")]
        assert len(inserted) == 1

    def test_quota_enforced(self, monkeypatch):
        cur = FakeCursor(fetchone_values=[(10,)])
        svc, _ = make_service(cur, monkeypatch, limits={"max_schedules_per_user": 10})
        with pytest.raises(ScheduleValidationError, match="Maximum"):
            svc.create_schedule("owner-1", 7, "one-more", "0 7 * * *", "UTC", "digest", ["a@cern.ch"])
