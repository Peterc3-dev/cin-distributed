#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 SUB-AGENT INTERFACE LAYER
 Single Telegram bot with transparent smart routing.
 
 Architecture: Option 1 + Option 4 hybrid
 • One bot (@claw_bot)
 • Router decides local vs cloud
 • Every response shows source + cost
 • Reply-chain preserves routing context
 • Slash commands for overrides only
═══════════════════════════════════════════════════════════════

 Design rationale:
 
 Option 2 (two bots) was rejected because it splits context.
 If you ask @claw_local something and need to escalate, you
 lose the conversation thread. You'd be copy-pasting between
 bots — that's more friction than Publix self-checkout.
 
 Option 3 (slash commands as primary) was rejected because
 it forces the user to pre-classify every query. The router
 exists precisely so you don't have to think about it.
 
 The winning design: ONE bot. Smart routing by default.
 Slash commands exist only as overrides. Every response
 carries a one-line status tag showing source, model, cost,
 and latency. Reply-chains maintain routing affinity.
"""

import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

# ─── Imports (Telegram + Router) ──────────────────────────────
# In production: pip install python-telegram-bot
# For now, this is the interface contract.

# Bootstrap Router integration.
# router.py lives alongside this file, so add this script's own
# directory to sys.path. (Historically the router lived in a sibling
# "bootstrap-router/" directory; fall back to that if present.)
ROUTER_DIR = Path(__file__).parent.resolve()
_LEGACY_ROUTER_DIR = ROUTER_DIR.parent / "bootstrap-router"
sys.path.insert(0, str(ROUTER_DIR))
if _LEGACY_ROUTER_DIR.is_dir():
    sys.path.insert(0, str(_LEGACY_ROUTER_DIR))

try:
    from router import BootstrapRouter
    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False

logger = logging.getLogger("sub-agent")


# ═══════════════════════════════════════════════════════════════
#  DATA MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass
class RoutingContext:
    """Tracks routing state for a conversation thread."""
    chat_id: int
    thread_id: Optional[int] = None
    pinned_node: Optional[str] = None      # "thinkcentre", "gpd", "cloud", or None (auto)
    last_node: Optional[str] = None
    last_model: Optional[str] = None
    last_tier: Optional[str] = None
    message_count: int = 0
    local_count: int = 0
    cloud_count: int = 0
    total_cost: float = 0.0
    history: list = field(default_factory=list)

    def record(self, node: str, model: str, tier: str, cost: float):
        self.last_node = node
        self.last_model = model
        self.last_tier = tier
        self.message_count += 1
        self.total_cost += cost
        if node in ("thinkcentre", "gpd"):
            self.local_count += 1
        else:
            self.cloud_count += 1
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "node": node,
            "model": model,
            "tier": tier,
        })
        # Keep history manageable
        if len(self.history) > 50:
            self.history = self.history[-50:]


# ═══════════════════════════════════════════════════════════════
#  STATUS TAG FORMATTER
# ═══════════════════════════════════════════════════════════════

class StatusTag:
    """
    Formats the transparent status line appended to every response.
    
    Examples:
      ⚡ ThinkCentre · qwen2.5:7b · 1.2s · $0.00
      ☁️ Cloud · kimi-2.5 · 3.4s · ~$0.002
      ⚡ GPD · qwen2.5:7b · 0.8s · $0.00 · [pinned]
    """

    LOCAL_ICON = "⚡"
    CLOUD_ICON = "☁️"
    ESCALATED_ICON = "⚡→☁️"

    NODE_LABELS = {
        "thinkcentre": "ThinkCentre",
        "gpd": "GPD",
        "cloud": "Cloud",
    }

    @classmethod
    def format(cls, node: str, model: str, duration: float,
               cost: float, pinned: bool = False,
               escalated: bool = False) -> str:
        """Build the status tag string."""
        is_local = node in ("thinkcentre", "gpd")

        if escalated:
            icon = cls.ESCALATED_ICON
        elif is_local:
            icon = cls.LOCAL_ICON
        else:
            icon = cls.CLOUD_ICON

        label = cls.NODE_LABELS.get(node, node)
        cost_str = "$0.00" if is_local else f"~${cost:.4f}"
        parts = [icon, label, model, f"{duration:.1f}s", cost_str]

        if pinned:
            parts.append("[pinned]")

        return " · ".join(parts)

    @classmethod
    def format_telegram(cls, node: str, model: str, duration: float,
                        cost: float, pinned: bool = False,
                        escalated: bool = False) -> str:
        """Telegram-formatted status (monospace line)."""
        raw = cls.format(node, model, duration, cost, pinned, escalated)
        return f"\n\n`{raw}`"


# ═══════════════════════════════════════════════════════════════
#  COMMAND PARSER
# ═══════════════════════════════════════════════════════════════

class CommandParser:
    """
    Parses slash commands and control phrases from messages.
    
    Override commands (rare — router handles most routing):
      /local <query>     → Force to ThinkCentre/GPD
      /cloud <query>     → Force to Cloud
      /pin local         → Pin thread to local until /unpin
      /pin cloud         → Pin thread to cloud until /unpin
      /unpin             → Return to auto-routing
      /status            → Show session stats
      /cost              → Show accumulated cost
    
    Feedback phrases (inline, no slash needed):
      "that needed cloud" → triggers feedback escalation
      "overkill"          → triggers feedback demotion
    """

    COMMANDS = {
        "/local": "force_local",
        "/cloud": "force_cloud",
        "/pin": "pin",
        "/unpin": "unpin",
        "/status": "status",
        "/cost": "cost",
        "/help": "help",
    }

    FEEDBACK_ESCALATE = [
        "that needed cloud",
        "not good enough",
        "try cloud",
        "escalate",
    ]

    FEEDBACK_DEMOTE = [
        "overkill",
        "could have been local",
        "too slow",
    ]

    @classmethod
    def parse(cls, text: str) -> Dict[str, Any]:
        """
        Parse message text for commands and feedback.
        Returns:
          {"type": "command"|"feedback"|"query",
           "action": str,
           "query": str (remaining text),
           "feedback_type": "escalate"|"demote"|None}
        """
        text = text.strip()

        # Check slash commands
        for cmd, action in cls.COMMANDS.items():
            if text.lower().startswith(cmd):
                remainder = text[len(cmd):].strip()
                return {
                    "type": "command",
                    "action": action,
                    "query": remainder,
                    "feedback_type": None,
                }

        # Check feedback phrases
        text_lower = text.lower()
        for phrase in cls.FEEDBACK_ESCALATE:
            if phrase in text_lower:
                return {
                    "type": "feedback",
                    "action": "feedback",
                    "query": text,
                    "feedback_type": "escalate",
                }
        for phrase in cls.FEEDBACK_DEMOTE:
            if phrase in text_lower:
                return {
                    "type": "feedback",
                    "action": "feedback",
                    "query": text,
                    "feedback_type": "demote",
                }

        # Normal query
        return {
            "type": "query",
            "action": "route",
            "query": text,
            "feedback_type": None,
        }


# ═══════════════════════════════════════════════════════════════
#  FILE ATTACHMENT HANDLER
# ═══════════════════════════════════════════════════════════════

class FileHandler:
    """
    Handles file attachments via SSH integration.
    
    When a user sends a file to the bot:
    1. Download to ThinkCentre temp directory
    2. If routed locally: file is already accessible
    3. If routed to GPD: SCP via Tailscale
    4. If routed to cloud: extract text/metadata, send to API
    
    When a response includes file output:
    1. Generate file on processing node
    2. SCP back to ThinkCentre if needed
    3. Send via Telegram
    """

    TEMP_DIR = Path("~/.openclaw/workspace/temp").expanduser()
    GPD_HOST = "100.77.212.27"

    @classmethod
    def prepare_for_node(cls, file_path: Path, target_node: str) -> str:
        """
        Ensure file is accessible on the target node.
        Returns the path on the target node.
        """
        if target_node in ("thinkcentre",):
            # Already local
            return str(file_path)

        elif target_node == "gpd":
            # SCP to GPD via Tailscale
            remote_path = f"/tmp/claw-upload/{file_path.name}"
            # In production:
            # subprocess.run(["scp", str(file_path),
            #                 f"boo@{cls.GPD_HOST}:{remote_path}"])
            return remote_path

        elif target_node == "cloud":
            # For cloud: extract text content, return as string
            # Binary files get base64 encoded metadata only
            return f"[file:{file_path.name}]"

        return str(file_path)

    @classmethod
    def retrieve_from_node(cls, remote_path: str,
                           source_node: str) -> Optional[Path]:
        """Retrieve output file from a remote node."""
        if source_node in ("thinkcentre",):
            return Path(remote_path)
        elif source_node == "gpd":
            local_path = cls.TEMP_DIR / Path(remote_path).name
            # In production:
            # subprocess.run(["scp",
            #                 f"boo@{cls.GPD_HOST}:{remote_path}",
            #                 str(local_path)])
            return local_path
        return None


# ═══════════════════════════════════════════════════════════════
#  MAIN INTERFACE HANDLER
# ═══════════════════════════════════════════════════════════════

class SubAgentInterface:
    """
    Main interface handler. Receives messages from Telegram,
    routes through Bootstrap Router, returns response with
    status tag.
    """

    def __init__(self):
        self.contexts: Dict[int, RoutingContext] = {}
        self.router = BootstrapRouter() if ROUTER_AVAILABLE else None
        self.parser = CommandParser()
        self.file_handler = FileHandler()

    def get_context(self, chat_id: int) -> RoutingContext:
        if chat_id not in self.contexts:
            self.contexts[chat_id] = RoutingContext(chat_id=chat_id)
        return self.contexts[chat_id]

    async def handle_message(self, chat_id: int, text: str,
                             file_path: Optional[Path] = None) -> str:
        """
        Main entry point. Process a user message and return response.
        
        Returns formatted response string with status tag appended.
        """
        ctx = self.get_context(chat_id)
        parsed = self.parser.parse(text)

        # ── Command handling ──
        if parsed["type"] == "command":
            return self._handle_command(ctx, parsed)

        # ── Feedback handling ──
        if parsed["type"] == "feedback":
            return self._handle_feedback(ctx, parsed)

        # ── Normal query routing ──
        return await self._handle_query(ctx, parsed["query"], file_path)

    def _handle_command(self, ctx: RoutingContext,
                        parsed: dict) -> str:
        action = parsed["action"]

        if action == "force_local":
            if not parsed["query"]:
                return "Usage: `/local <your question>`"
            # Will route with force flag below
            # For now, return placeholder
            return self._force_route_sync(ctx, parsed["query"], "thinkcentre")

        elif action == "force_cloud":
            if not parsed["query"]:
                return "Usage: `/cloud <your question>`"
            return self._force_route_sync(ctx, parsed["query"], "cloud")

        elif action == "pin":
            target = parsed["query"].lower().strip()
            if target in ("local", "thinkcentre", "gpd"):
                ctx.pinned_node = "thinkcentre" if target != "gpd" else "gpd"
                return f"📌 Pinned to {ctx.pinned_node}. All queries route locally until `/unpin`."
            elif target in ("cloud", "kimi"):
                ctx.pinned_node = "cloud"
                return "📌 Pinned to cloud. All queries route to Kimi until `/unpin`."
            else:
                return "Usage: `/pin local` or `/pin cloud`"

        elif action == "unpin":
            ctx.pinned_node = None
            return "📌 Unpinned. Smart routing resumed."

        elif action == "status":
            return self._format_status(ctx)

        elif action == "cost":
            return self._format_cost(ctx)

        elif action == "help":
            return self._format_help()

        return "Unknown command."

    def _handle_feedback(self, ctx: RoutingContext,
                         parsed: dict) -> str:
        fb_type = parsed["feedback_type"]
        last_tier = ctx.last_tier or "simple"

        # Integrate with feedback-loop.py.
        # The filename contains a hyphen, so it cannot be imported by name
        # (import_module("feedback-loop") is a SyntaxError-equivalent at the
        # module-name level). Load it by file path instead.
        try:
            import importlib.util

            fb_path = ROUTER_DIR / "feedback-loop.py"
            spec = importlib.util.spec_from_file_location("feedback_loop", fb_path)
            fb_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fb_mod)
            loop = fb_mod.FeedbackLoop()
            result = loop.apply_feedback(fb_type, last_tier, parsed["query"])

            if fb_type == "escalate":
                return (f"📝 Noted — escalating threshold adjusted.\n"
                        f"Future similar queries more likely to hit cloud.\n"
                        f"`Adjustment: {last_tier} {result['new_adjustments'][last_tier]:+.3f}`")
            else:
                return (f"📝 Noted — routing was overkill.\n"
                        f"Future similar queries more likely to stay local.\n"
                        f"`Adjustment: {last_tier} {result['new_adjustments'][last_tier]:+.3f}`")
        except Exception as e:
            logger.warning(f"Feedback loop error: {e}")
            return f"📝 Feedback recorded: {fb_type} (offline mode)"

    async def _handle_query(self, ctx: RoutingContext, query: str,
                            file_path: Optional[Path] = None) -> str:
        """Route query through Bootstrap Router."""
        if not self.router:
            return "⚠️ Router not available. Send directly to Kimi."

        # Determine force node from pin
        force_node = ctx.pinned_node

        # Route
        result = self.router.route(query, force_node=force_node)

        gen = result.get("generation")
        analysis = result.get("analysis", {})
        node = result.get("routed_to", "unknown")

        if gen and gen.get("success"):
            response_text = gen["response"]
            duration = gen.get("duration_sec", 0)
            model = gen.get("model", "unknown")
            cost = 0.0 if node != "cloud" else 0.002  # placeholder
            is_pinned = ctx.pinned_node is not None
            was_escalated = (analysis.get("tier") != "cloud"
                             and node == "cloud")

            # Record in context
            ctx.record(node, model, analysis.get("tier", "unknown"), cost)

            # Append status tag
            tag = StatusTag.format_telegram(
                node, model, duration, cost,
                pinned=is_pinned, escalated=was_escalated
            )

            return response_text + tag

        else:
            error = gen.get("error", "unknown") if gen else "no_response"
            return f"⚠️ Routing failed: {error}\nTry `/cloud` to force cloud."

    def _force_route_sync(self, ctx: RoutingContext,
                          query: str, node: str) -> str:
        """Synchronous forced routing (for command handlers)."""
        if not self.router:
            return "⚠️ Router not available."

        result = self.router.route(query, force_node=node)
        gen = result.get("generation")

        if gen and gen.get("success"):
            model = gen.get("model", "unknown")
            duration = gen.get("duration_sec", 0)
            cost = 0.0 if node != "cloud" else 0.002
            ctx.record(node, model, "forced", cost)
            tag = StatusTag.format_telegram(node, model, duration, cost)
            return gen["response"] + tag
        else:
            error = gen.get("error", "unknown") if gen else "no_response"
            return f"⚠️ Failed on {node}: {error}"

    def _format_status(self, ctx: RoutingContext) -> str:
        total = ctx.message_count
        if total == 0:
            return "No queries routed in this session yet."

        local_pct = (ctx.local_count / total * 100) if total else 0
        return (
            f"📊 *Session Stats*\n"
            f"```\n"
            f"Messages:     {total}\n"
            f"Local:        {ctx.local_count} ({local_pct:.0f}%)\n"
            f"Cloud:        {ctx.cloud_count}\n"
            f"Total cost:   ${ctx.total_cost:.4f}\n"
            f"Last node:    {ctx.last_node}\n"
            f"Last model:   {ctx.last_model}\n"
            f"Pinned:       {ctx.pinned_node or 'auto'}\n"
            f"```"
        )

    def _format_cost(self, ctx: RoutingContext) -> str:
        return (
            f"💰 Session cost: `${ctx.total_cost:.4f}`\n"
            f"Local queries: {ctx.local_count} × $0.00\n"
            f"Cloud queries: {ctx.cloud_count}"
        )

    def _format_help(self) -> str:
        return (
            "🤖 *OpenClaw Sub-Agent*\n\n"
            "Just send a message — the router decides where it goes.\n\n"
            "*Overrides:*\n"
            "`/local <query>` — Force local processing\n"
            "`/cloud <query>` — Force cloud (Kimi)\n"
            "`/pin local` — Pin all queries to local\n"
            "`/pin cloud` — Pin all queries to cloud\n"
            "`/unpin` — Resume smart routing\n\n"
            "*Info:*\n"
            "`/status` — Session stats\n"
            "`/cost` — Accumulated cost\n\n"
            "*Feedback (just type naturally):*\n"
            '"that needed cloud" — boosts future escalation\n'
            '"overkill" — reduces future escalation\n\n'
            "_Every response shows: node · model · time · cost_"
        )


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM BOT INTEGRATION (Scaffold)
# ═══════════════════════════════════════════════════════════════

TELEGRAM_BOT_SCAFFOLD = """
# ── Production Telegram Bot Setup ──────────────────────────
# 
# Prerequisites:
#   pip install python-telegram-bot --break-system-packages
#
# 1. Create bot via @BotFather on Telegram
# 2. Set BOT_TOKEN environment variable
# 3. Run: python3 sub-agent-interface.py --serve
#
# The bot uses long-polling (no webhook needed).
# For systemd service, see sub-agent.service below.

