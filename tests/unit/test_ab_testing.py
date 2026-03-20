import pytest

from src.cli.managers.config_manager import ConfigurationManager
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
