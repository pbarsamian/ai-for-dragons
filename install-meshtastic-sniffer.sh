#!/usr/bin/env bash
# install-meshtastic-sniffer.sh — build and install alphafox02/meshtastic-sniffer
#
# Idempotent: skips steps already done.
# Run once on the Pi after cloning ai-for-dragons.
#
# Usage:
#   bash install-meshtastic-sniffer.sh            # install if not present
#   bash install-meshtastic-sniffer.sh --update   # force pull + rebuild

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }

FORCE=false
for arg in "$@"; do
    case "$arg" in --update|-u) FORCE=true ;; esac
done

REPO_URL="https://github.com/alphafox02/meshtastic-sniffer"
SRC_DIR="$HOME/meshtastic-sniffer"
BINARY="$SRC_DIR/build/meshtastic-sniffer"
INSTALL_DEST="/usr/local/bin/meshtastic-sniffer"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  meshtastic-sniffer build + install"
echo "══════════════════════════════════════════════════════"
echo ""

# ── Already installed? ────────────────────────────────────────────────────
if command -v meshtastic-sniffer &>/dev/null && [ "$FORCE" = false ]; then
    success "Already installed: $(command -v meshtastic-sniffer)"
    echo ""
    echo "  Run with --update to force a pull + rebuild."
    echo ""
    exit 0
fi

# ── Build dependencies ────────────────────────────────────────────────────
info "Installing build dependencies (cmake g++ libhackrf-dev pkg-config)..."
sudo apt-get update -qq
sudo apt-get install -y cmake g++ libhackrf-dev pkg-config git
success "Build dependencies ready"

# ── Clone or update source ────────────────────────────────────────────────
if [ -d "$SRC_DIR/.git" ]; then
    info "Updating existing clone at $SRC_DIR ..."
    git -C "$SRC_DIR" pull
    success "Source updated: $(git -C "$SRC_DIR" log -1 --format='%h %s')"
else
    info "Cloning $REPO_URL ..."
    git clone "$REPO_URL" "$SRC_DIR"
    success "Cloned to $SRC_DIR"
fi

# ── Build ─────────────────────────────────────────────────────────────────
info "Building — takes ~1 min on Pi 5..."
mkdir -p "$SRC_DIR/build"
cd "$SRC_DIR/build"
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"

if [ ! -f "$BINARY" ]; then
    error "Build failed — binary not found at $BINARY"
    exit 1
fi
success "Build complete: $BINARY"

# ── Install ───────────────────────────────────────────────────────────────
info "Installing to $INSTALL_DEST ..."
sudo cp "$BINARY" "$INSTALL_DEST"
sudo chmod +x "$INSTALL_DEST"
success "Installed: $INSTALL_DEST"

echo ""
success "meshtastic-sniffer is ready."
echo ""
echo "  Test:           meshtastic-sniffer --help"
echo "  In dragon-agent: 'Listen to Meshtastic for 5 minutes'"
echo ""
