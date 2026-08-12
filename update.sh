#!/usr/bin/env bash
# update.sh — pull latest ai-for-dragons and deploy to the local venv
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[update] Pulling latest from GitHub..."
git pull

PY_TAG=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
SITE="$HOME/.local/share/ai-for-dragons/lib/$PY_TAG/site-packages"

if [ ! -d "$SITE" ]; then
    echo "[update] ERROR: venv site-packages not found at $SITE"
    exit 1
fi

echo "[update] Copying to $SITE..."
cp -r sdr_mcp "$SITE/"
cp ollama_agent.py "$SITE/"

echo "[update] Done. Restart dragon-agent to pick up the new code."
