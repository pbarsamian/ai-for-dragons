"""
Tool registry for sdr-mcp.
18 tools across 4 categories:
  - GQRX control    (6 tools)  — requires GQRX remote control enabled on :7356
  - HackRF CLI      (5 tools)  — wraps hackrf_info, hackrf_sweep, hackrf_transfer
  - DragonOS tools  (5 tools)  — meshtastic-sniffer, dump1090, grgsm_scanner, GNU Radio
  - Exercise guide  (2 tools)  — list and fetch exercises from the HTML guide
"""

import json
import os
import subprocess
import time
from typing import Any

from .gqrx        import GqrxClient, gqrx_stop, gqrx_start
from .app_manager import (
    sdrangel_start, sdrangel_stop, sdrangel_status,
    gnuradio_open, gnuradio_stop,
    inspectrum_open, inspectrum_stop,
    urh_open, urh_stop,
    satdump_open, satdump_stop,
    dump1090_start, dump1090_stop,
    qspectrumanalyzer_start, qspectrumanalyzer_stop,
    cubicsdr_start, cubicsdr_stop,
    sigdigger_open, sigdigger_stop,
    openwebrx_start, openwebrx_stop,
    fldigi_start, fldigi_stop,
    jaero_start, jaero_stop,
    rtlais_start,
    dumphfdl_start,
    dumpvdl2_start,
    wireshark_open, wireshark_stop,
    kismet_start, kismet_stop,
    wsjtx_start, wsjtx_stop,
    gpredict_start, gpredict_stop,
    rtl433_start,
    multimon_decode,
    qsstv_start, qsstv_stop,
    sdrpp_start, sdrpp_stop,
    dsdfme_decode,
    app_status,
)
from .hackrf  import hackrf_info, hackrf_sweep, hackrf_capture, hackrf_analyze, hackrf_replay
from .rtlsdr  import rtlsdr_info, rtlsdr_capture, rtlsdr_power
from .self_update import self_update, update_status
from .protocol_interpreter import (
    interpret_adsb,
    interpret_ais,
    interpret_acars,
    interpret_pocsag,
    interpret_meshtastic,
    explain_hex,
    identify_frequency,
)
from .dragonos import (
    signal_identify,
    meshtastic_sniff,
    adsb_scan,
    gsm_scan,
    flowgraph_run,
)

# ── Exercise catalogue (inline — no external file needed) ───────────────────

EXERCISES = {
    "ex01_fm": {
        "title": "FM Broadcast Reception & Waterfall Fundamentals",
        "level": "Beginner",
        "tool": "SDRAngel / GQRX",
        "steps": [
            "Open SDRAngel or GQRX from the DragonOS menu.",
            "Set center frequency to 98.0 MHz, sample rate to 8 MSPS.",
            "Add a WFM demodulator channel, click a strong signal spike.",
            "Adjust gain sliders until audio is clean with low noise floor.",
            "Identify 3 FM stations and note their frequency and waterfall width.",
        ],
        "goal": "Understand waterfall display, center frequency, gain, bandwidth, squelch.",
    },
    "ex02_sweep": {
        "title": "Wideband Spectrum Survey with hackrf_sweep",
        "level": "Beginner",
        "tool": "hackrf_sweep",
        "steps": [
            "Verify HackRF connected: hackrf_info",
            "Run: hackrf_sweep -f 100:1000 -g 32 -l 32 -w 1000000 -r spectrum.csv",
            "Visualize with heatmap.py (included with hackrf-tools).",
            "Identify active bands: FM, aviation, ISM 433/915 MHz.",
        ],
        "goal": "Map your local RF environment across a wide frequency range.",
    },
    "ex03_adsb": {
        "title": "ADS-B Aircraft Tracking with dump1090",
        "level": "Beginner",
        "tool": "dump1090",
        "steps": [
            "Run: dump1090 --interactive --net --gain -10",
            "Open http://localhost:8080 in a browser — aircraft appear on a live map.",
            "In terminal, observe raw Mode S hex messages alongside decoded fields.",
            "Capture IQ at 1090 MHz for 5 minutes and replay offline.",
        ],
        "goal": "Decode a real digital protocol from a real-world safety-critical system.",
    },
    "ex04_grc": {
        "title": "GNU Radio Companion — First Flowgraph",
        "level": "Intermediate",
        "tool": "GNU Radio Companion",
        "steps": [
            "Open GNU Radio Companion from the DragonOS menu.",
            "Build: HackRF Source → Low Pass Filter → WBFM Receive → Audio Sink",
            "Add QT GUI Waterfall Sink after HackRF Source.",
            "Run with F6. Right-click each block → Documentation.",
            "Work through the Great Scott Gadgets free SDR course: greatscottgadgets.com/sdr/",
        ],
        "goal": "Understand signal processing as a graph of operations — the foundation for every protocol decoder.",
    },
    "ex05_urh": {
        "title": "Unknown Signal Reverse Engineering with URH",
        "level": "Intermediate",
        "tool": "Universal Radio Hacker",
        "steps": [
            "Open URH. File → Record Signal. Select HackRF. Set frequency to 433.92 MHz.",
            "Trigger your own remote control or sensor. Capture several transmissions.",
            "In Analysis tab, verify auto-detected modulation (OOK/FSK/PSK).",
            "In Interpretation tab, identify preamble, sync word, device ID, payload.",
            "Capture 5 transmissions: compare bit streams to find fixed vs variable fields.",
        ],
        "goal": "The fundamental skill of RF security research — capture, demodulate, find structure.",
    },
    "ex10_meshtastic_visual": {
        "title": "Visualize LoRa Chirp Signatures on the Waterfall",
        "level": "Intermediate",
        "tool": "SDRAngel",
        "steps": [
            "Open SDRAngel. Set center frequency 906 MHz, sample rate 4 MSPS.",
            "Enable the QT Waterfall with ~500ms per row time scale.",
            "Look for short diagonal streaks: rising (low→high) = upchirp (preamble+data).",
            "If you have a Meshtastic node, configure it to beacon every 30 seconds.",
            "Zoom into a single chirp in inspectrum after recording to measure chirp slope.",
        ],
        "goal": "Recognize LoRa by sight before attempting to decode.",
    },
    "ex11_meshtastic_rx": {
        "title": "Real-Time Meshtastic Decode with Meshtastic_SDR",
        "level": "Intermediate",
        "tool": "GNU Radio + gr-lora_sdr",
        "steps": [
            "Install gr-lora_sdr OOT module for GNU Radio.",
            "git clone https://github.com/joshconway/meshtastic_sdr && cd meshtastic_sdr",
            "Open the .grc flowgraph in GNU Radio Companion. Set HackRF to 906.875 MHz, SF=11, BW=250kHz.",
            "In second terminal: python3 meshtastic_gnuradio_RX.py -n 127.0.0.1 -p 20004",
            "Decoded packets show: node IDs, GPS positions, message text, routing data.",
        ],
        "goal": "Passively decode Meshtastic packets using only the HackRF — no LoRa hardware required.",
    },
    "ex12_meshtastic_sniffer": {
        "title": "Wideband Passive Monitoring with meshtastic-sniffer",
        "level": "Advanced",
        "tool": "meshtastic-sniffer",
        "steps": [
            "git clone https://github.com/alphafox02/meshtastic-sniffer && cd meshtastic-sniffer",
            "mkdir build && cd build && cmake .. && make -j$(nproc)",
            "Run: ./meshtastic-sniffer --driver hackrf --freq 915e6 --rate 26e6 --web-port 8080 --json",
            "Open http://localhost:8080 for the live dashboard.",
            "Pipe JSON to jq: ... --json | jq '.packets[] | {from, to, snr, text}'",
        ],
        "goal": "Cover the entire US 902-928 MHz Meshtastic band simultaneously. Study polyphase channelizer architecture.",
    },
    "ex13_meshtastic_tx": {
        "title": "Transmit Meshtastic Messages via HackRF",
        "level": "Advanced",
        "tool": "Meshtastic_SDR TX",
        "steps": [
            "Ensure you have a Meshtastic network you control (at least one receiving node).",
            "Open the TX GRC flowgraph in Meshtastic_SDR. Set HackRF Sink to 906.875 MHz, SF=11, BW=250kHz.",
            "Run: python3 meshtastic_gnuradio_TX.py -n 127.0.0.1 -p 20005 --text 'Hello from HackRF'",
            "Verify receipt on your Meshtastic node's app.",
            "Experiment: deliberately mismatch SF between TX and RX. Note at what SF delta packets fail.",
        ],
        "goal": "Participate in a Meshtastic mesh as a full TX node using only the HackRF.",
    },
}


