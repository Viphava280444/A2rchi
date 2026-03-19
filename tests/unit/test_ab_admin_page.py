from flask import Flask
from unittest.mock import patch

from src.interfaces.chat_app.app import FlaskAppWrapper


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
