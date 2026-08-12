"""
Self-update module for AI for Dragons.

Pulls the latest code from GitHub, reinstalls into the running venv,
and optionally restarts the agent — all triggered by a natural language
request to the agent itself.

Usage via agent:
  "update AI for Dragons"
  "check for updates"
  "pull the latest version"
  "what's changed since my version?"
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone


# ── Paths ─────────────────────────────────────────────────────────────────

def _find_source_dir() -> str | None:
    """Find the AI for Dragons source directory."""
    candidates = [
        # Actual install location on Pi
        os.path.expanduser("~/sdr-mcp-pi5-bundle/sdr-mcp"),
        # Future/renamed install locations
        os.path.expanduser("~/ai-for-dragons-pi5-bundle/ai-for-dragons"),
        os.path.expanduser("~/ai-for-dragons"),
        # Relative to this file if running from source
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "pyproject.toml")) and \
           os.path.isdir(os.path.join(path, "sdr_mcp")):
            return path
    return None


def _venv_site_packages() -> str | None:
    """Find the venv site-packages directory."""
    venv = os.path.expanduser("~/.local/share/ai-for-dragons")
    py_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = os.path.join(venv, "lib", py_tag, "site-packages")
    return site if os.path.isdir(site) else None


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"


# ── Status check ──────────────────────────────────────────────────────────

def update_status() -> str:
    """
    Check whether AI for Dragons is up to date.
    Returns local version, latest GitHub version, and what's changed.
    """
    src = _find_source_dir()
    if not src:
        return json.dumps({
            "status": "error",
            "message": "Source directory not found. Expected ~/sdr-mcp-pi5-bundle/sdr-mcp",
        }, indent=2)

    result = {"source_dir": src}

    # Local git info
    rc, commit, _ = _run(["git", "rev-parse", "--short", "HEAD"], cwd=src)
    result["local_commit"] = commit if rc == 0 else "unknown (not a git repo)"

    rc, branch, _ = _run(["git", "branch", "--show-current"], cwd=src)
    result["branch"] = branch if rc == 0 else "unknown"

    rc, log, _ = _run(["git", "log", "-1", "--format=%ci %s"], cwd=src)
    result["local_last_commit"] = log if rc == 0 else "unknown"

    # Current version
    try:
        from sdr_mcp import __version__
        result["installed_version"] = __version__
    except ImportError:
        result["installed_version"] = "unknown"

    # Fetch remote without merging
    rc, _, err = _run(["git", "fetch", "--quiet", "origin"], cwd=src, timeout=30)
    if rc != 0:
        result["network_status"] = f"Cannot reach GitHub: {err}"
        result["up_to_date"] = "unknown"
        return json.dumps(result, indent=2)

    result["network_status"] = "reachable"

    # Compare local vs remote
    rc, ahead_behind, _ = _run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
        cwd=src
    )
    if rc == 0 and ahead_behind:
        parts = ahead_behind.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
            result["commits_ahead_of_remote"] = ahead
            result["commits_behind_remote"] = behind
            result["up_to_date"] = behind == 0

    # Show what's new on remote
    rc, new_commits, _ = _run(
        ["git", "log", "HEAD..origin/main", "--oneline", "--no-merges"],
        cwd=src
    )
    if rc == 0 and new_commits:
        result["new_commits"] = new_commits.splitlines()
        result["recommendation"] = "Run self_update to get these changes."
    elif rc == 0:
        result["new_commits"] = []
        result["recommendation"] = "Already up to date."

    return json.dumps(result, indent=2)


# ── Self update ───────────────────────────────────────────────────────────

def self_update(branch: str = "main", restart: bool = True) -> str:
    """
    Pull latest code from GitHub and reinstall into the running venv.
    Optionally restart the agent with the new version.
    """
    src = _find_source_dir()
    if not src:
        return json.dumps({
            "status": "error",
            "message": (
                "Source directory not found.\\n"
                "Expected at: ~/sdr-mcp-pi5-bundle/sdr-mcp\\n"
                "If you cloned the repo elsewhere, set GIT_REPO_PATH env var."
            ),
        }, indent=2)

    site = _venv_site_packages()
    result = {
        "source_dir": src,
        "branch": branch,
        "steps": [],
    }

    def step(name: str, ok: bool, detail: str = "") -> None:
        result["steps"].append({
            "step": name,
            "status": "ok" if ok else "failed",
            "detail": detail,
        })

    # 1. Check git remote is configured
    rc, remote, _ = _run(["git", "remote", "get-url", "origin"], cwd=src)
    if rc != 0:
        step("check remote", False, "No git remote configured — cannot pull updates")
        result["status"] = "error"
        result["message"] = (
            "No git remote configured.\\n"
            "Run: git remote add origin https://github.com/YOUR_USERNAME/ai-for-dragons.git"
        )
        return json.dumps(result, indent=2)
    step("check remote", True, remote)

    # 2. Stash any local changes so pull doesn't conflict
    rc, stash_out, _ = _run(["git", "stash"], cwd=src)
    stashed = "No local changes" not in stash_out
    step("stash local changes", True, stash_out[:100] if stash_out else "nothing to stash")

    # 3. Pull latest from GitHub
    rc, pull_out, pull_err = _run(
        ["git", "pull", "origin", branch, "--ff-only"],
        cwd=src, timeout=60
    )
    if rc != 0:
        step("git pull", False, pull_err[:200])
        if stashed:
            _run(["git", "stash", "pop"], cwd=src)
        result["status"] = "error"
        result["message"] = f"git pull failed: {pull_err[:200]}"
        return json.dumps(result, indent=2)
    step("git pull", True, pull_out[:200])

    # Get new version info
    rc, new_commit, _ = _run(["git", "rev-parse", "--short", "HEAD"], cwd=src)
    result["new_commit"] = new_commit if rc == 0 else "unknown"

    # 4. Reinstall into venv site-packages
    if site:
        try:
            # Copy sdr_mcp package
            src_pkg = os.path.join(src, "sdr_mcp")
            dst_pkg = os.path.join(site, "sdr_mcp")
            if os.path.isdir(dst_pkg):
                shutil.rmtree(dst_pkg)
            shutil.copytree(src_pkg, dst_pkg)

            # Copy ollama_agent
            src_agent = os.path.join(src, "ollama_agent.py")
            if os.path.isfile(src_agent):
                shutil.copy2(src_agent, os.path.join(site, "ollama_agent.py"))

            # Copy test_tools
            src_test = os.path.join(src, "test_tools.py")
            if os.path.isfile(src_test):
                shutil.copy2(src_test, os.path.join(site, "test_tools.py"))

            step("install to venv", True, f"Copied to {site}")
        except Exception as e:
            step("install to venv", False, str(e))
            result["status"] = "error"
            result["message"] = f"Failed to copy files to venv: {e}"
            return json.dumps(result, indent=2)
    else:
        step("install to venv", False, "Venv not found — running from source directly")

    # 5. Restore stashed changes if any
    if stashed:
        rc, _, _ = _run(["git", "stash", "pop"], cwd=src)
        step("restore stash", rc == 0, "local changes restored" if rc == 0 else "stash pop failed")

    # 6. Quick smoke test
    rc, test_out, test_err = _run(
        [sys.executable, os.path.join(src, "test_tools.py")],
        cwd=src, timeout=30
    )
    smoke_ok = rc == 0
    step("smoke test", smoke_ok, test_out[-200:] if test_out else test_err[-200:])

    result["status"] = "complete" if smoke_ok else "updated_with_warnings"

    # 7. Restart agent
    if restart and smoke_ok:
        result["message"] = (
            f"AI for Dragons updated to {new_commit}. "
            "Restarting agent with new version..."
        )
        result["steps"].append({"step": "restart", "status": "ok", "detail": "restarting now"})

        # Print result before restarting
        print(json.dumps(result, indent=2))
        sys.stdout.flush()

        # Replace current process with a fresh agent
        agent_path = shutil.which("dragon-agent") or os.path.join(
            os.path.expanduser("~/.local/bin"), "dragon-agent"
        )
        if agent_path and os.path.isfile(agent_path):
            os.execv(agent_path, [agent_path])
        else:
            # Fall back to running via Python
            os.execv(sys.executable, [sys.executable, "-m", "sdr_mcp"])
    elif not smoke_ok:
        result["message"] = (
            f"Updated to {new_commit} but smoke test had warnings. "
            "Check the steps above. Restart manually with: sdr-agent"
        )
    else:
        result["message"] = (
            f"Updated to {new_commit}. "
            "Restart skipped (restart=false). Run: sdr-agent"
        )

    return json.dumps(result, indent=2)
