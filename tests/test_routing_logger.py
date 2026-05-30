"""Unit tests for routing-logger pure log-parsing and report helpers.

The module is loaded by file path because its filename contains a hyphen.
No YAML config or log file on disk is touched; report generation is driven by
in-memory entry dicts.
"""

import pytest

from conftest import load_module_by_path

rl = load_module_by_path("routing_logger", "routing-logger.py")


def test_parse_log_entry_extracts_timestamp_and_fields():
    line = (
        "[2026-05-29T10:00:00] tier=simple method=keyword node=thinkcentre "
        "tokens≈42 kw_conf=0.8 model=qwen2.5:7b duration=1.2s success=True"
    )
    entry = rl.parse_log_entry(line)
    assert entry["timestamp"] == "2026-05-29T10:00:00"
    assert entry["tier"] == "simple"
    assert entry["method"] == "keyword"
    assert entry["node"] == "thinkcentre"
    assert entry["model"] == "qwen2.5:7b"


def test_parse_log_entry_numeric_coercion():
    entry = rl.parse_log_entry("[t] tokens≈42 kw_conf=0.8")
    assert entry["tokens"] == 42
    assert isinstance(entry["tokens"], int)
    assert entry["kw_conf"] == pytest.approx(0.8)
    assert isinstance(entry["kw_conf"], float)


def test_parse_log_entry_boolean_coercion():
    entry = rl.parse_log_entry("[t] success=True")
    assert entry["success"] is True
    entry_false = rl.parse_log_entry("[t] success=False")
    assert entry_false["success"] is False


def test_parse_log_entry_handles_unicode_tokens_key():
    entry = rl.parse_log_entry("[t] tokens≈100")
    assert entry["tokens"] == 100


def test_filter_by_date_keeps_only_target_day():
    entries = [
        {"timestamp": "2026-05-29T09:00:00", "tier": "simple"},
        {"timestamp": "2026-05-28T09:00:00", "tier": "cloud"},
        {"timestamp": "not-a-date", "tier": "broken"},
    ]
    filtered = rl.filter_by_date(entries, date_str="2026-05-29", days_back=1)
    assert len(filtered) == 1
    assert filtered[0]["tier"] == "simple"


def test_generate_daily_report_empty():
    report = rl.generate_daily_report([], "Today")
    assert "No routing decisions" in report


def test_generate_daily_report_counts_totals():
    entries = [
        {"tier": "simple", "node": "thinkcentre", "method": "keyword",
         "success": True, "duration": 1.0, "tokens": 10},
        {"tier": "cloud", "node": "cloud", "method": "token-length",
         "success": False, "duration": 3.0, "tokens": 600},
    ]
    report = rl.generate_daily_report(entries, "Today")
    assert "Total queries routed" in report
    # Two entries: one success, one failure.
    assert "1 / 1" in report
