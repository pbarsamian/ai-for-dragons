#!/usr/bin/env bash
# dragon_mcp/install.sh
# Installs dragon_mcp dependencies and registers the dragon-mcp.service systemd unit.
# Run as the user who will own the service (not root).
# Usage: bash dragon_mcp/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Reuse the project venv created by the main install.sh.
# Debian 13 enforces PEP 668 — pip3 --user is blocked outside a venv.
VENV="$HOME/.local/share/ai-for-dragons"
VENV_PY="$VENV/bin/python"
VENV_PIP="$VENV/bin/pip"

echo "=== dragon_mcp installer ==="
echo "Repo:  $REPO_DIR"
echo "Venv:  $VENV"
echo "User:  $(whoami)"

# ── Venv: create if the main installer hasn't run yet ─────────────────────

echo ""
echo "--- Python venv ---"
if [ ! -x "$VENV_PY" ]; then
    echo "Creating venv at $VENV ..."
    python3 -m venv "$VENV"
    echo "Venv created."
else
    echo "Venv already exists — reusing."
fi

# ── Python packages ────────────────────────────────────────────────────────

echo ""
echo "--- Installing Python dependencies into venv ---"
"$VENV_PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "fastmcp and flask installed."

# ── Link repo into venv so dragon_mcp is importable ──────────────────────
# Uses the same .pth editable-install pattern as the main install.sh.

PY_TAG=$("$VENV_PY" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
SITE="$VENV/lib/$PY_TAG/site-packages"
PTH_FILE="$SITE/ai-for-dragons.pth"

if [ ! -f "$PTH_FILE" ] || ! grep -qF "$REPO_DIR" "$PTH_FILE"; then
    echo "$REPO_DIR" >> "$PTH_FILE"
    echo "Repo linked into venv via $PTH_FILE"
else
    echo "Repo already linked in venv."
fi

# ── Smoke test ─────────────────────────────────────────────────────────────

echo ""
echo "--- Import smoke test ---"
"$VENV_PY" - <<'PYEOF'
from dragon_mcp.server import mcp
from dragon_mcp.streams import StreamManager
from dragon_mcp.tools.hackrf import _check_hackrf_free
from dragon_mcp.tools.rtlsdr import rtlsdr_info
from dragon_mcp.tools.gqrx import gqrx_get_status
from dragon_mcp.tools.decoders import dump1090_start
sm = StreamManager()
print("OK — all imports passed, StreamManager ready")
PYEOF

# ── System packages (best-effort) ─────────────────────────────────────────

echo ""
echo "--- Checking system packages ---"
PKGS_NEEDED=()
for pkg in scrot ffmpeg; do
    dpkg -s "$pkg" &>/dev/null || PKGS_NEEDED+=("$pkg")
done
if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
    echo "Installing: ${PKGS_NEEDED[*]}"
    sudo apt-get install -y "${PKGS_NEEDED[@]}"
else
    echo "scrot and ffmpeg already installed."
fi

# ── systemd user service ───────────────────────────────────────────────────

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/dragon-mcp.service"

mkdir -p "$UNIT_DIR"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Dragon MCP FastMCP SSE server (HackRF/RTL-SDR/GQRX tools over LAN)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_PY -m dragon_mcp.server
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

echo ""
echo "--- Registering dragon-mcp.service ---"
systemctl --user daemon-reload
systemctl --user enable dragon-mcp.service

echo ""
echo "=== Installation complete ==="
echo ""
echo "Start now:    systemctl --user start dragon-mcp.service"
echo "Check status: systemctl --user status dragon-mcp.service"
echo "Follow logs:  journalctl --user -fu dragon-mcp.service"
echo ""
echo "MCP endpoint: http://<pi-ip>:8765/sse"
echo "Add to Claude Code ~/.claude/settings.json:"
echo '  "mcpServers": {'
echo '    "dragon": {'
echo '      "url": "http://<pi-ip>:8765/sse"'
echo '    }'
echo '  }'
