# Contributing to sdr-mcp

## Development setup

```bash
git clone https://github.com/pbarsamian/sdr-mcp
cd sdr-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
python test_tools.py          # smoke test — no hardware needed
python -m pytest tests/       # full test suite
```

## Adding a tool

1. Implement the function in the appropriate module:
   - Hardware ops → `sdr_mcp/hackrf.py`
   - GUI app management → `sdr_mcp/app_manager.py`
   - Protocol decode → `sdr_mcp/protocol_interpreter.py`
   - DragonOS CLI tools → `sdr_mcp/dragonos.py`

2. Register it in `sdr_mcp/tools.py`:
```python
"my_tool": {
    "description": "What it does — the LLM reads this to decide when to call it.",
    "schema": {
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "What this param does"},
        },
        "required": ["param"],
    },
    "fn": _my_tool_wrapper,
},
```

3. Add a wrapper function before the registry:
```python
def _my_tool(args: dict) -> str:
    return my_function(args.get("param", ""))
```

4. Tools must:
   - Always return a string (never raise unhandled exceptions)
   - Return actionable error messages when hardware isn't available
   - Not hang — use subprocess timeouts
   - Be idempotent where possible

## Hardware exclusivity

If your tool uses the HackRF directly, call `_check_hackrf_free()` from `hackrf.py` first and add it to `GROUP_A` in `app_manager.py`.

If it's audio-only or file-based, it goes in GROUP B (no conflict).

## Commit style

```
feat: add rtl_433 ISM sensor decoder
fix: hackrf_sweep timeout on DragonOS Pi64
docs: add exercise for P25 digital voice
```

## Pull request checklist

- [ ] `python test_tools.py` passes
- [ ] Tool description is clear enough for an LLM to know when to call it
- [ ] Hardware-dependent tools fail gracefully with helpful error messages
- [ ] No hardcoded paths — use `command -v` / `shutil.which()`
- [ ] Subprocess calls have explicit timeouts
