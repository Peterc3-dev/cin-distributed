"""Unit tests for sub-agent-interface pure logic.

Loaded by file path (hyphenated filename). The module's optional router import
is wrapped in try/except and degrades to ROUTER_AVAILABLE = False here, so
importing it pulls in nothing beyond the standard library. No Telegram, no
network, no async event loop is exercised — only the pure formatting/parsing
classes.
"""

from conftest import load_module_by_path

sa = load_module_by_path("sub_agent_interface", "sub-agent-interface.py")


# ── StatusTag ───────────────────────────────────────────────────────


def test_status_tag_local_shows_zero_cost():
    tag = sa.StatusTag.format("thinkcentre", "qwen2.5:7b", 1.2, 0.0)
    assert "ThinkCentre" in tag
    assert "$0.00" in tag


def test_status_tag_cloud_shows_estimated_cost():
    tag = sa.StatusTag.format("cloud", "kimi-2.5", 3.4, 0.002)
    assert "Cloud" in tag
    assert "~$" in tag


def test_status_tag_pinned_marker():
    tag = sa.StatusTag.format("gpd", "qwen2.5:7b", 0.8, 0.0, pinned=True)
    assert "[pinned]" in tag


def test_status_tag_escalated_icon():
    tag = sa.StatusTag.format("cloud", "kimi-2.5", 2.0, 0.002, escalated=True)
    assert sa.StatusTag.ESCALATED_ICON in tag


def test_status_tag_telegram_wraps_in_backticks():
    tag = sa.StatusTag.format_telegram("gpd", "m", 0.5, 0.0)
    assert tag.strip().startswith("`")
    assert tag.strip().endswith("`")


# ── CommandParser ───────────────────────────────────────────────────


def test_parse_slash_cloud_command():
    parsed = sa.CommandParser.parse("/cloud explain quantum tunneling")
    assert parsed["type"] == "command"
    assert parsed["action"] == "force_cloud"
    assert parsed["query"] == "explain quantum tunneling"


def test_parse_pin_command():
    parsed = sa.CommandParser.parse("/pin local")
    assert parsed["action"] == "pin"
    assert parsed["query"] == "local"


def test_parse_feedback_escalate():
    parsed = sa.CommandParser.parse("hmm that needed cloud honestly")
    assert parsed["type"] == "feedback"
    assert parsed["feedback_type"] == "escalate"


def test_parse_feedback_demote():
    parsed = sa.CommandParser.parse("that was overkill")
    assert parsed["type"] == "feedback"
    assert parsed["feedback_type"] == "demote"


def test_parse_plain_query():
    parsed = sa.CommandParser.parse("what is the capital of France")
    assert parsed["type"] == "query"
    assert parsed["action"] == "route"
    assert parsed["feedback_type"] is None


# ── RoutingContext ──────────────────────────────────────────────────


def test_routing_context_record_counts_local_vs_cloud():
    ctx = sa.RoutingContext(chat_id=1)
    ctx.record("thinkcentre", "qwen2.5:7b", "simple", 0.0)
    ctx.record("gpd", "qwen2.5:7b", "complex-local", 0.0)
    ctx.record("cloud", "kimi-2.5", "cloud", 0.002)
    assert ctx.message_count == 3
    assert ctx.local_count == 2
    assert ctx.cloud_count == 1
    assert ctx.total_cost == 0.002
    assert ctx.last_node == "cloud"


def test_routing_context_history_is_capped():
    ctx = sa.RoutingContext(chat_id=1)
    for _ in range(60):
        ctx.record("thinkcentre", "m", "simple", 0.0)
    assert len(ctx.history) == 50
