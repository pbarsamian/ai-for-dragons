#!/usr/bin/env python3
"""
dragon-agent — Offline AI assistant using Ollama + ai-for-dragons tools.
Works without Claude Code or internet after install.

Usage:
  python3 ollama_agent.py
  python3 ollama_agent.py --model qwen3:8b
  python3 ollama_agent.py --watch 902 928 --interval 60
"""

import argparse
import json
import sys
import time
import threading
from sdr_mcp.tools import TOOL_REGISTRY, execute_tool


SYSTEM_PROMPT = """\
/no_think
RULE: Hardware actions → output ONLY the tool call. Zero words before or after. No plan, no acknowledgment, no explanation. The tool call IS your entire response.

You are an SDR assistant on Raspberry Pi 5 with HackRF One (1 MHz-6 GHz).

When to use tools vs text:
- User requests a hardware action → tool call only, immediately
- User asks about results already shown → text only
- User asks a general RF question → text only

Key tools:
  hackrf_sweep(freq_min, freq_max)           wideband spectrum survey
  hackrf_capture / analyze / replay          IQ file operations
  meshtastic_sniff(freq_mhz, duration_sec)   LoRa packets; US=906.875 MHz, duration unlimited
  adsb_scan(duration_sec)                    aircraft ADS-B at 1090 MHz
  gsm_scan(band)                             GSM base stations
  rtl433_start / rtlais_start / dumpvdl2_start  ISM/AIS/VDL2 decoders
  rtlsdr_info / rtlsdr_capture / rtlsdr_power   RTL-SDR (RX only, 24-1766 MHz, runs alongside HackRF)
  interpret_adsb/ais/acars/pocsag/meshtastic decode captured frames
  explain_hex / signal_identify / identify_frequency  signal analysis
  gqrx_stop / gqrx_start / gqrx_tune / gqrx_status  GQRX receiver control
  app_status                                 check what's running and HackRF availability

HackRF exclusivity: only one process at a time.
Call gqrx_stop before: hackrf_sweep, hackrf_capture, hackrf_replay,
  meshtastic_sniff, adsb_scan, gsm_scan, rtl433_start, rtlais_start, dumpvdl2_start.
"""

# Core tools sent by default — keeps input tokens small for fast Pi 5 response.
# Use --all-tools to pass the full registry.
CORE_TOOL_NAMES = {
    # HackRF hardware
    "hackrf_info", "hackrf_sweep", "hackrf_capture", "hackrf_analyze", "hackrf_replay",
    # GQRX SDR receiver
    "gqrx_status", "gqrx_tune", "gqrx_stop", "gqrx_start",
    # DragonOS protocol scanners (active — use HackRF)
    "meshtastic_sniff", "adsb_scan", "gsm_scan",
    "rtl433_start",     # ISM band sensors: weather, tire pressure, power meters (433/868/915 MHz)
    "rtlais_start",     # AIS marine vessel transponders (161/162 MHz)
    "dumpvdl2_start",   # VHF aircraft datalink (136 MHz — near airports)
    # RTL-SDR dongles (independent from HackRF — run simultaneously)
    "rtlsdr_info",      # enumerate RTL-SDR devices by index
    "rtlsdr_capture",   # capture IQ from RTL-SDR (24-1766 MHz, RX only)
    "rtlsdr_power",     # frequency power survey via RTL-SDR
    # Protocol decoders (passive — work on data already received)
    "interpret_adsb",       # decode raw ADS-B hex frame
    "interpret_ais",        # decode NMEA AIS sentence
    "interpret_acars",      # decode ACARS aircraft message
    "interpret_pocsag",     # decode POCSAG pager line from multimon-ng
    "interpret_meshtastic", # decode Meshtastic packet JSON
    # Signal analysis
    "signal_identify", "identify_frequency", "explain_hex",
    # App/system status and self-management
    "app_status", "update_status", "self_update",
}


def check_ollama(model: str) -> bool:
    """Verify Ollama is running and the model is available. Print clear errors if not."""
    try:
        import ollama
        client = ollama.Client(timeout=10)
        models = client.list()
        available = [m.model for m in models.models]

        # Ollama model names can vary: qwen3:1.7b might be listed as
        # qwen3:1.7b, qwen3:1.7b-instruct-q4_K_M, etc.
        # Match if the requested name is a prefix of any available model name.
        def matches(requested: str, candidate: str) -> bool:
            # Exact match
            if requested == candidate:
                return True
            # requested is a prefix of candidate up to a dash or colon
            # e.g. "qwen3:1.7b" matches "qwen3:1.7b-instruct-q4_K_M"
            if candidate.startswith(requested):
                return True
            # Base name match (ignore tag entirely)
            if requested.split(":")[0] == candidate.split(":")[0]:
                return True
            return False

        if not any(matches(model, a) for a in available):
            print(f"[dragon-agent] Model '{model}' not found in Ollama.")
            print(f"[dragon-agent] Available: {', '.join(available) or 'none'}")
            print(f"[dragon-agent] Download it: ollama pull {model}")
            print(f"[dragon-agent] Then retry:  dragon-agent --model {model}")
            return False

        # Resolve to the actual stored name and return it
        actual = next((a for a in available if matches(model, a)), model)
        if actual != model:
            print(f"[dragon-agent] Resolved '{model}' → '{actual}'")
        return actual  # return resolved name, not just True
    except Exception as e:
        print(f"[dragon-agent] Cannot reach Ollama: {e}")
        print("[dragon-agent] Try: sudo systemctl start ollama")
        print("[dragon-agent] Or:  ollama serve &")
        return False


