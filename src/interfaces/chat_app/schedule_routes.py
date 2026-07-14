"""REST Blueprint for playbook schedules — same conventions as playbook_routes.py:
per-app state on app.config (no module globals), blueprint-wide auth probe,
row-scoped ownership inside the service (not-found ≡ access-denied)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from src.utils.logging import get_logger
from src.utils.playbook_service import PlaybookNotFoundError
from src.utils.playbook_schedule_service import (
    ScheduleConflictError,
    ScheduleNotFoundError,
    ScheduleValidationError,
)

logger = get_logger(__name__)

schedules_bp = Blueprint("schedules", __name__)

_STATE_KEY = "SCHEDULES_BLUEPRINT_STATE"


def _state():
    return current_app.config[_STATE_KEY]


def _resolve_owner(client_id):
    return _state()["resolve_owner"](client_id)


def _svc():
    return _state()["schedule_svc"]()


def _playbook_svc():
    return _state()["playbook_svc"]()


def _is_admin():
    try:
        return bool(_state()["is_admin"]())
    except Exception:
        return False


def _serialize(obj):
    data = asdict(obj)
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    data.pop("owner_id", None)  # owner ids double as access credentials
    return data


def _serialize_admin(obj):
    data = _serialize(obj)
    data["owner_id"] = obj.owner_id  # admin listing may show owners
    return data


@schedules_bp.before_request
def _check_auth():
    sentinel = object()
    require_auth = _state()["require_auth"]

    @require_auth
    def _probe():
        return sentinel

    result = _probe()
    if result is not sentinel:
        return result


@schedules_bp.route("/api/schedules", methods=["GET"])
def list_schedules():
    try:
        owner, err = _resolve_owner(request.args.get("client_id"))
        if err:
            return err
        if request.args.get("all") == "true" and _is_admin():
            return jsonify({"schedules": [_serialize_admin(s) for s in _svc().list_all_schedules()]})
        return jsonify({"schedules": [_serialize(s) for s in _svc().list_schedules(owner)]})
    except Exception as exc:
        logger.error(f"Error listing schedules: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@schedules_bp.route("/api/schedules", methods=["POST"])
def create_schedule():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400
        owner, err = _resolve_owner(data.get("client_id"))
        if err:
            return err
        playbook_id = data.get("playbook_id")
        if not isinstance(playbook_id, int):
            return jsonify({"error": "playbook_id (integer) is required"}), 400
        _playbook_svc().get_playbook(owner, playbook_id, include_public=True)
        s = _svc().create_schedule(
            owner, playbook_id,
            data.get("name", ""), data.get("cron", ""),
            data.get("timezone") or _state().get("default_timezone", "UTC"),
            data.get("mode", "digest"),
            data.get("recipients") or [],
            subject_prefix=data.get("subject_prefix"),
            extra_instructions=data.get("extra_instructions"),
        )
        return jsonify({"success": True, "schedule": _serialize(s)}), 200
    except PlaybookNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ScheduleValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except ScheduleConflictError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        logger.error(f"Error creating schedule: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@schedules_bp.route("/api/schedules/<int:schedule_id>", methods=["PATCH"])
def update_schedule(schedule_id):
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400
        owner, err = _resolve_owner(data.get("client_id"))
        if err:
            return err
        # playbook_id rides along from the editor payload but the binding is
        # immutable by design — strip it with client_id rather than 400ing.
        fields = {k: v for k, v in data.items() if k not in ("client_id", "playbook_id")}
        s = _svc().update_schedule(owner, schedule_id, **fields)
        return jsonify({"success": True, "schedule": _serialize(s)}), 200
    except ScheduleNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ScheduleValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except ScheduleConflictError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        logger.error(f"Error updating schedule {schedule_id}: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@schedules_bp.route("/api/schedules/<int:schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    try:
        payload = request.get_json(silent=True) or {}
        owner, err = _resolve_owner(payload.get("client_id") or request.args.get("client_id"))
        if err:
            return err
        _svc().delete_schedule(owner, schedule_id)
        return jsonify({"success": True}), 200
    except ScheduleNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error(f"Error deleting schedule {schedule_id}: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@schedules_bp.route("/api/schedules/<int:schedule_id>/run", methods=["POST"])
def run_schedule_now(schedule_id):
    try:
        payload = request.get_json(silent=True) or {}
        owner, err = _resolve_owner(payload.get("client_id"))
        if err:
            return err
        _svc().request_manual_run(owner, schedule_id)
        return jsonify({"success": True, "queued": True}), 200
    except ScheduleNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error(f"Error queueing manual run for schedule {schedule_id}: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@schedules_bp.route("/api/schedules/<int:schedule_id>/runs", methods=["GET"])
def list_schedule_runs(schedule_id):
    try:
        owner, err = _resolve_owner(request.args.get("client_id"))
        if err:
            return err
        limit = request.args.get("limit", default=50, type=int)
        runs = _svc().list_runs(owner, schedule_id, limit=limit)
        return jsonify({"runs": [_serialize(r) for r in runs]})
    except Exception as exc:
        logger.error(f"Error listing runs for schedule {schedule_id}: {exc}")
        return jsonify({"error": "Internal server error"}), 500


def register_schedules(app, *, auth_enabled, require_auth, resolve_owner,
                       schedule_svc, playbook_svc, is_admin,
                       default_timezone: str = "UTC") -> None:
    """Attach the schedules Blueprint with per-app state (mirrors register_playbooks)."""
    app.config[_STATE_KEY] = {
        "auth_enabled": auth_enabled,
        "require_auth": require_auth,
        "resolve_owner": resolve_owner,
        "schedule_svc": schedule_svc,
        "playbook_svc": playbook_svc,
        "is_admin": is_admin,
        "default_timezone": default_timezone,
    }
    app.register_blueprint(schedules_bp)
