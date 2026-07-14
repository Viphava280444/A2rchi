"""Executes due playbook schedules by driving ChatWrapper headlessly.

Identity model: the run acts as the schedule owner. Conversation rows are
created here (not by ChatWrapper) so the title is controlled:
'[Scheduled] <name> — <ts>'. conversation_metadata.user_id carries the owner
only when it exists in users (FK); otherwise the owner IS the client_id
(anonymous deployments) so the conversation stays visible in their sidebar.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import psycopg2

from src.archi.pipelines.agents.tools.playbook_tools import (
    clear_pending_playbook,
    set_invocation_source,
    set_pending_playbook,
    set_playbook_owner,
)
from src.interfaces.playbook_scheduler.verdict import VERDICT_INSTRUCTION, parse_verdict
from src.utils.email_sender import EmailSendError
from src.utils.logging import get_logger
from src.utils.playbook_schedule_service import ScheduleValidationError
from src.utils.playbook_service import PlaybookNotFoundError
from src.utils.sql import SQL_CREATE_CONVERSATION

logger = get_logger(__name__)

SCHEDULER_CLIENT_ID = "playbook-scheduler"


class ScheduleRunner:
    def __init__(self, *, schedule_svc, playbook_svc, chat_callable: Callable,
                 chat_config_name: str, email_sender, pg_config: dict, config: dict):
        self.schedule_svc = schedule_svc
        self.playbook_svc = playbook_svc
        self.chat = chat_callable
        self.chat_config_name = chat_config_name
        self.email = email_sender
        self.pg_config = pg_config
        self.config = config

    # ------------------------------------------------------------------ loop

    def run_pending(self, now_utc: datetime) -> int:
        """Claim and execute everything due. Sequential by design (v1)."""
        executed = 0
        claims = self.schedule_svc.claim_due_schedules(
            now_utc, self.config["catchup_window_minutes"]
        )
        for schedule, trigger in claims:
            if self.schedule_svc.has_recent_running_run(
                schedule.id, self.config["run_timeout_seconds"]
            ):
                logger.warning("Schedule %s still running; skipping this fire", schedule.name)
                self.schedule_svc.record_skipped_overlap(schedule, trigger)
                continue
            try:
                self._execute(schedule, trigger, now_utc)
                executed += 1
            except Exception as exc:  # noqa: BLE001 — batch isolation
                logger.error("Schedule %s execution failed at the batch level: %s",
                             schedule.name, exc, exc_info=True)
        return executed

    # ------------------------------------------------------------------ one run

    def _execute(self, schedule, trigger: str, now_utc: datetime) -> None:
        run_id = self.schedule_svc.start_run(schedule, trigger)
        try:
            self._execute_run(run_id, schedule, trigger, now_utc)
        except Exception as exc:  # noqa: BLE001 — a run must never escape unfinalized
            logger.error("Unexpected error running schedule %s: %s", schedule.name, exc,
                         exc_info=True)
            try:
                self._fail(run_id, schedule, f"unexpected error: {exc}")
            except Exception:  # noqa: BLE001 — best-effort; stale sweep is the backstop
                logger.exception("Could not finalize failed run %s", run_id)

    def _execute_run(self, run_id, schedule, trigger: str, now_utc: datetime) -> None:
        playbook_name = None
        try:
            playbook = self.playbook_svc.get_playbook(
                schedule.owner_id, schedule.playbook_id, include_public=True
            )
            playbook_name = playbook.name
        except PlaybookNotFoundError:
            self._fail(run_id, schedule, "playbook deleted or access revoked")
            return
        except Exception as exc:
            self._fail(run_id, schedule, f"playbook lookup failed: {exc}")
            return

        owner_user_id = self._owner_user_id(schedule.owner_id)
        client_id = SCHEDULER_CLIENT_ID if owner_user_id else schedule.owner_id
        try:
            conversation_id = self._create_conversation(
                schedule, owner_user_id, client_id, now_utc
            )
        except Exception as exc:
            self._fail(run_id, schedule, f"could not create conversation: {exc}",
                       playbook_name=playbook_name)
            return

        content = (
            (schedule.extra_instructions
             or f"Run the '{playbook.name}' playbook and report the result.")
            + "\n\n" + VERDICT_INSTRUCTION
        )

        box: dict = {}

        def _target():
            # ContextVars do not cross bare-thread boundaries (see the A/B
            # threading note in chat_app/app.py) — stage them HERE.
            set_playbook_owner(schedule.owner_id)
            clear_pending_playbook()
            set_pending_playbook(
                playbook.name, playbook.body,
                foreign=playbook.owner_id != schedule.owner_id,
                playbook_id=playbook.id,
            )
            set_invocation_source("scheduled")
            try:
                box["result"] = self.chat(
                    [("User", content)], conversation_id, client_id, False,
                    now_utc, now_utc.timestamp(),
                    float(self.config["run_timeout_seconds"]) + 60.0,
                    self.chat_config_name, user_id=owner_user_id,
                )
            except Exception as exc:  # noqa: BLE001 — recorded, never raised
                box["exception"] = exc

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=float(self.config["run_timeout_seconds"]))

        if worker.is_alive():
            self._fail(run_id, schedule,
                       f"run timeout after {self.config['run_timeout_seconds']}s "
                       "(thread abandoned)", conversation_id=conversation_id,
                       playbook_name=playbook_name)
            return
        if "exception" in box:
            self._fail(run_id, schedule, f"pipeline raised: {box['exception']}",
                       conversation_id=conversation_id, playbook_name=playbook_name)
            return

        output, _cid, _message_ids, _timestamps, error_code = box["result"]
        if error_code is not None:
            self._fail(run_id, schedule, f"chat error code {error_code}",
                       conversation_id=conversation_id, playbook_name=playbook_name)
            return

        verdict = parse_verdict(output)
        banner = None
        if schedule.mode == "alert":
            if verdict is None:
                status = "verdict_unparsed"
                should_send = True  # fail-open
                banner = ("verdict unparseable — sending raw output; this counts "
                          "toward the schedule's failure counter")
            elif verdict["notify"]:
                status, should_send = "success", True
            else:
                status, should_send = "suppressed", False
        else:  # digest
            status, should_send = "success", True

        email_sent, email_error = False, None
        if should_send:
            try:
                # Send-time re-check: the domain allowlist may have tightened
                # since this schedule was created.
                self.schedule_svc.validate_recipients(schedule.recipients)
                self.email.send(
                    schedule.recipients,
                    self._subject(schedule, verdict),
                    output or "",
                    banner=banner,
                    footer=self._footer(schedule, conversation_id),
                )
                email_sent = True
            except (EmailSendError, ScheduleValidationError) as exc:
                status, email_error = "failed", str(exc)

        self.schedule_svc.finalize_run(
            run_id, status=status,
            verdict_notify=None if verdict is None else verdict["notify"],
            email_sent=email_sent, email_error=email_error,
            conversation_id=conversation_id,
            error=None if status != "failed" else (email_error or "failed"),
            playbook_name=playbook_name,
        )
        if status in ("success", "suppressed"):
            self.schedule_svc.reset_failures(schedule.id)
        else:
            self._bump_and_maybe_notify(schedule)

    # ------------------------------------------------------------------ helpers

    def _subject(self, schedule, verdict) -> str:
        base = schedule.subject_prefix or schedule.name
        extra = verdict.get("subject") if verdict else None
        return f"[archi] {base}" + (f": {extra}" if extra else "")

    def _footer(self, schedule, conversation_id) -> str:
        footer = (f"Automated by archi schedule '{schedule.name}', owned by "
                  f"{schedule.owner_id} — content is LLM-generated.")
        base_url = (self.config.get("chat_base_url") or "").rstrip("/")
        if base_url and conversation_id:
            footer += f" Run conversation: {base_url} (conversation {conversation_id})"
        return footer

    def _fail(self, run_id, schedule, error, conversation_id=None, playbook_name=None):
        logger.error("Schedule %s run failed: %s", schedule.name, error)
        self.schedule_svc.finalize_run(
            run_id, status="failed", conversation_id=conversation_id,
            error=error, playbook_name=playbook_name,
        )
        self._bump_and_maybe_notify(schedule)

    def _bump_and_maybe_notify(self, schedule) -> None:
        count, disabled_now = self.schedule_svc.bump_failures(
            schedule.id, self.config["max_consecutive_failures"]
        )
        if disabled_now:
            try:
                # Send-time recipient recheck: a de-allowlisted domain must not
                # receive the disable notice either.
                self.schedule_svc.validate_recipients(schedule.recipients)
                self.email.send(
                    schedule.recipients,
                    f"[archi] schedule '{schedule.name}' disabled after {count} failures",
                    (f"The schedule '{schedule.name}' was automatically disabled after "
                     f"{count} consecutive failed runs. Its owner can re-enable it from "
                     "the archi Settings → Schedules panel after fixing the problem."),
                    footer=self._footer(schedule, None),
                )
            except Exception as exc:  # noqa: BLE001 — best-effort notice
                logger.warning("Could not send auto-disable notice: %s", exc)

    def _owner_user_id(self, owner_id: str) -> Optional[str]:
        """Owner as a users.id when it exists there (authenticated identity);
        None otherwise — conversation_metadata.user_id has an FK on users."""
        conn = psycopg2.connect(**self.pg_config)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE id = %s", (owner_id,))
            return owner_id if cursor.fetchone() else None
        finally:
            conn.close()

    def _create_conversation(self, schedule, owner_user_id, client_id,
                             now_utc: datetime) -> int:
        title = f"[Scheduled] {schedule.name} — {now_utc:%Y-%m-%d %H:%M} UTC"
        conn = psycopg2.connect(**self.pg_config)
        try:
            cursor = conn.cursor()
            cursor.execute(
                SQL_CREATE_CONVERSATION,
                (title, now_utc, now_utc, client_id,
                 os.getenv("APP_VERSION", "unknown"), owner_user_id),
            )
            (conversation_id,) = cursor.fetchone()
            conn.commit()
            return conversation_id
        finally:
            conn.close()
