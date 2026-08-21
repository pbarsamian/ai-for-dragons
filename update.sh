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
# Also handles:
#   - Installing the openai package into the venv (for llama-server)
#   - Installing/updating the llama-server systemd service
#   - Updating the dragon-agent wrapper script
#
# Run from anywhere — the script resolves its own location.

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Pull ────────────────────────────────────────────────────────────────

cd "$SCRIPT_DIR"
info "Pulling latest from GitHub..."
git pull
success "Up to date: $(git log -1 --format='%h %s')"

# ── 2. Locate venv ────────────────────────────────────────────────────────
# Try known locations in order — the name changed across versions.

VENV=""
for candidate in \
    "$HOME/.local/share/ai-for-dragons" \
    "$HOME/.local/share/sdr-mcp" \
    "$HOME/.local/share/dragon-agent"; do
    if [ -x "$candidate/bin/python" ]; then
        VENV="$candidate"
        break
    fi
done

# Fallback: ask the wrapper script where it points
if [ -z "$VENV" ]; then
    for wrapper in dragon-agent sdr-agent ai-for-dragons sdr-mcp; do
        wrapper_path=$(which "$wrapper" 2>/dev/null || true)
        if [ -n "$wrapper_path" ]; then
            # Extract the python path from the exec line
            py=$(grep -oP '(?<=exec ")[^"]+python[^"]*' "$wrapper_path" 2>/dev/null \
                 || grep -oP '/\S+/python[0-9.]*' "$wrapper_path" 2>/dev/null | head -1)
            if [ -n "$py" ] && [ -x "$py" ]; then
                VENV=$(dirname "$(dirname "$py")")
                break
            fi
        fi
    done
fi

VENV_PY="${VENV}/bin/python"

if [ -z "$VENV" ] || [ ! -x "$VENV_PY" ]; then
    error "Could not find the venv. Checked:"
    error "  ~/.local/share/ai-for-dragons"
    error "  ~/.local/share/sdr-mcp"
    error "  wrapper scripts on PATH"
    error ""
    error "Run: ls ~/.local/share/ to see what's there, then tell dragon-agent."
    exit 1
fi

info "Found venv at: $VENV"

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

# ── 5. Install openai into venv ────────────────────────────────────────────

if ! "$VENV_PY" -c "import openai" 2>/dev/null; then
    info "Installing openai package into venv..."
    "$VENV/bin/pip" install openai
    success "openai installed"
else
    success "openai already installed"
fi

# ── 6. Install/update llama-server systemd service ─────────────────────────

LLAMA_BIN="$SCRIPT_DIR/llama.cpp/build/bin/llama-server"
LLAMA_MODEL=$(find "$SCRIPT_DIR/llama.cpp/models" -name "*.gguf" ! -name "ggml-vocab*" 2>/dev/null | sort | head -1)
LLAMA_SERVICE="/etc/systemd/system/llama-server.service"

if [ -x "$LLAMA_BIN" ] && [ -n "$LLAMA_MODEL" ]; then
    info "Installing/updating llama-server systemd service..."
    sudo tee "$LLAMA_SERVICE" > /dev/null <<EOF
[Unit]
Description=llama-server LLM inference
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR/llama.cpp
ExecStart=$LLAMA_BIN -m $LLAMA_MODEL --host 0.0.0.0 --port 8080 -t 4 -c 16384
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable llama-server
    if systemctl is-active --quiet llama-server; then
        sudo systemctl restart llama-server
        success "llama-server service restarted"
    else
        sudo systemctl start llama-server
        success "llama-server service started"
    fi
else
    warn "llama-server binary or model not found — skipping service install"
    if [ ! -x "$LLAMA_BIN" ]; then
        warn "  Binary missing: $LLAMA_BIN"
        warn "  Build llama.cpp first — see README"
    fi
    if [ -z "$LLAMA_MODEL" ]; then
        warn "  No .gguf model found in $SCRIPT_DIR/llama.cpp/models/"
        warn "  Download a model first — see README"
    fi
fi

# ── 7. Update dragon-agent wrapper ─────────────────────────────────────────

WRAPPER_PATH=$(which dragon-agent 2>/dev/null || true)
if [ -n "$WRAPPER_PATH" ]; then
    info "Updating dragon-agent wrapper..."
    sudo tee "$WRAPPER_PATH" > /dev/null <<EOF
#!/usr/bin/env bash
if ! pgrep -x llama-server &>/dev/null; then
    echo "[sdr-agent] Starting llama-server..."
    sudo systemctl start llama-server 2>/dev/null
    sleep 4
fi
exec "$VENV_PY" "$SCRIPT_DIR/ollama_agent.py" "\$@"
EOF
    sudo chmod +x "$WRAPPER_PATH"
    success "dragon-agent wrapper updated"
else
    warn "dragon-agent not found on PATH — wrapper not updated"
fi

echo ""
echo "  dragon-agent will use the new code on next start."
echo "  To start now: dragon-agent"
echo ""

# ── Remind about optional system dependencies ──────────────────────────────
if ! command -v meshtastic-sniffer &>/dev/null; then
    echo -e "\033[1;33m⚠\033[0m  meshtastic-sniffer not found — Meshtastic listening will report tool_not_found."
    echo "   Install it once with:  bash install-meshtastic-sniffer.sh"
    echo ""
fi
