#!/usr/bin/env bash
# dragon_mcp/install.sh
# Installs dragon_mcp dependencies and registers the dragon-mcp.service systemd unit.
# Run as the user who will own the service (not root).
# Usage: bash dragon_mcp/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== dragon_mcp installer ==="
echo "Repo: $REPO_DIR"
echo "User: $(whoami)"

# ── Python dependencies ────────────────────────────────────────────────────

echo ""
echo "--- Installing Python dependencies ---"
pip3 install --user -r "$SCRIPT_DIR/requirements.txt"

# ── System packages (best-effort, skip if already present) ────────────────

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
    echo "All system packages already installed."
fi

# ── systemd user service ───────────────────────────────────────────────────

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/dragon-mcp.service"
PYTHON_BIN="$(which python3)"

mkdir -p "$UNIT_DIR"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Dragon MCP FastMCP SSE server (HackRF/RTL-SDR/GQRX tools over LAN)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$PYTHON_BIN -m dragon_mcp.server
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
