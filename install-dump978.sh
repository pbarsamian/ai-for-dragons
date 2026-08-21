#!/usr/bin/env bash
# install-dump978.sh — Build and install dump978-fa (FlightAware UAT decoder)
#
# dump978-fa is not in DragonOS apt repos; it must be built from source.
# This script is idempotent: safe to re-run; skips steps already done.
#
# Usage:
#   bash install-dump978.sh

set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }

BUILD_DIR="$HOME/dump978-fa-build"
DEST="/usr/local/bin/dump978-fa"

echo ""
echo -e "${CYAN}  dump978-fa installer (FlightAware UAT decoder)${RESET}"
echo ""

# ── Already installed? ──────────────────────────────────────────────────────
if [ -x "$DEST" ]; then
    success "dump978-fa already installed at $DEST"
    "$DEST" --version 2>/dev/null || true
    exit 0
fi

# ── System dependencies ─────────────────────────────────────────────────────
info "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    build-essential cmake git \
    librtlsdr-dev \
    libboost-system-dev libboost-program-options-dev libboost-regex-dev \
    pkg-config
success "Build dependencies ready"

# ── Clone / update ──────────────────────────────────────────────────────────
if [ ! -d "$BUILD_DIR/.git" ]; then
    info "Cloning dump978 from FlightAware..."
    git clone https://github.com/flightaware/dump978 "$BUILD_DIR"
else
    info "Updating dump978 source..."
    git -C "$BUILD_DIR" pull
fi

# ── Build ───────────────────────────────────────────────────────────────────
info "Building dump978-fa (~1 min on Pi 5)..."
cmake -B "$BUILD_DIR/build" "$BUILD_DIR"
cmake --build "$BUILD_DIR/build" --config Release -j4

# ── Install ─────────────────────────────────────────────────────────────────
BIN="$BUILD_DIR/build/dump978-fa"
if [ ! -x "$BIN" ]; then
    error "Build failed — $BIN not found"
    exit 1
fi

sudo install -m 755 "$BIN" "$DEST"
success "dump978-fa installed to $DEST"

echo ""
echo "  Test it:"
echo "    rtl_sdr -f 978000000 -s 2083334 -g 48 - | dump978-fa --raw-stdin --json-stdout"
echo ""
echo "  Or use the agent: 'scan for UAT aircraft on 978 MHz'"
echo ""
