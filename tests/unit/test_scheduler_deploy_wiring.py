"""Registry entry + template rendering for the playbook-scheduler service."""
from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

TEMPLATES = Path("src/cli/templates")


def _env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=ChainableUndefined)


def test_registry_declares_playbook_scheduler():
    from src.cli.service_registry import ServiceRegistry

    registry = ServiceRegistry()
    svc = registry.get_service("playbook-scheduler")
    assert svc is not None
    assert svc.requires_image is False  # reuses the chatbot image
    assert "chatbot" in svc.requires_services
    assert {"SENDER_SERVER", "SENDER_PORT", "SENDER_USER", "SENDER_PW"} <= set(svc.required_secrets)


def test_compose_renders_scheduler_block_when_enabled():
    tmpl = _env().get_template("base-compose.yaml")
    out = tmpl.render(
        playbook_scheduler_enabled=True, chatbot_enabled=True, postgres_enabled=True,
        chatbot_image="chatbot-test", chatbot_tag="2000", name="test",
        postgres_port=5432, verbosity=3, app_version="x",
        required_secrets=["sender_server", "sender_port", "sender_user", "sender_pw"],
        data_volume_name="archi-test",
    )
    assert "playbook-scheduler:" in out
    assert "service_playbook_scheduler.py" in out
    assert "chatbot-test" in out  # image reuse


def test_compose_omits_scheduler_block_when_disabled():
    tmpl = _env().get_template("base-compose.yaml")
    out = tmpl.render(
        chatbot_enabled=False, postgres_enabled=False, name="test",
        postgres_port=5432, verbosity=3, app_version="x", required_secrets=[],
        data_volume_name="archi-test",
    )
    assert "playbook-scheduler:" not in out


def test_compose_scheduler_block_is_valid_yaml():
    tmpl = _env().get_template("base-compose.yaml")
    out = tmpl.render(
        playbook_scheduler_enabled=True, chatbot_enabled=True, postgres_enabled=True,
        chatbot_image="i", chatbot_tag="t", name="n",
        postgres_port=5432, verbosity=3, app_version="x",
        required_secrets=["sender_user"],
        data_volume_name="v",
        # chatbot's own (pre-existing, unconditional) ports: mapping renders
        # "- :" and breaks YAML parsing unless these are supplied; unrelated
        # to the scheduler block itself but needed for a full-document parse.
        chatbot_port_host=7861, chatbot_port_container=7861,
    )
    parsed = yaml.safe_load(out)
    assert "playbook-scheduler" in parsed["services"]


def test_config_template_renders_scheduler_section():
    tmpl = _env().get_template("base-config.yaml")
    out = tmpl.render(name="test", services={}, data_manager={}, archi={}, global_={})
    assert "playbook_scheduler:" in out
    assert "poll_interval_seconds: 30" in out
    assert "max_consecutive_failures: 3" in out