# ── GQRX tools ─────────────────────────────────────────────────────────────

def _gqrx_status(args: dict) -> str:
    c = GqrxClient()
    freq = c.get_frequency()
    mode = c.get_mode()
    level = c.get_signal_level()
    return json.dumps({
        "frequency_hz": freq,
        "frequency_mhz": round(freq / 1e6, 4) if freq else None,
        "mode": mode,
        "signal_level_dbm": level,
        "remote_control": "connected",
    }, indent=2)

def _gqrx_tune(args: dict) -> str:
    freq_mhz = float(args["frequency_mhz"])
    freq_hz  = int(freq_mhz * 1e6)
    mode     = args.get("mode")
    c = GqrxClient()
    c.set_frequency(freq_hz)
    if mode:
        c.set_mode(mode)
    actual = c.get_frequency()
    return f"Tuned to {actual/1e6:.4f} MHz" + (f" ({mode})" if mode else "")

def _gqrx_set_squelch(args: dict) -> str:
    level = float(args["level_dbm"])
    GqrxClient().set_squelch(level)
    return f"Squelch set to {level} dBm"

def _gqrx_record(args: dict) -> str:
    action    = args.get("action", "start")
    directory = args.get("directory", os.path.expanduser("~/sdr-captures"))
    c = GqrxClient()
    if action == "start":
        c.start_recording(directory)
        return f"Recording started → {directory}"
    else:
        c.stop_recording()
        return "Recording stopped"

def _gqrx_set_frequency(args: dict) -> str:
    return _gqrx_tune(args)

def _gqrx_set_mode(args: dict) -> str:
    mode = args["mode"]
    GqrxClient().set_mode(mode)
    return f"Mode set to {mode}"


def _gqrx_stop(args: dict) -> str:
    return gqrx_stop()

def _gqrx_start(args: dict) -> str:
    return gqrx_start()


# ── HackRF tool wrappers ───────────────────────────────────────────────────

def _hackrf_info(args: dict) -> str:
    return hackrf_info()

def _hackrf_sweep(args: dict) -> str:
    return hackrf_sweep(
        freq_min_mhz=float(args["freq_min_mhz"]),
        freq_max_mhz=float(args["freq_max_mhz"]),
        gain=int(args.get("gain", 32)),
        bin_width_hz=int(args.get("bin_width_hz", 1_000_000)),
    )

def _hackrf_capture(args: dict) -> str:
    return hackrf_capture(
        freq_mhz=float(args["freq_mhz"]),
        sample_rate_msps=float(args.get("sample_rate_msps", 8)),
        duration_sec=float(args.get("duration_sec", 10)),
        output_path=args.get("output_path"),
    )

def _hackrf_analyze(args: dict) -> str:
    return hackrf_analyze(args["iq_file"])

def _hackrf_replay(args: dict) -> str:
    return hackrf_replay(
        iq_file=args["iq_file"],
        freq_mhz=float(args["freq_mhz"]),
        sample_rate_msps=float(args.get("sample_rate_msps", 8)),
        tx_gain=int(args.get("tx_gain", 20)),
    )


