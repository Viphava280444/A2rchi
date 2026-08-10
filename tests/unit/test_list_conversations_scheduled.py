"""Unit tests for the is_scheduled flag on the list_conversations endpoint.

Scheduled runs create real conversations; the sidebar folds them into their own
group. The server marks each conversation with a boolean is_scheduled sourced
from playbook_schedule_runs.conversation_id (an EXISTS in SQL), never from the
user-editable title prefix.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg2
from flask import Flask

from src.interfaces.chat_app.app import FlaskAppWrapper


def _wrapper():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.pg_config = {}
    return app, wrapper


def _conn_returning(rows, execute_side_effect=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    if execute_side_effect is not None:
        cursor.execute.side_effect = execute_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_list_conversations_marks_scheduled_flag_both_values():
    app, wrapper = _wrapper()
    now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    rows = [
        (1, "Personal chat", now, now, False),
        (2, "[Scheduled] daily transfers", now, now, True),
    ]
    conn, _ = _conn_returning(rows)

    with app.test_request_context("/api/list_conversations?client_id=c1"):
        with patch("src.interfaces.chat_app.app.psycopg2.connect", return_value=conn):
            resp, status = FlaskAppWrapper.list_conversations(wrapper)

    assert status == 200
    convos = resp.get_json()["conversations"]
    assert convos[0]["is_scheduled"] is False
    assert convos[1]["is_scheduled"] is True


def test_list_conversations_keeps_existing_fields_backward_compatible():
    app, wrapper = _wrapper()
    now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    rows = [(7, "Personal chat", now, now, False)]
    conn, _ = _conn_returning(rows)

    with app.test_request_context("/api/list_conversations?client_id=c1"):
        with patch("src.interfaces.chat_app.app.psycopg2.connect", return_value=conn):
            resp, status = FlaskAppWrapper.list_conversations(wrapper)

    assert status == 200
    convo = resp.get_json()["conversations"][0]
    assert convo["conversation_id"] == 7
    assert convo["title"] == "Personal chat"
    assert convo["created_at"] == now.isoformat()
    assert convo["last_message_at"] == now.isoformat()
    assert convo["is_scheduled"] is False


def test_list_conversations_falls_back_when_schedule_runs_table_missing():
    app, wrapper = _wrapper()
    now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    fallback_rows = [(1, "Personal chat", now, now, False)]
    # First execute (EXISTS variant) raises UndefinedTable; the retry with the
    # no-schedules fallback succeeds and every row degrades to is_scheduled False.
    conn, cursor = _conn_returning(
        fallback_rows,
        execute_side_effect=[psycopg2.errors.UndefinedTable("no such table"), None],
    )

    with app.test_request_context("/api/list_conversations?client_id=c1"):
        with patch("src.interfaces.chat_app.app.psycopg2.connect", return_value=conn):
            resp, status = FlaskAppWrapper.list_conversations(wrapper)

    assert status == 200
    convos = resp.get_json()["conversations"]
    assert convos[0]["is_scheduled"] is False
    # rolled back the aborted transaction and retried with the fallback query
    cursor.connection.rollback.assert_called_once()
    assert cursor.execute.call_count == 2