def spinner(stop_event: threading.Event, message: str = "Thinking") -> None:
    """Show a spinner while waiting for model response."""
    chars = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    i = 0
    while not stop_event.is_set():
        print(f"\r{chars[i % len(chars)]} {message}...", end="", flush=True)
        time.sleep(0.1)
        i += 1
    print("\r" + " " * (len(message) + 10) + "\r", end="", flush=True)


def build_ollama_tools(all_tools: bool = False) -> list[dict]:
    names = TOOL_REGISTRY.keys() if all_tools else CORE_TOOL_NAMES
    tools = []
    for name in names:
        if name not in TOOL_REGISTRY:
            continue
        spec = TOOL_REGISTRY[name]
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["schema"],
            },
        })
    return tools


def _show_result(result: str, max_items: int = 8) -> None:
    """Print a compact, readable summary of a tool result."""
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        for line in result.strip().splitlines()[:max_items]:
            print(f"   {line[:120]}")
        return

    if not isinstance(data, dict):
        print(f"   {str(data)[:200]}")
        return

    for k, v in list(data.items())[:max_items]:
        if isinstance(v, list):
            print(f"   {k}: [{len(v)} item{'s' if len(v) != 1 else ''}]")
        elif isinstance(v, dict):
            inner = ", ".join(f"{ik}: {iv}" for ik, iv in list(v.items())[:3])
            print(f"   {k}: {{{inner}}}")
        else:
            print(f"   {k}: {str(v)[:120]}")
    if len(data) > max_items:
        print(f"   ... ({len(data) - max_items} more fields)")


def chat_loop(model: str, all_tools: bool = False) -> None:
    try:
        import ollama
    except ImportError:
        print("ERROR: ollama package not installed.")
        print("Run: pip install ollama --break-system-packages")
        sys.exit(1)

    resolved = check_ollama(model)
    if not resolved:
        sys.exit(1)
    model = resolved  # use exact name Ollama knows

    import httpx
    client  = ollama.Client(timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10))
    tools   = build_ollama_tools(all_tools)
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    tool_count = len(tools)
    print(f"\n[dragon-agent] Model: {model}  |  Tools: {tool_count}  |  Type 'quit' to exit\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[dragon-agent] Exiting.")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue

        history.append({"role": "user", "content": user_input})

        # Agentic loop: model may call tools multiple times
        for round_num in range(8):
            stop = threading.Event()
            spin_msg = "Thinking" if round_num == 0 else "Analyzing results"
            spin = threading.Thread(target=spinner, args=(stop, spin_msg), daemon=True)
            spin.start()

            try:
                response = client.chat(
                    model=model,
                    messages=history,
                    tools=tools,
                    think=False,
                    options={"num_predict": 512},
                )
                stop.set()
                spin.join()
            except KeyboardInterrupt:
                stop.set()
                spin.join()
                print("\n[interrupted]")
                break
            except Exception as e:
                stop.set()
                spin.join()
                print(f"\n[dragon-agent] Ollama error: {e}")
                print("[dragon-agent] Is Ollama still running? Check: sudo systemctl status ollama")
                break

            msg = response.message
            history.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])],
            })

            # Always show the model's reasoning/content immediately so the user
            # can see what it decided before any long-running tool executes.
            model_text = (msg.content or "").strip()
            if model_text:
                label = "Reasoning" if msg.tool_calls else "Assistant"
                print(f"\n{label}: {model_text}")

            if not msg.tool_calls:
                content = (msg.content or "").strip()

                # Round 0 produced text but no tool call — nudge once.
                # Short content (< 200 chars) that ends without terminal punctuation
                # is almost certainly a preamble ("I'll listen...", "Let me...").
                # We pop it, inject a forcing message, and let the loop continue.
                if content and round_num == 0 and len(content) < 200 and not content.endswith((".", "?", "!")):
                    history.pop()
                    history.append({"role": "assistant", "content": content, "tool_calls": []})
                    history.append({"role": "user", "content": "Call the tool now."})
                    print(f"\n[nudging — calling tool...]\n")
                    continue

                # Empty response on round 0 — retry without tools to get a text answer.
                if not content and round_num == 0:
                    history.pop()
                    stop_r = threading.Event()
                    spin_r = threading.Thread(
                        target=spinner, args=(stop_r, "Thinking"), daemon=True
                    )
                    spin_r.start()
                    try:
                        r2 = client.chat(
                            model=model,
                            messages=history,
                            think=False,
                            options={"num_predict": 512},
                        )
                        content = (r2.message.content or "").strip()
                        history.append({
                            "role": "assistant",
                            "content": content,
                            "tool_calls": [],
                        })
                    except Exception:
                        pass
                    finally:
                        stop_r.set()
                        spin_r.join()
                    # Print the retry content (initial was empty so model_text didn't show it)
                    if content:
                        print(f"\nAssistant: {content}")

                if not content and not model_text:
                    print("\nAssistant: [no response — try rephrasing]")
                print()
                break

            # Execute tool calls
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = tc.function.arguments or {}
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}

                bar = "─" * max(0, 52 - len(tool_name))
                print(f"\n── {tool_name} {bar}")
                if tool_args:
                    w = max(len(k) for k in tool_args)
                    for k, v in tool_args.items():
                        print(f"   {k:<{w}} : {v}")

                t0 = time.time()
                stop2 = threading.Event()
                spin2 = threading.Thread(
                    target=spinner, args=(stop2, f"Running {tool_name}"), daemon=True
                )
                spin2.start()
                try:
                    if tool_name not in TOOL_REGISTRY:
                        result = f"Unknown tool: {tool_name}"
                    else:
                        result = execute_tool(tool_name, tool_args)
                except Exception as tool_exc:
                    result = f"Tool error [{tool_name}]: {tool_exc}"
                finally:
                    stop2.set()
                    spin2.join()

                elapsed = time.time() - t0
                print(f"   completed in {elapsed:.1f}s")
                _show_result(result)
                history.append({
                    "role": "tool",
                    "content": result,
                    "name": tool_name,
                })


