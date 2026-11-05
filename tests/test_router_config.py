"""Tests for router.load_config error handling."""

import pytest

import router


@pytest.mark.parametrize("content", [
    "",                       # empty file -> None
    "- just\n- a list\n",     # wrong top-level type
    "nodes: [unclosed\n",     # malformed YAML
    "nodes: {}\npaths: {}\n", # missing routing_rules
])
def test_bad_config_raises_config_error(tmp_path, content):
    cfg = tmp_path / "routing-config.yaml"
    cfg.write_text(content)
    with pytest.raises(router.ConfigError):
        router.load_config(cfg)


def test_missing_config_raises_config_error(tmp_path):
    with pytest.raises(router.ConfigError, match="not found"):
        router.load_config(tmp_path / "nope.yaml")


def test_shipped_config_loads():
    cfg = router.load_config()
    assert "routing_rules" in cfg
