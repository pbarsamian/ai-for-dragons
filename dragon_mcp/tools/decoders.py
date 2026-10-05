"""
Decoder tools for dragon_mcp FastMCP server.

Wraps: dump1090 (ADS-B), rtl_433 (ISM band), multimon-ng (POCSAG/FLEX/DTMF).

Each decoder can optionally start a live stream (MJPEG web view).
Stream URLs are returned immediately after launch.
"""

import json
import os
import socket
import subprocess
import time

# ── Shared helpers ─────────────────────────────────────────────────────────

def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


_stream_manager_instance = None


def _get_stream_manager():
    global _stream_manager_instance
    if _stream_manager_instance is None:
        from ..streams import StreamManager
        _stream_manager_instance = StreamManager()
    return _stream_manager_instance


# Running background decoder processes tracked by name
_running: dict[str, subprocess.Popen] = {}


def _kill(name: str) -> None:
    proc = _running.pop(name, None)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass


# ── dump1090 ───────────────────────────────────────────────────────────────

def dump1090_start(device_index: int = 0, gain: int = 40, stream: bool = False, pi_ip: str | None = None) -> str:
    """Start dump1090 to decode ADS-B (aircraft transponders) on 1090 MHz.

    Args:
        device_index: RTL-SDR dongle index (default 0).
        gain: Tuner gain in dB (0–49, default 40; use 0 for auto).
        stream: If True, also start a web view stream of the dump1090 map and return a URL.
        pi_ip: LAN IP of this Pi (auto-detected if omitted), used in returned stream URL.

    dump1090 runs in the background.  Call dump1090_stop to terminate.
    The built-in HTTP server (port 8080) serves an interactive aircraft map.
    """
    _kill("dump1090")
    cmd = [
        "/usr/bin/dump1090",
        "--net",
        "--quiet",
        "--device-index", str(device_index),
        "--gain", str(gain),
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _running["dump1090"] = proc
    except FileNotFoundError:
        return "dump1090 not found — install: sudo apt install dump1090-mutability"

    time.sleep(1)  # let dump1090 open its HTTP server
    ip = pi_ip or _local_ip()
    result: dict = {
        "status": "started",
        "map_url": f"http://{ip}:8080",
        "beast_port": 30005,
        "raw_port": 30002,
    }
    if stream:
        url = _get_stream_manager().start("dump1090", "mjpeg", ip)
        result["stream_url"] = url
    return json.dumps(result, indent=2)


def dump1090_stop() -> str:
    """Stop the background dump1090 ADS-B decoder and release the RTL-SDR device."""
    _kill("dump1090")
    _get_stream_manager().stop("dump1090")
    return json.dumps({"status": "stopped", "tool": "dump1090"}, indent=2)


# ── rtl_433 ────────────────────────────────────────────────────────────────

def rtl433_start(
    freq_mhz: float = 433.92,
    device_index: int = 0,
    stream: bool = False,
    pi_ip: str | None = None,
) -> str:
    """Start rtl_433 to decode ISM-band sensors (weather stations, tire pressure, doorbells, etc.).

    Args:
        freq_mhz: Center frequency in MHz (default 433.92 for EU ISM band; 915.0 for US).
        device_index: RTL-SDR dongle index (default 0).
        stream: If True, start a MJPEG web view of the terminal output and return a URL.
        pi_ip: LAN IP for the stream URL (auto-detected if omitted).

    Decoded packets are written to stderr / stdout.  rtl_433 runs in the background.
    Call rtl433_stop to terminate.
    """
    _kill("rtl433")
    cmd = [
        "/usr/bin/rtl_433",
        "-f", f"{int(freq_mhz * 1e6)}",
        "-d", str(device_index),
        "-F", "json",
    ]
    try:
        log_path = os.path.expanduser("~/sdr-captures/rtl433.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_file = open(log_path, "a")
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.DEVNULL)
        _running["rtl433"] = proc
    except FileNotFoundError:
        return "rtl_433 not found — install: sudo apt install rtl-433"

    ip = pi_ip or _local_ip()
    result: dict = {
        "status": "started",
        "freq_mhz": freq_mhz,
        "log_file": log_path,
        "note": "Decoded sensor packets appended to log_file as newline-delimited JSON.",
    }
    if stream:
        url = _get_stream_manager().start("rtl433", "mjpeg", ip)
        result["stream_url"] = url
    return json.dumps(result, indent=2)


def rtl433_stop() -> str:
    """Stop the background rtl_433 ISM-band sensor decoder."""
    _kill("rtl433")
    _get_stream_manager().stop("rtl433")
    return json.dumps({"status": "stopped", "tool": "rtl_433"}, indent=2)


# ── multimon-ng ────────────────────────────────────────────────────────────

def multimon_start(
    freq_mhz: float,
    modes: list[str] | None = None,
    device_index: int = 0,
    stream: bool = False,
    pi_ip: str | None = None,
) -> str:
    """Start multimon-ng to decode digital protocols (POCSAG, FLEX, DTMF, EAS, etc.).

    Args:
        freq_mhz: Center frequency in MHz to monitor.
        modes: List of decoders to enable (e.g. ["POCSAG512", "FLEX", "DTMF"]).
               Defaults to ["POCSAG512", "POCSAG1200", "FLEX"] when omitted.
        device_index: RTL-SDR dongle index (default 0).
        stream: If True, start a MJPEG web view and return a URL.
        pi_ip: LAN IP for the stream URL (auto-detected if omitted).

    Uses rtl_fm to pipe IQ into multimon-ng.  Runs in the background.
    Call multimon_stop to terminate.
    """
    _kill("multimon")
    if not modes:
        modes = ["POCSAG512", "POCSAG1200", "FLEX"]

    rtl_cmd = [
        "rtl_fm",
        "-f", str(int(freq_mhz * 1e6)),
        "-d", str(device_index),
        "-s", "22050",
        "-",
    ]
    mode_args: list[str] = []
    for m in modes:
        mode_args += ["-a", m]
    multi_cmd = ["multimon-ng", "-t", "raw"] + mode_args + ["-"]

    log_path = os.path.expanduser("~/sdr-captures/multimon.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    try:
        log_file = open(log_path, "a")
        rtl_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        multi_proc = subprocess.Popen(
            multi_cmd, stdin=rtl_proc.stdout, stdout=log_file, stderr=subprocess.DEVNULL
        )
        rtl_proc.stdout.close()
        _running["multimon_rtl"] = rtl_proc
        _running["multimon"] = multi_proc
    except FileNotFoundError as exc:
        return f"Required tool not found: {exc}.  Install: sudo apt install rtl-sdr multimon-ng"

    ip = pi_ip or _local_ip()
    result: dict = {
        "status": "started",
        "freq_mhz": freq_mhz,
        "decoders": modes,
        "log_file": log_path,
    }
    if stream:
        url = _get_stream_manager().start("multimon", "mjpeg", ip)
        result["stream_url"] = url
    return json.dumps(result, indent=2)


def multimon_stop() -> str:
    """Stop the background multimon-ng decoder and its rtl_fm feed."""
    _kill("multimon")
    _kill("multimon_rtl")
    _get_stream_manager().stop("multimon")
    return json.dumps({"status": "stopped", "tool": "multimon-ng"}, indent=2)
