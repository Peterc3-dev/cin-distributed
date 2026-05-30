#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 ROUTING LOGGER — Decision Tracking & Daily Reports
 Reads routing-decisions.log, generates summaries and reports.
═══════════════════════════════════════════════════════════════
"""

import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "routing-config.yaml"

G = "\033[38;2;51;255;102m"
DIM = "\033[2m"
BOLD = "\033[1m"
R = "\033[0m"


def load_config() -> dict:
    # Imported lazily so the pure log-parsing/report helpers in this module
    # can be imported and used without PyYAML installed.
    import yaml

    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p))


def parse_log_entry(line: str) -> dict:
    """Parse a single log line into a dict."""
    entry = {}
    # Extract timestamp
    ts_match = re.match(r'\[(.*?)\]', line)
    if ts_match:
        entry["timestamp"] = ts_match.group(1)

    # Extract key=value pairs
    for match in re.finditer(r'(\w+)=([^\s]+)', line):
        key, val = match.groups()
        # Try numeric conversion
        try:
            if '.' in val:
                val = float(val)
            elif val.isdigit():
                val = int(val)
            elif val in ('True', 'true'):
                val = True
            elif val in ('False', 'false'):
                val = False
        except (ValueError, AttributeError):
            pass
        entry[key] = val

    # Handle tokens≈N (unicode key)
    tok_match = re.search(r'tokens≈(\d+)', line)
    if tok_match:
        entry["tokens"] = int(tok_match.group(1))

    return entry


def load_log(config: dict) -> list:
    """Load and parse the routing log."""
    log_path = expand_path(config["paths"]["routing_log"])
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(parse_log_entry(line))
    return entries


def filter_by_date(entries: list, date_str: str = None,
                   days_back: int = 1) -> list:
    """Filter entries by date. Default: today."""
    if date_str:
        target = datetime.fromisoformat(date_str).date()
    else:
        target = datetime.now().date()

    start = target - timedelta(days=days_back - 1)
    filtered = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e.get("timestamp", ""))
            if start <= ts.date() <= target:
                filtered.append(e)
        except (ValueError, TypeError):
            pass
    return filtered


def generate_daily_report(entries: list, date_label: str = "Today") -> str:
    """Generate a phosphor-green daily routing report."""
    if not entries:
        return f"  {G}No routing decisions logged for {date_label}.{R}"

    total = len(entries)
    tier_counts = Counter(e.get("tier", "unknown") for e in entries)
    node_counts = Counter(e.get("node", "unknown") for e in entries)
    method_counts = Counter(e.get("method", "unknown") for e in entries)

    successes = sum(1 for e in entries if e.get("success") is True)
    failures = sum(1 for e in entries if e.get("success") is False)

    durations = [e["duration"] for e in entries
                 if isinstance(e.get("duration"), (int, float))]
    avg_dur = sum(durations) / len(durations) if durations else 0

    tokens_list = [e["tokens"] for e in entries
                   if isinstance(e.get("tokens"), (int, float))]
    total_tokens = sum(tokens_list)

    # Cost estimate: local = $0, cloud = placeholder
    cloud_count = node_counts.get("cloud", 0)
    est_cost = cloud_count * 0.002  # rough placeholder per cloud call

    lines = [
        "",
        f"  {G}{BOLD}═══ BOOTSTRAP ROUTER — Daily Report ═══{R}",
        f"  {G}{DIM}{date_label}{R}",
        f"  {G}───────────────────────────────────────{R}",
        f"  {G}Total queries routed:{R}  {total}",
        f"  {G}Success / Fail:{R}       {successes} / {failures}",
        f"  {G}Avg response time:{R}    {avg_dur:.2f}s",
        f"  {G}Tokens processed ≈{R}    {total_tokens}",
        f"  {G}Estimated cost:{R}       ${est_cost:.4f}",
        "",
        f"  {G}{BOLD}By Tier:{R}",
    ]
    for tier, count in tier_counts.most_common():
        pct = count / total * 100
        bar = "█" * int(pct / 5)
        lines.append(f"    {G}{tier:15s}{R} {count:4d}  {G}{bar}{R} {pct:.0f}%")

    lines.append("")
    lines.append(f"  {G}{BOLD}By Node:{R}")
    for node, count in node_counts.most_common():
        pct = count / total * 100
        lines.append(f"    {G}{node:15s}{R} {count:4d}  ({pct:.0f}%)")

    lines.append("")
    lines.append(f"  {G}{BOLD}By Method:{R}")
    for method, count in method_counts.most_common():
        lines.append(f"    {G}{method:20s}{R} {count:4d}")

    lines.append(f"  {G}───────────────────────────────────────{R}")

    return "\n".join(lines)


def save_report(config: dict, report: str, date_str: str = None):
    """Save daily report to file."""
    report_dir = expand_path(config["paths"]["daily_report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)

    date_label = date_str or datetime.now().strftime("%Y-%m-%d")
    filename = f"routing-report-{date_label}.txt"
    report_path = report_dir / filename

    # Strip ANSI codes for file output
    clean = re.sub(r'\033\[[^m]*m', '', report)
    report_path.write_text(clean)

    print(f"  {G}Report saved: {report_path}{R}")


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Router — Routing Logger & Reports"
    )
    parser.add_argument("--report", action="store_true",
                        help="Generate daily report")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of days to include (default: 1)")
    parser.add_argument("--date", type=str,
                        help="Specific date (YYYY-MM-DD)")
    parser.add_argument("--save", action="store_true",
                        help="Save report to file")
    parser.add_argument("--stats", action="store_true",
                        help="Show all-time statistics")
    parser.add_argument("--tail", type=int, default=0,
                        help="Show last N log entries")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()
    config = load_config()
    entries = load_log(config)

    if args.tail > 0:
        recent = entries[-args.tail:]
        if args.json:
            print(json.dumps(recent, indent=2, default=str))
        else:
            for e in recent:
                ts = e.get("timestamp", "?")[:19]
                tier = e.get("tier", "?")
                node = e.get("node", "?")
                ok = "✓" if e.get("success") else "✗"
                dur = e.get("duration", "?")
                print(f"  {G}{ts}{R}  {tier:14s}  → {node:12s}  "
                      f"{ok}  {dur}s")
        return

    if args.report or args.stats:
        if args.stats:
            filtered = entries
            label = "All Time"
        else:
            filtered = filter_by_date(entries, args.date, args.days)
            label = args.date or "Today"

        if args.json:
            print(json.dumps({
                "date": label,
                "total": len(filtered),
                "entries": filtered,
            }, indent=2, default=str))
        else:
            report = generate_daily_report(filtered, label)
            print(report)
            if args.save:
                save_report(config, report, args.date)
        return

    # Default: show summary
    print(f"\n  {G}Routing log: {len(entries)} total entries{R}")
    if entries:
        first = entries[0].get("timestamp", "?")[:10]
        last = entries[-1].get("timestamp", "?")[:10]
        print(f"  {G}Date range: {first} → {last}{R}")
    print(f"  {DIM}Use --report, --tail N, or --stats for details{R}")


if __name__ == "__main__":
    main()
