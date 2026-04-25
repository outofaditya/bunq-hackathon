#!/usr/bin/env bash
# Stop both ngrok + the FastAPI server cleanly.
set -euo pipefail

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RESET='\033[0m'
log()  { printf "${CYAN}→${RESET} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}!${RESET} %s\n" "$1"; }

# Kill anything bound to :8000 (the orchestrator).
if lsof -ti :8000 >/dev/null 2>&1; then
    log "killing FastAPI server on :8000…"
    lsof -ti :8000 | xargs -r kill -9 2>/dev/null || true
    ok "server stopped"
else
    warn "no server running on :8000"
fi

# Kill ngrok if it's running.
if pgrep -x ngrok >/dev/null 2>&1; then
    log "killing ngrok…"
    pkill -x ngrok || true
    ok "ngrok stopped"
else
    warn "no ngrok process found"
fi

# Free port 4040 too (ngrok's API), just in case.
if lsof -ti :4040 >/dev/null 2>&1; then
    lsof -ti :4040 | xargs -r kill -9 2>/dev/null || true
fi

ok "all stopped"
