#!/usr/bin/env bash
# check-home.sh — read-only audit of the dragon home directory
#
# Shows exactly what is installed, where the wrappers point, whether
# editable mode is active, and anything that could cause conflicts.
# Makes no changes.

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
ok()   { echo -e "  ${GREEN}✓${RESET} $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET} $*"; }
hdr()  { echo -e "\n${CYAN}▸ $*${RESET}"; }

# Detect venv across renamed install locations
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
VENV_PY="${VENV}/bin/python"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  ai-for-dragons home directory audit"
echo "══════════════════════════════════════════════════════"

# ── Wrapper scripts ────────────────────────────────────────────────────────
hdr "Wrapper commands (~/.local/bin/):"
for cmd in dragon-agent ai-for-dragons sdr-agent sdr-mcp; do
    path=$(which "$cmd" 2>/dev/null || echo "")
    if [ -n "$path" ]; then
        target=$(grep -E 'exec|python' "$path" 2>/dev/null | head -1 | sed 's/^ *//')
        ok "$cmd → $path"
        echo "       calls: $target"
    else
        warn "$cmd — not found on PATH"
    fi
done

# ── Venv ───────────────────────────────────────────────────────────────────
hdr "Venv ($VENV):"
if [ -x "$VENV_PY" ]; then
    PY_TAG=$("$VENV_PY" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    SITE="$VENV/lib/$PY_TAG/site-packages"
    ok "Python: $("$VENV_PY" --version 2>&1)"

    PTH="$SITE/ai-for-dragons.pth"
    if [ -f "$PTH" ]; then
        ok "Install mode: EDITABLE"
        echo "       points to: $(cat "$PTH")"
        # Check if that path actually exists and has sdr_mcp
        SRC=$(cat "$PTH")
        if [ -d "$SRC/sdr_mcp" ]; then
            VERSION=$("$VENV_PY" -c "import sdr_mcp; print(sdr_mcp.__version__)" 2>/dev/null || echo "unknown")
            ok "sdr_mcp $VERSION — live from git repo"
        else
            warn ".pth points to $SRC but sdr_mcp/ not found there"
        fi
    elif [ -d "$SITE/sdr_mcp" ]; then
        warn "Install mode: STATIC COPY (changes after git pull need manual copy)"
        echo "       at: $SITE/sdr_mcp"
        echo "       Fix: bash update.sh  (switches to editable mode)"
    else
        warn "sdr_mcp not found in venv — reinstall needed"
    fi

    # Check for leftover static copy alongside editable
    if [ -f "$PTH" ] && [ -d "$SITE/sdr_mcp" ]; then
        warn "Old static sdr_mcp/ copy still in site-packages alongside .pth"
        echo "       Fix: rm -rf $SITE/sdr_mcp"
    fi
else
    warn "Venv not found — run the installer"
fi

# ── Stale bundle directories ───────────────────────────────────────────────
hdr "Stale bundle directories (could confuse old wrapper scripts):"
FOUND_ANY=false
for d in \
    ~/sdr-mcp-pi5-bundle \
    ~/sdr-mcp-dist \
    ~/sdr-mcp \
    ~/ai-for-dragons-bundle \
    ~/Downloads/sdr-mcp* \
    ~/Desktop/sdr-mcp*; do
    # expand glob
    for expanded in $d; do
        if [ -d "$expanded" ]; then
            warn "Found: $expanded"
            FOUND_ANY=true
        fi
    done
done
if [ "$FOUND_ANY" = false ]; then
    ok "None found"
fi

# ── System Python conflict check ───────────────────────────────────────────
hdr "sdr_mcp visible from system Python:"
SYS_IMPORT=$(python3 -c "import sdr_mcp; print(sdr_mcp.__file__)" 2>/dev/null || echo "")
if [ -n "$SYS_IMPORT" ]; then
    warn "Importable from system Python: $SYS_IMPORT"
    echo "       This could shadow the venv version if scripts use system python3"
else
    ok "Not importable from system Python (good)"
fi

# ── Git repo ───────────────────────────────────────────────────────────────
hdr "Git repo:"
for candidate in \
    ~/ai-for-dragons \
    ~/sdr-mcp-pi5-bundle/sdr-mcp \
    ~/sdr-mcp; do
    if [ -d "$candidate/.git" ]; then
        ok "Found: $candidate"
        cd "$candidate"
        echo "       branch: $(git branch --show-current 2>/dev/null)"
        echo "       last commit: $(git log -1 --format='%h %s' 2>/dev/null)"
        BEHIND=$(git rev-list HEAD..origin/main --count 2>/dev/null || echo "?")
        [ "$BEHIND" = "0" ] && ok "Up to date with origin/main" || warn "$BEHIND commit(s) behind origin/main — run: git pull"
        cd - > /dev/null
    fi
done

# ── Ollama ────────────────────────────────────────────────────────────────
hdr "Ollama:"
if command -v ollama &>/dev/null; then
    ok "Installed: $(ollama --version 2>/dev/null | head -1)"
    if systemctl is-active ollama &>/dev/null 2>&1; then
        ok "Systemd service: active"
    elif pgrep -x ollama &>/dev/null; then
        ok "Process: running (not via systemd)"
    else
        warn "NOT running — start with: ollama serve &  or  sudo systemctl start ollama"
    fi

    MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' ' ')
    if [ -n "$MODELS" ]; then
        ok "Models available: $MODELS"
    else
        warn "No models pulled — run: ollama pull qwen3:4b"
    fi
else
    warn "Ollama not installed"
fi

# ── HackRF ────────────────────────────────────────────────────────────────
hdr "HackRF:"
if command -v hackrf_info &>/dev/null; then
    if hackrf_info &>/dev/null 2>&1; then
        ok "Connected and responding"
    else
        warn "hackrf_info found but device not responding (unplugged or held by another app?)"
    fi
else
    warn "hackrf_info not found — try: sudo apt install hackrf"
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo ""
