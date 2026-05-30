#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 SHELL GHOST — Secure Shell Execution Layer
 
 A capability module within Bootstrap Router that handles
 bash/shell command execution with:
 • Command whitelist validation
 • Dry-run mode for destructive operations
 • Full audit logging
 • Privilege escalation confirmation
 • Output capture and formatting
 
 NOT a separate service — integrated into the router as a
 specialized node. The router detects shell intent and
 delegates here.
═══════════════════════════════════════════════════════════════
"""

import os
import re
import json
import shlex
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict, List, Any
from dataclasses import dataclass, asdict

# ─── Display ──────────────────────────────────────────────────
G = "\033[38;2;51;255;102m"
DIM = "\033[2m"
BOLD = "\033[1m"
R = "\033[0m"
YELLOW = "\033[33m"
RED = "\033[31m"

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "ghost-config.yaml"


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p))


# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "ghost": {
        "audit_dir": "~/.local/share/shell-ghost",
        "audit_log": "~/.local/share/shell-ghost/audit.log",
        "max_output_lines": 100,
        "timeout_sec": 30,
        "dry_run_default": False,
        "require_confirm_sudo": True,

        # ── Command Classification ──
        "whitelist": {
            # Package management
            "pacman": {"allow_flags": ["-S", "-Syu", "-Syyu", "-Ss", "-Q", "-Qi", "-Ql", "-R"],
                       "destructive": ["-Syu", "-Syyu", "-R"],
                       "description": "Package manager"},
            "yay":    {"allow_flags": ["-S", "-Syu", "-Ss", "-Q", "-R"],
                       "destructive": ["-Syu", "-R"],
                       "description": "AUR helper"},
            "paru":   {"allow_flags": ["-S", "-Syu", "-Ss", "-Q", "-R"],
                       "destructive": ["-Syu", "-R"],
                       "description": "AUR helper"},
            "pip":    {"allow_flags": ["install", "list", "show", "freeze", "uninstall"],
                       "destructive": ["uninstall"],
                       "description": "Python packages"},

            # System info / monitoring
            "systemctl":  {"allow_flags": ["status", "start", "stop", "restart", "enable",
                                           "disable", "list-units", "is-active", "is-enabled"],
                           "destructive": ["stop", "disable"],
                           "description": "Service manager"},
            "journalctl": {"allow_flags": ["-u", "-f", "--since", "-n", "-b", "--no-pager"],
                           "destructive": [],
                           "description": "System logs"},
            "df":         {"allow_flags": ["-h", "-T", "--total"],
                           "destructive": [],
                           "description": "Disk usage"},
            "du":         {"allow_flags": ["-sh", "-h", "--max-depth"],
                           "destructive": [],
                           "description": "Directory size"},
            "free":       {"allow_flags": ["-h", "-m", "-g"],
                           "destructive": [],
                           "description": "Memory usage"},
            "top":        {"allow_flags": ["-bn1"],
                           "destructive": [],
                           "description": "Process monitor"},
            "htop":       {"allow_flags": [],
                           "destructive": [],
                           "description": "Process monitor"},
            "ps":         {"allow_flags": ["aux", "-ef", "-A"],
                           "destructive": [],
                           "description": "Process list"},
            "uptime":     {"allow_flags": [],
                           "destructive": [],
                           "description": "System uptime"},
            "uname":      {"allow_flags": ["-a", "-r"],
                           "destructive": [],
                           "description": "System info"},
            "lsblk":      {"allow_flags": ["-f", "-o"],
                           "destructive": [],
                           "description": "Block devices"},
            "ip":         {"allow_flags": ["addr", "link", "route", "a"],
                           "destructive": [],
                           "description": "Network config"},
            "ss":         {"allow_flags": ["-tulpn", "-a", "-t", "-u", "-l", "-n", "-p"],
                           "destructive": [],
                           "description": "Socket stats"},
            "netstat":    {"allow_flags": ["-tulpn", "-a", "-t"],
                           "destructive": [],
                           "description": "Network stats"},
            "ping":       {"allow_flags": ["-c"],
                           "destructive": [],
                           "description": "Network ping"},
            "curl":       {"allow_flags": ["-s", "-I", "-o", "--head", "-L"],
                           "destructive": [],
                           "description": "HTTP requests"},
            "wget":       {"allow_flags": ["-q", "-O"],
                           "destructive": [],
                           "description": "File download"},

            # File operations (read-only emphasis)
            "ls":         {"allow_flags": ["-la", "-lah", "-R", "-t", "-S"],
                           "destructive": [],
                           "description": "List files"},
            "cat":        {"allow_flags": [],
                           "destructive": [],
                           "description": "Read file"},
            "head":       {"allow_flags": ["-n"],
                           "destructive": [],
                           "description": "File head"},
            "tail":       {"allow_flags": ["-n", "-f"],
                           "destructive": [],
                           "description": "File tail"},
            "wc":         {"allow_flags": ["-l", "-w", "-c"],
                           "destructive": [],
                           "description": "Word count"},
            "find":       {"allow_flags": ["-name", "-type", "-size", "-mtime"],
                           "destructive": [],
                           "description": "Find files"},
            "grep":       {"allow_flags": ["-r", "-i", "-n", "-l", "-c"],
                           "destructive": [],
                           "description": "Search text"},
            "which":      {"allow_flags": [],
                           "destructive": [],
                           "description": "Locate command"},
            "file":       {"allow_flags": [],
                           "destructive": [],
                           "description": "File type"},
            "stat":       {"allow_flags": [],
                           "destructive": [],
                           "description": "File info"},

            # Ollama
            "ollama":     {"allow_flags": ["list", "ps", "show", "pull", "run", "serve"],
                           "destructive": ["pull"],
                           "description": "LLM runtime"},

            # Tailscale
            "tailscale":  {"allow_flags": ["status", "ping", "ip"],
                           "destructive": [],
                           "description": "VPN mesh"},

            # Git
            "git":        {"allow_flags": ["status", "log", "diff", "branch", "remote",
                                           "pull", "push", "add", "commit", "stash"],
                           "destructive": ["push"],
                           "description": "Version control"},

            # Docker (if applicable)
            "docker":     {"allow_flags": ["ps", "images", "logs", "stats", "inspect"],
                           "destructive": [],
                           "description": "Containers"},
        },

        # ── Blacklist (never execute) ──
        "blacklist": [
            "rm -rf /",
            "rm -rf /*",
            "mkfs",
            "dd if=",
            ":(){:|:&};:",
            "chmod -R 777 /",
            "chown -R",
            "> /dev/sda",
            "mv /* ",
            "wget .* | sh",
            "curl .* | sh",
            "eval",
            "exec",
        ],

        # ── Shell intent keywords (for router integration) ──
        "intent_keywords": [
            "check ports",
            "check disk",
            "update packages",
            "restart service",
            "show logs",
            "list files",
            "check memory",
            "check network",
            "system status",
            "disk space",
            "running processes",
            "installed packages",
            "service status",
            "check tailscale",
            "ollama status",
            "git status",
            "check ssh",
            "network connections",
            "uptime",
            "free memory",
        ],
    }
}


def load_ghost_config() -> dict:
    # PyYAML is imported lazily so the pure validation/translation logic in
    # this module can be imported and exercised without PyYAML installed.
    # When no config file exists we fall back to DEFAULT_CONFIG (no yaml needed).
    if CONFIG_PATH.exists():
        import yaml

        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return DEFAULT_CONFIG


def save_default_config():
    """Write default config to disk if it doesn't exist."""
    if not CONFIG_PATH.exists():
        import yaml

        with open(CONFIG_PATH, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False,
                      sort_keys=False, width=120)


