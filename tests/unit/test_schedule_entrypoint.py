"""Entrypoint helpers: config defaults and the poll loop."""
import threading
from unittest.mock import MagicMock

from src.bin.service_playbook_scheduler import DEFAULTS, load_scheduler_config, run_loop


def test_load_scheduler_config_applies_defaults():
    cfg = load_scheduler_config({"services": {}})
    assert cfg["poll_interval_seconds"] == DEFAULTS["poll_interval_seconds"]
    assert cfg["max_consecutive_failures"] == 3


def test_load_scheduler_config_overrides_win():
    cfg = load_scheduler_config(
        {"services": {"playbook_scheduler": {"poll_interval_seconds": 5}}}
    )
    assert cfg["poll_interval_seconds"] == 5
    assert cfg["run_timeout_seconds"] == DEFAULTS["run_timeout_seconds"]


def test_run_loop_polls_until_stopped():
    runner = MagicMock()
    stop = threading.Event()
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) >= 3:
            stop.set()

    run_loop(runner, poll_seconds=7, stop_event=stop, sleep_fn=fake_sleep)

    assert runner.run_pending.call_count == 3
    assert calls == [7, 7, 7]


def test_run_loop_survives_runner_exception():
    runner = MagicMock()
    runner.run_pending.side_effect = RuntimeError("transient")
    stop = threading.Event()

    def fake_sleep(seconds):
        stop.set()

    run_loop(runner, poll_seconds=1, stop_event=stop, sleep_fn=fake_sleep)
    assert runner.run_pending.call_count == 1  # raised but loop didn't die
