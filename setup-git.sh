#!/usr/bin/env bash
# =============================================================================
#  sdr-mcp — Git repo + PyPI setup
#
#  Run this once on the Pi (or any machine with the sdr-mcp source) to:
#    1. Initialize a local git repo
#    2. Make the first commit
#    3. Connect to your GitHub repo
#    4. Push everything up
#    5. (Optional) Set up PyPI trusted publishing
#
#  Prerequisites:
#    - GitHub account and a new empty repo created at github.com
#    - Git configured: git config --global user.name / user.email
#    - SSH key added to GitHub (recommended) OR use HTTPS with a token
#
#  Usage:
#    bash setup-git.sh --github YOUR_USERNAME/sdr-mcp
#    bash setup-git.sh --github YOUR_USERNAME/sdr-mcp --https   # use HTTPS
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
header()  { echo -e "\n${BOLD}═══ $* ═══${RESET}"; }

GITHUB_REPO=""
USE_HTTPS=false

for arg in "$@"; do
    case "$arg" in
        --github=*) GITHUB_REPO="${arg#*=}" ;;
        --github)   shift; GITHUB_REPO="$1" ;;
        --https)    USE_HTTPS=true ;;
        -h|--help)
            echo "Usage: $0 --github USERNAME/REPO [--https]"
            echo ""
            echo "  --github USERNAME/REPO   Your GitHub repo (e.g. jsmith/sdr-mcp)"
            echo "  --https                  Use HTTPS instead of SSH"
            exit 0 ;;
    esac
done

if [[ -z "$GITHUB_REPO" ]]; then
    echo ""
    echo -e "${BOLD}Usage: $0 --github USERNAME/REPO${RESET}"
    echo ""
    echo "First create an empty repo on GitHub:"
    echo "  https://github.com/new"
    echo "  Name: sdr-mcp"
    echo "  Description: AI-powered SDR tool bridge for DragonOS + HackRF One"
    echo "  Visibility: Public"
    echo "  Do NOT initialize with README, .gitignore, or license"
    echo ""
    echo "Then run:"
    echo "  bash setup-git.sh --github YOUR_USERNAME/sdr-mcp"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BOLD}${CYAN}  sdr-mcp Git Setup${RESET}"
echo -e "  Repo: github.com/${GITHUB_REPO}"
echo ""

# ── 1. Git identity check ────────────────────────────────────────────────
header "Git identity"

GIT_NAME=$(git config --global user.name 2>/dev/null || echo "")
GIT_EMAIL=$(git config --global user.email 2>/dev/null || echo "")

if [[ -z "$GIT_NAME" || -z "$GIT_EMAIL" ]]; then
    echo ""
    warn "Git identity not configured. Set it with:"
    echo "  git config --global user.name  'Your Name'"
    echo "  git config --global user.email 'you@example.com'"
    echo ""
    read -p "Enter your name: " name
    read -p "Enter your email: " email
    git config --global user.name  "$name"
    git config --global user.email "$email"
fi
success "Git identity: $(git config --global user.name) <$(git config --global user.email)>"

# ── 2. Update README with actual GitHub URL ────────────────────────────
header "Updating README"

USERNAME="${GITHUB_REPO%%/*}"
sed -i "s|YOUR_USERNAME|${USERNAME}|g" README.md CONTRIBUTING.md pyproject.toml 2>/dev/null || true
success "README and pyproject.toml updated with GitHub username: $USERNAME"

# ── 3. Initialize repo ───────────────────────────────────────────────────
header "Git repository"

if [[ -d .git ]]; then
    warn "Git repo already initialized — skipping git init"
else
    git init -b main
    success "Git repo initialized (branch: main)"
fi

# ── 4. Initial commit ────────────────────────────────────────────────────
header "Initial commit"

git add -A

if git diff --cached --quiet; then
    warn "Nothing to commit — working tree already committed"
else
    git commit -m "feat: initial release v0.3.0

71 tools across 6 categories:
- HackRF direct control (sweep, capture, replay, analyze)
- GQRX remote control (tune, mode, squelch, record, stop/start)
- App management: GQRX, SDRAngel, SDR++, CubicSDR, GNU Radio,
  inspectrum, URH, SatDump, SigDigger, OpenWebRX+, Wireshark,
  Kismet, WSJT-X, Gpredict, fldigi, QSSTV, dump1090, rtl_433,
  multimon-ng, DSD-FME, JAERO, rtl-ais, dumpvdl2, dumphfdl
- Protocol interpretation: ADS-B, AIS, ACARS, POCSAG, Meshtastic,
  hex explainer, frequency band identifier
- DragonOS tool wrappers: meshtastic-sniffer, dump1090, gr-gsm
- 9 SDR learning exercises (Beginner → Advanced)

