#!/bin/python
"""Playbook scheduler service: polls playbook_schedules, runs due playbooks
through the chat pipeline headlessly, emails results. Opt-in container that
reuses the chat image with this entrypoint (see base-compose.yaml)."""
import os
import signal
import threading
import time
from datetime import datetime, timezone

from src.utils.env import read_secret
from src.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

DEFAULTS = {
    "poll_interval_seconds": 30,
    "run_timeout_seconds": 600,
    "catchup_window_minutes": 60,
    "max_schedules_per_user": 10,
    "min_interval_minutes": 5,
    "max_consecutive_failures": 3,
    "default_timezone": "UTC",
    "allowed_recipient_domains": [],
    "email_from_display_name": "archi scheduler",
    "chat_base_url": "",
}


def load_scheduler_config(full_config: dict) -> dict:
    section = (full_config.get("services") or {}).get("playbook_scheduler") or {}
    return {**DEFAULTS, **{k: v for k, v in section.items() if v is not None}}


def run_loop(runner, poll_seconds: float, stop_event, sleep_fn=time.sleep, now_fn=None):
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    while not stop_event.is_set():
        try:
            runner.run_pending(now_fn())
        except Exception as exc:  # noqa: BLE001 — the loop must outlive bad polls
            logger.error("Scheduler poll failed: %s", exc, exc_info=True)
        sleep_fn(poll_seconds)


def _export_llm_secrets():
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
        value = read_secret(key)
        if value:
            os.environ[key] = value


def _wait_for_config(max_tries: int = 60, delay: float = 5.0) -> dict:
    from src.utils.config_access import get_full_config

    for attempt in range(1, max_tries + 1):
        try:
            return get_full_config()
        except Exception as exc:  # config-seed may not have run yet (esp. Helm)
            logger.info("Config not ready (attempt %d/%d): %s", attempt, max_tries, exc)
            time.sleep(delay)
    raise RuntimeError("Config never became ready; is config-seed / postgres up?")


def main():
    _export_llm_secrets()

    from src.utils.postgres_service_factory import PostgresServiceFactory

    factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
    PostgresServiceFactory.set_instance(factory)

    full_config = _wait_for_config()
    cfg = load_scheduler_config(full_config)

    # Schema self-sufficiency: Helm has no config-seed→schema path, and the
    # scheduler may boot before (or without) a chatbot restart.
    try:
        factory.playbook_service.ensure_schema()
    except Exception as exc:
        logger.error("Could not ensure playbook schema: %s", exc)

    from src.utils.playbook_schedule_service import PlaybookScheduleService

    pg_config = {
        "password": read_secret("PG_PASSWORD"),
        **full_config["services"]["postgres"],
    }
    schedule_svc = PlaybookScheduleService(
        pg_config=pg_config,
        limits={
            "max_schedules_per_user": cfg["max_schedules_per_user"],
            "min_interval_minutes": cfg["min_interval_minutes"],
            "allowed_recipient_domains": cfg["allowed_recipient_domains"],
        },
    )
    schedule_svc.ensure_schema()
    swept = schedule_svc.sweep_stale_runs(cfg["run_timeout_seconds"])
    if swept:
        logger.warning("Marked %d stale 'running' rows as failed on startup", swept)

    # Heavy imports last: ChatWrapper builds the whole agent stack.
    from src.interfaces.chat_app.app import ChatWrapper
    from src.interfaces.playbook_scheduler.runner import ScheduleRunner
    from src.utils.email_sender import EmailSender

    chat = ChatWrapper()
    runner = ScheduleRunner(
        schedule_svc=schedule_svc,
        playbook_svc=factory.playbook_service,
        chat_callable=chat,
        chat_config_name=chat.default_config_name,
        email_sender=EmailSender(from_display_name=cfg["email_from_display_name"]),
        pg_config=pg_config,
        config=cfg,
    )

    stop_event = threading.Event()

    def _graceful(signum, frame):
        logger.info("Signal %s received; stopping after current poll", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    logger.info("Playbook scheduler started (poll every %ss)", cfg["poll_interval_seconds"])
    run_loop(runner, cfg["poll_interval_seconds"], stop_event)
    logger.info("Playbook scheduler stopped")


if __name__ == "__main__":
    main()
