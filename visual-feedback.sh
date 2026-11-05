#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  VISUAL FEEDBACK — Phosphor Green Terminal Status Display
#  Standalone status script for Bootstrap Router
# ═══════════════════════════════════════════════════════════════

# Phosphor green palette
G='\033[38;2;51;255;102m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'
YELLOW='\033[33m'
RED='\033[31m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Status display functions ────────────────────────────────

status_analyzing() {
    echo -e "  ${G}●${RESET} ${G}Analyzing request...${RESET}"
}

status_routing() {
    local target="${1:-unknown}"
    local tier="${2:-unknown}"
    echo -e "  ${G}${BOLD}▶${RESET} ${G}Routing to ${target} (${tier})${RESET}"
}

status_processing_local() {
    echo -e "  ${G}${DIM}\$${RESET} ${G}Processing locally (\$0.00)${RESET}"
}

status_escalating() {
    echo -e "  ${YELLOW}⚠${RESET} ${G}Escalating to cloud${RESET}"
}

status_success() {
    local duration="${1:-?}"
    local model="${2:-unknown}"
    echo -e "  ${G}${BOLD}✓${RESET} ${G}Done in ${duration}s via ${model}${RESET}"
}

status_error() {
    local msg="${1:-unknown error}"
    echo -e "  ${RED}✗${RESET} ${G}Failed: ${msg}${RESET}"
}

# ─── Health check display ────────────────────────────────────

check_node() {
    local name="$1"
    local host="$2"
    local port="${3:-11434}"

    if curl -s --connect-timeout 3 "http://${host}:${port}/api/tags" > /dev/null 2>&1; then
        echo -e "  ${G}✓ online${RESET}  ${name} (${host}:${port})"
        return 0
    else
        echo -e "  ${RED}✗ offline${RESET} ${name} (${host}:${port})"
        return 1
    fi
}

health_check() {
    echo -e "\n  ${G}${BOLD}═══ Node Health ═══${RESET}"
    echo -e "  ${G}───────────────────${RESET}"
    check_node "ThinkCentre M70q" "192.168.12.190" 11434
    check_node "GPD Pocket 4"    "100.77.212.27"  11434
    echo -e "  ${G}✓ online${RESET}  Kimi 2.5 Cloud (assumed)"
    echo -e "  ${G}───────────────────${RESET}"
}

# ─── Dashboard ────────────────────────────────────────────────

dashboard() {
    local log_path="${CIN_WORKSPACE:-$HOME/.openclaw/workspace}/reports/routing-decisions.log"

    echo -e "\n  ${G}${BOLD}═══ BOOTSTRAP ROUTER ═══${RESET}"
    echo -e "  ${G}${DIM}$(date '+%Y-%m-%d %H:%M:%S')${RESET}"
    echo -e "  ${G}────────────────────────${RESET}"

    # Node health
    health_check

    # Quick stats from log
    if [ -f "$log_path" ]; then
        local total=$(wc -l < "$log_path" 2>/dev/null || echo 0)
        local today=$(grep "$(date +%Y-%m-%d)" "$log_path" 2>/dev/null | wc -l || echo 0)
        local local_count=$(grep -c "node=thinkcentre\|node=gpd" "$log_path" 2>/dev/null || echo 0)
        local cloud_count=$(grep -c "node=cloud" "$log_path" 2>/dev/null || echo 0)

        echo -e "\n  ${G}${BOLD}Routing Stats${RESET}"
        echo -e "  ${G}─────────────${RESET}"
        echo -e "  ${G}Total routed:${RESET}    $total"
        echo -e "  ${G}Today:${RESET}           $today"
        echo -e "  ${G}Local:${RESET}           $local_count"
        echo -e "  ${G}Cloud:${RESET}           $cloud_count"

        if [ "$total" -gt 0 ]; then
            local pct=$(( local_count * 100 / total ))
            echo -e "  ${G}Local rate:${RESET}      ${pct}%"
        fi
    else
        echo -e "\n  ${DIM}No routing log found yet.${RESET}"
    fi

    echo ""
}

# ─── Spinner for long operations ──────────────────────────────

spinner() {
    local pid=$1
    local msg="${2:-Processing}"
    local chars="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    while kill -0 "$pid" 2>/dev/null; do
        for (( i=0; i<${#chars}; i++ )); do
            echo -ne "\r  ${G}${chars:$i:1}${RESET} ${G}${msg}${RESET}" >&2
            sleep 0.1
            kill -0 "$pid" 2>/dev/null || break
        done
    done
    echo -ne "\r" >&2
}

# ─── CLI dispatch ─────────────────────────────────────────────

case "${1:-dashboard}" in
    dashboard)   dashboard ;;
    health)      health_check ;;
    analyzing)   status_analyzing ;;
    routing)     status_routing "$2" "$3" ;;
    local)       status_processing_local ;;
    escalate)    status_escalating ;;
    success)     status_success "$2" "$3" ;;
    error)       status_error "$2" ;;
    *)
        echo -e "  ${G}Usage: visual-feedback.sh {dashboard|health|analyzing|routing|local|escalate|success|error}${RESET}"
        ;;
esac