# ═══════════════════════════════════════════════════════════════
#  COMMAND VALIDATOR
# ═══════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    allowed: bool
    command: str
    base_cmd: str
    reason: str
    is_destructive: bool = False
    needs_sudo: bool = False
    dry_run_suggested: bool = False


class CommandValidator:
    """Validates commands against whitelist and blacklist."""

    def __init__(self, config: dict):
        ghost = config.get("ghost", config)
        self.whitelist = ghost["whitelist"]
        self.blacklist = ghost["blacklist"]

    def validate(self, raw_command: str) -> ValidationResult:
        """
        Validate a command string.
        Returns ValidationResult with allow/deny decision.
        """
        raw = raw_command.strip()

        # ── Blacklist check (exact and pattern) ──
        for pattern in self.blacklist:
            if pattern in raw:
                return ValidationResult(
                    allowed=False,
                    command=raw,
                    base_cmd="",
                    reason=f"Blocked by blacklist pattern: '{pattern}'",
                )

        # ── Parse command ──
        needs_sudo = False
        cmd_to_parse = raw

        if raw.startswith("sudo "):
            needs_sudo = True
            cmd_to_parse = raw[5:].strip()

        try:
            parts = shlex.split(cmd_to_parse)
        except ValueError:
            return ValidationResult(
                allowed=False,
                command=raw,
                base_cmd="",
                reason="Malformed command (unparseable)",
            )

        if not parts:
            return ValidationResult(
                allowed=False,
                command=raw,
                base_cmd="",
                reason="Empty command",
            )

        base_cmd = parts[0]

        # ── Pipe/chain detection ──
        dangerous_operators = ["|", "&&", "||", ";", "`", "$("]
        for op in dangerous_operators:
            if op in raw:
                return ValidationResult(
                    allowed=False,
                    command=raw,
                    base_cmd=base_cmd,
                    reason=f"Command chaining operator '{op}' not allowed. "
                           f"Submit each command separately.",
                )

        # ── Redirect detection ──
        if ">" in raw or ">>" in raw:
            return ValidationResult(
                allowed=False,
                command=raw,
                base_cmd=base_cmd,
                reason="Output redirection not allowed through Ghost. "
                       "Use the command directly if you need file output.",
            )

        # ── Whitelist check ──
        if base_cmd not in self.whitelist:
            return ValidationResult(
                allowed=False,
                command=raw,
                base_cmd=base_cmd,
                reason=f"'{base_cmd}' is not in the Ghost whitelist. "
                       f"Allowed commands: {', '.join(sorted(self.whitelist.keys()))}",
            )

        entry = self.whitelist[base_cmd]

        # ── Check for destructive flags ──
        is_destructive = False
        for flag in entry.get("destructive", []):
            if flag in parts:
                is_destructive = True
                break

        return ValidationResult(
            allowed=True,
            command=raw,
            base_cmd=base_cmd,
            reason=f"Allowed ({entry.get('description', base_cmd)})",
            is_destructive=is_destructive,
            needs_sudo=needs_sudo,
            dry_run_suggested=is_destructive,
        )