# ── DragonOS tool wrappers ─────────────────────────────────────────────────

def _signal_identify(args: dict) -> str:
    return signal_identify(
        freq_mhz=float(args["freq_mhz"]),
        bandwidth_khz=float(args.get("bandwidth_khz", 200)),
    )

def _meshtastic_sniff(args: dict) -> str:
    return meshtastic_sniff(
        freq_mhz=float(args.get("freq_mhz", 906.875)),
        duration_sec=int(args.get("duration_sec", 60)),
        device=str(args.get("device", "auto")),
    )

def _adsb_scan(args: dict) -> str:
    return adsb_scan(
        duration_sec=int(args.get("duration_sec", 30)),
        device=str(args.get("device", "auto")),
    )

def _gsm_scan(args: dict) -> str:
    return gsm_scan(band=args.get("band", "GSM850"))

def _flowgraph_run(args: dict) -> str:
    return flowgraph_run(
        grc_file=args["grc_file"],
        timeout_sec=int(args.get("timeout_sec", 30)),
    )


# ── Exercise tools ─────────────────────────────────────────────────────────

def _exercise_list(args: dict) -> str:
    result = []
    level_filter = args.get("level")
    for eid, ex in EXERCISES.items():
        if level_filter and ex["level"].lower() != level_filter.lower():
            continue
        result.append({
            "id": eid,
            "title": ex["title"],
            "level": ex["level"],
            "tool": ex["tool"],
        })
    return json.dumps(result, indent=2)

def _exercise_get(args: dict) -> str:
    eid = args.get("exercise_id", "")
    ex  = EXERCISES.get(eid)
    if ex is None:
        return f"Exercise '{eid}' not found. Available: {', '.join(EXERCISES.keys())}"
    lines = [
        f"## {ex['title']}",
        f"**Level:** {ex['level']}  |  **Tool:** {ex['tool']}",
        f"**Goal:** {ex['goal']}",
        "",
        "### Steps",
    ]
    for i, step in enumerate(ex["steps"], 1):
        lines.append(f"{i}. {step}")
    return "\n".join(lines)


# ── Self-update wrappers ──────────────────────────────────────────────────

def _self_update(args: dict) -> str:
    return self_update(
        branch=args.get("branch", "main"),
        restart=args.get("restart", True),
    )

def _update_status(args: dict) -> str:
    return update_status()


# ── Protocol interpreter wrappers ─────────────────────────────────────────

def _interpret_adsb(args: dict) -> str:
    return interpret_adsb(args.get("hex_frame", ""))

def _interpret_ais(args: dict) -> str:
    return interpret_ais(args.get("nmea_sentence", ""))

def _interpret_acars(args: dict) -> str:
    return interpret_acars(args.get("raw_message", ""))

def _interpret_pocsag(args: dict) -> str:
    return interpret_pocsag(args.get("decoded_line", ""))

def _interpret_meshtastic(args: dict) -> str:
    return interpret_meshtastic(args.get("packet_json", "{}"))

def _explain_hex(args: dict) -> str:
    return explain_hex(args.get("hex_data", ""), args.get("protocol_hint", ""))

def _identify_frequency(args: dict) -> str:
    return identify_frequency(float(args.get("freq_mhz", 0)))


# ── App manager wrappers ──────────────────────────────────────────────────

def _sdrangel_start(args: dict) -> str:  return sdrangel_start()
def _sdrangel_stop(args: dict)  -> str:  return sdrangel_stop()
def _sdrangel_status(args: dict) -> str: return sdrangel_status()

def _gnuradio_open(args: dict) -> str:
    return gnuradio_open(args.get("grc_file", ""))

def _gnuradio_stop(args: dict) -> str:   return gnuradio_stop()

def _inspectrum_open(args: dict) -> str:
    return inspectrum_open(args.get("iq_file", ""))

def _inspectrum_stop(args: dict) -> str: return inspectrum_stop()

def _urh_open(args: dict) -> str:
    return urh_open(args.get("iq_file", ""))

def _urh_stop(args: dict) -> str:        return urh_stop()

def _satdump_open(args: dict) -> str:
    return satdump_open(args.get("mode", "gui"))

def _satdump_stop(args: dict) -> str:    return satdump_stop()

def _dump1090_start(args: dict) -> str:
    return dump1090_start(int(args.get("duration_sec", 60)), device=args.get("device", "hackrf"))

def _dump1090_stop(args: dict) -> str:   return dump1090_stop()

def _app_status(args: dict) -> str:      return app_status()


def _qspectrumanalyzer_start(args: dict) -> str: return qspectrumanalyzer_start()
def _qspectrumanalyzer_stop(args: dict)  -> str: return qspectrumanalyzer_stop()
def _cubicsdr_start(args: dict) -> str:          return cubicsdr_start()
def _cubicsdr_stop(args: dict)  -> str:          return cubicsdr_stop()
def _sigdigger_open(args: dict) -> str:          return sigdigger_open(args.get("iq_file", ""))
def _sigdigger_stop(args: dict) -> str:          return sigdigger_stop()
def _openwebrx_start(args: dict) -> str:         return openwebrx_start()
def _openwebrx_stop(args: dict)  -> str:         return openwebrx_stop()
def _fldigi_start(args: dict) -> str:            return fldigi_start()
def _fldigi_stop(args: dict)  -> str:            return fldigi_stop()


def _jaero_start(args: dict) -> str:    return jaero_start()
def _jaero_stop(args: dict) -> str:     return jaero_stop()
def _rtlais_start(args: dict) -> str:
    return rtlais_start(int(args.get("duration_sec", 60)), device=args.get("device", "hackrf"))
def _dumphfdl_start(args: dict) -> str:
    return dumphfdl_start(args.get("freq_list", None), int(args.get("duration_sec", 60)))
