#!/usr/bin/env bash
# Mission Mode launcher — opens ngrok + FastAPI server in two new Terminal windows.
#
# Usage:
#   ./start.sh             open both ngrok + server in separate Terminal tabs
#   ./start.sh --no-ngrok  start server only (polling-only mode, no real webhooks)
#   ./start.sh --inline    run both in current terminal, multiplexed (Ctrl-C kills both)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log()   { printf "${CYAN}→${RESET} %s\n" "$1"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$1"; }
err()   { printf "${RED}✗${RESET} %s\n" "$1"; }

USE_NGROK=1
INLINE=0
for arg in "$@"; do
    case "$arg" in
        --no-ngrok) USE_NGROK=0 ;;
        --inline)   INLINE=1 ;;
        --help|-h)
            sed -n '2,10p' "$0"
            exit 0
            ;;
    esac
done

# --- preflight ---------------------------------------------------------

if [[ ! -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    err ".venv not found at $PROJECT_ROOT/.venv"
    err "Run:  python3.13 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    err ".env not found in project root. Phase 0 setup required."
    exit 1
fi

if [[ "$USE_NGROK" == "1" ]] && ! command -v ngrok >/dev/null 2>&1; then
    warn "ngrok not in PATH — falling back to --no-ngrok (polling-only mode)."
    warn "  install via:  brew install ngrok/ngrok/ngrok"
    USE_NGROK=0
fi

if lsof -ti :8000 >/dev/null 2>&1; then
    warn "Port 8000 already in use — killing the existing process."
    lsof -ti :8000 | xargs -r kill -9 2>/dev/null || true
    sleep 1
fi

# --- inline mode (single terminal, prefixed output) --------------------

if [[ "$INLINE" == "1" ]]; then
    log "Inline mode — both processes in this terminal. Ctrl-C kills both."
    pids=()
    cleanup() {
        for pid in "${pids[@]:-}"; do
            kill "$pid" 2>/dev/null || true
        done
        wait 2>/dev/null || true
        log "stopped."
    }
    trap cleanup EXIT INT TERM

    if [[ "$USE_NGROK" == "1" ]]; then
        log "starting ngrok…"
        ngrok http 8000 --log=stdout 2>&1 | sed -e $'s/^/\033[33m[ngrok]\033[0m /' &
        pids+=($!)
        sleep 2
    fi

    log "starting server…"
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
    python -m orchestrator.server 2>&1 | sed -e $'s/^/\033[36m[server]\033[0m /' &
    pids+=($!)

    sleep 4
    ok "dashboard: http://localhost:8000/"
    wait
    exit 0
fi

# --- two-terminal mode (default; macOS only) ---------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
    err "Two-terminal mode requires macOS Terminal.app. Use --inline instead."
    exit 1
fi

# Open a new Terminal.app window running the given command.
open_terminal() {
    local title="$1"; local cmd="$2"
    osascript >/dev/null <<APPLESCRIPT
tell application "Terminal"
    activate
    do script "printf '\\\\033]0;${title}\\\\007'; clear; ${cmd}"
end tell
APPLESCRIPT
}

if [[ "$USE_NGROK" == "1" ]]; then
    log "opening ngrok in a new Terminal window…"
    open_terminal "ngrok · Mission Mode" "cd '$PROJECT_ROOT' && ngrok http 8000"
    log "  waiting 3s for tunnel to come up…"
    sleep 3
fi

log "opening server in a new Terminal window…"
open_terminal "server · Mission Mode" "cd '$PROJECT_ROOT' && source .venv/bin/activate && python -m orchestrator.server"

log "  waiting 5s for FastAPI to bind :8000…"
sleep 5

if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    ok "server is live"
    if [[ "$USE_NGROK" == "1" ]]; then
        public_url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | python3 -c 'import json,sys; d=json.load(sys.stdin); ts=[t["public_url"] for t in d.get("tunnels",[]) if t.get("proto")=="https"]; print(ts[0] if ts else "")' 2>/dev/null || true)
        if [[ -n "$public_url" ]]; then
            ok "ngrok public URL: $public_url"
            ok "bunq webhooks should now be reachable"
        else
            warn "ngrok tunnel not yet visible at 127.0.0.1:4040 — give it a few seconds, refresh dashboard"
        fi
    fi
else
    warn "server health check failed — check the server Terminal window for errors"
fi

echo
ok "dashboard: http://localhost:8000/"
echo
log "to stop: ./stop.sh   (or close the two Terminal windows)"
echo

# Auto-open the dashboard in the default browser.
open "http://localhost:8000/" || true
