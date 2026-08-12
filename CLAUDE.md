# ai-for-dragons — Claude Code context

AI-powered SDR tool bridge for DragonOS Pi64 + HackRF One.
Exposes 71 DragonOS tools to Claude Code and local Ollama via MCP (stdio transport).

## Architecture

```
Claude Code / Ollama
      │
  sdr_mcp/server.py       MCP stdio server — JSON-RPC 2024-11-05
      │
  sdr_mcp/tools.py        Central registry — all 71 tools defined here
      │
  ┌───┴──────────────────────────────────────────┐
  │ hackrf.py          HackRF CLI wrappers        │
  │ gqrx.py            GQRX TCP remote control   │
  │ app_manager.py     GUI app lifecycle          │
  │ dragonos.py        DragonOS CLI tools         │
  │ protocol_interpreter.py  Decode/explain data  │
  └──────────────────────────────────────────────┘
      │
  DragonOS Pi64 (Raspberry Pi 5)
  GQRX, dump1090, GNU Radio, meshtastic-sniffer, etc.
```

## How to add a tool

1. Write the function in the right module (see Module guide below)
2. Add a wrapper + registry entry in `sdr_mcp/tools.py`
3. Run `python test_tools.py` — must pass without hardware
4. Add a test in `tests/` if it's a pure-Python function

**Registry entry pattern:**
```python
"tool_name": {
    "description": "What it does — the LLM reads this to decide when to call it. Be specific.",
    "schema": {
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "What this param does"},
        },
        "required": ["param"],   # or [] if all optional
    },
    "fn": _tool_name_wrapper,
},
```

**Wrapper pattern (above the registry):**
```python
def _tool_name(args: dict) -> str:
    return my_function(args.get("param", "default"))
```

## Module guide

| Module | Contains | Hardware |
|---|---|---|
| `hackrf.py` | hackrf_info/sweep/capture/analyze/replay | HackRF exclusive |
| `gqrx.py` | GqrxClient TCP, gqrx_stop/start | GQRX remote :7356 |
| `app_manager.py` | All GUI app start/stop + GROUP_A/B | Varies |
| `dragonos.py` | meshtastic_sniff, adsb_scan, gsm_scan, flowgraph_run | HackRF exclusive |
| `protocol_interpreter.py` | interpret_adsb/ais/acars/pocsag/meshtastic, explain_hex, identify_frequency | None |
| `tools.py` | Registry + wrappers only — no logic here | N/A |
| `server.py` | MCP stdio loop — rarely needs changes | N/A |
| `ollama_agent.py` | Offline AI agent — standalone script | N/A |

## Hardware exclusivity (critical)

The HackRF is a single-receiver USB device — only one process can hold it.

**GROUP_A** (in `app_manager.py`) — hold HackRF exclusively:
gqrx, sdrangel, sdrpp, cubicsdr, qspectrumanalyzer, openwebrx, dump1090, gnuradio

**GROUP_B** — no hardware conflict, run alongside anything:
inspectrum, urh (file mode), satdump (gui mode), sigdigger (file mode),
wireshark, kismet, gpredict, fldigi, wsjtx, qsstv, multimon-ng, dsdfme, jaero

Any tool in GROUP_A must:
1. Call `_check_hackrf_free()` from `hackrf.py` first
2. Call `_stop_hardware_holders(exclude="self")` from `app_manager.py`
3. Be added to the `GROUP_A` dict in `app_manager.py`

## Tool requirements

Every tool must:
- Return `str` always — never raise unhandled exceptions
- Return helpful error messages when hardware is missing/busy
- Use subprocess timeouts (never hang indefinitely)
- Use `shutil.which()` or `command -v` — never hardcode paths
- Be idempotent where possible

## Install / deploy

**Dev machine:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python test_tools.py        # smoke test, no hardware
pytest tests/               # full suite
```

**Pi 5 (DragonOS Pi64):**
- Source lives in `~/sdr-mcp-pi5-bundle/sdr-mcp/`
- Venv at `~/.local/share/ai-for-dragons/`
- Commands: `dragon-agent`, `ai-for-dragons` (symlinked to /usr/local/bin)
- Update: `git pull` then copy source files into venv site-packages

**After any change to sdr_mcp/ on the Pi:**
```bash
PY_TAG=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
SITE="$HOME/.local/share/ai-for-dragons/lib/$PY_TAG/site-packages"
cp -r sdr_mcp "$SITE/" && cp ollama_agent.py "$SITE/"
```

## Versioning and release

- Version in `pyproject.toml` and `sdr_mcp/__init__.py` (keep in sync)
- CHANGELOG.md follows Keep a Changelog format
- Tags trigger PyPI publish via GitHub Actions (`ci.yml`)

Release flow:
```bash
# bump version in both files + update CHANGELOG
git add -A && git commit -m "chore: bump to X.Y.Z"
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push && git push origin vX.Y.Z
# then create GitHub Release → CI publishes to PyPI
```

## Current tool count: 71

Categories:
- HackRF direct: 5
- GQRX control: 8
- App management: 32 (GUI apps + aviation/maritime)
- Protocol interpretation: 7
- DragonOS tools: 5 (meshtastic, ADS-B scan, GSM, flowgraph)
- Exercises: 2 (list, get)
- Signal identify: 1 (frequency heuristic)

## Key design decisions

**Why direct file copy instead of pip install?**
DragonOS Pi64 ships old setuptools that don't support `setuptools.backends.legacy`.
The installer copies source directly into the venv site-packages.

**Why Popen for hackrf_sweep instead of subprocess.run?**
The `-n` flag (number of passes) is not available in all DragonOS hackrf versions.
We use `Popen` with `communicate(timeout=8)` then `kill()` for reliable termination.

**Why configparser strict=False for GQRX?**
GQRX sometimes writes duplicate keys in its config file, which Python's configparser
rejects in strict mode (default). `strict=False` accepts duplicates, matching GQRX's
own parser behavior.

**Why fuzzy model name matching in check_ollama()?**
Ollama stores models with full quantization suffixes (e.g. `qwen3:1.7b-instruct-q4_K_M`)
but users specify short names (`qwen3:1.7b`). Prefix matching resolves the model name
before passing it to client.chat().