# ═══════════════════════════════════════════════════════════════
#  NATURAL LANGUAGE → COMMAND TRANSLATOR
# ═══════════════════════════════════════════════════════════════

class IntentTranslator:
    """
    Translates natural language shell requests to commands.
    Rule-based — no LLM needed for common patterns.
    Falls back to the router's LLM if no pattern matches.
    """

    PATTERNS: List[Tuple[str, str]] = [
        # Package management
        (r"update\s*(all\s*)?(packages|system)", "sudo pacman -Syu"),
        (r"install\s+(\S+)", "sudo pacman -S {0}"),
        (r"search\s*(?:for\s+)?package\s+(\S+)", "pacman -Ss {0}"),
        (r"(which|what)\s+packages?\s+installed", "pacman -Q"),
        (r"remove\s+(\S+)\s+package", "sudo pacman -R {0}"),

        # System info
        (r"(check|show|what)\s*(is\s+)?(disk\s*space|disk\s*usage)", "df -h"),
        (r"(check|show)\s*(free\s+)?memory", "free -h"),
        (r"uptime", "uptime"),
        (r"system\s*info", "uname -a"),

        # Network
        (r"(check|show|list)\s*(open\s+)?ports", "ss -tulpn"),
        (r"(check|show)\s*network", "ip addr"),
        (r"ping\s+(\S+)", "ping -c 4 {0}"),
        (r"(check|show)\s*(network\s+)?connections", "ss -tulpn"),

        # Services
        (r"(restart|start|stop)\s+(\S+)\s*(service)?", "sudo systemctl {0} {1}"),
        (r"(check|show|what\s+is)\s+(\S+)\s+service\s*status", "systemctl status {1}"),
        (r"(show|check|list)\s*services", "systemctl list-units --type=service --state=running"),
        (r"(show|check)\s*(the\s+)?logs?\s*(for\s+)?(\S+)", "journalctl -u {3} -n 50 --no-pager"),

        # Processes
        (r"(show|list|check)\s*(running\s+)?processes", "ps aux"),
        (r"(what|which|show).*running", "ps aux"),

        # Files
        (r"(list|show)\s+files?\s*(in\s+)?(\S+)?", "ls -lah {2}"),
        (r"(find|search)\s+(\S+)\s+files?", "find . -name '*{1}*'"),
        (r"(size|how\s+big)\s*(is\s+)?(\S+)", "du -sh {2}"),

        # Ollama
        (r"ollama\s+status", "ollama ps"),
        (r"(list|show)\s+models?", "ollama list"),
        (r"(which|what)\s+models?\s+(are\s+)?(running|loaded)", "ollama ps"),

        # Tailscale
        (r"(check|show)\s+tailscale", "tailscale status"),
        (r"tailscale\s+status", "tailscale status"),

        # Git
        (r"git\s+status", "git status"),
        (r"(show|check)\s+git\s+log", "git log --oneline -20"),
    ]

    @classmethod
    def translate(cls, natural_text: str) -> Optional[str]:
        """
        Attempt to translate natural language to a shell command.
        Returns None if no pattern matches.
        """
        text = natural_text.lower().strip()

        for pattern, template in cls.PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                cmd = template
                for i, g in enumerate(groups):
                    if g is not None:
                        cmd = cmd.replace(f"{{{i}}}", g.strip())
                # Clean up unreplaced placeholders
                cmd = re.sub(r'\{[0-9]+\}', '', cmd).strip()
                return cmd

        return None


