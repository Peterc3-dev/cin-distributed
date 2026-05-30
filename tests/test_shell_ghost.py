"""Unit tests for shell_ghost pure logic: command validation and NL translation.

These tests use the in-memory DEFAULT_CONFIG and never execute any shell
command (ShellExecutor is not exercised), never write the audit log, and never
read a YAML config file.
"""

import pytest

import shell_ghost as sg


@pytest.fixture
def validator():
    return sg.CommandValidator(sg.DEFAULT_CONFIG)


# ── CommandValidator ────────────────────────────────────────────────


def test_whitelisted_readonly_command_allowed(validator):
    result = validator.validate("df -h")
    assert result.allowed is True
    assert result.base_cmd == "df"
    assert result.is_destructive is False


def test_blacklisted_command_blocked(validator):
    result = validator.validate("rm -rf /")
    assert result.allowed is False
    assert "blacklist" in result.reason.lower()


def test_pipe_operator_blocked(validator):
    result = validator.validate("ls | grep foo")
    assert result.allowed is False
    assert "chaining" in result.reason.lower()


def test_command_chaining_semicolon_blocked(validator):
    assert validator.validate("ls ; rm file").allowed is False


def test_output_redirection_blocked(validator):
    assert validator.validate("ls > out.txt").allowed is False


def test_unknown_command_not_in_whitelist(validator):
    result = validator.validate("foobarbaz --do-something")
    assert result.allowed is False
    assert "whitelist" in result.reason.lower()


def test_destructive_flag_flagged(validator):
    result = validator.validate("sudo pacman -Syu")
    assert result.allowed is True
    assert result.is_destructive is True
    assert result.needs_sudo is True
    assert result.dry_run_suggested is True


def test_sudo_prefix_detected(validator):
    result = validator.validate("sudo systemctl restart foo")
    assert result.needs_sudo is True
    assert result.base_cmd == "systemctl"


def test_empty_command_rejected(validator):
    assert validator.validate("   ").allowed is False


def test_malformed_quotes_rejected(validator):
    # Unbalanced quote makes shlex.split raise ValueError.
    assert validator.validate('cat "unterminated').allowed is False


# ── IntentTranslator ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("check disk space", "df -h"),
        ("show free memory", "free -h"),
        ("uptime", "uptime"),
        ("system info", "uname -a"),
        ("ollama status", "ollama ps"),
        ("git status", "git status"),
        ("ping example.com", "ping -c 4 example.com"),
    ],
)
def test_translate_known_patterns(phrase, expected):
    assert sg.IntentTranslator.translate(phrase) == expected


def test_translate_returns_none_for_unknown():
    assert sg.IntentTranslator.translate("write me a sonnet about magnetism") is None


def test_translate_strips_unfilled_placeholders():
    # "list files" with no directory should not leave a literal "{2}" behind.
    result = sg.IntentTranslator.translate("list files")
    assert result is not None
    assert "{" not in result


# ── ShellGhost prefix handling (pure string logic) ──────────────────


def test_is_ghost_request_detects_prefix():
    ghost = sg.ShellGhost(sg.DEFAULT_CONFIG)
    assert ghost.is_ghost_request("ghost: df -h") is True
    assert ghost.is_ghost_request("just a normal question") is False


def test_extract_command_strips_prefix():
    ghost = sg.ShellGhost(sg.DEFAULT_CONFIG)
    assert ghost.extract_command("ghost: df -h") == "df -h"
    assert ghost.extract_command("df -h") == "df -h"
