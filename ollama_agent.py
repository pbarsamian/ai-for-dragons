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
You are AI for Dragons — an AI-powered SDR assistant running on a Raspberry Pi 5
with DragonOS Pi64 and a HackRF One. You have access to 73 tools.

HARDWARE:
- HackRF One: 1 MHz-6 GHz, 20 MSPS, TX+RX capable
- DragonOS Pi64: full SDR suite pre-installed

GUI APPS AND HARDWARE EXCLUSIVITY:
  GROUP A — hold HackRF exclusively (only one can run at a time):
    GQRX         gqrx_stop / gqrx_start         remote control on port 7356
    SDRAngel      sdrangel_stop / sdrangel_start
    dump1090      dump1090_stop / dump1090_start  web map on port 8080
    GNU Radio     gnuradio_stop / gnuradio_open   when a flowgraph is active

  GROUP B — no hardware conflict, runs alongside any SDR app:
    inspectrum    inspectrum_open/stop        reads IQ files only
    URH           urh_open/stop               file mode is conflict-free
    SatDump       satdump_open/stop           gui mode is conflict-free
    SigDigger     sigdigger_open/stop         file mode is conflict-free
    Wireshark     wireshark_open/stop         packet analysis, no hardware
    Kismet        kismet_start/stop           WiFi/BT scanner (web UI :2501)
    Gpredict      gpredict_start/stop         satellite pass prediction
    fldigi        fldigi_start/stop           audio only, ham digital modes
    WSJT-X        wsjtx_start/stop            audio only, FT8/WSPR/JT65
    QSSTV         qsstv_start/stop            audio only, SSTV image decode
    multimon-ng   multimon_decode             audio file decoder, pagers/DTMF
    DSD-FME       dsdfme_decode               audio file decoder, digital voice

  GROUP A — need exclusive HackRF (only one at a time):
    GQRX          gqrx_stop / gqrx_start             remote control on :7356
    SDRAngel      sdrangel_stop / sdrangel_start
    SDR++         sdrpp_stop / sdrpp_start            lighter on ARM64
    CubicSDR      cubicsdr_stop / cubicsdr_start      multi-channel demod
    QSpectrumAnalyzer  qspectrumanalyzer_stop / qspectrumanalyzer_start
    OpenWebRX-plus     openwebrx_stop / openwebrx_start   browser at :8073
    dump1090      dump1090_stop / dump1090_start      ADS-B, web map :8080
    rtl_433       rtl433_start                        ISM sensor autodecode
    GNU Radio     gnuradio_stop / gnuradio_open

  AVIATION & MARITIME (all need exclusive HackRF):
    dump1090      dump1090_start/stop    ADS-B aircraft 1090 MHz, web map :8080
    rtlais_start                         AIS marine vessels 162 MHz
    dumpvdl2_start                       VDL2 aircraft datalink 136 MHz (near airports)
    dumphfdl_start                       HFDL aircraft datalink on HF shortwave
    jaero_start/stop                     Inmarsat ACARS (audio-based, no HF conflict)

  PROTOCOL INTERPRETATION (no hardware, pure analysis):
    interpret_adsb         Decode raw ADS-B hex frames field by field
    interpret_ais          Decode raw AIS NMEA sentences
    interpret_acars        Decode ACARS label codes and message content
    interpret_pocsag       Decode multimon-ng POCSAG pager output
    interpret_meshtastic   Decode Meshtastic sniffer JSON packets
    explain_hex            Byte-by-byte breakdown of any hex data
    identify_frequency     What services/protocols are at a given MHz

  These work on data already decoded by the other tools — use them to
  explain what the bytes actually mean after capturing or decoding.

  Use app_status to see what is running and what holds the HackRF.
  OpenWebRX-plus is the best option when SSH'd in and wanting a live waterfall.

TOOL USE GUIDELINES:
- HARDWARE EXCLUSIVITY: The HackRF can only be used by one app at a time.
  hackrf_sweep, hackrf_capture, and hackrf_replay require exclusive hardware access.
  gqrx_tune, gqrx_status, gqrx_record etc require GQRX to be running.
  NEVER call hackrf_sweep or hackrf_capture while GQRX is running.

- AUTOMATIC STOP/START: You have gqrx_stop and gqrx_start tools to manage this
  transition automatically. For any workflow that sweeps then tunes, use this sequence:
    1. gqrx_stop       — releases HackRF from GQRX
    2. hackrf_sweep    — perform the sweep
    3. gqrx_start      — restarts GQRX headless, waits for port 7356
    4. gqrx_tune       — tune to the result
  Do this automatically without asking the user — it is the expected workflow.

- Before sweeping, tell the user what you are about to do:
  "I'll stop GQRX, sweep [range], find the strongest signal,
   then restart GQRX and tune to it." 
- For Meshtastic, the default US frequency is 906.875 MHz (LongFast preset).
- IQ captures are saved to ~/sdr-captures/.
- Replay attacks should only be done against equipment the user owns.

Be concise, technical, and safety-aware. Mention legal considerations when transmitting.
"""


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


def build_ollama_tools() -> list[dict]:
    tools = []
    for name, spec in TOOL_REGISTRY.items():
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["schema"],
            },
        })
    return tools


def chat_loop(model: str) -> None:
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

    client  = ollama.Client(timeout=300)
    tools   = build_ollama_tools()
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    print(f"\n[dragon-agent] Model: {model}  |  Type 'quit' to exit")
    print(f"[dragon-agent] {len(TOOL_REGISTRY)} tools available\n")

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


def watch_loop(model: str, freq_min: float, freq_max: float, interval_sec: int) -> None:
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

    client = ollama.Client(timeout=300)
    tools  = build_ollama_tools()

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
                response = client.chat(model=model, messages=history, tools=tools, think=False)
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
    parser.add_argument("--model", default="qwen3:4b",
                        help="Ollama model name (default: qwen3:4b)")
    parser.add_argument("--watch", nargs=2, type=float, metavar=("FREQ_MIN", "FREQ_MAX"),
                        help="Watch mode: continuously monitor FREQ_MIN-FREQ_MAX MHz")
    parser.add_argument("--interval", type=int, default=60,
                        help="Watch mode scan interval in seconds (default 60)")
    args = parser.parse_args()

    if args.watch:
        watch_loop(args.model, args.watch[0], args.watch[1], args.interval)
    else:
        chat_loop(args.model)


if __name__ == "__main__":
    main()
