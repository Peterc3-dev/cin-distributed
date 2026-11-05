"""Tests for feedback-loop.py DB loading."""

import json

from conftest import load_module_by_path

fl = load_module_by_path("feedback_loop", "feedback-loop.py")


def _loop(tmp_path, content):
    db_path = tmp_path / "fb.json"
    db_path.write_text(content)
    loop = fl.FeedbackLoop.__new__(fl.FeedbackLoop)
    loop.fb_config = {"learning_weight": 0.1}
    loop.db_path = db_path
    loop.db = loop._load_db()
    return loop


def test_partial_db_without_stats_does_not_crash(tmp_path):
    loop = _loop(tmp_path, json.dumps({"adjustments": {"simple": 0.5}}))
    result = loop.apply_feedback("escalate", "simple", "q")
    assert result["new_adjustments"]["simple"] == 0.4
    assert loop.db["stats"]["total_escalate"] == 1


def test_non_dict_db_falls_back_to_defaults(tmp_path):
    loop = _loop(tmp_path, "[]")
    loop.apply_feedback("demote", "cloud", "q")
    assert loop.db["stats"]["total_demote"] == 1
