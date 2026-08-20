#!/usr/bin/env bash
# install.sh — First-time install of ai-for-dragons on DragonOS Pi64
#
# Idempotent: safe to re-run. Skips steps already done.
#
# What this does:
#   1. Installs system dependencies
#   2. Creates Python venv at ~/.local/share/ai-for-dragons
#   3. Installs openai + httpx into the venv
#   4. Links the repo into the venv (editable mode — git pull = live update)
#   5. Builds llama.cpp from source with Pi 5 / Cortex-A76 optimizations (~10 min)
#   6. Downloads the default model (Qwen2.5-1.5B Q4_K_M, ~1 GB)
#   7. Installs llama-server as a systemd service (auto-start on boot)
#   8. Installs the dragon-agent command to /usr/local/bin
#
# Usage:
#   git clone https://github.com/pbarsamian/ai-for-dragons
#   cd ai-for-dragons
#   bash install.sh

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}── $* ──${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/ai-for-dragons"
VENV_PY="$VENV/bin/python"
LLAMA_DIR="$SCRIPT_DIR/llama.cpp"
LLAMA_BIN="$LLAMA_DIR/build/bin/llama-server"
LLAMA_MODELS_DIR="$LLAMA_DIR/models"
LLAMA_MODEL_FILE="$LLAMA_MODELS_DIR/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
LLAMA_MODEL_URL="https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
WRAPPER_DEST="/usr/local/bin/dragon-agent"
LLAMA_SERVICE="/etc/systemd/system/llama-server.service"

echo ""
echo -e "${BOLD}${CYAN}  ai-for-dragons installer${RESET}"
echo -e "  Source: $SCRIPT_DIR"
echo ""

# ── 1. System packages ─────────────────────────────────────────────────────
header "System packages"
info "Installing dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    cmake build-essential git \
    libopenblas-dev \
    wget curl lsof usbutils netcat-openbsd
success "System packages ready"

# ── 2. Python venv ─────────────────────────────────────────────────────────
header "Python venv"
if [ ! -x "$VENV_PY" ]; then
    info "Creating venv at $VENV..."
    python3 -m venv "$VENV"
    success "Venv created"
else
    success "Venv already exists: $VENV"
fi

# ── 3. Python packages ─────────────────────────────────────────────────────
header "Python packages"
if ! "$VENV_PY" -c "import openai" 2>/dev/null; then
    info "Installing openai, httpx..."
    "$VENV/bin/pip" install --quiet openai httpx
    success "Packages installed"
else
    success "openai already installed"
fi

# ── 4. Editable install (live from git) ────────────────────────────────────
header "Package install"
PY_TAG=$("$VENV_PY" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
SITE="$VENV/lib/$PY_TAG/site-packages"
PTH_FILE="$SITE/ai-for-dragons.pth"

if [ ! -f "$PTH_FILE" ]; then
    info "Linking repo into venv (editable mode)..."
    echo "$SCRIPT_DIR" > "$PTH_FILE"
    # Remove stale static copies if present from a previous install
    rm -rf "$SITE/sdr_mcp" "$SITE/ollama_agent.py" 2>/dev/null || true
    success "Editable install active — git pull is all you need to update"
else
    success "Already in editable mode → $(cat "$PTH_FILE")"
fi

VERSION=$("$VENV_PY" -c "import sdr_mcp; print(sdr_mcp.__version__)" 2>/dev/null || echo "unknown")
success "sdr_mcp $VERSION importable"

# ── 5. Build llama.cpp ─────────────────────────────────────────────────────
header "llama.cpp (LLM inference server)"

if [ -x "$LLAMA_BIN" ]; then
    success "llama-server already built: $LLAMA_BIN"
else
    if [ ! -d "$LLAMA_DIR/.git" ]; then
        info "Cloning llama.cpp..."
        git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    else
        info "Updating llama.cpp..."
        git -C "$LLAMA_DIR" pull
    fi

    info "Configuring with Pi 5 / Cortex-A76 optimizations..."
    cmake -B "$LLAMA_DIR/build" "$LLAMA_DIR" \
        -DGGML_BLAS=ON \
        -DGGML_BLAS_VENDOR=OpenBLAS \
        -DGGML_ARM_FMA=ON \
        -DGGML_ARM_DOTPROD=ON \
        -DGGML_NATIVE=ON \
        -DGGML_LTO=ON \
        -DGGML_ARM_SVE=OFF

    info "Building — takes ~10 minutes on Pi 5, please wait..."
    cmake --build "$LLAMA_DIR/build" --config Release -j4

    if [ ! -x "$LLAMA_BIN" ]; then
        error "Build failed — $LLAMA_BIN not found after cmake"
        exit 1
    fi
    success "llama-server built: $LLAMA_BIN"
fi

# ── 6. Download model ──────────────────────────────────────────────────────
header "LLM model"
mkdir -p "$LLAMA_MODELS_DIR"

if [ -f "$LLAMA_MODEL_FILE" ]; then
    SIZE=$(du -h "$LLAMA_MODEL_FILE" | cut -f1)
    success "Model already present ($SIZE)"
else
    info "Downloading Qwen2.5-1.5B-Instruct-Q4_K_M (~1 GB)..."
    wget -q --show-progress -O "$LLAMA_MODEL_FILE" "$LLAMA_MODEL_URL"
    success "Model downloaded: $LLAMA_MODEL_FILE"
fi

# ── 7. Systemd service ─────────────────────────────────────────────────────
header "llama-server systemd service"
info "Installing service..."
sudo tee "$LLAMA_SERVICE" > /dev/null <<EOF
[Unit]
Description=llama-server LLM inference
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$LLAMA_DIR
ExecStart=$LLAMA_BIN -m $LLAMA_MODEL_FILE --host 0.0.0.0 --port 8080 -t 4 -c 8192
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
    success "llama-server started and enabled at boot"
fi

# ── 8. dragon-agent command ────────────────────────────────────────────────
header "dragon-agent command"
sudo tee "$WRAPPER_DEST" > /dev/null <<EOF
#!/usr/bin/env bash
if ! pgrep -x llama-server &>/dev/null; then
    echo "[sdr-agent] Starting llama-server..."
    sudo systemctl start llama-server 2>/dev/null
    sleep 4
fi
exec "$VENV_PY" "$SCRIPT_DIR/ollama_agent.py" "\$@"
EOF
sudo chmod +x "$WRAPPER_DEST"
success "dragon-agent installed to $WRAPPER_DEST"

# ── Optional: meshtastic-sniffer reminder ──────────────────────────────────
echo ""
if ! command -v meshtastic-sniffer &>/dev/null; then
    echo -e "${YELLOW}⚠${RESET}  meshtastic-sniffer not installed — Meshtastic listening unavailable."
    echo "   Install it later with:  bash install-meshtastic-sniffer.sh"
    echo ""
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}${GREEN}  Install complete!${RESET}"
echo ""
echo "  Start the AI agent:"
echo "    dragon-agent"
echo ""
echo "  To update in the future:"
echo "    cd $SCRIPT_DIR && bash update.sh"
echo ""
