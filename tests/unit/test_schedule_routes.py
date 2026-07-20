"""Unit tests for the schedules REST Blueprint (schedule_routes.py)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import flask
import pytest

from src.interfaces.chat_app.schedule_routes import register_schedules
from src.utils.playbook_service import PlaybookNotFoundError
from src.utils.playbook_schedule_service import (
    PlaybookSchedule,
    ScheduleNotFoundError,
    ScheduleValidationError,
)


def _passthrough_auth(view):
    return view


def _schedule(**over):
    base = dict(
        id=1, owner_id="owner-1", playbook_id=7, name="daily", cron="0 7 * * *",
        timezone="UTC", mode="digest", recipients=["a@cern.ch"], subject_prefix=None,
        extra_instructions=None, enabled=True, manual_run_requested=False,
        consecutive_failures=0, last_run_at=None,
        next_run_at=datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc),
        created_at=None, updated_at=None,
    )
    base.update(over)
    return PlaybookSchedule(**base)


def _make_app(owner="owner-1", svc=None, playbook_svc=None, admin=False, resolve=None):
    app = flask.Flask(__name__)
    register_schedules(
        app,
        auth_enabled=False,
        require_auth=_passthrough_auth,
        resolve_owner=resolve if resolve is not None else (lambda cid: (owner, None)),
        schedule_svc=lambda: svc if svc is not None else MagicMock(),
        playbook_svc=lambda: playbook_svc if playbook_svc is not None else MagicMock(),
        is_admin=lambda: admin,
    )
    return app


def test_list_returns_owner_schedules():
    svc = MagicMock()
    svc.list_schedules.return_value = [_schedule()]
    client = _make_app(svc=svc).test_client()

    resp = client.get("/api/schedules?client_id=c1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schedules"][0]["name"] == "daily"
    assert data["schedules"][0]["next_run_at"].startswith("2026-07-15")
    svc.list_schedules.assert_called_once_with("owner-1")


def test_list_all_requires_admin():
    svc = MagicMock()
    svc.list_all_schedules.return_value = []
    client = _make_app(svc=svc, admin=False).test_client()
    client.get("/api/schedules?client_id=c1&all=true")
    svc.list_all_schedules.assert_not_called()

    client = _make_app(svc=svc, admin=True).test_client()
    client.get("/api/schedules?client_id=c1&all=true")
    svc.list_all_schedules.assert_called_once()


def test_create_checks_playbook_access_then_creates():
    svc = MagicMock()
    svc.create_schedule.return_value = _schedule()
    playbook_svc = MagicMock()
    client = _make_app(svc=svc, playbook_svc=playbook_svc).test_client()

    resp = client.post("/api/schedules", json={
        "client_id": "c1", "playbook_id": 7, "name": "daily", "cron": "0 7 * * *",
        "timezone": "UTC", "mode": "digest", "recipients": ["a@cern.ch"],
    })

    assert resp.status_code == 200
    playbook_svc.get_playbook.assert_called_once_with("owner-1", 7, include_public=True)
    svc.create_schedule.assert_called_once()


def test_create_maps_validation_error_to_400():
    svc = MagicMock()
    svc.create_schedule.side_effect = ScheduleValidationError("bad cron")
    client = _make_app(svc=svc).test_client()
    resp = client.post("/api/schedules", json={
        "client_id": "c1", "playbook_id": 7, "name": "x", "cron": "nope",
        "timezone": "UTC", "mode": "digest", "recipients": ["a@cern.ch"],
    })
    assert resp.status_code == 400
    assert "bad cron" in resp.get_json()["error"]


def test_create_denied_playbook_404s_and_never_creates():
    svc = MagicMock()
    playbook_svc = MagicMock()
    playbook_svc.get_playbook.side_effect = PlaybookNotFoundError("Playbook 7 not found")
    client = _make_app(svc=svc, playbook_svc=playbook_svc).test_client()

    resp = client.post("/api/schedules", json={
        "client_id": "c1", "playbook_id": 7, "name": "daily", "cron": "0 7 * * *",
        "timezone": "UTC", "mode": "digest", "recipients": ["a@cern.ch"],
    })

    assert resp.status_code == 404
    svc.create_schedule.assert_not_called()


def test_missing_body_is_400():
    client = _make_app().test_client()
    resp = client.post("/api/schedules", data="not json",
                       content_type="text/plain")
    assert resp.status_code == 400


def test_patch_maps_not_found_to_404():
    svc = MagicMock()
    svc.update_schedule.side_effect = ScheduleNotFoundError("Schedule 9 not found")
    client = _make_app(svc=svc).test_client()
    resp = client.patch("/api/schedules/9", json={"client_id": "c1", "enabled": False})
    assert resp.status_code == 404


def test_patch_cannot_smuggle_owner_id():
    # The route strips only client_id before forwarding fields to the service;
    # owner_id would pass through as an ordinary field. The service's
    # _UPDATABLE allowlist (see playbook_schedule_service.py) rejects it as an
    # unknown field, so it can never replace the resolved owner argument.
    svc = MagicMock()
    svc.update_schedule.side_effect = ScheduleValidationError("Unknown fields: ['owner_id']")
    client = _make_app(svc=svc).test_client()

    resp = client.patch("/api/schedules/3", json={
        "client_id": "c1", "owner_id": "victim", "enabled": False,
    })

    assert resp.status_code == 400
    assert svc.update_schedule.call_args == call("owner-1", 3, owner_id="victim", enabled=False)


def test_patch_tolerates_editor_payload_with_playbook_id():
    # The schedule editor always includes playbook_id in its save payload
    # (the field is required for create), even in edit mode where the
    # binding is immutable. The route must strip it like client_id rather
    # than forwarding it to the service and 400ing on "Unknown fields".
    svc = MagicMock()
    svc.update_schedule.return_value = _schedule(name="renamed")
    client = _make_app(svc=svc).test_client()

    resp = client.patch("/api/schedules/3", json={
        "client_id": "c1", "playbook_id": 7, "name": "renamed", "cron": "0 8 * * *",
    })

    assert resp.status_code == 200
    svc.update_schedule.assert_called_once_with("owner-1", 3, name="renamed", cron="0 8 * * *")


def test_delete_schedule_owner_scoped_happy_path():
    svc = MagicMock()
    client = _make_app(svc=svc).test_client()

    resp = client.delete("/api/schedules/5", json={"client_id": "c1"})

    assert resp.status_code == 200
    svc.delete_schedule.assert_called_once_with("owner-1", 5)


def test_delete_schedule_not_found_maps_to_404():
    svc = MagicMock()
    svc.delete_schedule.side_effect = ScheduleNotFoundError("Schedule 5 not found")
    client = _make_app(svc=svc).test_client()

    resp = client.delete("/api/schedules/5", json={"client_id": "c1"})

    assert resp.status_code == 404


def test_run_now_sets_manual_flag():
    svc = MagicMock()
    client = _make_app(svc=svc).test_client()
    resp = client.post("/api/schedules/3/run", json={"client_id": "c1"})
    assert resp.status_code == 200
    svc.request_manual_run.assert_called_once_with("owner-1", 3)


def test_runs_listing():
    svc = MagicMock()
    svc.list_runs.return_value = []
    client = _make_app(svc=svc).test_client()
    resp = client.get("/api/schedules/3/runs?client_id=c1&limit=10")
    assert resp.status_code == 200
    svc.list_runs.assert_called_once_with("owner-1", 3, limit=10)


def test_owner_resolution_failure_short_circuits():
    app = flask.Flask(__name__)
    # jsonify() needs an active app context, so the error response must be
    # built lazily inside resolve_owner (called during request dispatch, once
    # Flask has pushed one) rather than eagerly at test-body scope — the same
    # pattern test_playbook_routes.py::test_auth_gate_applies_per_app uses.
    register_schedules(
        app, auth_enabled=True, require_auth=_passthrough_auth,
        resolve_owner=lambda cid: (None, (flask.jsonify({"error": "who are you"}), 400)),
        schedule_svc=MagicMock(), playbook_svc=MagicMock(), is_admin=lambda: False,
    )
    resp = app.test_client().get("/api/schedules?client_id=c1")
    assert resp.status_code == 400


def test_list_schedules_resolve_owner_crash_maps_to_500():
    def _raiser(cid):
        raise RuntimeError("db down")

    app = flask.Flask(__name__)
    register_schedules(
        app, auth_enabled=False, require_auth=_passthrough_auth,
        resolve_owner=_raiser,
        schedule_svc=MagicMock(), playbook_svc=MagicMock(), is_admin=lambda: False,
    )
    resp = app.test_client().get("/api/schedules?client_id=c1")
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Internal server error"}


def test_register_schedules_is_per_app():
    app1 = flask.Flask("app1")
    app2 = flask.Flask("app2")
    register_schedules(
        app1, auth_enabled=False, require_auth=_passthrough_auth,
        resolve_owner=lambda cid: ("owner-1", None),
        schedule_svc=MagicMock(), playbook_svc=MagicMock(), is_admin=lambda: False,
    )
    register_schedules(
        app2, auth_enabled=False, require_auth=_passthrough_auth,
        resolve_owner=lambda cid: ("owner-2", None),
        schedule_svc=MagicMock(), playbook_svc=MagicMock(), is_admin=lambda: False,
    )
    assert app1.config["SCHEDULES_BLUEPRINT_STATE"]["resolve_owner"]("c1")[0] == "owner-1"
    assert app2.config["SCHEDULES_BLUEPRINT_STATE"]["resolve_owner"]("c1")[0] == "owner-2"
    assert app1.config["SCHEDULES_BLUEPRINT_STATE"] is not app2.config["SCHEDULES_BLUEPRINT_STATE"]


def test_preview_returns_next_instants():
    svc = MagicMock()
    svc.preview_next_runs.return_value = [
        datetime(2026, 7, 21, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc),
    ]
    client = _make_app(svc=svc).test_client()

    resp = client.post("/api/schedules/preview", json={
        "client_id": "c1", "cron": "0 7 * * *", "timezone": "Europe/Zurich",
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["next"]) == 3
    assert data["next"][0] == "2026-07-21T05:00:00+00:00"
    svc.preview_next_runs.assert_called_once_with("0 7 * * *", "Europe/Zurich")


def test_preview_maps_validation_error_to_400():
    svc = MagicMock()
    svc.preview_next_runs.side_effect = ScheduleValidationError("Invalid cron expression: '14 10 * *'")
    client = _make_app(svc=svc).test_client()
    resp = client.post("/api/schedules/preview", json={
        "client_id": "c1", "cron": "14 10 * *", "timezone": "UTC",
    })
    assert resp.status_code == 400
    assert "Invalid cron expression" in resp.get_json()["error"]


def test_preview_requires_json_body():
    client = _make_app().test_client()
    resp = client.post("/api/schedules/preview", data="not json",
                       content_type="text/plain")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Request body must be valid JSON"


def test_preview_defaults_timezone_from_blueprint_state():
    svc = MagicMock()
    svc.preview_next_runs.return_value = []
    client = _make_app(svc=svc).test_client()
    client.post("/api/schedules/preview", json={"client_id": "c1", "cron": "0 7 * * *"})
    svc.preview_next_runs.assert_called_once_with("0 7 * * *", "UTC")


def test_preview_owner_resolution_failure_short_circuits():
    svc = MagicMock()
    client = _make_app(svc=svc, resolve=lambda cid: (None, ({"error": "denied"}, 401))).test_client()
    resp = client.post("/api/schedules/preview", json={"client_id": "bad", "cron": "0 7 * * *"})
    assert resp.status_code == 401
    svc.preview_next_runs.assert_not_called()
