"""The playbook_invocations ledger must record 'scheduled' for scheduler-driven runs.

Covers: the invocation-source ContextVar helpers, the ChatWrapper ledger write
reading that ContextVar, and the idempotent CHECK-constraint widening in
PlaybookService.ensure_schema().
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.archi.pipelines.agents.tools.playbook_tools import (
    get_invocation_source,
    set_invocation_source,
)
from src.utils.playbook_service import PlaybookService


@pytest.fixture(autouse=True)
def _reset_source():
    yield
    set_invocation_source("explicit")


def test_invocation_source_defaults_to_explicit():
    assert get_invocation_source() == "explicit"


def test_invocation_source_set_and_get():
    set_invocation_source("scheduled")
    assert get_invocation_source() == "scheduled"


def _fake_wrapper_self(svc):
    return SimpleNamespace(_playbook_svc=lambda: svc)


def _context():
    return SimpleNamespace(playbook_name="daily-check", playbook_id=7, conversation_id=42)


def test_ledger_write_uses_default_explicit_source():
    from src.interfaces.chat_app.app import ChatWrapper

    svc = MagicMock()
    ChatWrapper._record_playbook_turn_best_effort(_fake_wrapper_self(svc), 11, _context())
    _, kwargs = svc.record_invocation.call_args
    assert kwargs.get("source") == "explicit"


def test_ledger_write_uses_scheduled_source_when_set():
    from src.interfaces.chat_app.app import ChatWrapper

    svc = MagicMock()
    set_invocation_source("scheduled")
    ChatWrapper._record_playbook_turn_best_effort(_fake_wrapper_self(svc), 11, _context())
    call = svc.record_invocation.call_args
    assert "scheduled" in call.args + tuple(call.kwargs.values())


class _RecordingCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RecordingConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_ensure_schema_widens_source_check_constraint(monkeypatch):
    svc = PlaybookService(pg_config={"dummy": True})
    cur = _RecordingCursor()
    monkeypatch.setattr(svc, "_get_connection", lambda: _RecordingConn(cur))
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)

    svc.ensure_schema()

    joined = " ".join(cur.statements)
    assert "DROP CONSTRAINT IF EXISTS playbook_invocations_source_check" in joined
    assert "'scheduled'" in joined
