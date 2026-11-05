"""Workspace paths are configurable via CIN_WORKSPACE, not hardcoded."""

from pathlib import Path

import router
from conftest import REPO_ROOT, load_module_by_path

fl = load_module_by_path("feedback_loop_paths", "feedback-loop.py")
rl = load_module_by_path("routing_logger_paths", "routing-logger.py")


def test_expand_path_uses_cin_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("CIN_WORKSPACE", str(tmp_path))
    for mod in (router, fl, rl):
        assert mod.expand_path("${CIN_WORKSPACE}/reports/x.log") == (
            tmp_path / "reports" / "x.log"
        )


def test_expand_path_defaults_to_openclaw_workspace(monkeypatch):
    monkeypatch.delenv("CIN_WORKSPACE", raising=False)
    assert router.expand_path("${CIN_WORKSPACE}/a") == (
        Path("~/.openclaw/workspace/a").expanduser()
    )


def test_config_paths_have_no_hardcoded_workspace():
    cfg = router.load_config()
    for value in cfg["paths"].values():
        assert value.startswith("${CIN_WORKSPACE}/")


def test_service_unit_has_no_hardcoded_home():
    unit = (REPO_ROOT / "sub-agent@.service").read_text()
    assert "/home/" not in unit
    assert "User=%i" in unit
