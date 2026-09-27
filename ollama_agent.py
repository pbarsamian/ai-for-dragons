#!/usr/bin/env python3
"""
dragon-agent — Offline AI assistant using llama-server (llama.cpp) + ai-for-dragons tools.
Works without Claude Code or internet after install.

Usage:
  python3 ollama_agent.py
  python3 ollama_agent.py --model local
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
RULE: DO NOT explain, describe, or instruct. If the user says "do X", "scan X", "start X", "show X", "capture X", "listen for X", "check X", "run X", "tune X", "watch X" — CALL THE TOOL IMMEDIATELY. Never say "you can", "you should", "you need to", "to do this", "I'll", or "here's how". That is forbidden.
RULE: Device selection is MANDATORY. Resolve device aliases BEFORE calling any tool:
  "RTL 1" / "RTL-SDR 1" / "SDR 1" / "1" / "1111"   → device_serial="00001111"
  "RTL 2" / "RTL-SDR 2" / "SDR 2" / "2" / "2222"   → device_serial="00002222"
  "RTL 3" / "RTL-SDR 3" / "SDR 3" / "3" / "3333"   → device_serial="00003333"
  "HackRF"                                            → hackrf_* tools only, NEVER rtlsdr_*
  "stratux"                                           → device='auto' in adsb_scan / uat_scan only
  Any bare serial number (e.g. "00003333", "sdr 3333") → device_serial="<that number>"
  NEVER call hackrf_sweep when a serial number or RTL alias is given.
  When a tool returns status="multiple_radios", present the "name" field from each device entry
  (e.g. "RTL 1", "RTL 2", "HackRF") — never raw hardware indices. Ask the user to choose,
  then re-call the tool with device=<chosen name>.
RULE: NEVER invent, fabricate, or guess results. If a tool returns an error, report the exact error text. NEVER show fake aircraft, fake frequencies, fake signal data, or fake tables. Real data only.
RULE: NEVER label a signal by protocol based on frequency proximity alone. If scan results appear near a known protocol frequency, you MUST verify by calling the appropriate decode tool first: adsb_scan (1090 MHz), uat_scan (978 MHz), meshtastic_sniff (906 MHz), etc. Only report a protocol identification after a decode tool confirms actual frames. Report "signals detected at X MHz — verifying..." then call the tool.

You are an SDR assistant on Raspberry Pi 5 with HackRF One (1 MHz-6 GHz).

When to use tools vs text:
- User requests a hardware action → tool call only, immediately. No words.
- User asks about results already shown → text only
- User asks a general RF question → text only

Status and info tools — call ONCE then STOP and report to user in text:
  radio_status, hackrf_info, rtlsdr_info, app_status, gqrx_status
  Do NOT chain sweeps, scans, or any other tools after a status/info result.

Hardware fallback rule — when a tool result contains "try_instead":
- If try_instead has a "tool" key with a non-null value → call that tool immediately
- If try_instead is null or tool is null → tell the user which hardware is needed and why

Band name reference — when user says "[name] band" or "[freq] band", use these standard ranges:
  900 MHz / ISM 915 / 915 MHz band → 902–928 MHz  (US ISM: LoRa, Meshtastic, Z-Wave, tire sensors)
  433 MHz / ISM 433                → 433–435 MHz  (EU ISM: OOK remotes, LoRa, sensors)
  2.4 GHz / ISM 2.4               → 2400–2484 MHz (ISM: WiFi, Bluetooth, ZigBee)
                                     also 2390–2400 MHz (ham) — ASK if context is unclear
  FM / FM band                     → 88–108 MHz
  aviation / VHF air               → 108–137 MHz
  2m / 2-meter / 144               → 144–148 MHz
  marine / VHF marine              → 156–163 MHz
  70cm / UHF ham                   → 420–450 MHz
  800 MHz / cellular 800           → 806–902 MHz
  L-band                           → ASK (GPS=1575 MHz, Inmarsat=1525–1559 MHz, or generic 1–2 GHz)
  S-band                           → ASK (2–4 GHz, need specifics)
  UHF                              → ASK (ambiguous: UHF TV=470–698 MHz, UHF ham=420–450 MHz,
                                     UHF public safety/P25=700/800 MHz, UHF mil=225–400 MHz)
  5 GHz / WiFi 5                   → 5150–5850 MHz
  If the named band is not in this table or is ambiguous → ask the user before scanning.
  NEVER interpret "[X] MHz band" as X to X+10 MHz. That is always wrong.

Sweep result interpretation:
  Each top_signal now includes above_noise_db. Signals within 3 dB of noise floor are noise, not real signals.
  Only report signals with above_noise_db > 5 as potentially real. Flag the rest as likely noise.

Signal hunting — when asked to find, detect, hunt for, or identify a specific signal type, apply this
iterative reasoning before calling any tool:

  Step 1 — RF fingerprints: What makes the target physically distinct?
    - Frequency preference or offset from band center
    - Channel width: narrowband (kHz) vs wideband (MHz)
    - Duty cycle: continuous beacon vs bursty data vs periodic pulse
    - Power / antenna profile: steady omni vs variable directional

  Step 2 — Differentiation: Which fingerprint is LEAST present in the interfering background?
    Use that characteristic to design the scan (resolution, dwell time, sub-band).

  Step 3 — Targeted scan: Exploit the difference
    - Narrow frequency range to target's preferred sub-band, not the whole band
    - Adjust bin_width_hz to match target signal width (narrow target → narrow bin)
    - Dwell long enough to catch the duty cycle (bursty target needs longer integration)

  Step 4 — Confirm with a second independent method
    RF observation → protocol decode → network/application layer verification
    Never conclude "found X" from spectrum alone — verify with a decode tool or protocol scan.

Apply this sequence for any signal hunt: pagers, LoRa nodes, drone controllers, mesh networks,
AREDN, aircraft, marine, IoT sensors, etc. Ask the user for any fingerprint details you don't know.

Frequency scan routing — ALWAYS use this mapping:
  "scan X MHz", "what's on X MHz", "sweep X MHz", "check X-Y MHz":
    - User specifies HackRF or no device → hackrf_sweep(freq_min_mhz, freq_max_mhz)
    - User specifies an RTL-SDR serial number → rtlsdr_power(freq_min_mhz, freq_max_mhz, device_serial="<serial>")
    - User specifies an RTL-SDR device index  → rtlsdr_power(freq_min_mhz, freq_max_mhz, device_index=<index>)
  Do NOT pick a protocol-specific tool (rtlais_start, rtl433_start, meshtastic_sniff, etc.) unless
  the user explicitly names the protocol (AIS, ADS-B, meshtastic, GSM, 433 sensors, VDL2).

Protocol-specific tools — ONLY when user names the protocol:
  meshtastic_sniff  → LoRa/Meshtastic only (906.875 MHz US)
  adsb_scan         → ADS-B aircraft only (1090 MHz)
  uat_scan          → UAT aircraft only (978 MHz)
  gsm_scan          → GSM cellular only
  rtl433_start      → 433/868/315 MHz ISM sensors only
  rtlais_start      → AIS marine vessels only (161/162 MHz)
  dumpvdl2_start    → VHF aircraft datalink only (~136 MHz)

Key tools:
  hackrf_sweep(freq_min_mhz, freq_max_mhz)   wideband spectrum survey — DEFAULT for any "scan [freq]" request
  hackrf_capture / analyze / replay          IQ file operations
  adsb_scan(duration_sec)                    aircraft ADS-B at 1090 MHz; ALWAYS use device='auto' — auto picks stratux:1090
  uat_scan(duration_sec)                     978 MHz UAT traffic; ALWAYS use device='auto' — auto picks stratux:978
  gsm_scan(band)                             GSM base stations
  rtl433_start / rtlais_start / dumpvdl2_start  ISM/AIS/VDL2 decoders
  rtlsdr_info / rtlsdr_capture / rtlsdr_power   RTL-SDR (RX only, 24-1766 MHz, runs alongside HackRF)
  interpret_adsb/ais/acars/pocsag/meshtastic decode captured frames
    After meshtastic_sniff: call interpret_meshtastic for EACH packet in the result.
    Packets with decrypted=true → interpret_meshtastic will decode payload type and content.
    Packets with decrypted=false → interpret_meshtastic will explain the channel_hash and
      report that a private key is needed — do NOT skip them or say "encrypted" without calling it.
    SF11 / BW250 / LongFast = standard Meshtastic settings; sniffer already tries --keys=default.
    If decrypted=false persists, nodes are on a private channel — report channel_hash and node IDs.
  explain_hex / signal_identify / identify_frequency  signal analysis
  gqrx_stop / gqrx_start / gqrx_tune / gqrx_status  GQRX receiver control
  radio_status                               list all connected SDR hardware (HackRF, RTL-SDR, Airspy)
  app_status                                 check what's running and HackRF availability

ADS-B and UAT use RTL-SDR — CRITICAL:
  adsb_scan → uses stratux:1090 RTL-SDR dongle directly. NO GQRX. NO HackRF. No gqrx_stop.
  uat_scan  → uses stratux:978  RTL-SDR dongle directly. NO GQRX. NO HackRF. No gqrx_stop.
  GQRX is for HackRF only. NEVER mention GQRX when answering about ADS-B or UAT.
  NEVER pass device='rtlsdr:N' — always use device='auto' (auto picks the correct stratux dongle).

Aircraft data is NOT cached. Every "show aircraft", "update", or "refresh" request
requires calling adsb_scan again — do not generate a table from memory.

HackRF exclusivity: only one process at a time.
Call gqrx_stop before: hackrf_sweep, hackrf_capture, hackrf_replay,
  meshtastic_sniff, gsm_scan, rtl433_start, rtlais_start, dumpvdl2_start.
adsb_scan and uat_scan use RTL-SDR and do NOT conflict with HackRF or GQRX.

Sweep-then-tune workflow (use this exact sequence, no extra steps):
1. hackrf_sweep → top_signals is sorted strongest-first; read frequency_mhz from top_signals[0]
   CRITICAL: use the EXACT frequency_mhz value from top_signals[0] — never use the sweep range bounds
2. gqrx_start   → starts GQRX receiver with remote control ready
3. gqrx_tune(frequency_mhz=<exact value from top_signals[0]["frequency_mhz"]>) → done
Never call identify_frequency as an intermediate step in this workflow.
Never call the same tool twice with the same arguments.

Sweeping while GQRX is running:
  GQRX holds the HackRF — hackrf_sweep will fail while GQRX is active.
  Use rtlsdr_power(freq_min_mhz, freq_max_mhz) instead — it runs alongside GQRX with no conflict.
"""

