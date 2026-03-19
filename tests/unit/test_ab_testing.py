import pytest

from src.utils.ab_testing import ABPool, ABPoolError


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
