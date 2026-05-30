#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 FEEDBACK LOOP — Adaptive Routing Threshold Adjustment
 Processes user feedback phrases to shift routing boundaries.
 "that needed cloud" → future similar queries route higher.
 "overkill" → future similar queries route lower.
═══════════════════════════════════════════════════════════════
"""

import os
import json
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "routing-config.yaml"

G = "\033[38;2;51;255;102m"
DIM = "\033[2m"
BOLD = "\033[1m"
R = "\033[0m"


def load_config() -> dict:
    import yaml

    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p))


class FeedbackLoop:
    """Processes routing feedback and adjusts decision thresholds."""

    def __init__(self):
        self.config = load_config()
        self.fb_config = self.config["feedback"]
        self.db_path = expand_path(self.config["paths"]["feedback_db"])
        self.db = self._load_db()

    def _load_db(self) -> dict:
        if self.db_path.exists():
            try:
                return json.loads(self.db_path.read_text())
            except (json.JSONDecodeError, KeyError):
                pass
        return {
            "adjustments": {
                "simple": 0.0,
                "complex-local": 0.0,
                "cloud": 0.0,
            },
            "history": [],
            "stats": {
                "total_escalate": 0,
                "total_demote": 0,
                "total_feedback": 0,
            },
        }

    def _save_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(self.db, indent=2))

    def detect_feedback(self, text: str) -> dict:
        """
        Detect if text contains a feedback trigger phrase.
        Returns {"type": "escalate"|"demote"|None, "phrase": str}
        """
        text_lower = text.lower().strip()

        for phrase in self.fb_config["trigger_phrases"]["escalate"]:
            if phrase in text_lower:
                return {"type": "escalate", "phrase": phrase}

        for phrase in self.fb_config["trigger_phrases"]["demote"]:
            if phrase in text_lower:
                return {"type": "demote", "phrase": phrase}

        return {"type": None, "phrase": None}

    def apply_feedback(self, feedback_type: str,
                       current_tier: str,
                       query_summary: str = "") -> dict:
        """
        Apply feedback to adjust routing thresholds.
        - "escalate": shift current tier's score down, next tier up
        - "demote": shift current tier's score up, lower tier down
        """
        weight = self.fb_config["learning_weight"]
        adjustments = self.db["adjustments"]

        tier_order = ["simple", "complex-local", "cloud"]

        if feedback_type == "escalate":
            # Current tier wasn't enough → decrease its attractiveness
            adjustments[current_tier] = adjustments.get(current_tier, 0) - weight
            # Boost next tier
            idx = tier_order.index(current_tier)
            if idx < len(tier_order) - 1:
                next_tier = tier_order[idx + 1]
                adjustments[next_tier] = adjustments.get(next_tier, 0) + weight
            self.db["stats"]["total_escalate"] += 1

        elif feedback_type == "demote":
            # Current tier was overkill → decrease its attractiveness
            adjustments[current_tier] = adjustments.get(current_tier, 0) - weight
            # Boost lower tier
            idx = tier_order.index(current_tier)
            if idx > 0:
                prev_tier = tier_order[idx - 1]
                adjustments[prev_tier] = adjustments.get(prev_tier, 0) + weight
            self.db["stats"]["total_demote"] += 1

        self.db["stats"]["total_feedback"] += 1

        # Record history
        self.db["history"].append({
            "timestamp": datetime.now().isoformat(),
            "type": feedback_type,
            "tier": current_tier,
            "query_summary": query_summary[:100],
            "adjustments_after": dict(adjustments),
        })

        # Keep history manageable
        if len(self.db["history"]) > 500:
            self.db["history"] = self.db["history"][-500:]

        self._save_db()

        return {
            "feedback_type": feedback_type,
            "tier_adjusted": current_tier,
            "new_adjustments": dict(adjustments),
        }

    def show_status(self):
        """Display current feedback state."""
        adj = self.db["adjustments"]
        stats = self.db["stats"]
        history = self.db["history"]

        print(f"\n  {G}{BOLD}═══ Feedback Loop Status ═══{R}")
        print(f"  {G}───────────────────────────{R}")
        print(f"  {G}Total feedback received:{R}  {stats['total_feedback']}")
        print(f"  {G}Escalations:{R}              {stats['total_escalate']}")
        print(f"  {G}Demotions:{R}                {stats['total_demote']}")
        print()
        print(f"  {G}{BOLD}Current Adjustments:{R}")
        for tier, val in adj.items():
            direction = "↑" if val > 0 else "↓" if val < 0 else "─"
            color = G if val >= 0 else "\033[33m"
            print(f"    {G}{tier:15s}{R}  {color}{direction} {val:+.3f}{R}")

        if history:
            print()
            print(f"  {G}{BOLD}Last 5 Feedback Events:{R}")
            for entry in history[-5:]:
                ts = entry["timestamp"][:16]
                ft = entry["type"]
                tier = entry["tier"]
                print(f"    {DIM}{ts}{R}  {ft:10s}  on {tier}")

        print(f"  {G}───────────────────────────{R}")

    def reset(self):
        """Reset all learned adjustments."""
        self.db["adjustments"] = {
            "simple": 0.0,
            "complex-local": 0.0,
            "cloud": 0.0,
        }
        self._save_db()
        print(f"  {G}✓ Feedback adjustments reset to zero.{R}")


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Router — Feedback Loop"
    )
    parser.add_argument("--feedback", type=str,
                        help="Process feedback phrase (e.g., 'that needed cloud')")
    parser.add_argument("--tier", type=str,
                        choices=["simple", "complex-local", "cloud"],
                        help="Which tier the last query was routed to")
    parser.add_argument("--status", action="store_true",
                        help="Show current feedback state")
    parser.add_argument("--reset", action="store_true",
                        help="Reset all learned adjustments")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()
    loop = FeedbackLoop()

    if args.status:
        if args.json:
            print(json.dumps(loop.db, indent=2))
        else:
            loop.show_status()
        return

    if args.reset:
        loop.reset()
        return

    if args.feedback:
        detection = loop.detect_feedback(args.feedback)

        if detection["type"] is None:
            print(f"  {G}No feedback trigger detected in: '{args.feedback}'{R}")
            return

        tier = args.tier
        if not tier:
            # Default: assume last routed tier was "simple"
            print(f"  {G}⚠ No --tier specified, defaulting to 'simple'{R}")
            tier = "simple"

        result = loop.apply_feedback(
            detection["type"], tier, args.feedback
        )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  {G}✓ Feedback applied: {detection['type']} "
                  f"(trigger: '{detection['phrase']}'){R}")
            print(f"  {G}  Adjusted tier: {tier}{R}")
            for t, v in result["new_adjustments"].items():
                print(f"    {G}{t}: {v:+.3f}{R}")
        return

    # Default
    loop.show_status()


if __name__ == "__main__":
    main()
