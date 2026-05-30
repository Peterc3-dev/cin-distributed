#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 BOOTSTRAP ROUTER — Distributed Inference Routing Engine
 Routes queries to ThinkCentre / GPD / Cloud based on
 complexity analysis, keyword signals, and feedback history.
═══════════════════════════════════════════════════════════════
"""

import sys
import os
import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# ─── Constants ───────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "routing-config.yaml"

# ANSI phosphor green
G = "\033[38;2;51;255;102m"
DIM = "\033[2m"
BOLD = "\033[1m"
R = "\033[0m"


def load_config() -> dict:
    # Imported lazily so that the pure complexity-analysis logic in this
    # module (ComplexityAnalyzer) can be imported without PyYAML installed.
    import yaml

    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p))


# ═══════════════════════════════════════════════════════════════
#  COMPLEXITY ANALYZER
# ═══════════════════════════════════════════════════════════════

class ComplexityAnalyzer:
    """Rule-based query complexity scorer."""

    def __init__(self, config: dict):
        self.rules = config["routing_rules"]
        self.thresholds = self.rules["token_thresholds"]
        self.keywords = self.rules["keywords"]
        self.feedback_db = self._load_feedback(config)

    def _load_feedback(self, config: dict) -> dict:
        fb_path = expand_path(config["paths"]["feedback_db"])
        if fb_path.exists():
            try:
                return json.loads(fb_path.read_text())
            except (json.JSONDecodeError, KeyError):
                pass
        return {"adjustments": {}, "history": []}

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: words * 1.3"""
        return int(len(text.split()) * 1.3)

    def keyword_score(self, text: str) -> Tuple[str, float]:
        """
        Scan for routing keywords. Returns (tier, confidence).
        Higher confidence = stronger signal.
        """
        text_lower = text.lower()
        scores = {"simple": 0.0, "complex_local": 0.0, "cloud_escalation": 0.0}

        for kw in self.keywords["simple"]:
            if kw in text_lower:
                scores["simple"] += 1.0

        for kw in self.keywords["complex_local"]:
            if kw in text_lower:
                scores["complex_local"] += 1.5  # weighted higher

        for kw in self.keywords["cloud_escalation"]:
            if kw in text_lower:
                scores["cloud_escalation"] += 2.0  # weighted highest

        # Apply learned adjustments
        for tier, adj in self.feedback_db.get("adjustments", {}).items():
            key = tier.replace("-", "_")
            if key in scores:
                scores[key] += adj

        best_tier = max(scores, key=scores.get)
        confidence = scores[best_tier] / max(sum(scores.values()), 1.0)

        tier_map = {
            "simple": "simple",
            "complex_local": "complex-local",
            "cloud_escalation": "cloud",
        }
        return tier_map.get(best_tier, "simple"), confidence

    def analyze(self, query: str) -> dict:
        """
        Full complexity analysis. Returns routing decision dict.
        """
        tokens = self.estimate_tokens(query)
        kw_tier, kw_confidence = self.keyword_score(query)

        # Token-based tier
        if tokens <= self.thresholds["simple_max"]:
            token_tier = "simple"
        elif tokens <= self.thresholds["complex_local_max"]:
            token_tier = "complex-local"
        else:
            token_tier = "cloud"

        # Combine signals: keyword signal wins if confident, else tokens decide
        if kw_confidence >= 0.4:
            final_tier = kw_tier
            method = "keyword"
        else:
            final_tier = token_tier
            method = "token-length"

        # Check for multi-sentence / multi-step indicators
        sentences = len(re.split(r'[.!?]+', query.strip()))
        if sentences >= 4 and final_tier == "simple":
            final_tier = "complex-local"
            method = "sentence-complexity"

        # Question depth: nested questions or "and also" patterns
        if re.search(r'\b(and also|additionally|furthermore|then)\b', query.lower()):
            if final_tier == "simple":
                final_tier = "complex-local"
                method = "multi-step-detection"

        return {
            "tier": final_tier,
            "method": method,
            "tokens_est": tokens,
            "keyword_tier": kw_tier,
            "keyword_confidence": round(kw_confidence, 3),
            "token_tier": token_tier,
            "sentences": sentences,
            "timestamp": datetime.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════════
#  NODE CONNECTOR
# ═══════════════════════════════════════════════════════════════

class NodeConnector:
    """Handles Ollama API calls to ThinkCentre and GPD."""

    def __init__(self, config: dict):
        self.nodes = config["nodes"]

    def _ollama_url(self, node_key: str) -> str:
        node = self.nodes[node_key]
        host = node.get("tailscale_ip") or node["host"]
        port = node["port"]
        return f"http://{host}:{port}"

    def check_health(self, node_key: str) -> bool:
        """Ping Ollama endpoint."""
        if node_key == "cloud":
            return True  # cloud assumed available
        import requests

        try:
            url = self._ollama_url(node_key)
            r = requests.get(f"{url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_default_model(self, node_key: str) -> str:
        models = self.nodes[node_key]["models"]
        # pick highest priority (lowest number)
        best = sorted(models, key=lambda m: m["priority"])[0]
        return best["name"]

    def generate(self, node_key: str, query: str,
                 model: Optional[str] = None,
                 stream: bool = False) -> dict:
        """
        Send generation request to an Ollama node.
        Returns {"response": str, "duration_sec": float, "model": str}
        """
        if node_key == "cloud":
            return self._cloud_generate(query)

        import requests

        url = self._ollama_url(node_key)
        model = model or self.get_default_model(node_key)

        payload = {
            "model": model,
            "prompt": query,
            "stream": False,
        }

        t0 = time.time()
        try:
            r = requests.post(
                f"{url}/api/generate",
                json=payload,
                timeout=self.nodes[node_key].get("latency_ceiling_sec", 30),
            )
            r.raise_for_status()
            data = r.json()
            duration = time.time() - t0
            return {
                "response": data.get("response", ""),
                "duration_sec": round(duration, 2),
                "model": model,
                "node": node_key,
                "success": True,
            }
        except (requests.RequestException, KeyError) as e:
            return {
                "response": "",
                "duration_sec": round(time.time() - t0, 2),
                "model": model,
                "node": node_key,
                "success": False,
                "error": str(e),
            }

    def _cloud_generate(self, query: str) -> dict:
        """
        Placeholder for Kimi 2.5 / Moonshot API call.
        In production, this calls the OpenClaw relay or direct API.
        """
        # TODO: Wire to OpenClaw's Kimi relay
        return {
            "response": "[CLOUD] Kimi 2.5 relay not yet wired. Route manually via OpenClaw.",
            "duration_sec": 0,
            "model": "kimi-2.5",
            "node": "cloud",
            "success": False,
            "error": "cloud_relay_not_configured",
        }


# ═══════════════════════════════════════════════════════════════
#  ROUTER ENGINE
# ═══════════════════════════════════════════════════════════════

class BootstrapRouter:
    """Main routing engine. Analyze → Route → Log → Escalate."""

    TIER_TO_NODE = {
        "simple": "thinkcentre",
        "complex-local": "gpd",
        "cloud": "cloud",
    }

    def __init__(self):
        self.config = load_config()
        self.analyzer = ComplexityAnalyzer(self.config)
        self.connector = NodeConnector(self.config)
        self.escalation = self.config["routing_rules"]["escalation"]

    def visual(self, msg: str, style: str = "info"):
        """Phosphor green terminal feedback."""
        prefix_map = {
            "info":    f"{G}●{R}",
            "route":   f"{G}{BOLD}▶{R}",
            "success": f"{G}{BOLD}✓{R}",
            "warn":    f"\033[33m⚠{R}",
            "error":   f"\033[31m✗{R}",
            "cost":    f"{G}{DIM}${R}",
        }
        prefix = prefix_map.get(style, f"{G}●{R}")
        print(f"  {prefix} {G}{msg}{R}", file=sys.stderr)

    def route(self, query: str, force_node: Optional[str] = None) -> dict:
        """
        Full routing pipeline:
        1. Analyze complexity
        2. Show visual feedback
        3. Send to appropriate node
        4. Escalate on failure
        5. Log decision
        """
        # ── Step 1: Analyze ──
        self.visual("Analyzing request...", "info")
        analysis = self.analyzer.analyze(query)
        tier = analysis["tier"]

        if force_node:
            target_node = force_node
            self.visual(f"Forced routing to {target_node}", "route")
        else:
            target_node = self.TIER_TO_NODE[tier]
            node_name = self.config["nodes"][target_node]["name"]
            self.visual(f"Routing to {node_name} ({tier})", "route")

        # ── Step 2: Health check ──
        if not self.connector.check_health(target_node):
            self.visual(f"{target_node} unreachable — escalating", "warn")
            target_node = self._escalate_node(target_node)
            if not target_node:
                self.visual("All nodes unreachable!", "error")
                return self._build_result(analysis, None, "all_nodes_down")

        # ── Step 3: Generate ──
        is_local = target_node != "cloud"
        cost_msg = "Processing locally ($0.00)" if is_local else "Escalating to cloud"
        self.visual(cost_msg, "cost")

        result = self.connector.generate(target_node, query)

        # ── Step 4: Escalate on failure ──
        if not result["success"] and self.escalation["on_local_failure"]:
            self.visual(f"{target_node} failed — escalating", "warn")
            next_node = self._escalate_node(target_node)
            if next_node:
                result = self.connector.generate(next_node, query)
                target_node = next_node

        # ── Step 5: Log ──
        if result["success"]:
            self.visual(
                f"Done in {result['duration_sec']}s via {result['model']}",
                "success"
            )
        else:
            self.visual(f"Failed: {result.get('error', 'unknown')}", "error")

        full_result = self._build_result(analysis, result, target_node)
        self._log_decision(full_result)

        return full_result

    def _escalate_node(self, current: str) -> Optional[str]:
        """Get next tier up."""
        chain = ["thinkcentre", "gpd", "cloud"]
        try:
            idx = chain.index(current)
            for next_node in chain[idx + 1:]:
                if self.connector.check_health(next_node):
                    return next_node
        except ValueError:
            pass
        return None

    def _build_result(self, analysis: dict, gen_result: Optional[dict],
                      node: str) -> dict:
        return {
            "analysis": analysis,
            "generation": gen_result,
            "routed_to": node,
            "timestamp": datetime.now().isoformat(),
            "cost": 0.0 if node != "cloud" else None,  # cloud cost TBD
        }

    def _log_decision(self, result: dict):
        """Append to routing-decisions.log"""
        log_path = expand_path(self.config["paths"]["routing_log"])
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = (
            f"[{result['timestamp']}] "
            f"tier={result['analysis']['tier']} "
            f"method={result['analysis']['method']} "
            f"node={result['routed_to']} "
            f"tokens≈{result['analysis']['tokens_est']} "
            f"kw_conf={result['analysis']['keyword_confidence']} "
        )

        if result["generation"]:
            entry += (
                f"model={result['generation']['model']} "
                f"duration={result['generation']['duration_sec']}s "
                f"success={result['generation']['success']}"
            )

        with open(log_path, "a") as f:
            f.write(entry + "\n")


# ═══════════════════════════════════════════════════════════════
#  CLI INTERFACE
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Router — Distributed Inference Routing"
    )
    parser.add_argument("query", nargs="?", help="Query to route")
    parser.add_argument("--force", choices=["thinkcentre", "gpd", "cloud"],
                        help="Force routing to specific node")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Show routing analysis without generating")
    parser.add_argument("--health", action="store_true",
                        help="Check all node health")
    parser.add_argument("--stdin", action="store_true",
                        help="Read query from stdin")
    parser.add_argument("--json", action="store_true",
                        help="Output result as JSON")

    args = parser.parse_args()
    router = BootstrapRouter()

    # Health check mode
    if args.health:
        for node_key in ["thinkcentre", "gpd", "cloud"]:
            name = router.config["nodes"][node_key]["name"]
            healthy = router.connector.check_health(node_key)
            status = f"{G}✓ online{R}" if healthy else f"\033[31m✗ offline{R}"
            print(f"  {status}  {name}")
        return

    # Get query
    query = args.query
    if args.stdin or (not query and not sys.stdin.isatty()):
        query = sys.stdin.read().strip()

    if not query:
        parser.print_help()
        return

    # Analyze-only mode
    if args.analyze_only:
        analysis = router.analyzer.analyze(query)
        if args.json:
            print(json.dumps(analysis, indent=2))
        else:
            tier = analysis["tier"]
            node = BootstrapRouter.TIER_TO_NODE[tier]
            name = router.config["nodes"][node]["name"]
            print(f"\n  {G}{BOLD}Routing Analysis{R}")
            print(f"  {G}────────────────{R}")
            print(f"  Tier:       {G}{tier}{R}")
            print(f"  Target:     {G}{name}{R}")
            print(f"  Method:     {analysis['method']}")
            print(f"  Tokens ≈    {analysis['tokens_est']}")
            print(f"  KW tier:    {analysis['keyword_tier']} "
                  f"(conf: {analysis['keyword_confidence']})")
            print(f"  Sentences:  {analysis['sentences']}")
        return

    # Full routing
    result = router.route(query, force_node=args.force)

    if args.json:
        print(json.dumps(result, indent=2))
    elif result["generation"] and result["generation"]["success"]:
        print(f"\n{result['generation']['response']}")
    elif result["generation"]:
        print(f"\n  {G}[Router]{R} Generation failed: "
              f"{result['generation'].get('error', 'unknown')}")


if __name__ == "__main__":
    main()
