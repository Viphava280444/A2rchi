from flask import Flask
from pathlib import Path
from unittest.mock import Mock, patch

from src.interfaces.chat_app.app import ChatWrapper, FlaskAppWrapper
from src.utils.config_service import StaticConfig


def _static_config_with_ab(sample_rate=1.0):
    return StaticConfig(
        deployment_name="ab",
        config_version="1",
        data_path="/root/data",
        embedding_model="dummy",
        embedding_dimensions=384,
        chunk_size=500,
        chunk_overlap=50,
        distance_metric="cosine",
        global_config={"DATA_PATH": "/root/data", "ACCOUNTS_PATH": "/root/accounts"},
        services_config={
            "postgres": {"host": "localhost", "port": 5432},
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "sample_rate": sample_rate,
                    "pool": {
                        "champion": "baseline",
                        "variants": [
                            {"label": "baseline", "agent_spec": "baseline.md"},
                            {"label": "challenger", "agent_spec": "challenger.md"},
                        ],
                    },
                }
            },
        },
        data_manager_config={"sources": {}},
    )


def test_data_viewer_page_passes_ab_manage_flag_to_template():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/data"):
        with patch.object(wrapper, "_can_manage_ab_testing", return_value=True):
            with patch("src.interfaces.chat_app.app.render_template", return_value="ok") as render_template_mock:
                result = FlaskAppWrapper.data_viewer_page(wrapper)

    assert result == "ok"
    render_template_mock.assert_called_once_with("data.html", can_manage_ab_testing=True)


def test_ab_testing_admin_page_requires_manage_permission():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/admin/ab-testing"):
        with patch.object(wrapper, "_can_manage_ab_testing", return_value=False):
            result = FlaskAppWrapper.ab_testing_admin_page(wrapper)

    assert result == ("Forbidden", 403)


def test_ab_testing_admin_page_renders_template_for_admin():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/admin/ab-testing"):
        with patch.object(wrapper, "_can_manage_ab_testing", return_value=True):
            with patch("src.interfaces.chat_app.app.render_template", return_value="ok") as render_template_mock:
                result = FlaskAppWrapper.ab_testing_admin_page(wrapper)

    assert result == "ok"
    render_template_mock.assert_called_once_with("ab_testing.html")


def test_ab_testing_template_includes_theme_init_and_inline_agent_creation():
    template_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/templates/ab_testing.html"
    template = template_path.read_text()

    assert "modules/theme-init.js" in template
    assert 'id="ab-admin-create-agent"' not in template


def test_refresh_runtime_config_uses_local_static_config_snapshot_not_global_accessor():
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.config_service = Mock()
    wrapper.config_service.get_static_config.return_value = _static_config_with_ab(sample_rate=0.4)
    wrapper.chat = Mock()

    with patch("src.interfaces.chat_app.app.get_full_config", side_effect=AssertionError("should not use get_full_config")):
        FlaskAppWrapper._refresh_runtime_config(wrapper)

    assert wrapper.services_config["chat_app"]["ab_testing"]["sample_rate"] == 0.4
    assert wrapper.chat_app_config["ab_testing"]["sample_rate"] == 0.4
    wrapper.chat.reload_static_state.assert_called_once()


def test_chat_reload_static_state_uses_local_static_config_snapshot_not_global_accessor():
    chat = object.__new__(ChatWrapper)
    chat.config_service = Mock()
    chat.config_service.get_static_config.return_value = _static_config_with_ab(sample_rate=0.25)
    chat.refresh_ab_pool = Mock()

    with patch("src.interfaces.chat_app.app.get_full_config", side_effect=AssertionError("should not use get_full_config")):
        ChatWrapper.reload_static_state(chat)

    assert chat.services_config["chat_app"]["ab_testing"]["sample_rate"] == 0.25
    assert chat.global_config["DATA_PATH"] == "/root/data"
    chat.refresh_ab_pool.assert_called_once()


def test_data_template_uses_labeled_header_actions_without_expand_collapse_buttons():
    template_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/templates/data.html"
    template = template_path.read_text()

    assert ">Uploader<" in template
    assert ">Postgres<" in template
    assert ">Refresh<" in template
    assert 'id="expand-all-btn"' not in template
    assert 'id="collapse-all-btn"' not in template
