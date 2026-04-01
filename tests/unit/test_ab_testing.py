import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.cli.managers.config_manager import ConfigurationManager
from src.interfaces.chat_app.app import ChatWrapper
from src.utils.ab_testing import ABPool, ABPoolError, load_ab_pool_state


def test_ab_pool_from_config_requires_label_and_agent_spec():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md", "model": "gpt-4o"},
                ],
            },
        }
    )

    assert pool.champion_name == "Baseline"
    assert [variant.label for variant in pool.variants] == ["Baseline", "Challenger"]
    assert [variant.agent_spec for variant in pool.variants] == ["baseline.md", "challenger.md"]
    assert pool.pool_info()["variants"] == ["Baseline", "Challenger"]
    assert pool.pool_info()["variant_details"] == [
        {"label": "Baseline", "agent_spec": "baseline.md"},
        {"label": "Challenger", "agent_spec": "challenger.md", "model": "gpt-4o"},
    ]


def test_ab_pool_from_config_rejects_duplicate_labels():
    with pytest.raises(ABPoolError, match="Duplicate variant label"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"label": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Baseline", "agent_spec": "challenger.md"},
                    ],
                },
            }
        )


def test_ab_pool_from_config_rejects_missing_agent_spec():
    with pytest.raises(ABPoolError, match="agent_spec"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"label": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Challenger"},
                    ],
                },
            }
        )


def test_ab_pool_from_config_rejects_name_only_variant_config():
    with pytest.raises(ABPoolError, match="deprecated 'name'"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"name": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Challenger", "agent_spec": "challenger.md"},
                    ],
                },
            }
        )


def test_load_ab_pool_state_allows_incomplete_setup_with_warning():
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                    }
                }
            }
        }
    )

    assert state.pool is None
    assert state.enabled_requested is True
    assert state.warnings
    assert "inactive" in state.warnings[-1].lower()


def test_load_ab_pool_state_requires_ab_agent_specs_to_exist(tmp_path):
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "ab_agents_dir": str(tmp_path),
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        }
    )

    assert state.pool is None
    assert "missing" in state.warnings[-1].lower()


def test_load_ab_pool_state_activates_when_ab_specs_exist(tmp_path):
    (tmp_path / "baseline.md").write_text("---\nname: Baseline\ntools:\n  - search\n---\nBaseline prompt\n")
    (tmp_path / "challenger.md").write_text("---\nname: Challenger\ntools:\n  - search\n---\nChallenger prompt\n")

    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "ab_agents_dir": str(tmp_path),
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        }
    )

    assert state.pool is not None
    assert state.pool.champion_name == "Baseline"


def test_load_ab_pool_state_accepts_database_spec_lookup_callback():
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        },
        agent_spec_exists=lambda filename: filename in {"baseline.md", "challenger.md"},
    )

    assert state.pool is not None
    assert state.pool.champion_name == "Baseline"


def test_ab_pool_targeting_requires_role_and_permission_groups_when_both_are_set():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "target_roles": ["archi-expert"],
            "target_permissions": ["ab:metrics"],
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["ab:metrics"],
    ) is True
    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["chat:query"],
    ) is False
    assert pool.is_targeted_user(
        roles=["base-user"],
        permissions=["ab:metrics"],
    ) is False


def test_ab_pool_targeting_matches_any_role_or_permission_within_each_group():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "target_roles": ["archi-expert", "reviewer"],
            "target_permissions": ["ab:view", "ab:metrics"],
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.is_targeted_user(
        roles=["reviewer"],
        permissions=["ab:view"],
    ) is True
    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["ab:metrics"],
    ) is True


def test_validate_ab_testing_config_allows_incomplete_ui_bootstrap():
    manager = object.__new__(ConfigurationManager)

    manager._validate_ab_testing_config(
        {
            "ab_testing": {
                "enabled": True,
                "sample_rate": 0.2,
            }
        }
    )


def test_chat_refresh_ab_pool_merges_import_warnings_with_pool_state():
    chat = object.__new__(ChatWrapper)
    chat.config = {
        "services": {
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "pool": {
                        "champion": "Baseline",
                        "variants": [
                            {"label": "Baseline", "agent_spec": "baseline.md"},
                            {"label": "Challenger", "agent_spec": "challenger.md"},
                        ],
                    },
                }
            }
        }
    }
    chat.ab_agent_spec_service = Mock()
    chat.ab_agent_spec_service.spec_exists = Mock(return_value=True)
    chat._sync_ab_agent_specs_from_filesystem = Mock(return_value={
        "warnings": ["A/B agent import conflict: baseline.md failed to import"],
        "conflicts": ["baseline.md failed to import"],
        "imported": 0,
        "updated": 0,
        "skipped": 0,
    })

    with patch(
        "src.interfaces.chat_app.app.load_ab_pool_state",
        return_value=SimpleNamespace(
            pool=None,
            warnings=["A/B testing is enabled but inactive because the A/B agent pool is missing: ['baseline.md']."],
            enabled_requested=True,
            agent_dir="/root/archi/ab_agents",
            agent_dir_configured=True,
        ),
    ):
        ChatWrapper.refresh_ab_pool(chat)

    assert chat.ab_pool is None
    assert chat.ab_pool_state.warnings == [
        "A/B agent import conflict: baseline.md failed to import",
        "A/B testing is enabled but inactive because the A/B agent pool is missing: ['baseline.md'].",
    ]
    assert chat.ab_agent_import_diagnostics["conflicts"] == ["baseline.md failed to import"]
