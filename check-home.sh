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
hdr "Wrapper commands (~/.local/bin/ or /usr/local/bin/):"
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
        warn "sdr_mcp not found in venv — run: bash install.sh"
    fi

    # Check for leftover static copy alongside editable
    if [ -f "$PTH" ] && [ -d "$SITE/sdr_mcp" ]; then
        warn "Old static sdr_mcp/ copy still in site-packages alongside .pth"
        echo "       Fix: rm -rf $SITE/sdr_mcp"
    fi

    # Check openai
    if "$VENV_PY" -c "import openai" 2>/dev/null; then
        OPENAI_VER=$("$VENV_PY" -c "import openai; print(openai.__version__)" 2>/dev/null)
        ok "openai $OPENAI_VER installed"
    else
        warn "openai not installed in venv"
        echo "       Fix: bash update.sh"
    fi
else
    warn "Venv not found — run: bash install.sh"
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
REPO_FOUND=false
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
        [ "$BEHIND" = "0" ] && ok "Up to date with origin/main" || warn "$BEHIND commit(s) behind origin/main — run: bash update.sh"
        cd - > /dev/null
        REPO_FOUND=true
    fi
done
if [ "$REPO_FOUND" = false ]; then
    warn "No git repo found — clone with: git clone https://github.com/pbarsamian/ai-for-dragons"
fi

# ── llama-server ──────────────────────────────────────────────────────────
hdr "llama-server (LLM inference):"
if systemctl is-active --quiet llama-server 2>/dev/null; then
    ok "Systemd service: active"
    # Quick API check
    if curl -sf http://localhost:8080/v1/models > /dev/null 2>&1; then
        ok "API responding at http://localhost:8080"
    else
        warn "Service active but API not responding on port 8080 — still starting up?"
    fi
elif pgrep -x llama-server &>/dev/null; then
    ok "Process running (not via systemd)"
    curl -sf http://localhost:8080/v1/models > /dev/null 2>&1 \
        && ok "API responding at http://localhost:8080" \
        || warn "Process running but API not responding on port 8080"
else
    warn "llama-server NOT running"
    if systemctl is-enabled --quiet llama-server 2>/dev/null; then
        echo "       Service is enabled but not running — start with: sudo systemctl start llama-server"
    else
        echo "       Service not installed — run: bash install.sh"
    fi
fi

# Check binary and model
LLAMA_BIN=$(find "$HOME" -name "llama-server" -path "*/build/bin/*" 2>/dev/null | head -1)
if [ -n "$LLAMA_BIN" ]; then
    ok "Binary: $LLAMA_BIN"
else
    warn "llama-server binary not found — run: bash install.sh"
fi

LLAMA_MODEL=$(find "$HOME" -name "*.gguf" 2>/dev/null | head -1)
if [ -n "$LLAMA_MODEL" ]; then
    SIZE=$(du -h "$LLAMA_MODEL" | cut -f1)
    ok "Model ($SIZE): $LLAMA_MODEL"
else
    warn "No .gguf model found — run: bash install.sh"
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

# ── meshtastic-sniffer ───────────────────────────────────────────────────
hdr "meshtastic-sniffer:"
if command -v meshtastic-sniffer &>/dev/null; then
    ok "Installed: $(command -v meshtastic-sniffer)"
else
    SNIFF_BIN=""
    for p in \
        "$HOME/meshtastic-sniffer/build/meshtastic-sniffer" \
        "/usr/local/bin/meshtastic-sniffer"; do
        if [ -x "$p" ]; then SNIFF_BIN="$p"; break; fi
    done

    if [ -n "$SNIFF_BIN" ]; then
        warn "Binary exists at $SNIFF_BIN but not on PATH"
        echo "       Fix: sudo cp $SNIFF_BIN /usr/local/bin/meshtastic-sniffer"
    else
        warn "Not installed — meshtastic_sniff tool will return tool_not_found"
        echo "       Fix: bash install-meshtastic-sniffer.sh"
    fi
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo ""
