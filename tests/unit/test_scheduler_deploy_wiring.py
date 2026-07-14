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


def test_deployment_plan_accepts_playbook_scheduler(tmp_path):
    """archi create --services chatbot playbook-scheduler must not crash.

    DeploymentPlan hardcodes its known-services dict; a registry entry alone
    is not enough (reproduced: ValueError 'Unknown service: playbook-scheduler')."""
    from src.cli.utils.service_builder import ServiceBuilder

    plan = ServiceBuilder.build_compose_config(
        name="demo",
        verbosity=3,
        base_dir=tmp_path,
        enabled_services=["chatbot", "playbook-scheduler"],
        secrets={"PG_PASSWORD"},
        tag="dev",
    )

    assert set(plan.get_enabled_services()) == {
        "data-manager",
        "postgres",
        "chatbot",
        "playbook-scheduler",
    }

    template_vars = plan.to_template_vars()
    assert template_vars.get("playbook_scheduler_enabled") is True
    # requires_image=False: the compose block reuses chatbot_image/chatbot_tag
    # directly, so the scheduler's own image fields stay at their natural
    # (unset) defaults rather than pointing at a nonexistent per-service image.
    scheduler_state = plan.get_service("playbook-scheduler")
    assert scheduler_state.image_name == ""
    assert scheduler_state.enabled is True