def watch_loop(model: str, freq_min: float, freq_max: float, interval_sec: int, all_tools: bool = False) -> None:
    """Continuous band monitoring with LLM anomaly analysis."""
    try:
        import ollama
    except ImportError:
        print("ERROR: ollama package not installed.")
        sys.exit(1)

    resolved = check_ollama(model)
    if not resolved:
        sys.exit(1)
    model = resolved  # use exact name Ollama knows

    import httpx
    client = ollama.Client(timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10))
    tools  = build_ollama_tools(all_tools)

    print(f"[dragon-agent] Watch mode: {freq_min}-{freq_max} MHz every {interval_sec}s")
    print("[dragon-agent] Press Ctrl+C to stop\n")

    baseline = None
    while True:
        try:
            prompt = (
                f"Sweep {freq_min} to {freq_max} MHz. "
                + (f"Previous baseline: {baseline}. " if baseline else "")
                + "Report any signals stronger than -60 dBm or unusual activity."
            )
            history = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ]

            for _ in range(4):
                response = client.chat(model=model, messages=history, tools=tools, think=False, options={"num_predict": 512})
                msg = response.message
                history.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])],
                })

                if not msg.tool_calls:
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] {msg.content}\n")
                    baseline = msg.content[:200]
                    break

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    tool_args = tc.function.arguments or {}
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {}
                    if tool_name in TOOL_REGISTRY:
                        result = execute_tool(tool_name, tool_args)
                        history.append({"role": "tool", "content": result, "name": tool_name})

            time.sleep(interval_sec)

        except KeyboardInterrupt:
            print("\n[dragon-agent] Watch stopped.")
            break
        except Exception as e:
            print(f"[dragon-agent] Error: {e}")
            time.sleep(interval_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="dragon-agent — Offline SDR AI assistant")
    parser.add_argument("--model", default="qwen2.5:7b",
                        help="Ollama model name (default: qwen2.5:7b)")
    parser.add_argument("--watch", nargs=2, type=float, metavar=("FREQ_MIN", "FREQ_MAX"),
                        help="Watch mode: continuously monitor FREQ_MIN-FREQ_MAX MHz")
    parser.add_argument("--interval", type=int, default=60,
                        help="Watch mode scan interval in seconds (default 60)")
    parser.add_argument("--all-tools", action="store_true",
                        help="Pass all 73 tools instead of the default core 26 (slower on Pi 5)")
    args = parser.parse_args()

    if args.watch:
        watch_loop(args.model, args.watch[0], args.watch[1], args.interval, args.all_tools)
    else:
        chat_loop(args.model, args.all_tools)


if __name__ == "__main__":
    main()