async def telegram_main():
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder, MessageHandler,
        CommandHandler, filters
    )
    
    token = os.environ.get("CLAW_TELEGRAM_TOKEN")
    if not token:
        print("Set CLAW_TELEGRAM_TOKEN environment variable")
        sys.exit(1)
    
    interface = SubAgentInterface()
    
    async def on_message(update: Update, context):
        chat_id = update.effective_chat.id
        text = update.message.text or ""
        
        # Handle file attachments
        file_path = None
        if update.message.document:
            doc = update.message.document
            tg_file = await doc.get_file()
            file_path = Path(f"/tmp/claw-upload/{doc.file_name}")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            await tg_file.download_to_drive(str(file_path))
            if not text:
                text = f"Process this file: {doc.file_name}"
        
        response = await interface.handle_message(
            chat_id, text, file_path
        )
        
        await update.message.reply_text(
            response, parse_mode="Markdown"
        )
    
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(
        filters.TEXT | filters.Document.ALL, on_message
    ))
    
    print("Sub-agent bot online. Ctrl+C to stop.")
    await app.run_polling()
"""


# ═══════════════════════════════════════════════════════════════
#  CLI TEST MODE
# ═══════════════════════════════════════════════════════════════

def cli_test():
    """Interactive CLI for testing without Telegram."""
    G = "\033[38;2;51;255;102m"
    DIM = "\033[2m"
    R = "\033[0m"

    print(f"\n  {G}═══ Sub-Agent Interface — CLI Test Mode ═══{R}")
    print(f"  {DIM}Type queries, /commands, or feedback phrases{R}")
    print(f"  {DIM}Type 'quit' to exit{R}\n")

    interface = SubAgentInterface()
    chat_id = 12345  # fake chat ID for testing

    while True:
        try:
            query = input(f"  {G}>{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if query.lower() in ("quit", "exit", "q"):
            break
        if not query:
            continue

        # Run async handler
        response = asyncio.run(
            interface.handle_message(chat_id, query)
        )

        # Strip markdown for terminal display
        clean = response.replace("`", "").replace("*", "")
        print(f"\n{clean}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Sub-Agent Interface Layer"
    )
    parser.add_argument("--serve", action="store_true",
                        help="Start Telegram bot")
    parser.add_argument("--test", action="store_true",
                        help="Interactive CLI test mode")

    args = parser.parse_args()

    if args.serve:
        print("Telegram bot mode requires python-telegram-bot.")
        print("Install: pip install python-telegram-bot --break-system-packages")
        print("Set: export CLAW_TELEGRAM_TOKEN=your_token")
        # asyncio.run(telegram_main())
    elif args.test:
        cli_test()
    else:
        parser.print_help()
