# ai-for-dragons — Claude Code context

AI-powered SDR tool bridge for DragonOS Pi64 + HackRF One.
Exposes 71 DragonOS tools to Claude Code and a local LLM via MCP (stdio transport).

## Architecture

```
Claude Code / dragon-agent (llama-server / llama.cpp)
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

**LLM stack:** llama-server (llama.cpp) runs locally on the Pi on port 8080. It exposes an OpenAI-compatible `/v1/chat/completions` API. The agent (`ollama_agent.py`) uses the `openai` Python package to call it. Default model: Qwen2.5-1.5B-Instruct Q4_K_M.

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
| `ollama_agent.py` | Offline AI agent using llama-server (OpenAI-compatible API) | N/A |

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

**Pi 5 (DragonOS Pi64) — first time:**
```bash
git clone https://github.com/pbarsamian/ai-for-dragons
cd ai-for-dragons
bash install.sh             # ~15 min: builds llama.cpp, downloads model, sets up service
dragon-agent                # starts the agent
```

**Pi 5 — after first install (updates):**
```bash
cd ai-for-dragons && bash update.sh
```

Key paths on Pi:
- Source: wherever you cloned (e.g. `~/ai-for-dragons/`)
- Venv: `~/.local/share/ai-for-dragons/`
- llama-server binary: `<source>/llama.cpp/build/bin/llama-server`
- Model: `<source>/llama.cpp/models/*.gguf`
- Command wrapper: `/usr/local/bin/dragon-agent`
- Systemd service: `/etc/systemd/system/llama-server.service`

The venv uses an editable `.pth` install — `git pull` is all that's needed for code changes.

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

**Why direct .pth editable install instead of pip install -e?**
DragonOS Pi64 ships old setuptools that don't support `setuptools.backends.legacy`.
Writing a `.pth` file directly into site-packages is equivalent and works on any Python.

**Why Popen for hackrf_sweep instead of subprocess.run?**
The `-n` flag (number of passes) is not available in all DragonOS hackrf versions.
We use `Popen` with `communicate(timeout=8)` then `kill()` for reliable termination.

**Why configparser strict=False for GQRX?**
GQRX sometimes writes duplicate keys in its config file, which Python's configparser
rejects in strict mode (default). `strict=False` accepts duplicates, matching GQRX's
own parser behavior.

**Why llama-server instead of Ollama?**
llama.cpp built from source with Cortex-A76 flags (ARM_DOTPROD, ARM_FMA, NATIVE, LTO)
runs significantly faster on Pi 5 than the Ollama binary. llama-server exposes an
OpenAI-compatible `/v1/chat/completions` API, so the agent uses the standard `openai`
Python package — no Ollama-specific library needed.

**Why hardcode "local" as the model name in API calls?**
llama-server ignores the `model` field — it always serves whichever model it was
started with. Hardcoding `"local"` makes this explicit and avoids confusion.