# ═══════════════════════════════════════════════════════════════
#  SHELL EXECUTOR
# ═══════════════════════════════════════════════════════════════

class ShellExecutor:
    """Executes validated commands with output capture."""

    def __init__(self, config: dict):
        ghost = config.get("ghost", config)
        self.timeout = ghost.get("timeout_sec", 30)
        self.max_lines = ghost.get("max_output_lines", 100)

    def execute(self, command: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute a shell command and capture output.
        Returns result dict with stdout, stderr, returncode, duration.
        """
        if dry_run:
            return {
                "command": command,
                "stdout": f"[DRY RUN] Would execute: {command}",
                "stderr": "",
                "returncode": 0,
                "duration_sec": 0,
                "dry_run": True,
                "timestamp": datetime.now().isoformat(),
            }

        start = datetime.now()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ, "TERM": "dumb"},  # disable color codes from commands
            )
            duration = (datetime.now() - start).total_seconds()

            stdout = result.stdout
            # Truncate if too long
            lines = stdout.splitlines()
            if len(lines) > self.max_lines:
                stdout = "\n".join(lines[:self.max_lines])
                stdout += f"\n... ({len(lines) - self.max_lines} lines truncated)"

            return {
                "command": command,
                "stdout": stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "duration_sec": round(duration, 2),
                "dry_run": False,
                "timestamp": datetime.now().isoformat(),
            }

        except subprocess.TimeoutExpired:
            duration = (datetime.now() - start).total_seconds()
            return {
                "command": command,
                "stdout": "",
                "stderr": f"Command timed out after {self.timeout}s",
                "returncode": -1,
                "duration_sec": round(duration, 2),
                "dry_run": False,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            return {
                "command": command,
                "stdout": "",
                "stderr": str(e),
                "returncode": -2,
                "duration_sec": 0,
                "dry_run": False,
                "timestamp": datetime.now().isoformat(),
            }


# ═══════════════════════════════════════════════════════════════
#  AUDIT LOGGER
# ═══════════════════════════════════════════════════════════════

class AuditLogger:
    """Logs all Ghost interactions for security auditing."""

    def __init__(self, config: dict):
        ghost = config.get("ghost", config)
        self.log_path = expand_path(ghost["audit_log"])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, validation: ValidationResult,
            execution: Optional[Dict] = None,
            user_input: str = "",
            translated: bool = False):
        """Write audit entry."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input[:200],
            "command": validation.command,
            "base_cmd": validation.base_cmd,
            "allowed": validation.allowed,
            "reason": validation.reason,
            "destructive": validation.is_destructive,
            "sudo": validation.needs_sudo,
            "translated": translated,
        }

        if execution:
            entry["returncode"] = execution.get("returncode")
            entry["duration_sec"] = execution.get("duration_sec")
            entry["dry_run"] = execution.get("dry_run", False)
            # Don't log full stdout/stderr to audit — too verbose
            entry["output_lines"] = len(
                execution.get("stdout", "").splitlines()
            )

        # Append as JSON line
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def recent(self, n: int = 20) -> List[Dict]:
        """Get last N audit entries."""
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text().splitlines()
        entries = []
        for line in lines[-n:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries

    def stats(self) -> Dict:
        """Aggregate audit statistics."""
        entries = self.recent(1000)
        total = len(entries)
        allowed = sum(1 for e in entries if e.get("allowed"))
        denied = total - allowed
        destructive = sum(1 for e in entries if e.get("destructive"))
        sudo_count = sum(1 for e in entries if e.get("sudo"))
        translated = sum(1 for e in entries if e.get("translated"))

        from collections import Counter
        cmd_freq = Counter(e.get("base_cmd", "?") for e in entries if e.get("allowed"))

        return {
            "total": total,
            "allowed": allowed,
            "denied": denied,
            "destructive": destructive,
            "sudo_requests": sudo_count,
            "translated_from_natural": translated,
            "top_commands": cmd_freq.most_common(10),
        }


# ═══════════════════════════════════════════════════════════════
#  SHELL GHOST — MAIN CLASS
# ═══════════════════════════════════════════════════════════════

class ShellGhost:
    """
    Main Shell Ghost interface.
    
    Usage:
      ghost = ShellGhost()
      result = ghost.execute("ghost: check disk space")
      result = ghost.execute("df -h")
      result = ghost.execute("ghost: update packages", confirm_destructive=True)
    """

    GHOST_PREFIX = "ghost:"
    GHOST_PREFIX_ALT = "ghost "

    def __init__(self, config: dict = None):
        self.config = config or load_ghost_config()
        ghost_cfg = self.config.get("ghost", self.config)
        self.validator = CommandValidator(self.config)
        self.translator = IntentTranslator()
        self.executor = ShellExecutor(self.config)
        self.auditor = AuditLogger(self.config)
        self.dry_run_default = ghost_cfg.get("dry_run_default", False)

    def is_ghost_request(self, text: str) -> bool:
        """Check if text is directed at Shell Ghost."""
        t = text.strip().lower()
        return (t.startswith(self.GHOST_PREFIX)
                or t.startswith(self.GHOST_PREFIX_ALT))

    def extract_command(self, text: str) -> str:
        """Strip ghost prefix from text."""
        t = text.strip()
        for prefix in (self.GHOST_PREFIX, self.GHOST_PREFIX_ALT):
            if t.lower().startswith(prefix):
                return t[len(prefix):].strip()
        return t

    def process(self, user_input: str,
                confirm_destructive: bool = False,
                force_dry_run: bool = False) -> Dict[str, Any]:
        """
        Full Ghost pipeline:
        1. Extract/translate command
        2. Validate against whitelist
        3. Check destructive/sudo
        4. Execute (or dry-run)
        5. Audit log
        6. Return formatted result
        """
        raw = self.extract_command(user_input)
        translated = False

        # ── Step 1: Translate natural language if needed ──
        # If it doesn't look like a command, try translation
        if not self._looks_like_command(raw):
            translated_cmd = self.translator.translate(raw)
            if translated_cmd:
                raw = translated_cmd
                translated = True
            else:
                return {
                    "success": False,
                    "error": "unrecognized",
                    "message": f"Couldn't understand '{raw}' as a shell command.\n"
                               f"Try being more specific or use the exact command.",
                    "command": None,
                }

        # ── Step 2: Validate ──
        validation = self.validator.validate(raw)

        if not validation.allowed:
            self.auditor.log(validation, user_input=user_input, translated=translated)
            return {
                "success": False,
                "error": "denied",
                "message": f"🚫 {validation.reason}",
                "command": raw,
                "validation": asdict(validation),
            }

        # ── Step 3: Destructive / sudo gate ──
        dry_run = force_dry_run or self.dry_run_default

        if validation.is_destructive and not confirm_destructive:
            # Auto dry-run for destructive commands unless explicitly confirmed
            dry_run = True

        if validation.needs_sudo and not confirm_destructive:
            self.auditor.log(validation, user_input=user_input, translated=translated)
            return {
                "success": False,
                "error": "sudo_confirm",
                "message": (f"⚠️ This command requires sudo:\n"
                            f"`{raw}`\n\n"
                            f"Reply with `ghost: confirm {raw}` to execute."),
                "command": raw,
                "needs_confirm": True,
                "validation": asdict(validation),
            }

        # ── Step 4: Execute ──
        execution = self.executor.execute(raw, dry_run=dry_run)

        # ── Step 5: Audit ──
        self.auditor.log(validation, execution, user_input=user_input,
                         translated=translated)

        # ── Step 6: Format result ──
        return self._format_result(execution, validation, translated, dry_run)

    def _looks_like_command(self, text: str) -> bool:
        """Heuristic: does this look like an actual shell command?"""
        if not text:
            return False
        first_word = text.split()[0].lower()
        # Check if first word is a known command or looks like a path
        ghost_cfg = self.config.get("ghost", self.config)
        known = set(ghost_cfg["whitelist"].keys())
        known.add("sudo")
        return (first_word in known
                or first_word.startswith("/")
                or first_word.startswith("./"))

    def _format_result(self, execution: Dict, validation: ValidationResult,
                       translated: bool, dry_run: bool) -> Dict[str, Any]:
        """Build the response dict."""
        stdout = execution.get("stdout", "")
        stderr = execution.get("stderr", "")
        rc = execution.get("returncode", -1)
        duration = execution.get("duration_sec", 0)

        # Status icon
        if dry_run:
            icon = "🔍"
            status = "DRY RUN"
        elif rc == 0:
            icon = "👻"
            status = "OK"
        else:
            icon = "⚠️"
            status = f"EXIT {rc}"

        # Build display message
        parts = [f"{icon} `{validation.command}`"]

        if translated:
            parts.append(f"{DIM}(translated from natural language){R}")

        if stdout:
            # Wrap in code block for Telegram
            parts.append(f"```\n{stdout}\n```")

        if stderr and rc != 0:
            parts.append(f"⚠️ stderr:\n```\n{stderr}\n```")

        # Status tag (matches router format)
        tag = f"👻 Ghost · {validation.base_cmd} · {duration}s · {status}"
        if dry_run:
            tag += " · [dry-run]"
        parts.append(f"`{tag}`")

        return {
            "success": rc == 0,
            "error": None if rc == 0 else f"exit_{rc}",
            "message": "\n\n".join(parts),
            "command": validation.command,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": rc,
            "duration_sec": duration,
            "dry_run": dry_run,
            "translated": translated,
            "validation": asdict(validation),
        }


# ═══════════════════════════════════════════════════════════════
#  ROUTER INTEGRATION HOOKS
# ═══════════════════════════════════════════════════════════════

def detect_shell_intent(query: str) -> bool:
    """
    Called by Bootstrap Router to detect shell intent.
    Add to router.py's ComplexityAnalyzer.
    """
    config = load_ghost_config()
    ghost = ShellGhost(config)

    # Explicit ghost prefix
    if ghost.is_ghost_request(query):
        return True

    # Check intent keywords
    ghost_cfg = config.get("ghost", config)
    text_lower = query.lower()
    for keyword in ghost_cfg.get("intent_keywords", []):
        if keyword in text_lower:
            return True

    # Check if it looks like a raw command
    raw = query.strip()
    if ghost._looks_like_command(raw):
        return True

    return False


def route_to_ghost(query: str, confirm: bool = False) -> Dict[str, Any]:
    """
    Called by Bootstrap Router when shell intent is detected.
    Returns Ghost result dict.
    """
    ghost = ShellGhost()
    return ghost.process(query, confirm_destructive=confirm)


# ═══════════════════════════════════════════════════════════════
#  CLI INTERFACE
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Shell Ghost — Secure Shell Execution Layer"
    )
    parser.add_argument("command", nargs="*",
                        help="Command or natural language request")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview command without executing")
    parser.add_argument("--confirm", action="store_true",
                        help="Confirm destructive/sudo operations")
    parser.add_argument("--audit", action="store_true",
                        help="Show recent audit log")
    parser.add_argument("--stats", action="store_true",
                        help="Show audit statistics")
    parser.add_argument("--whitelist", action="store_true",
                        help="Show whitelisted commands")
    parser.add_argument("--init-config", action="store_true",
                        help="Write default config to ghost-config.yaml")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    # Init config
    if args.init_config:
        save_default_config()
        print(f"  {G}✓ Config written to {CONFIG_PATH}{R}")
        return

    ghost = ShellGhost()

    # Audit mode
    if args.audit:
        entries = ghost.auditor.recent(20)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"\n  {G}{BOLD}═══ Shell Ghost Audit Log ═══{R}")
            for e in entries:
                ts = e.get("timestamp", "?")[:19]
                cmd = e.get("command", "?")
                ok = "✓" if e.get("allowed") else "✗"
                rc = e.get("returncode", "")
                icon = G if e.get("allowed") else RED
                print(f"  {icon}{ok}{R}  {DIM}{ts}{R}  {cmd}  rc={rc}")
        return

    # Stats mode
    if args.stats:
        stats = ghost.auditor.stats()
        if args.json:
            print(json.dumps(stats, indent=2, default=str))
        else:
            print(f"\n  {G}{BOLD}═══ Shell Ghost Stats ═══{R}")
            print(f"  {G}Total commands:{R}     {stats['total']}")
            print(f"  {G}Allowed:{R}            {stats['allowed']}")
            print(f"  {G}Denied:{R}             {stats['denied']}")
            print(f"  {G}Destructive:{R}        {stats['destructive']}")
            print(f"  {G}Sudo requests:{R}      {stats['sudo_requests']}")
            print(f"  {G}NL translated:{R}      {stats['translated_from_natural']}")
            if stats['top_commands']:
                print(f"\n  {G}{BOLD}Top Commands:{R}")
                for cmd, count in stats['top_commands']:
                    print(f"    {G}{cmd:15s}{R} {count}")
        return

    # Whitelist mode
    if args.whitelist:
        config = load_ghost_config()
        wl = config.get("ghost", config)["whitelist"]
        print(f"\n  {G}{BOLD}═══ Shell Ghost Whitelist ═══{R}")
        for cmd, info in sorted(wl.items()):
            desc = info.get("description", "")
            destr = info.get("destructive", [])
            d_tag = f"  {YELLOW}[destructive: {', '.join(destr)}]{R}" if destr else ""
            print(f"  {G}{cmd:15s}{R} {desc}{d_tag}")
        return

    # Execute mode
    if args.command:
        user_input = " ".join(args.command)
        result = ghost.process(
            user_input,
            confirm_destructive=args.confirm,
            force_dry_run=args.dry_run,
        )

        if args.json:
            # Remove ANSI from message for JSON
            msg = re.sub(r'\033\[[^m]*m', '', result.get("message", ""))
            result["message"] = msg
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{result['message']}\n")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