Pi 5 installer: idempotent, handles swap/CPU/venv/PATH setup
Autostart: systemd services, SSH+desktop coexistence
Ollama agent: spinner, timeout, fuzzy model name matching"
    success "Initial commit created"
fi

# ── 5. Connect to GitHub ─────────────────────────────────────────────────
header "GitHub remote"

if [[ "$USE_HTTPS" == "true" ]]; then
    REMOTE_URL="https://github.com/${GITHUB_REPO}.git"
    warn "HTTPS mode — you'll need a Personal Access Token for push"
    warn "Create one at: https://github.com/settings/tokens"
    warn "Scope needed: repo (or Contents: read+write)"
else
    REMOTE_URL="git@github.com:${GITHUB_REPO}.git"
    info "SSH mode — make sure your SSH key is added to GitHub:"
    info "  https://github.com/settings/ssh/new"
    info "  Test with: ssh -T git@github.com"
fi

if git remote get-url origin &>/dev/null 2>&1; then
    CURRENT=$(git remote get-url origin)
    if [[ "$CURRENT" != "$REMOTE_URL" ]]; then
        git remote set-url origin "$REMOTE_URL"
        success "Remote 'origin' updated to: $REMOTE_URL"
    else
        success "Remote 'origin' already set to: $REMOTE_URL"
    fi
else
    git remote add origin "$REMOTE_URL"
    success "Remote 'origin' added: $REMOTE_URL"
fi

# ── 6. Push ──────────────────────────────────────────────────────────────
header "Push to GitHub"

info "Pushing to github.com/${GITHUB_REPO}..."
if git push -u origin main; then
    success "Pushed to github.com/${GITHUB_REPO}"
else
    echo ""
    warn "Push failed. Common fixes:"
    echo ""
    echo "  SSH issues:"
    echo "    ssh-keygen -t ed25519 -C 'you@example.com'"
    echo "    cat ~/.ssh/id_ed25519.pub    ← copy this to github.com/settings/ssh/new"
    echo "    ssh -T git@github.com        ← test connection"
    echo ""
    echo "  HTTPS issues:"
    echo "    git remote set-url origin https://YOUR_TOKEN@github.com/${GITHUB_REPO}.git"
    echo "    (replace YOUR_TOKEN with a GitHub Personal Access Token)"
    echo ""
    echo "  After fixing, push manually: git push -u origin main"
    exit 1
fi

# ── 7. Tag the release ───────────────────────────────────────────────────
header "Tag v0.3.0"

if git tag -l | grep -q "v0.3.0"; then
    skip() { echo -e "${GREEN}✓${RESET} $* (already done)"; }
    skip "Tag v0.3.0 already exists"
else
    git tag -a v0.3.0 -m "Release v0.3.0 — 71 tools, Pi 5 installer, protocol interpreter"
    git push origin v0.3.0
    success "Tagged and pushed v0.3.0"
fi

# ── 8. PyPI setup instructions ───────────────────────────────────────────
header "PyPI setup (optional)"

echo ""
echo -e "${BOLD}  To publish to PyPI so anyone can: pip install sdr-mcp${RESET}"
echo ""
echo "  1. Create account at https://pypi.org"
echo ""
echo "  2. Add Trusted Publisher (no API key needed):"
echo "     https://pypi.org/manage/account/publishing/"
echo "     Fill in:"
echo "       PyPI project name : sdr-mcp"
echo "       GitHub owner      : $USERNAME"
echo "       GitHub repo       : sdr-mcp"
echo "       Workflow filename  : ci.yml"
echo "       Environment       : pypi"
echo ""
echo "  3. Create a GitHub Release to trigger the publish workflow:"
echo "     https://github.com/${GITHUB_REPO}/releases/new"
echo "     Tag: v0.3.0"
echo "     Title: v0.3.0 — 71 tools"
echo "     Body: paste CHANGELOG.md entry"
echo "     Click: Publish release"
echo ""
echo "  The CI workflow in .github/workflows/ci.yml handles the rest."
echo ""

# ── 9. Done ──────────────────────────────────────────────────────────────
header "Done"

echo ""
echo -e "${GREEN}${BOLD}  Repo live at: https://github.com/${GITHUB_REPO}${RESET}"
echo ""
echo "  Daily workflow:"
echo "    git add -A"
echo "    git commit -m 'feat: describe your change'"
echo "    git push"
echo ""
echo "  Releasing a new version:"
echo "    # Edit pyproject.toml version and CHANGELOG.md"
echo "    git add -A && git commit -m 'chore: bump version to 0.4.0'"
echo "    git tag -a v0.4.0 -m 'Release v0.4.0'"
echo "    git push && git push origin v0.4.0"
echo "    # Then create GitHub Release → triggers PyPI publish automatically"
echo ""
