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


@pytest.mark.parametrize(
    "text,expected",
    [
        ("which models are running", "ollama ps"),
        ("what is running", "ps aux"),
    ],
)
def test_translate_running_queries(text, expected):
    assert sg.IntentTranslator.translate(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("restart MyService", "sudo systemctl restart MyService"),
        ("Show Logs for NginX", "journalctl -u NginX -n 50 --no-pager"),
        ("list files in /Data/MyDir", "ls -lah /Data/MyDir"),
    ],
)
def test_translate_preserves_input_case(text, expected):
    assert sg.IntentTranslator.translate(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Restart nginx", "sudo systemctl restart nginx"),
        ("STOP MyService", "sudo systemctl stop MyService"),
        ("Install foo", "sudo pacman -S foo"),
        ("Install Foo", "sudo pacman -S Foo"),
        ("Ping example.com", "ping -c 4 example.com"),
    ],
)
def test_translate_lowercases_captured_verbs(text, expected):
    assert sg.IntentTranslator.translate(text) == expected


@pytest.mark.parametrize("cmd", [
    "ls & touch /tmp/pwned",
    "ls\ntouch /tmp/pwned",
    "ls\r\ntouch /tmp/pwned",
])
def test_background_and_newline_chaining_blocked(validator, cmd):
    # Under shell=True each of these runs a second, unvalidated command.
    result = validator.validate(cmd)
    assert not result.allowed
    assert "chaining" in result.reason.lower()


@pytest.mark.parametrize("text, expected", [
    ("show logs", None),
    ("show logs for nginx", "journalctl -u nginx -n 50 --no-pager"),
    ("check log sshd", "journalctl -u sshd -n 50 --no-pager"),
])
def test_translate_logs_does_not_split_word_into_unit(text, expected):
    # "show logs" used to become "journalctl -u s" by backtracking "logs?".
    assert sg.IntentTranslator.translate(text) == expected


# ── ShellExecutor argv hardening ──

class _FakeCompleted:
    stdout = "ok\n"
    stderr = ""
    returncode = 0


def _capture_run(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _FakeCompleted()

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    return calls


def _executor():
    return sg.ShellExecutor({"ghost": {}})


def test_translated_command_runs_as_argv_without_shell(monkeypatch):
    calls = _capture_run(monkeypatch)
    cmd = sg.IntentTranslator.translate("show disk space")
    _executor().execute(cmd)
    args, kwargs = calls[0]
    assert isinstance(args, list)
    assert args == ["df", "-h"]
    assert kwargs["shell"] is False


def test_shell_required_template_passes_string_with_shell(monkeypatch):
    calls = _capture_run(monkeypatch)
    monkeypatch.setattr(
        sg.IntentTranslator, "PATTERNS",
        [(r"top\s+procs", "ps aux | head -5", {"shell": True})],
    )
    cmd, needs_shell = sg.IntentTranslator.translate_entry("top procs")
    assert needs_shell is True
    _executor().execute(cmd, shell=needs_shell)
    args, kwargs = calls[0]
    assert args == "ps aux | head -5"
    assert kwargs["shell"] is True


def test_default_templates_need_no_shell():
    for entry in sg.IntentTranslator.PATTERNS:
        assert len(entry) < 3 or not entry[2].get("shell")


def test_quoted_argument_survives_shlex_split(monkeypatch):
    calls = _capture_run(monkeypatch)
    cmd = sg.IntentTranslator.translate("find notes files")
    _executor().execute(cmd)
    assert calls[0][0] == ["find", ".", "-name", "*notes*"]
    calls.clear()
    _executor().execute("echo 'hello world' two")
    assert calls[0][0] == ["echo", "hello world", "two"]


# ── ShellExecutor argv expansion (tilde, allowlisted vars, globs) ──

def test_tilde_expands_to_home_in_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = _capture_run(monkeypatch)
    _executor().execute("ls -lah ~")
    assert calls[0][0] == ["ls", "-lah", str(tmp_path)]
    assert calls[0][0][2] == str(tmp_path)
    calls.clear()
    _executor().execute("ls ~/Downloads")
    assert calls[0][0] == ["ls", str(tmp_path / "Downloads")]


def test_glob_with_matches_expands(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / ".hidden.txt").write_text("")
    monkeypatch.chdir(tmp_path)
    calls = _capture_run(monkeypatch)
    _executor().execute("du -sh *.txt")
    assert calls[0][0] == ["du", "-sh", "a.txt", "b.txt"]


def test_glob_without_match_stays_literal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _capture_run(monkeypatch)
    _executor().execute("ls *.nomatch")
    assert calls[0][0] == ["ls", "*.nomatch"]


def test_quoted_glob_and_tilde_not_expanded(monkeypatch, tmp_path):
    (tmp_path / "my-notes.txt").write_text("")
    monkeypatch.chdir(tmp_path)
    calls = _capture_run(monkeypatch)
    _executor().execute("find . -name \"*notes*\" '~'")
    assert calls[0][0] == ["find", ".", "-name", "*notes*", "~"]


def test_only_allowlisted_env_vars_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SECRET_TOKEN", "s3cret")
    calls = _capture_run(monkeypatch)
    _executor().execute("echo $HOME ${HOME}/x $SECRET_TOKEN")
    assert calls[0][0] == [
        "echo", str(tmp_path), f"{tmp_path}/x", "$SECRET_TOKEN",
    ]


def test_shell_true_command_string_unchanged(monkeypatch):
    calls = _capture_run(monkeypatch)
    _executor().execute("ls ~ *.py $HOME", shell=True)
    assert calls[0][0] == "ls ~ *.py $HOME"
    assert calls[0][1]["shell"] is True