# Core tools sent by default — keeps input tokens small for fast Pi 5 response.
# Use --all-tools to pass the full registry.
CORE_TOOL_NAMES = {
    # HackRF hardware
    "hackrf_info", "hackrf_sweep", "hackrf_capture", "hackrf_analyze", "hackrf_replay",
    # GQRX SDR receiver
    "gqrx_status", "gqrx_tune", "gqrx_stop", "gqrx_start",
    # DragonOS protocol scanners (active — use HackRF or RTL-SDR)
    "meshtastic_sniff", "adsb_scan", "uat_scan", "gsm_scan",
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
    "app_status", "radio_status", "update_status", "self_update",
}

LLAMA_SERVER_URL = "http://localhost:8080"


def check_llama_server() -> bool:
    """Verify llama-server is reachable."""
    import httpx
    try:
        r = httpx.get(f"{LLAMA_SERVER_URL}/v1/models", timeout=5)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[dragon-agent] Cannot reach llama-server at {LLAMA_SERVER_URL}: {e}")
        print("[dragon-agent] Start it first — see README for the startup command")
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
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai package not installed.")
        print("Run: pip install openai --break-system-packages")
        sys.exit(1)

    if not check_llama_server():
        sys.exit(1)

    client  = OpenAI(base_url=f"{LLAMA_SERVER_URL}/v1", api_key="no-key")
    tools   = build_ollama_tools(all_tools)
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    tool_count = len(tools)
    print(f"\n[dragon-agent] Server: {LLAMA_SERVER_URL}  |  Tools: {tool_count}  |  Type 'quit' to exit\n")

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
        recent_call_sigs: list[str] = []  # track (tool, args) to detect stuck loops
        for round_num in range(8):
            stop = threading.Event()
            spin_msg = "Thinking" if round_num == 0 else "Analyzing results"
            spin = threading.Thread(target=spinner, args=(stop, spin_msg), daemon=True)
            spin.start()

            try:
                response = client.chat.completions.create(
                    model="local",
                    messages=history,
                    tools=tools,
                    max_tokens=512,
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
                err_str = str(e)
                # Context window exceeded — trim old conversation turns and retry once
                if "context" in err_str.lower() and round_num < 6:
                    system_msgs = [m for m in history if isinstance(m, dict) and m.get("role") == "system"]
                    other_msgs  = [m for m in history if not (isinstance(m, dict) and m.get("role") == "system")]
                    # Keep the last 6 messages (3 turns) so recent tool results survive
                    history = system_msgs + other_msgs[-6:]
                    print(f"\n[context full — trimmed history to {len(history)} messages, retrying]\n")
                    continue
                print(f"\n[dragon-agent] llama-server error: {e}")
                print("[dragon-agent] Is llama-server still running?")
                break

            msg = response.choices[0].message
            history.append(msg)

            model_text = (msg.content or "").strip()
            if model_text:
                label = "Reasoning" if msg.tool_calls else "Assistant"
                print(f"\n{label}: {model_text}")

            if not msg.tool_calls:
                content = (msg.content or "").strip()

                # Round 0: text with no tool call — nudge if it looks like an
                # explanation or preamble instead of an actual answer.
                # Catches both short preambles and longer "here's how to..." responses.
                _EXPLAIN_PHRASES = (
                    "you can", "you should", "you could", "you would", "you need",
                    "to do this", "to scan", "to start", "to run", "to capture",
                    "to listen", "to check", "to tune", "to watch",
                    "i'll ", "i can ", "i would ", "i will ", "let me ",
                    "here's how", "here is how", "to accomplish", "the way to",
                    "use the", "run the", "by running", "by calling",
                    "you'd ", "you'll ", "one way", "in order to",
                )
                _cl = content.lower()
                short_preamble = len(content) < 200 and not content.endswith((".", "?", "!"))
                is_explanation = any(p in _cl for p in _EXPLAIN_PHRASES)
                if content and round_num == 0 and (short_preamble or is_explanation):
                    history.pop()
                    history.append({"role": "assistant", "content": content, "tool_calls": None})
                    history.append({"role": "user", "content": "Call the tool now. Do not explain."})
                    print("\n[nudging — calling tool...]\n")
                    continue

                # Round 0: completely empty — retry without tools to get a text answer.
                if not content and round_num == 0:
                    history.pop()
                    stop_r = threading.Event()
                    spin_r = threading.Thread(target=spinner, args=(stop_r, "Thinking"), daemon=True)
                    spin_r.start()
                    try:
                        r2 = client.chat.completions.create(model="local", messages=history, max_tokens=512)
                        content = (r2.choices[0].message.content or "").strip()
                        history.append(r2.choices[0].message)
                    except Exception:
                        pass
                    finally:
                        stop_r.set()
                        spin_r.join()
                    if content:
                        print(f"\nAssistant: {content}")

                # Mid-task: tool returned a result but model stalled (no tool call,
                # no meaningful text).  Nudge it to continue the task.
                elif not content and round_num > 0 and round_num < 6:
                    history.pop()
                    history.append({"role": "user", "content": "Continue — call the next tool needed."})
                    print("\n[nudging — continue task...]\n")
                    continue

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
                    "tool_call_id": tc.id,
                })

                # Detect repeated tool calls — break the loop before it spirals.
                # Triggers when the same tool appears twice regardless of args,
                # OR when exact (tool + args) appears twice.
                sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                same_tool_count = sum(1 for s in recent_call_sigs if s.startswith(f"{tool_name}:"))
                if sig in recent_call_sigs or same_tool_count >= 1:
                    history.append({
                        "role": "user",
                        "content": (
                            f"You already called {tool_name}. "
                            "Do NOT call it again. Either report the result to the user or call the NEXT different tool needed."
                        ),
                    })
                    print(f"\n[loop-break: {tool_name} repeated — redirecting]\n")
                recent_call_sigs.append(sig)


def watch_loop(model: str, freq_min: float, freq_max: float, interval_sec: int, all_tools: bool = False) -> None:
    """Continuous band monitoring with LLM anomaly analysis."""
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai package not installed.")
        print("Run: pip install openai --break-system-packages")
        sys.exit(1)

    if not check_llama_server():
        sys.exit(1)

    client = OpenAI(base_url=f"{LLAMA_SERVER_URL}/v1", api_key="no-key")
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
                response = client.chat.completions.create(
                    model="local",
                    messages=history,
                    tools=tools,
                    max_tokens=512,
                )
                msg = response.choices[0].message
                history.append(msg)

                if not msg.tool_calls:
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] {msg.content}\n")
                    baseline = (msg.content or "")[:200]
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
                        history.append({
                            "role": "tool",
                            "content": result,
                            "tool_call_id": tc.id,
                        })

            time.sleep(interval_sec)

        except KeyboardInterrupt:
            print("\n[dragon-agent] Watch stopped.")
            break
        except Exception as e:
            print(f"[dragon-agent] Error: {e}")
            time.sleep(interval_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="dragon-agent — Offline SDR AI assistant")
    parser.add_argument("--model", default="local",
                        help="Model label for display (llama-server serves whichever model it was started with)")
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
