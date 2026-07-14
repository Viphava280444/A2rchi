"""Unit tests for PlaybookScheduleService — mocked psycopg2, no real DB."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from psycopg2 import errors as pg_errors

from src.utils.playbook_schedule_service import (
    PlaybookSchedule,
    PlaybookScheduleService,
    ScheduleConflictError,
    ScheduleNotFoundError,
    ScheduleValidationError,
)


class FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_values=None, raise_on_sql_prefix=None):
        """raise_on_sql_prefix: optional (prefix, exception_instance) tuple. When
        set, execute() raises that exception the first time a normalized SQL
        statement starts with `prefix` — used to simulate a DB-level error (e.g.
        a UniqueViolation) on a specific statement without touching real psycopg2."""
        self.statements = []
        self._fetchone = list(fetchone_values or [])
        self._fetchall = list(fetchall_values or [])
        self.rowcount = 1
        self._raise_on_sql_prefix = raise_on_sql_prefix

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if self._raise_on_sql_prefix is not None:
            prefix, exc = self._raise_on_sql_prefix
            if normalized.startswith(prefix):
                raise exc

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

    def test_daily_7am_zurich_fall_back_day(self):
        # Oct 25 2026 is the CEST->CET fall-back day (25h long).
        after = datetime(2026, 10, 24, 5, 0, 1, tzinfo=UTC)
        nxt = compute_next_run("0 7 * * *", "Europe/Zurich", after)
        # 07:00 local on Oct 25 is CET (UTC+1) -> 06:00Z, NOT 07:00Z.
        assert nxt == datetime(2026, 10, 25, 6, 0, tzinfo=UTC)

    def test_daily_7am_zurich_spring_forward_day(self):
        # Mar 29 2026 is the CET->CEST spring-forward day (23h long).
        after = datetime(2026, 3, 28, 6, 0, 1, tzinfo=UTC)
        nxt = compute_next_run("0 7 * * *", "Europe/Zurich", after)
        # 07:00 local on Mar 29 is CEST (UTC+2) -> 05:00Z.
        assert nxt == datetime(2026, 3, 29, 5, 0, tzinfo=UTC)

    def test_chained_walk_across_transitions_never_double_fires(self):
        # Feed each result back in as `after` for a year of daily fires spanning
        # both 2026 transitions; every local wall-clock time must be 07:00 sharp
        # and every gap between consecutive UTC fires must be 23-25h.
        tz_name = "Europe/Zurich"
        current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        previous = None
        for _ in range(365):
            current = compute_next_run("0 7 * * *", tz_name, current)
            local = current.astimezone(ZoneInfo(tz_name))
            assert (local.hour, local.minute) == (7, 0)
            if previous is not None:
                gap_hours = (current - previous).total_seconds() / 3600
                assert 23 <= gap_hours <= 25
            previous = current


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

    def test_create_schedule_name_conflict_maps_to_conflict_error(self, monkeypatch):
        cur = FakeCursor(
            fetchone_values=[(0,)],  # quota count
            raise_on_sql_prefix=("INSERT INTO playbook_schedules",
                                 pg_errors.UniqueViolation()),
        )
        svc, conn = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleConflictError, match="daily-transfers"):
            svc.create_schedule(
                "owner-1", 7, "daily-transfers", "0 7 * * *", "Europe/Zurich",
                "digest", ["ops@cern.ch"],
            )

        assert conn.rolled_back
        assert not conn.committed


class TestCrudMethods:
    @staticmethod
    def _row(**overrides):
        """Build a full 17-field playbook_schedules row tuple, matching
        _SCHEDULE_COLS / PlaybookSchedule field order, the way TestCreateSchedule
        does. Pass field names (not column positions) to override defaults."""
        now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        defaults = dict(
            id=1, owner_id="owner-1", playbook_id=7, name="daily-transfers",
            cron="0 7 * * *", timezone="Europe/Zurich", mode="digest",
            recipients=json.dumps(["ops@cern.ch"]), subject_prefix=None,
            extra_instructions=None, enabled=True, manual_run_requested=False,
            consecutive_failures=0, last_run_at=None,
            next_run_at=datetime(2026, 7, 15, 5, 0, tzinfo=UTC),
            created_at=now, updated_at=now,
        )
        defaults.update(overrides)
        return (
            defaults["id"], defaults["owner_id"], defaults["playbook_id"],
            defaults["name"], defaults["cron"], defaults["timezone"],
            defaults["mode"], defaults["recipients"], defaults["subject_prefix"],
            defaults["extra_instructions"], defaults["enabled"],
            defaults["manual_run_requested"], defaults["consecutive_failures"],
            defaults["last_run_at"], defaults["next_run_at"],
            defaults["created_at"], defaults["updated_at"],
        )

    # ---- get_schedule ----

    def test_get_schedule_success(self, monkeypatch):
        row = self._row()
        cur = FakeCursor(fetchone_values=[row])
        svc, _ = make_service(cur, monkeypatch)

        s = svc.get_schedule("owner-1", 1)

        assert isinstance(s, PlaybookSchedule)
        assert s.id == 1
        assert s.owner_id == "owner-1"
        assert s.name == "daily-transfers"
        assert s.cron == "0 7 * * *"
        assert s.timezone == "Europe/Zurich"
        assert s.recipients == ["ops@cern.ch"]
        sql, params = cur.statements[0]
        assert "WHERE id = %s AND owner_id = %s" in sql

    def test_get_schedule_not_found(self, monkeypatch):
        cur = FakeCursor(fetchone_values=[None])
        svc, _ = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleNotFoundError):
            svc.get_schedule("owner-1", 999)

    # ---- list_schedules / list_all_schedules ----

    def test_list_schedules_scopes_to_owner(self, monkeypatch):
        row = self._row()
        cur = FakeCursor(fetchall_values=[[row]])
        svc, _ = make_service(cur, monkeypatch)

        result = svc.list_schedules("owner-1")

        assert len(result) == 1
        assert result[0].name == "daily-transfers"
        sql, params = cur.statements[0]
        assert "WHERE owner_id = %s ORDER BY name" in sql

    def test_list_all_schedules_has_no_owner_filter(self, monkeypatch):
        cur = FakeCursor(fetchall_values=[[]])
        svc, _ = make_service(cur, monkeypatch)

        result = svc.list_all_schedules()

        assert result == []
        sql, params = cur.statements[0]
        assert "owner_id = %s" not in sql
        assert "ORDER BY owner_id, name" in sql

    # ---- update_schedule ----

    def test_update_schedule_rejects_unknown_fields_before_any_sql(self, monkeypatch):
        cur = FakeCursor()
        svc, _ = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleValidationError):
            svc.update_schedule("o", 1, nope=True)

        assert cur.statements == []

    def test_update_schedule_recomputes_next_run_when_cron_changes(self, monkeypatch):
        current_row = self._row()
        updated_row = self._row(cron="0 8 * * *")
        cur = FakeCursor(fetchone_values=[current_row, updated_row])
        svc, conn = make_service(cur, monkeypatch)

        svc.update_schedule("owner-1", 1, cron="0 8 * * *")

        update_sql = next(sql for sql, _ in cur.statements
                           if sql.startswith("UPDATE playbook_schedules"))
        assert "next_run_at = %s" in update_sql
        assert conn.committed

    def test_update_schedule_does_not_recompute_for_unrelated_field(self, monkeypatch):
        current_row = self._row()
        updated_row = self._row(subject_prefix="[URGENT]")
        cur = FakeCursor(fetchone_values=[current_row, updated_row])
        svc, _ = make_service(cur, monkeypatch)

        svc.update_schedule("owner-1", 1, subject_prefix="[URGENT]")

        update_sql = next(sql for sql, _ in cur.statements
                           if sql.startswith("UPDATE playbook_schedules"))
        # "next_run_at" (bare) always appears in the trailing RETURNING column
        # list, so assert on the SET-clause form specifically.
        assert "next_run_at = %s" not in update_sql

    def test_update_schedule_enabled_true_resets_consecutive_failures(self, monkeypatch):
        current_row = self._row(enabled=False, consecutive_failures=3)
        updated_row = self._row(enabled=True, consecutive_failures=0)
        cur = FakeCursor(fetchone_values=[current_row, updated_row])
        svc, _ = make_service(cur, monkeypatch)

        svc.update_schedule("owner-1", 1, enabled=True)

        update_sql = next(sql for sql, _ in cur.statements
                           if sql.startswith("UPDATE playbook_schedules"))
        assert "consecutive_failures = 0" in update_sql

    def test_update_schedule_name_conflict_maps_to_conflict_error(self, monkeypatch):
        current_row = self._row()  # consumed by the internal get_schedule
        cur = FakeCursor(
            fetchone_values=[current_row],
            raise_on_sql_prefix=("UPDATE playbook_schedules",
                                 pg_errors.UniqueViolation()),
        )
        svc, conn = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleConflictError, match="taken-name"):
            svc.update_schedule("owner-1", 1, name="taken-name")

        assert conn.rolled_back
        assert not conn.committed

    # ---- delete_schedule ----

    def test_delete_schedule_success(self, monkeypatch):
        cur = FakeCursor()
        cur.rowcount = 1
        svc, conn = make_service(cur, monkeypatch)

        svc.delete_schedule("owner-1", 1)

        sql, params = cur.statements[0]
        assert "DELETE FROM playbook_schedules WHERE id = %s AND owner_id = %s" in sql
        assert conn.committed

    def test_delete_schedule_not_found(self, monkeypatch):
        cur = FakeCursor()
        cur.rowcount = 0
        svc, _ = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleNotFoundError):
            svc.delete_schedule("owner-1", 999)

    # ---- request_manual_run ----

    def test_request_manual_run_success(self, monkeypatch):
        cur = FakeCursor()
        cur.rowcount = 1
        svc, conn = make_service(cur, monkeypatch)

        svc.request_manual_run("owner-1", 1)

        sql, params = cur.statements[0]
        assert "manual_run_requested = TRUE" in sql
        assert conn.committed

    def test_request_manual_run_not_found(self, monkeypatch):
        cur = FakeCursor()
        cur.rowcount = 0
        svc, _ = make_service(cur, monkeypatch)

        with pytest.raises(ScheduleNotFoundError):
            svc.request_manual_run("owner-1", 999)

    # ---- boundary allows ----

    def test_quota_boundary_allows_one_below_max(self, monkeypatch):
        row = self._row()
        cur = FakeCursor(fetchone_values=[(9,), row])
        svc, _ = make_service(cur, monkeypatch, limits={"max_schedules_per_user": 10})

        s = svc.create_schedule(
            "owner-1", 7, "daily-transfers", "0 7 * * *", "Europe/Zurich",
            "digest", ["ops@cern.ch"],
        )

        assert s.name == "daily-transfers"

    def test_min_interval_boundary_allows_exactly_floor(self, monkeypatch):
        cur = FakeCursor()
        svc, _ = make_service(cur, monkeypatch, limits={"min_interval_minutes": 5})

        svc.validate("ok-name", "*/5 * * * *", "UTC", "digest", ["a@cern.ch"])
