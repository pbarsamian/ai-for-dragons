#!/usr/bin/env bash
# update.sh — pull latest ai-for-dragons and deploy to the local venv
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[update] Pulling latest from GitHub..."
git pull

# Find site-packages by asking Python where sdr_mcp is already installed
SITE=$(python3 -c "import sdr_mcp, os; print(os.path.dirname(os.path.dirname(sdr_mcp.__file__)))" 2>/dev/null)

if [ -z "$SITE" ] || [ ! -d "$SITE" ]; then
    echo "[update] ERROR: could not locate installed sdr_mcp. Is the venv active or on PATH?"
    echo "[update] Try: python3 -c \"import sdr_mcp; print(sdr_mcp.__file__)\""
    exit 1
fi

echo "[update] Copying to $SITE..."
cp -r sdr_mcp "$SITE/"
cp ollama_agent.py "$SITE/"

echo "[update] Done. Restart dragon-agent to pick up the new code."
