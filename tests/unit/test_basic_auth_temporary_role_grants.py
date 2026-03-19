from flask import Flask, session
import pytest
from unittest.mock import patch

from src.cli.managers.config_manager import ConfigurationManager
from src.interfaces.chat_app.app import FlaskAppWrapper


def test_temp_basic_role_grants_require_tracking_metadata():
    manager = object.__new__(ConfigurationManager)

    with pytest.raises(ValueError, match="tracking_id"):
        manager._validate_basic_auth_temporary_role_grants(
            {
                "auth": {
                    "enabled": True,
                    "basic": {
                        "enabled": True,
                        "temporary_role_grants": {
                            "enabled": True,
                            "remove_after": "Remove after testing",
                            "users": {"alice": {"roles": ["ab-admin"]}},
                        },
                    },
                    "auth_roles": {"roles": {"ab-admin": {"permissions": ["config:modify"]}}},
                }
            }
        )


def test_temp_basic_role_grants_require_defined_roles():
    manager = object.__new__(ConfigurationManager)

    with pytest.raises(ValueError, match="undefined role 'ab-admin'"):
        manager._validate_basic_auth_temporary_role_grants(
            {
                "auth": {
                    "enabled": True,
                    "basic": {
                        "enabled": True,
                        "temporary_role_grants": {
                            "enabled": True,
                            "tracking_id": "ab-test",
                            "remove_after": "Remove after testing",
                            "users": {"alice": {"roles": ["ab-admin"]}},
                        },
                    },
                    "auth_roles": {"roles": {"base-user": {"permissions": ["chat:query"]}}},
                }
            }
        )


def test_temp_basic_role_grants_assign_roles_on_basic_login():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.add_url_rule("/chat", "index", lambda: "ok")

    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.salt = "salt"
    wrapper.sso_enabled = False
    wrapper.basic_auth_enabled = True
    wrapper.basic_temp_role_grants = {
        "tracking_id": "ab-test",
        "remove_after": "Remove after testing",
        "users": {"alice": ["ab-admin"]},
    }

    with app.test_request_context("/login", method="POST", data={"username": "alice", "password": "secret"}):
        with patch("src.interfaces.chat_app.app.check_credentials", return_value=True):
            response = FlaskAppWrapper.login(wrapper)

        assert response.status_code == 302
        assert session["auth_method"] == "basic"
        assert session["roles"] == ["ab-admin"]


def test_temp_basic_role_grants_do_not_affect_other_basic_users():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.add_url_rule("/chat", "index", lambda: "ok")

    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.salt = "salt"
    wrapper.sso_enabled = False
    wrapper.basic_auth_enabled = True
    wrapper.basic_temp_role_grants = {
        "tracking_id": "ab-test",
        "remove_after": "Remove after testing",
        "users": {"alice": ["ab-admin"]},
    }

    with app.test_request_context("/login", method="POST", data={"username": "bob", "password": "secret"}):
        with patch("src.interfaces.chat_app.app.check_credentials", return_value=True):
            FlaskAppWrapper.login(wrapper)

        assert session["roles"] == []
