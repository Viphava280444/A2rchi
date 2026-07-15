"""ScheduleRunner orchestration — every collaborator mocked."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.utils.playbook_schedule_service import PlaybookSchedule
from src.interfaces.playbook_scheduler.runner import ScheduleRunner, SCHEDULER_CLIENT_ID

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 7, 0, tzinfo=UTC)

ANSWER_TRUE = 'rates look bad\n```json\n{"notify": true, "subject": "failures 85%"}\n```'
ANSWER_FALSE = 'all good\n```json\n{"notify": false}\n```'
ANSWER_NO_VERDICT = "I forgot the JSON block entirely."

CONFIG = {
    "run_timeout_seconds": 600,
    "catchup_window_minutes": 60,
    "max_consecutive_failures": 3,
    "email_from_display_name": "archi scheduler",
    "chat_base_url": "",
}


def make_schedule(mode="digest", **overrides):
    base = dict(
        id=1, owner_id="owner-1", playbook_id=7, name="daily", cron="0 7 * * *",
        timezone="UTC", mode=mode, recipients=["ops@cern.ch"], subject_prefix=None,
        extra_instructions=None, enabled=True, manual_run_requested=False,
        consecutive_failures=0, last_run_at=None, next_run_at=NOW,
        created_at=None, updated_at=None,
    )
    base.update(overrides)
    return PlaybookSchedule(**base)


@pytest.fixture
def deps(monkeypatch):
    schedule_svc = MagicMock()
    schedule_svc.has_recent_running_run.return_value = False
    schedule_svc.start_run.return_value = 99
    schedule_svc.bump_failures.return_value = (1, False)

    playbook = MagicMock()
    playbook.id, playbook.name, playbook.body, playbook.owner_id = 7, "check-rates", "body", "owner-1"
    playbook_svc = MagicMock()
    playbook_svc.get_playbook.return_value = playbook

    chat = MagicMock(return_value=(ANSWER_TRUE, 123, [11, 12], {}, None))
    email = MagicMock()

    monkeypatch.setattr(
        "src.interfaces.playbook_scheduler.runner.ScheduleRunner._create_conversation",
        lambda self, schedule, owner_user_id, client_id, now: 123,
    )
    monkeypatch.setattr(
        "src.interfaces.playbook_scheduler.runner.ScheduleRunner._owner_user_id",
        lambda self, owner: owner,  # treat owner as an authenticated users.id
    )
    runner = ScheduleRunner(
        schedule_svc=schedule_svc, playbook_svc=playbook_svc, chat_callable=chat,
        chat_config_name="default", email_sender=email, pg_config={}, config=CONFIG,
    )
    return runner, schedule_svc, playbook_svc, chat, email


def _run_one(runner, schedule_svc, schedule, trigger="cron"):
    schedule_svc.claim_due_schedules.return_value = [(schedule, trigger)]
    return runner.run_pending(NOW)


def test_digest_always_emails(deps):
    runner, ssvc, _, chat, email = deps
    n = _run_one(runner, ssvc, make_schedule("digest"))
    assert n == 1
    email.send.assert_called_once()
    kwargs = ssvc.finalize_run.call_args.kwargs
    assert kwargs["status"] == "success" and kwargs["email_sent"] is True
    ssvc.reset_failures.assert_called_once_with(1)


def test_alert_true_emails(deps):
    runner, ssvc, _, chat, email = deps
    _run_one(runner, ssvc, make_schedule("alert"))
    email.send.assert_called_once()
    assert ssvc.finalize_run.call_args.kwargs["status"] == "success"


def test_alert_false_suppresses(deps):
    runner, ssvc, _, chat, email = deps
    chat.return_value = (ANSWER_FALSE, 123, [11, 12], {}, None)
    _run_one(runner, ssvc, make_schedule("alert"))
    email.send.assert_not_called()
    assert ssvc.finalize_run.call_args.kwargs["status"] == "suppressed"
    ssvc.reset_failures.assert_called_once()


def test_alert_unparseable_fails_open_with_banner(deps):
    runner, ssvc, _, chat, email = deps
    chat.return_value = (ANSWER_NO_VERDICT, 123, [11, 12], {}, None)
    _run_one(runner, ssvc, make_schedule("alert"))
    email.send.assert_called_once()
    assert email.send.call_args.kwargs.get("banner")
    assert ssvc.finalize_run.call_args.kwargs["status"] == "verdict_unparsed"
    ssvc.bump_failures.assert_called_once()


def test_chat_error_code_marks_failed(deps):
    runner, ssvc, _, chat, email = deps
    chat.return_value = (None, None, None, {}, 500)
    _run_one(runner, ssvc, make_schedule("digest"))
    email.send.assert_not_called()
    assert ssvc.finalize_run.call_args.kwargs["status"] == "failed"
    ssvc.bump_failures.assert_called_once()


def test_email_failure_marks_failed_with_email_error(deps):
    from src.utils.email_sender import EmailSendError
    runner, ssvc, _, chat, email = deps
    email.send.side_effect = EmailSendError("smtp down")
    _run_one(runner, ssvc, make_schedule("digest"))
    kwargs = ssvc.finalize_run.call_args.kwargs
    assert kwargs["status"] == "failed" and "smtp down" in kwargs["email_error"]


def test_send_time_recipient_recheck_blocks_disallowed(deps):
    from src.utils.playbook_schedule_service import ScheduleValidationError
    runner, ssvc, _, chat, email = deps
    ssvc.validate_recipients.side_effect = ScheduleValidationError("domain not allowed")
    _run_one(runner, ssvc, make_schedule("digest"))
    email.send.assert_not_called()
    kwargs = ssvc.finalize_run.call_args.kwargs
    assert kwargs["status"] == "failed" and "domain not allowed" in kwargs["email_error"]


def test_auto_disable_sends_notice_to_recipients(deps):
    from src.utils.playbook_schedule_service import ScheduleValidationError
    runner, ssvc, _, chat, email = deps
    chat.return_value = (None, None, None, {}, 500)
    ssvc.bump_failures.return_value = (3, True)
    _run_one(runner, ssvc, make_schedule("digest"))
    notices = [c for c in email.send.call_args_list if "disabled" in c.args[1].lower()]
    assert len(notices) == 1

    # Finding 2: the disable notice also honours a send-time recipient recheck —
    # a de-allowlisted domain must not receive the notice either.
    email.reset_mock()
    ssvc.validate_recipients.reset_mock()
    ssvc.validate_recipients.side_effect = ScheduleValidationError("domain not allowed")
    _run_one(runner, ssvc, make_schedule("digest"))
    ssvc.validate_recipients.assert_called_once()   # the recheck ran in the notice path
    email.send.assert_not_called()                  # ...and blocked the notice


def test_overlap_skips_without_executing(deps):
    runner, ssvc, _, chat, email = deps
    ssvc.has_recent_running_run.return_value = True
    n = _run_one(runner, ssvc, make_schedule("digest"))
    assert n == 0
    chat.assert_not_called()
    ssvc.record_skipped_overlap.assert_called_once()


def test_playbook_gone_marks_failed(deps):
    from src.utils.playbook_service import PlaybookNotFoundError
    runner, ssvc, psvc, chat, email = deps
    psvc.get_playbook.side_effect = PlaybookNotFoundError("gone")
    _run_one(runner, ssvc, make_schedule("digest"))
    chat.assert_not_called()
    assert ssvc.finalize_run.call_args.kwargs["status"] == "failed"


def test_timeout_marks_failed(deps):
    import time as _time
    runner, ssvc, _, chat, email = deps
    runner.config["run_timeout_seconds"] = 0.05

    def slow(*a, **k):
        _time.sleep(0.5)
        return (ANSWER_TRUE, 123, [11], {}, None)

    chat.side_effect = slow
    _run_one(runner, ssvc, make_schedule("digest"))
    assert ssvc.finalize_run.call_args.kwargs["status"] == "failed"
    assert "timeout" in (ssvc.finalize_run.call_args.kwargs["error"] or "").lower()


def test_unexpected_error_finalizes_failed_and_bumps(deps, monkeypatch):
    """Finding 1a: an unexpected raise past start_run (here in _owner_user_id)
    is caught by the _execute guard — the run is finalized failed with an
    'unexpected error' message, the failure counter is bumped, no email is sent."""
    runner, ssvc, _, chat, email = deps

    def _boom(self, owner):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "src.interfaces.playbook_scheduler.runner.ScheduleRunner._owner_user_id",
        _boom,
    )
    _run_one(runner, ssvc, make_schedule("digest"))

    kwargs = ssvc.finalize_run.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert "unexpected error" in (kwargs["error"] or "")
    ssvc.bump_failures.assert_called_once()
    email.send.assert_not_called()


def test_one_bad_schedule_does_not_abort_batch(deps):
    """Finding 1b: when _execute raises (here start_run fails for the first
    schedule), the run_pending batch guard logs and keeps going, so the second
    schedule still runs to completion. Only the second counts as executed."""
    runner, ssvc, _, chat, email = deps
    bad = make_schedule("digest", id=1, name="bad")
    good = make_schedule("digest", id=2, name="good")
    ssvc.claim_due_schedules.return_value = [(bad, "cron"), (good, "cron")]
    ssvc.start_run.side_effect = [RuntimeError("boom"), 99]

    n = runner.run_pending(NOW)

    assert n == 1                            # only the good schedule counted as executed
    assert ssvc.start_run.call_count == 2    # both were attempted — batch not aborted
    email.send.assert_called_once()          # the good schedule still emailed
    assert ssvc.finalize_run.call_args.kwargs["status"] == "success"


def test_digest_unparseable_verdict_still_success_no_banner(deps):
    """Digest ignores the verdict entirely: an unparseable answer still emails,
    with no banner, marks success, and resets the failure counter."""
    runner, ssvc, _, chat, email = deps
    chat.return_value = (ANSWER_NO_VERDICT, 123, [11, 12], {}, None)
    _run_one(runner, ssvc, make_schedule("digest"))
    email.send.assert_called_once()
    assert email.send.call_args.kwargs.get("banner") is None
    kwargs = ssvc.finalize_run.call_args.kwargs
    assert kwargs["status"] == "success"
    ssvc.reset_failures.assert_called_once_with(1)


def test_alert_html_rendered_verdict_suppresses(deps):
    """Live-smoke finding: ChatWrapper.__call__ can return the server-side
    HTML-rendered answer (syntax-highlighted code boxes) instead of raw
    markdown — the ```json fence never survives rendering, but the JSON
    text content does, chopped into <span>s with &quot; entities. The
    runner must still parse the verdict and suppress on notify: false."""
    runner, ssvc, _, chat, email = deps
    html_answer = (
        '<p>Everything checked out fine this run.</p>\n'
        '<div class="highlight"><pre><span></span><span class="o">{</span>'
        '<span class="s2">&quot;notify&quot;</span>:<span class="w"> </span>'
        '<span class="kc">false</span>,<span class="w"> </span>'
        '<span class="s2">&quot;subject&quot;</span>:<span class="w"> </span>'
        '<span class="s2">&quot;Scheduler smoke check: all quiet&quot;</span>,'
        '<span class="w"> </span><span class="s2">&quot;summary&quot;</span>:'
        '<span class="w"> </span><span class="s2">&quot;No issues detected; '
        'monitoring indicates normal operation.&quot;</span><span class="o">}</span>\n'
        '</pre></div>'
    )
    chat.return_value = (html_answer, 123, [11, 12], {}, None)
    _run_one(runner, ssvc, make_schedule("alert"))
    email.send.assert_not_called()
    assert ssvc.finalize_run.call_args.kwargs["status"] == "suppressed"
    ssvc.reset_failures.assert_called_once()


def test_anonymous_owner_uses_client_id_identity(deps, monkeypatch):
    """Anonymous owner (not a row in users): the client_id passed to chat IS the
    schedule's owner_id, and the user_id kwarg is None."""
    runner, ssvc, _, chat, email = deps
    monkeypatch.setattr(
        "src.interfaces.playbook_scheduler.runner.ScheduleRunner._owner_user_id",
        lambda self, owner: None,
    )
    _run_one(runner, ssvc, make_schedule("digest"))
    chat.assert_called_once()
    args, kwargs = chat.call_args
    assert args[2] == "owner-1"        # client_id positional == owner_id
    assert kwargs["user_id"] is None
