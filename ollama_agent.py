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
You are an SDR tool caller on a Raspberry Pi 5 with HackRF One (1 MHz-6 GHz).

Three modes — pick one, never mix:
1. New hardware action → call a tool immediately. No text before or after.
2. Question about results already shown in this conversation → answer in text. No tool call.
3. General knowledge question (frequencies, protocols, definitions) → answer in text. No tool call.

Rules:
- HackRF is exclusive: call gqrx_stop before hackrf_sweep or hackrf_capture.
- Sweep workflow: gqrx_stop → hackrf_sweep → gqrx_start → gqrx_tune.
- Meshtastic: US 906.875 MHz, SF11, BW 250 kHz. Use meshtastic_sniff to listen for packets.
- Never reason aloud. Never explain before calling a tool.
"""

# Core tools sent by default — keeps input tokens small for fast Pi 5 response.
# Use --all-tools to pass the full registry.
CORE_TOOL_NAMES = {
    "hackrf_info", "hackrf_sweep", "hackrf_capture", "hackrf_analyze", "hackrf_replay",
    "gqrx_status", "gqrx_tune", "gqrx_stop", "gqrx_start",
    "signal_identify", "app_status",
    "meshtastic_sniff",
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
    client  = ollama.Client(timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10))
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
            spin = threading.Thread(
                target=spinner,
                args=(stop, "Thinking" if round_num == 0 else "Running tool"),
                daemon=True,
            )
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

            if not msg.tool_calls:
                print(f"\nAssistant: {msg.content}\n")
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

                print(f"[tool] {tool_name}({json.dumps(tool_args, separators=(',', ':'))})")

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
                finally:
                    stop2.set()
                    spin2.join()

                print(f"[result] {result[:300]}{'...' if len(result) > 300 else ''}\n")
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
    client = ollama.Client(timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10))
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
    parser.add_argument("--model", default="qwen2.5:3b",
                        help="Ollama model name (default: qwen2.5:3b)")
    parser.add_argument("--watch", nargs=2, type=float, metavar=("FREQ_MIN", "FREQ_MAX"),
                        help="Watch mode: continuously monitor FREQ_MIN-FREQ_MAX MHz")
    parser.add_argument("--interval", type=int, default=60,
                        help="Watch mode scan interval in seconds (default 60)")
    parser.add_argument("--all-tools", action="store_true",
                        help="Pass all 73 tools instead of the default core 11 (slower on Pi 5)")
    args = parser.parse_args()

    if args.watch:
        watch_loop(args.model, args.watch[0], args.watch[1], args.interval, args.all_tools)
    else:
        chat_loop(args.model, args.all_tools)


if __name__ == "__main__":
    main()
