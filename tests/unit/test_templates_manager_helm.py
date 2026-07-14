"""_stage_service_artifacts must tolerate services that ship no service.yaml
(headless workers like playbook-scheduler expose no ports)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import TemplateNotFound


def test_stage_service_artifacts_skips_missing_service_yaml(tmp_path):
    from src.cli.managers.templates_manager import TemplateManager

    manager = TemplateManager.__new__(TemplateManager)  # no full init needed
    manager.env = MagicMock()
    manager.env.get_template.side_effect = TemplateNotFound("playbook-scheduler/service.yaml")

    context = MagicMock()
    context.helm = True
    context.base_dir = tmp_path
    context.plan.get_enabled_services.return_value = ["playbook-scheduler"]
    context.plan.name = "test"

    # must not raise
    manager._stage_service_artifacts(context)
    assert not (tmp_path / "templates" / "playbook-scheduler-service.yaml").exists()


def test_scheduler_helm_deployment_template_exists_and_renders():
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("src/cli/templates"),
                      undefined=ChainableUndefined)
    out = env.get_template("helm/templates/playbook-scheduler/deployment.yaml").render(name="demo")
    assert "demo-playbook-scheduler" in out
    assert "{{ .Values.chat.image }}" in out          # Helm-side reference survives
    assert "service_playbook_scheduler.py" in out