def _dumpvdl2_start(args: dict) -> str:
    return dumpvdl2_start(int(args.get("duration_sec", 60)), device=args.get("device", "hackrf"))


def _wireshark_open(args: dict) -> str:  return wireshark_open(args.get("capture_file", ""))
def _wireshark_stop(args: dict) -> str:  return wireshark_stop()
def _kismet_start(args: dict) -> str:    return kismet_start()
def _kismet_stop(args: dict) -> str:     return kismet_stop()
def _wsjtx_start(args: dict) -> str:     return wsjtx_start()
def _wsjtx_stop(args: dict) -> str:      return wsjtx_stop()
def _gpredict_start(args: dict) -> str:  return gpredict_start()
def _gpredict_stop(args: dict) -> str:   return gpredict_stop()
def _rtl433_start(args: dict) -> str:
    return rtl433_start(float(args.get("freq_mhz", 433.92)), int(args.get("duration_sec", 30)), device=args.get("device", "hackrf"))


def _rtlsdr_info(args: dict) -> str:
    return rtlsdr_info(args.get("device_index"))


def _rtlsdr_capture(args: dict) -> str:
    return rtlsdr_capture(
        freq_mhz=float(args["freq_mhz"]),
        duration_sec=int(args.get("duration_sec", 10)),
        device_index=int(args.get("device_index", 0)),
        sample_rate_msps=float(args.get("sample_rate_msps", 2.048)),
        output_path=args.get("output_path"),
    )


def _rtlsdr_power(args: dict) -> str:
    return rtlsdr_power(
        freq_min_mhz=float(args["freq_min_mhz"]),
        freq_max_mhz=float(args["freq_max_mhz"]),
        device_index=int(args.get("device_index", 0)),
        integration_sec=int(args.get("integration_sec", 10)),
    )


def _multimon_decode(args: dict) -> str:
    return multimon_decode(args.get("audio_file", ""), args.get("modes", []))
def _qsstv_start(args: dict) -> str:     return qsstv_start()
def _qsstv_stop(args: dict) -> str:      return qsstv_stop()
def _sdrpp_start(args: dict) -> str:     return sdrpp_start()
def _sdrpp_stop(args: dict) -> str:      return sdrpp_stop()
def _dsdfme_decode(args: dict) -> str:
    return dsdfme_decode(args.get("audio_file", ""), args.get("mode", "auto"))


