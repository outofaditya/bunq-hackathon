#!/usr/bin/env bash
# Mission Mode launcher — IDE-friendly by default.
#
# Default behaviour: run ngrok + FastAPI server in the CURRENT terminal,
# multiplexed with prefixed output. Ideal for VS Code, Cursor, JetBrains,
# tmux — any IDE-integrated terminal. Ctrl-C kills both.
#
# If you want a separate macOS Terminal.app window per service, pass
# --macos-terminal.
#
# Usage:
#   ./start.sh                     run inline in this terminal (recommended; default)
#   ./start.sh --no-ngrok          server only (polling-only mode)
#   ./start.sh --macos-terminal    spawn two Terminal.app windows
#   ./start.sh --vscode            print the VS Code task command and exit (use Cmd+Shift+P → Run Task)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log()   { printf "${CYAN}→${RESET} %s\n" "$1"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$1"; }
err()   { printf "${RED}✗${RESET} %s\n" "$1"; }

USE_NGROK=1
MODE="inline"
for arg in "$@"; do
    case "$arg" in
        --no-ngrok)        USE_NGROK=0 ;;
        --inline)          MODE="inline" ;;            # explicit (already default)
        --macos-terminal)  MODE="macos-terminal" ;;
        --vscode)          MODE="vscode-hint" ;;
        --help|-h)
            sed -n '2,15p' "$0"
            exit 0
            ;;
    esac
done

# --- vscode hint mode --------------------------------------------------

if [[ "$MODE" == "vscode-hint" ]]; then
    cat <<EOM
${CYAN}→${RESET} VS Code / Cursor users — use the integrated task runner instead of this script:

  1) Cmd+Shift+P → "Tasks: Run Task"
  2) Pick "${GREEN}Mission Mode: launch (ngrok + server)${RESET}"
  3) Two dedicated terminal panels open inside the editor — one for ngrok,
     one for the server. Both stay attached to the workspace.

Other tasks defined in .vscode/tasks.json:
  - Mission Mode: ngrok               (just the tunnel)
  - Mission Mode: server              (just the FastAPI app)
  - Mission Mode: stop everything     (./stop.sh)
  - Mission Mode: smoke tests (fast)  (pytest, no LLM cost)

Or just run ${GREEN}./start.sh${RESET} in any IDE terminal — the default mode (inline)
shows both services prefixed in one panel.
EOM
    exit 0
fi

# --- preflight ---------------------------------------------------------

# Bootstrap Python venv if missing (fresh clone path).
if [[ ! -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    warn ".venv not found — bootstrapping (one-time, ~30s)…"
    PYTHON_BIN="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)"
    if [[ -z "$PYTHON_BIN" ]]; then
        err "no python3 found in PATH. Install Python 3.10+ first."
        exit 1
    fi
    "$PYTHON_BIN" -m venv .venv
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    ok "venv ready ($("$PYTHON_BIN" --version))"
fi

# .env: copy from example on first run, then prompt the user to fill it in.
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
        cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
        warn ".env was missing — copied .env.example to .env."
        warn "Fill in BUNQ_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID then re-run."
        exit 1
    fi
    err ".env not found and no .env.example to copy from. Manual setup required."
    exit 1
fi

# Build the dashboard if dist/ is missing or stale (every fresh clone hits this).
if [[ ! -f "$PROJECT_ROOT/dashboard-react/dist/index.html" ]]; then
    if ! command -v npm >/dev/null 2>&1; then
        err "npm not in PATH — install Node.js 22+ to build the React dashboard."
        err "  brew install node@22       # macOS"
        exit 1
    fi
    log "dashboard-react/dist/ missing — building…"
    (
        cd "$PROJECT_ROOT/dashboard-react"
        if [[ ! -d "node_modules" ]]; then
            log "running npm ci…"
            npm ci --no-audit --no-fund
        fi
        npm run build
    ) || { err "dashboard build failed."; exit 1; }
    ok "dashboard built → dashboard-react/dist/"
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

# Detect IDE so we can give the user a relevant hint.
if [[ "${TERM_PROGRAM:-}" == "vscode" ]] && [[ "$MODE" == "inline" ]]; then
    log "detected IDE terminal — see .vscode/tasks.json for split panel mode."
fi

# --- inline mode (DEFAULT; current terminal, prefixed output) ----------

if [[ "$MODE" == "inline" ]]; then
    log "inline mode — both services in this terminal. Ctrl-C kills both."
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
        ngrok http 8000 --log=stdout 2>&1 | sed -e $'s/^/\033[33m[ngrok]\033[0m  /' &
        pids+=($!)
        sleep 2
    fi

    log "starting server…"
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
    python -m orchestrator.server 2>&1 | sed -e $'s/^/\033[36m[server]\033[0m /' &
    pids+=($!)

    sleep 5

    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        ok "server is live at http://localhost:8000/"
        if [[ "$USE_NGROK" == "1" ]]; then
            public_url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
                | python3 -c 'import json,sys; d=json.load(sys.stdin); ts=[t["public_url"] for t in d.get("tunnels",[]) if t.get("proto")=="https"]; print(ts[0] if ts else "")' 2>/dev/null || true)
            [[ -n "$public_url" ]] && ok "ngrok public URL: $public_url"
        fi
    fi

    # Try to open the dashboard, but only if not in a headless CI-like env.
    if [[ -n "${TERM_PROGRAM:-}" ]] || [[ -z "${SSH_TTY:-}" ]]; then
        (sleep 1 && open "http://localhost:8000/" >/dev/null 2>&1 || true) &
    fi

    wait
    exit 0
fi

# --- macOS Terminal.app mode (opt-in via --macos-terminal) -------------

if [[ "$MODE" == "macos-terminal" ]]; then
    if [[ "$(uname -s)" != "Darwin" ]]; then
        err "--macos-terminal requires macOS. Use the default (inline) mode."
        exit 1
    fi

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
        sleep 3
    fi

    log "opening server in a new Terminal window…"
    open_terminal "server · Mission Mode" "cd '$PROJECT_ROOT' && source .venv/bin/activate && python -m orchestrator.server"

    sleep 5
    ok "dashboard: http://localhost:8000/"
    open "http://localhost:8000/" || true
    log "to stop: ./stop.sh   (or close the two Terminal windows)"
    exit 0
fi
