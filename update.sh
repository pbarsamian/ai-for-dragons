#!/usr/bin/env bash
# update.sh — pull latest ai-for-dragons and make changes live immediately
#
# First run: switches the venv from a static copy to editable mode.
#            A .pth file is added so the venv points directly into this
#            git directory — no reinstall ever needed again.
#
# Every run after that: git pull is all that's required. Changes are
#                       live the moment the pull completes.
#
# Run from anywhere — the script resolves its own location.

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/ai-for-dragons"
VENV_PY="$VENV/bin/python"

# ── 1. Pull ────────────────────────────────────────────────────────────────

cd "$SCRIPT_DIR"
info "Pulling latest from GitHub..."
git pull
success "Up to date: $(git log -1 --format='%h %s')"

# ── 2. Locate venv site-packages ──────────────────────────────────────────

if [ ! -x "$VENV_PY" ]; then
    error "Venv not found at $VENV"
    error "Run the installer first: bash sdr-mcp-install-pi5.sh"
    exit 1
fi

PY_TAG=$("$VENV_PY" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
SITE="$VENV/lib/$PY_TAG/site-packages"

if [ ! -d "$SITE" ]; then
    error "site-packages not found at $SITE"
    exit 1
fi

# ── 3. Switch to editable mode (one-time) ─────────────────────────────────

PTH_FILE="$SITE/ai-for-dragons.pth"

if [ ! -f "$PTH_FILE" ]; then
    info "Switching to editable mode (one-time setup)..."

    # Write .pth file — Python adds this directory to sys.path at startup,
    # so imports go directly to the git repo. No reinstall ever needed.
    echo "$SCRIPT_DIR" > "$PTH_FILE"

    # Remove the old static copy so it doesn't shadow the live files
    if [ -d "$SITE/sdr_mcp" ]; then
        rm -rf "$SITE/sdr_mcp"
        info "Removed static sdr_mcp copy from venv"
    fi
    if [ -f "$SITE/ollama_agent.py" ]; then
        rm -f "$SITE/ollama_agent.py"
        info "Removed static ollama_agent.py from venv"
    fi

    success "Editable mode active → $SCRIPT_DIR"
    success "Future git pulls are live immediately — no further steps needed."
else
    success "Editable mode already active → $(cat "$PTH_FILE")"
fi

# ── 4. Verify the live code is importable ─────────────────────────────────

VERSION=$("$VENV_PY" -c "import sdr_mcp; print(sdr_mcp.__version__)" 2>/dev/null || echo "unknown")
SOURCE=$("$VENV_PY"  -c "import sdr_mcp, os; print(os.path.dirname(sdr_mcp.__file__))" 2>/dev/null || echo "unknown")
success "sdr_mcp $VERSION loaded from: $SOURCE"

echo ""
echo "  dragon-agent will use the new code on next start."
echo "  To start now: dragon-agent"
echo ""