# ── Tool registry ──────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, dict] = {

    # GQRX (6)
    "gqrx_status": {
        "description": "Get current GQRX receiver status: frequency, mode, signal level.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gqrx_status,
    },
    "gqrx_tune": {
        "description": "Tune GQRX to a frequency in MHz, optionally setting the demodulation mode.",
        "schema": {
            "type": "object",
            "properties": {
                "frequency_mhz": {"type": "number", "description": "Center frequency in MHz"},
                "mode": {"type": "string", "enum": ["WFM", "NFM", "AM", "USB", "LSB", "CWL", "CWU"],
                         "description": "Demodulation mode (optional)"},
            },
            "required": ["frequency_mhz"],
        },
        "fn": _gqrx_tune,
    },
    "gqrx_set_frequency": {
        "description": "Set GQRX center frequency in MHz.",
        "schema": {
            "type": "object",
            "properties": {
                "frequency_mhz": {"type": "number"},
            },
            "required": ["frequency_mhz"],
        },
        "fn": _gqrx_set_frequency,
    },
    "gqrx_set_mode": {
        "description": "Set GQRX demodulation mode (WFM, NFM, AM, USB, LSB, CWL, CWU).",
        "schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["WFM", "NFM", "AM", "USB", "LSB", "CWL", "CWU"]},
            },
            "required": ["mode"],
        },
        "fn": _gqrx_set_mode,
    },
    "gqrx_set_squelch": {
        "description": "Set GQRX squelch level in dBm. Signals below this level are muted.",
        "schema": {
            "type": "object",
            "properties": {
                "level_dbm": {"type": "number", "description": "Squelch threshold in dBm (e.g. -80)"},
            },
            "required": ["level_dbm"],
        },
        "fn": _gqrx_set_squelch,
    },
    "gqrx_record": {
        "description": "Start or stop GQRX IQ recording to ~/sdr-captures/.",
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop"]},
                "directory": {"type": "string", "description": "Output directory (default: ~/sdr-captures/)"},
            },
            "required": ["action"],
        },
        "fn": _gqrx_record,
    },

    "gqrx_stop": {
        "description": (
            "Stop GQRX and release the HackRF hardware so it can be used for sweeps "
            "or captures. Always call this before hackrf_sweep or hackrf_capture. "
            "Call gqrx_start when done to resume GQRX."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gqrx_stop,
    },
    "gqrx_start": {
        "description": (
            "Start GQRX (headless) after a sweep or capture is complete. "
            "Waits for the remote control port to be ready before returning. "
            "Call this after hackrf_sweep or hackrf_capture to restore GQRX."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gqrx_start,
    },

    # App manager — GUI tools (14)
    "sdrangel_start": {
        "description": "Start SDRAngel SDR application. Stops GQRX or dump1090 first if running to free the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sdrangel_start,
    },
    "sdrangel_stop": {
        "description": "Stop SDRAngel and release the HackRF hardware.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sdrangel_stop,
    },
    "sdrangel_status": {
        "description": "Check whether SDRAngel is currently running.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sdrangel_status,
    },
    "gnuradio_open": {
        "description": "Open GNU Radio Companion, optionally with a .grc flowgraph file.",
        "schema": {
            "type": "object",
            "properties": {
                "grc_file": {"type": "string", "description": "Path to .grc file to open (optional)"},
            },
            "required": [],
        },
        "fn": _gnuradio_open,
    },
    "gnuradio_stop": {
        "description": "Close GNU Radio Companion.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gnuradio_stop,
    },
    "inspectrum_open": {
        "description": "Open inspectrum with an IQ capture file for signal analysis. No HackRF conflict — reads files only.",
        "schema": {
            "type": "object",
            "properties": {
                "iq_file": {"type": "string", "description": "Path to IQ capture file to open"},
            },
            "required": ["iq_file"],
        },
        "fn": _inspectrum_open,
    },
    "inspectrum_stop": {
        "description": "Close inspectrum.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _inspectrum_stop,
    },
    "urh_open": {
        "description": "Open Universal Radio Hacker (URH) for signal reverse engineering. Optionally open with an IQ file.",
        "schema": {
            "type": "object",
            "properties": {
                "iq_file": {"type": "string", "description": "Path to IQ file to open (optional — omit to open blank)"},
            },
            "required": [],
        },
        "fn": _urh_open,
    },
    "urh_stop": {
        "description": "Close Universal Radio Hacker.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _urh_stop,
    },
    "satdump_open": {
        "description": "Open SatDump for satellite signal decoding. Use mode='gui' for file analysis (no hardware conflict) or mode='live' for live SDR input (stops GQRX first).",
        "schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["gui", "live"], "description": "gui=file mode (default), live=live SDR input"},
            },
            "required": [],
        },
        "fn": _satdump_open,
    },
    "satdump_stop": {
        "description": "Close SatDump.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _satdump_stop,
    },
    "dump1090_start": {
        "description": "Start dump1090 for ADS-B aircraft tracking on 1090 MHz. Use device='rtlsdr:N' for RTL-SDR (recommended) or 'hackrf' for HackRF. Opens web map on http://localhost:8080.",
        "schema": {
            "type": "object",
            "properties": {
                "duration_sec": {"type": "integer", "description": "Run duration in seconds (default 60)"},
                "device": {"type": "string", "description": "SDR device: 'hackrf' (default) or 'rtlsdr:N' for RTL-SDR device index N"},
            },
            "required": [],
        },
        "fn": _dump1090_start,
    },
    "dump1090_stop": {
        "description": "Stop dump1090 and release the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _dump1090_stop,
    },
    "app_status": {
        "description": "Show status of all DragonOS GUI apps (GQRX, SDRAngel, GNU Radio, inspectrum, URH, SatDump, dump1090) and which one currently holds the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _app_status,
    },

    "qspectrumanalyzer_start": {
        "description": "Start QSpectrumAnalyzer — persistent waterfall using hackrf_sweep backend. Better than GQRX for wideband surveys. Needs exclusive HackRF access.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _qspectrumanalyzer_start,
    },
    "qspectrumanalyzer_stop": {
        "description": "Stop QSpectrumAnalyzer and release the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _qspectrumanalyzer_stop,
    },
    "cubicsdr_start": {
        "description": "Start CubicSDR — multi-channel SDR with per-channel demodulators, good for monitoring multiple signals at once. Needs exclusive HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _cubicsdr_start,
    },
    "cubicsdr_stop": {
        "description": "Stop CubicSDR and release the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _cubicsdr_stop,
    },
    "sigdigger_open": {
        "description": "Open SigDigger for deep signal analysis — symbol rate detection, unknown protocol demodulation, burst/gap analysis. Opens IQ files (no hardware conflict) or live SDR source.",
        "schema": {
            "type": "object",
            "properties": {
                "iq_file": {"type": "string", "description": "Path to IQ file to analyze (optional — omit for live SDR input)"},
            },
            "required": [],
        },
        "fn": _sigdigger_open,
    },
    "sigdigger_stop": {
        "description": "Close SigDigger.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sigdigger_stop,
    },
    "openwebrx_start": {
        "description": "Start OpenWebRX-plus — browser-based SDR receiver accessible from any device at http://<pi-ip>:8073. Ideal for SSH workflows where you want to see the waterfall without a desktop. Needs exclusive HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _openwebrx_start,
    },
    "openwebrx_stop": {
        "description": "Stop OpenWebRX-plus and release the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _openwebrx_stop,
    },
    "fldigi_start": {
        "description": "Start fldigi for ham radio digital mode decoding (PSK31, RTTY, Olivia, CW, MFSK etc). Audio-based only — no hardware conflict, runs alongside GQRX.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _fldigi_start,
    },
    "fldigi_stop": {
        "description": "Close fldigi.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _fldigi_stop,
    },

    "jaero_start": {
        "description": "Start JAERO for decoding ACARS messages from Inmarsat satellites — covers aircraft over oceans beyond ADS-B range. Audio-based, no HackRF conflict. Requires GQRX tuned to Inmarsat frequency as audio source.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _jaero_start,
    },
    "jaero_stop": {
        "description": "Close JAERO.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _jaero_stop,
    },
    "rtlais_start": {
        "description": "Decode AIS marine vessel transponders on 161.975/162.025 MHz — the maritime equivalent of ADS-B. Returns vessel positions, MMSI, speed, heading. Needs exclusive HackRF access, or use device='rtlsdr:N' for RTL-SDR.",
        "schema": {
            "type": "object",
            "properties": {
                "duration_sec": {"type": "integer", "description": "Scan duration in seconds (default 60)"},
                "device": {"type": "string", "description": "SDR device: 'hackrf' (default) or 'rtlsdr:N' for RTL-SDR device index N"},
            },
            "required": [],
        },
        "fn": _rtlais_start,
    },
    "dumphfdl_start": {
        "description": "Decode HFDL aircraft datalink messages on HF shortwave frequencies — covers polar routes and remote areas. Needs exclusive HackRF access and a good HF antenna.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_list":    {"type": "array", "items": {"type": "integer"}, "description": "HF frequencies in kHz (default: [8825, 11384, 13270])"},
                "duration_sec": {"type": "integer", "description": "Scan duration in seconds (default 60)"},
            },
            "required": [],
        },
        "fn": _dumphfdl_start,
    },
    "dumpvdl2_start": {
        "description": "Decode VDL Mode 2 aircraft datalink on 136-137 MHz — ACARS over VHF digital radio, very active near airports. Needs exclusive HackRF access, or use device='rtlsdr:N' for RTL-SDR.",
        "schema": {
            "type": "object",
            "properties": {
                "duration_sec": {"type": "integer", "description": "Scan duration in seconds (default 60)"},
                "device": {"type": "string", "description": "SDR device: 'hackrf' (default) or 'rtlsdr:N' for RTL-SDR device index N"},
            },
            "required": [],
        },
        "fn": _dumpvdl2_start,
    },

    "wireshark_open": {
        "description": "Open Wireshark for packet analysis. No HackRF conflict — analyzes capture files or live loopback traffic from gr-gsm, kismet etc. Optionally open with a capture file.",
        "schema": {"type": "object", "properties": {"capture_file": {"type": "string", "description": "Path to .pcap file to open (optional)"}}, "required": []},
        "fn": _wireshark_open,
    },
    "wireshark_stop": {
        "description": "Close Wireshark.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _wireshark_stop,
    },
    "kismet_start": {
        "description": "Start Kismet passive WiFi/Bluetooth scanner. Web UI at http://localhost:2501. No HackRF conflict — uses WiFi adapter.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _kismet_start,
    },
    "kismet_stop": {
        "description": "Stop Kismet.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _kismet_stop,
    },
    "wsjtx_start": {
        "description": "Start WSJT-X for weak signal digital modes: FT8, FT4, WSPR, JT65. Audio-based, no HackRF conflict — runs alongside GQRX. Tune GQRX to 14.074 MHz USB for FT8.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _wsjtx_start,
    },
    "wsjtx_stop": {
        "description": "Close WSJT-X.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _wsjtx_stop,
    },
    "gpredict_start": {
        "description": "Start Gpredict satellite pass predictor — shows when NOAA, ISS, Iridium etc pass overhead. No hardware conflict.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gpredict_start,
    },
    "gpredict_stop": {
        "description": "Close Gpredict.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _gpredict_stop,
    },
    "rtl433_start": {
        "description": "Decode 433/868/315 MHz ISM band sensors automatically — weather stations, tire pressure, doorbells, power meters etc. Needs exclusive HackRF access, or use device='rtlsdr:N' for RTL-SDR.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz":     {"type": "number",  "description": "Center frequency in MHz (default 433.92 for EU, try 315.0 for US)"},
                "duration_sec": {"type": "integer", "description": "Scan duration in seconds (default 30)"},
                "device": {"type": "string", "description": "SDR device: 'hackrf' (default) or 'rtlsdr:N' for RTL-SDR device index N"},
            },
            "required": [],
        },
        "fn": _rtl433_start,
    },
    "multimon_decode": {
        "description": "Decode POCSAG pagers, AFSK, DTMF, EAS from an audio WAV file using multimon-ng. Audio-based, no HackRF conflict.",
        "schema": {
            "type": "object",
            "properties": {
                "audio_file": {"type": "string", "description": "Path to WAV audio file to decode (omit for usage instructions)"},
                "modes":      {"type": "array",  "items": {"type": "string"}, "description": "Modes to try e.g. ['POCSAG1200','AFSK1200','DTMF'] (default: all common modes)"},
            },
            "required": [],
        },
        "fn": _multimon_decode,
    },
    "qsstv_start": {
        "description": "Start QSSTV for Slow Scan TV image decoding. Audio-based, no HackRF conflict. ISS transmits SSTV on 145.800 MHz FM during events.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _qsstv_start,
    },
    "qsstv_stop": {
        "description": "Close QSSTV.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _qsstv_stop,
    },
    "sdrpp_start": {
        "description": "Start SDR++ — modern, lightweight alternative to GQRX with lower CPU usage on ARM64. Needs exclusive HackRF access, stops GQRX first.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sdrpp_start,
    },
    "sdrpp_stop": {
        "description": "Stop SDR++ and release the HackRF.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _sdrpp_stop,
    },
    "dsdfme_decode": {
        "description": "Decode digital voice from a WAV file using DSD-FME: P25, DMR, NXDN, D-STAR, ProVoice. Audio-based, no HackRF conflict.",
        "schema": {
            "type": "object",
            "properties": {
                "audio_file": {"type": "string", "description": "Path to WAV audio file (omit for setup instructions)"},
                "mode":       {"type": "string", "description": "Protocol hint: auto, p25, dmr, nxdn, dstar (default: auto)"},
            },
            "required": [],
        },
        "fn": _dsdfme_decode,
    },

    # RTL-SDR (3)
    "rtlsdr_info": {
        "description": "List connected RTL-SDR devices by index, or get info on a specific device. RTL-SDR and HackRF can run simultaneously — they are independent USB devices.",
        "schema": {
            "type": "object",
            "properties": {
                "device_index": {"type": "integer", "description": "Optional: get info on this specific RTL-SDR device index"},
            },
            "required": [],
        },
        "fn": _rtlsdr_info,
    },
    "rtlsdr_capture": {
        "description": "Capture raw IQ data from an RTL-SDR device (receive only, 24-1766 MHz). Can run simultaneously with HackRF. Use device_index to select which RTL-SDR.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz": {"type": "number", "description": "Center frequency in MHz (24-1766)"},
                "duration_sec": {"type": "integer", "description": "Capture duration in seconds (default 10)"},
                "device_index": {"type": "integer", "description": "RTL-SDR device index (default 0)"},
                "sample_rate_msps": {"type": "number", "description": "Sample rate in MSPS (default 2.048, max ~2.8)"},
                "output_path": {"type": "string", "description": "Output file path (default auto-named in ~/sdr-captures/)"},
            },
            "required": ["freq_mhz"],
        },
        "fn": _rtlsdr_capture,
    },
    "rtlsdr_power": {
        "description": "Frequency power survey across a range using RTL-SDR (equivalent of hackrf_sweep but for RTL-SDR). Can run simultaneously with HackRF. Returns top signals by power.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_min_mhz": {"type": "number", "description": "Start frequency in MHz"},
                "freq_max_mhz": {"type": "number", "description": "End frequency in MHz"},
                "device_index": {"type": "integer", "description": "RTL-SDR device index (default 0)"},
                "integration_sec": {"type": "integer", "description": "Integration time in seconds (default 10)"},
            },
            "required": ["freq_min_mhz", "freq_max_mhz"],
        },
        "fn": _rtlsdr_power,
    },

    # Self-management (2)
    "self_update": {
        "description": (
            "Update AI for Dragons from GitHub — pulls latest code, reinstalls into the venv, "
            "and restarts the agent with the new version. Call this when the user asks to update, "
            "upgrade, or pull the latest version of AI for Dragons."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "branch":  {"type": "string",  "description": "Git branch to pull (default: main)"},
                "restart": {"type": "boolean", "description": "Restart agent after update (default: true)"},
            },
            "required": [],
        },
        "fn": _self_update,
    },
    "update_status": {
        "description": (
            "Check whether AI for Dragons is up to date — compares local git commit to GitHub "
            "and shows what has changed. Call before self_update to preview changes."
        ),
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _update_status,
    },

    # Protocol interpretation (7)
    "interpret_adsb": {
        "description": "Interpret a raw ADS-B/Mode S hex frame — decodes ICAO address, downlink format, type code, callsign, altitude, position, velocity, and validates CRC.",
        "schema": {
            "type": "object",
            "properties": {
                "hex_frame": {"type": "string", "description": "Raw hex frame e.g. '8D4840D6202CC371C32CE0576098'"},
            },
            "required": ["hex_frame"],
        },
        "fn": _interpret_adsb,
    },
    "interpret_ais": {
        "description": "Interpret a raw AIS NMEA sentence (!AIVDM/!AIVDO) — decodes MMSI, vessel name, position, speed, heading, nav status, ship type.",
        "schema": {
            "type": "object",
            "properties": {
                "nmea_sentence": {"type": "string", "description": "Raw NMEA sentence e.g. '!AIVDM,1,1,,B,15M67N0000G?Uf6E..."},
            },
            "required": ["nmea_sentence"],
        },
        "fn": _interpret_ais,
    },
    "interpret_acars": {
        "description": "Interpret a raw ACARS message — decodes label code, aircraft registration, flight ID, message content, and identifies CPDLC/ADS-C sub-protocols.",
        "schema": {
            "type": "object",
            "properties": {
                "raw_message": {"type": "string", "description": "Raw ACARS message text from acarsdec or JAERO"},
            },
            "required": ["raw_message"],
        },
        "fn": _interpret_acars,
    },
    "interpret_pocsag": {
        "description": "Interpret a multimon-ng POCSAG decoded line — explains cap code, baud rate, function bits, and message content.",
        "schema": {
            "type": "object",
            "properties": {
                "decoded_line": {"type": "string", "description": "Decoded line from multimon-ng e.g. 'POCSAG1200: Address: 1234567 Function: 3 Alpha: Hello'"},
            },
            "required": ["decoded_line"],
        },
        "fn": _interpret_pocsag,
    },
    "interpret_meshtastic": {
        "description": "Interpret a Meshtastic packet from meshtastic-sniffer JSON output — decodes node IDs, application port, position, text messages, SNR, hop count.",
        "schema": {
            "type": "object",
            "properties": {
                "packet_json": {"type": "string", "description": "JSON packet string from meshtastic-sniffer"},
            },
            "required": ["packet_json"],
        },
        "fn": _interpret_meshtastic,
    },
    "explain_hex": {
        "description": "Explain a raw hex string — auto-detects protocol (ADS-B, AIS) or returns byte-by-byte breakdown with ASCII. Useful for unknown protocols.",
        "schema": {
            "type": "object",
            "properties": {
                "hex_data":      {"type": "string", "description": "Hex string to explain"},
                "protocol_hint": {"type": "string", "description": "Optional protocol hint: adsb, ais, meshtastic (default: auto-detect)"},
            },
            "required": ["hex_data"],
        },
        "fn": _explain_hex,
    },
    "identify_frequency": {
        "description": "Identify what services and protocols operate at a given frequency in MHz — returns band name, typical users, and suggested decoder tools.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz": {"type": "number", "description": "Frequency in MHz"},
            },
            "required": ["freq_mhz"],
        },
        "fn": _identify_frequency,
    },

    # HackRF (5)
    "hackrf_info": {
        "description": "Get HackRF hardware info: serial number, firmware version, board revision.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _hackrf_info,
    },
    "hackrf_sweep": {
        "description": "Sweep a frequency range with the HackRF and return signal strength data. Good for finding active signals.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_min_mhz": {"type": "number", "description": "Start frequency in MHz"},
                "freq_max_mhz": {"type": "number", "description": "End frequency in MHz"},
                "gain":         {"type": "integer", "description": "LNA gain in dB (0-40, default 32)"},
                "bin_width_hz": {"type": "integer", "description": "FFT bin width in Hz (default 1000000)"},
            },
            "required": ["freq_min_mhz", "freq_max_mhz"],
        },
        "fn": _hackrf_sweep,
    },
    "hackrf_capture": {
        "description": "Capture raw IQ data from the HackRF to a file.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz":         {"type": "number",  "description": "Center frequency in MHz"},
                "sample_rate_msps": {"type": "number",  "description": "Sample rate in MSPS (default 8)"},
                "duration_sec":     {"type": "number",  "description": "Duration in seconds (default 10)"},
                "output_path":      {"type": "string",  "description": "Output file path (default auto-named in ~/sdr-captures/)"},
            },
            "required": ["freq_mhz"],
        },
        "fn": _hackrf_capture,
    },
    "hackrf_analyze": {
        "description": "Analyze a previously captured IQ file: compute power spectrum, identify dominant signals.",
        "schema": {
            "type": "object",
            "properties": {
                "iq_file": {"type": "string", "description": "Path to IQ capture file"},
            },
            "required": ["iq_file"],
        },
        "fn": _hackrf_analyze,
    },
    "hackrf_replay": {
        "description": "Replay a previously captured IQ file through the HackRF transmitter.",
        "schema": {
            "type": "object",
            "properties": {
                "iq_file":          {"type": "string",  "description": "Path to IQ capture file"},
                "freq_mhz":         {"type": "number",  "description": "Transmit center frequency in MHz"},
                "sample_rate_msps": {"type": "number",  "description": "Sample rate in MSPS (default 8)"},
                "tx_gain":          {"type": "integer", "description": "TX VGA gain 0-47 dB (default 20 — keep low)"},
            },
            "required": ["iq_file", "freq_mhz"],
        },
        "fn": _hackrf_replay,
    },

    # DragonOS protocol tools (5)
    "signal_identify": {
        "description": "Attempt to identify what protocol or signal is active at a given frequency.",
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz":        {"type": "number", "description": "Frequency to analyze in MHz"},
                "bandwidth_khz":   {"type": "number", "description": "Analysis bandwidth in kHz (default 200)"},
            },
            "required": ["freq_mhz"],
        },
        "fn": _signal_identify,
    },
    "meshtastic_sniff": {
        "description": (
            "Passively listen for Meshtastic LoRa packets using meshtastic-sniffer. "
            "Auto-detects connected SDR; if multiple radios are found, returns a list "
            "so you can ask the user which to use. Prints packets and stats in real-time. "
            "Duration is unlimited — use 1800 for 30 min, 3600 for 1 hour."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "freq_mhz":     {"type": "number",  "description": "Center frequency in MHz (default 906.875 — US LongFast)"},
                "duration_sec": {"type": "integer", "description": "Listen duration in seconds (default 60; use 1800 for 30 min)"},
                "device":       {"type": "string",  "description": "SDR to use: 'auto' (default), 'hackrf', 'rtlsdr', 'airspy', 'bladerf'"},
            },
            "required": [],
        },
        "fn": _meshtastic_sniff,
    },
    "adsb_scan": {
        "description": (
            "Decode ADS-B aircraft transponders at 1090 MHz. "
            "Auto-detects the best available radio+decoder: RTL-SDR+dump1090 preferred, "
            "HackRF+modes_rx (gr-air-modes) as fallback. "
            "Returns aircraft list with ICAO, callsign, position, altitude. "
            "If multiple viable combinations exist, returns a list so you can ask the user which to use."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "duration_sec": {"type": "integer", "description": "Scan duration in seconds (default 30)"},
                "device":       {"type": "string",  "description": "Radio to use: 'auto' (default), 'rtlsdr', 'rtlsdr:N' (device index N), 'hackrf'"},
            },
            "required": [],
        },
        "fn": _adsb_scan,
    },
    "gsm_scan": {
        "description": "Scan for GSM base stations using grgsm_scanner. Returns active ARFCNs and cell IDs.",
        "schema": {
            "type": "object",
            "properties": {
                "band": {"type": "string", "enum": ["GSM850", "GSM900", "DCS1800", "PCS1900"],
                         "description": "GSM band to scan (default GSM850 for US)"},
            },
            "required": [],
        },
        "fn": _gsm_scan,
    },
    "flowgraph_run": {
        "description": "Execute a GNU Radio flowgraph (.grc or .py) and return its stdout output.",
        "schema": {
            "type": "object",
            "properties": {
                "grc_file":    {"type": "string",  "description": "Path to .grc or .py flowgraph file"},
                "timeout_sec": {"type": "integer", "description": "Maximum run time in seconds (default 30)"},
            },
            "required": ["grc_file"],
        },
        "fn": _flowgraph_run,
    },

    # Exercise guide (2)
    "exercise_list": {
        "description": "List available SDR exercises, optionally filtered by skill level.",
        "schema": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["Beginner", "Intermediate", "Advanced"],
                          "description": "Filter by level (optional)"},
            },
            "required": [],
        },
        "fn": _exercise_list,
    },
    "exercise_get": {
        "description": "Get the step-by-step instructions for a specific exercise.",
        "schema": {
            "type": "object",
            "properties": {
                "exercise_id": {"type": "string", "description": "Exercise ID from exercise_list (e.g. ex01_fm)"},
            },
            "required": ["exercise_id"],
        },
        "fn": _exercise_get,
    },

    # Self-update (2)
    "update_status": {
        "description": "Check whether AI for Dragons is up to date. Fetches the latest commit from GitHub and reports what has changed since the installed version. Call this when the user asks 'are there updates?' or 'what version am I running?'.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "fn": _update_status,
    },
    "self_update": {
        "description": "Pull the latest AI for Dragons code from GitHub, reinstall it into the running venv, run a smoke test, and restart the agent. Call this when the user asks to update, upgrade, or install the latest version.",
        "schema": {
            "type": "object",
            "properties": {
                "branch":  {"type": "string",  "description": "Git branch to pull from (default: main)"},
                "restart": {"type": "boolean", "description": "Restart the agent after updating (default: true)"},
            },
            "required": [],
        },
        "fn": _self_update,
    },
}


# ── Dispatcher ─────────────────────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> str:
    spec = TOOL_REGISTRY[name]
    try:
        return spec["fn"](args)
    except Exception as e:
        return f"Tool error [{name}]: {e}"
