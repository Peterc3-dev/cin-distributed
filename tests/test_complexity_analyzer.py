"""Unit tests for router.ComplexityAnalyzer — pure, rule-based logic.

These tests build a minimal config dict in-memory and never touch the network,
Ollama, the YAML config file, or the feedback database (the path points at a
non-existent file, which the analyzer handles by falling back to empty
adjustments).
"""

import pytest

import router


@pytest.fixture
def config():
    return {
        "routing_rules": {
            "token_thresholds": {
                "simple_max": 150,
                "complex_local_max": 500,
                "cloud_min": 501,
            },
            "keywords": {
                "simple": ["summarize", "translate", "list"],
                "complex_local": ["analyze", "debug", "refactor"],
                "cloud_escalation": ["architecture", "research", "philosophy"],
            },
        },
        "paths": {"feedback_db": "/nonexistent/does-not-exist.json"},
    }


@pytest.fixture
def analyzer(config):
    return router.ComplexityAnalyzer(config)


def test_estimate_tokens_scales_with_word_count(analyzer):
    # 10 words * 1.3 = 13 (int-truncated)
    assert analyzer.estimate_tokens("a b c d e f g h i j") == 13


def test_estimate_tokens_empty_is_zero(analyzer):
    assert analyzer.estimate_tokens("") == 0


def test_missing_feedback_db_yields_empty_adjustments(analyzer):
    assert analyzer.feedback_db == {"adjustments": {}, "history": []}


def test_simple_keyword_routes_simple(analyzer):
    result = analyzer.analyze("summarize this short note")
    assert result["tier"] == "simple"


def test_complex_local_keyword_routes_complex_local(analyzer):
    result = analyzer.analyze("debug and refactor this function please")
    assert result["tier"] == "complex-local"


def test_cloud_keyword_routes_cloud(analyzer):
    result = analyzer.analyze("design the architecture and research the strategy")
    assert result["tier"] == "cloud"


def test_keyword_score_returns_normalized_confidence(analyzer):
    tier, confidence = analyzer.keyword_score("please summarize this")
    assert tier == "simple"
    assert 0.0 <= confidence <= 1.0


def test_multi_step_phrase_promotes_simple_to_complex(analyzer):
    # No strong keyword signal, but an "and also" multi-step marker is present.
    result = analyzer.analyze("do the thing and also do the other thing")
    assert result["tier"] == "complex-local"
    assert result["method"] == "multi-step-detection"


def test_long_query_routes_cloud_by_token_length(analyzer):
    long_query = "word " * 600  # ~780 estimated tokens, no keywords
    result = analyzer.analyze(long_query)
    assert result["tier"] == "cloud"
    assert result["token_tier"] == "cloud"


def test_analyze_returns_expected_keys(analyzer):
    result = analyzer.analyze("list the planets")
    expected = {
        "tier",
        "method",
        "tokens_est",
        "keyword_tier",
        "keyword_confidence",
        "token_tier",
        "sentences",
        "timestamp",
    }
    assert expected <= set(result)
