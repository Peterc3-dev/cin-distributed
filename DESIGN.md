# Sub-Agent Interface Layer — Design Document

## Decision: Option 1+4 Hybrid

**One bot. Smart routing. Transparent status. Reply-chain affinity.**

### Why not the others

**Option 2 (two bots)** splits conversation context. Escalation means
copy-pasting between @claw_local and @claw_bot. Context loss is the
enemy of useful AI — you'd be manually doing what the router should do.

**Option 3 (slash commands primary)** forces pre-classification on every
message. The router exists so you don't have to think. Slash commands
survive as overrides only — for the 5% of cases where you know better
than the heuristic.

**Option 4 (reply-to routing)** is excellent UX for mid-conversation
control but insufficient as a primary interface. Adopted as the
feedback/affinity mechanism within Option 1.

### The winning design

```
 ┌─────────────────────────────────────────────────┐
 │  USER sends message to @claw_bot                │
 └──────────────────────┬──────────────────────────┘
                        │
                 ┌──────▼──────┐
                 │  /command?  │──yes──▶ handle override
                 └──────┬──────┘
                        │ no
                 ┌──────▼──────┐
                 │  feedback?  │──yes──▶ adjust thresholds
                 └──────┬──────┘
                        │ no
                 ┌──────▼──────┐
                 │   pinned?   │──yes──▶ route to pinned node
                 └──────┬──────┘
                        │ no
                 ┌──────▼──────┐
                 │   ROUTER    │ analyze → route → generate
                 └──────┬──────┘
                        │
                 ┌──────▼──────┐
                 │  RESPONSE   │ + status tag
                 └─────────────┘
```

## Status Tag (every response)

```
⚡ ThinkCentre · qwen2.5:7b · 1.2s · $0.00
☁️ Cloud · kimi-2.5 · 3.4s · ~$0.002
⚡→☁️ ThinkCentre → Cloud · kimi-2.5 · 4.1s · ~$0.002  [escalated]
⚡ GPD · qwen2.5:7b · 0.8s · $0.00 · [pinned]
```

The tag tells you four things at a glance:
1. Where it ran (and if it escalated)
2. Which model answered
3. How long it took
4. What it cost

This satisfies the "know when burning tokens" requirement without
adding friction. It's always there, always small, never in the way.

## Selective Mutism Support

The interface is text-first by design. No voice, no calls, no
mandatory interaction patterns. Key accommodations:

- **Feedback phrases are inline** — "that needed cloud" is just
  part of normal texting, not a special interaction
- **Pin and forget** — `/pin local` means zero routing decisions
  until you `/unpin`
- **No confirmation dialogs** — everything is one-shot
- **Status is passive** — the tag is there if you want it,
  ignorable if you don't

## File Attachments

```
User sends file to @claw_bot
    │
    ├─ Routed to ThinkCentre: file already local
    ├─ Routed to GPD: SCP via Tailscale (auto)
    └─ Routed to Cloud: text extraction, metadata only
```

Files live in `~/.openclaw/workspace/temp/` on ThinkCentre.
Cross-node transfers use existing Tailscale tunnel.

## Deployment

```bash
# 1. Create Telegram bot via @BotFather
# 2. Install deps
pip install python-telegram-bot pyyaml requests --break-system-packages

# 3. Set token
export CLAW_TELEGRAM_TOKEN="your_token_here"

# 4. Test locally
python3 sub-agent-interface.py --test

# 5. Deploy as service
sudo cp sub-agent@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sub-agent@YOUR_USER
```

## Slash Commands (overrides only)

| Command          | Effect                              |
|------------------|-------------------------------------|
| `/local <query>` | Force to ThinkCentre/GPD            |
| `/cloud <query>` | Force to Kimi                       |
| `/pin local`     | All queries stay local until unpin  |
| `/pin cloud`     | All queries go cloud until unpin    |
| `/unpin`         | Resume smart routing                |
| `/status`        | Session stats                       |
| `/cost`          | Accumulated cost                    |
| `/help`          | Command reference                   |

## Feedback Loop Integration

The interface pipes feedback directly to `feedback-loop.py`:

- "that needed cloud" → `apply_feedback("escalate", last_tier)`
- "overkill" → `apply_feedback("demote", last_tier)`

The router's keyword weights shift over time based on real usage.
No manual threshold tuning needed after initial deployment.
