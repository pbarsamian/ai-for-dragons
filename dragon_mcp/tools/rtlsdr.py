"""
RTL-SDR tools for dragon_mcp FastMCP server.

RTL-SDR devices are independent USB receivers (GROUP_B) — they can run
simultaneously with each other, addressed by device_index (0, 1, 2...).
Two tools cannot share the *same* device_index at the same time.
RTL-SDR devices can run concurrently alongside HackRF (GROUP_A) with no conflict.
"""

import json
import os
import subprocess
import time
from datetime import datetime

CAPTURE_DIR = os.path.expanduser("~/sdr-captures")


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"


def rtlsdr_info() -> str:
    """List all connected RTL-SDR devices with index, name, and serial number."""
    rc, out, err = _run(["rtl_test", "-t"], timeout=10)
    combined = out + err
    if not combined.strip():
        return json.dumps({"status": "none_found", "message": "No RTL-SDR devices detected."}, indent=2)

    devices = []
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped and stripped[0].isdigit() and ":" in stripped:
            parts = stripped.split(":", 1)
            try:
                idx = int(parts[0].strip())
                devices.append({"index": idx, "description": parts[1].strip()})
            except (ValueError, IndexError):
                pass

    if not devices:
        return json.dumps({"status": "none_found", "raw": combined[:300]}, indent=2)
    return json.dumps({"devices": devices}, indent=2)


def rtlsdr_power(
    freq_min_mhz: float,
    freq_max_mhz: float,
    device_index: int = 0,
    gain: int = 40,
    integration_sec: int = 5,
) -> str:
    """Scan a frequency range with an RTL-SDR dongle and return signal power by bin.

    Args:
        freq_min_mhz: Start frequency in MHz.
        freq_max_mhz: End frequency in MHz.  RTL-SDR max is ~1766 MHz.
        device_index: RTL-SDR device index (0 for first dongle).
        gain: Tuner gain in dB (0–49, default 40; use 0 for auto-gain).
        integration_sec: How many seconds to integrate power (default 5).

    Returns JSON with top_signals and noise_floor_dbm.
    Can run concurrently with HackRF tools on a different device.
    """
    if freq_max_mhz > 1766:
        return json.dumps({
            "status": "out_of_range",
            "message": f"RTL-SDR max frequency is 1766 MHz; requested {freq_max_mhz} MHz. Use hackrf_sweep for higher frequencies.",
        }, indent=2)

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    out_file = os.path.join(CAPTURE_DIR, f"rtl_power_{int(freq_min_mhz)}_{int(freq_max_mhz)}.csv")

    freq_range = f"{int(freq_min_mhz)}M:{int(freq_max_mhz)}M:1M"
    cmd = [
        "rtl_power",
        "-f", freq_range,
        "-d", str(device_index),
        "-g", str(gain),
        "-i", "1",
        "-1",  # single run (exit after one sweep)
        out_file,
    ]
    # rtl_power -1 exits after one sweep pass
    rc, out, err = _run(cmd, timeout=integration_sec + 15)

    if not os.path.exists(out_file):
        return json.dumps({"status": "error", "stderr": err[:400]}, indent=2)

    bin_max: dict[float, float] = {}
    try:
        with open(out_file) as f:
            for line in f:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                try:
                    hz_low = float(parts[2])
                    hz_high = float(parts[3])
                    center = round((hz_low + hz_high) / 2 / 1e6, 3)
                    dbm_vals = [float(x) for x in parts[6:] if x.strip()]
                    if dbm_vals:
                        peak = max(dbm_vals)
                        if center not in bin_max or peak > bin_max[center]:
                            bin_max[center] = peak
                except (ValueError, IndexError):
                    continue
    except OSError as exc:
        return json.dumps({"status": "read_error", "detail": str(exc)}, indent=2)

    if not bin_max:
        return json.dumps({"status": "no_data"}, indent=2)

    all_powers = sorted(bin_max.values())
    noise_floor = all_powers[len(all_powers) // 2]
    peaks = sorted(bin_max.items(), key=lambda x: x[1], reverse=True)

    return json.dumps({
        "sweep_range_mhz": f"{freq_min_mhz}–{freq_max_mhz}",
        "device_index": device_index,
        "noise_floor_dbm": round(noise_floor, 1),
        "top_signals": [
            {"freq_mhz": f, "power_dbm": round(p, 1), "above_noise_db": round(p - noise_floor, 1)}
            for f, p in peaks[:10]
        ],
    }, indent=2)


def rtlsdr_fm(freq_mhz: float, device_index: int = 0, duration_sec: int = 10) -> str:
    """Receive FM audio from a given frequency and save it as a WAV file.

    Args:
        freq_mhz: FM station frequency in MHz (e.g. 98.7).
        device_index: RTL-SDR device index (default 0).
        duration_sec: How many seconds of audio to capture (default 10).

    Returns the path to the saved WAV file.
    """
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(CAPTURE_DIR, f"fm_{int(freq_mhz * 10)}_{ts}.wav")

    freq_hz = int(freq_mhz * 1e6)
    cmd = [
        "rtl_fm",
        "-f", str(freq_hz),
        "-d", str(device_index),
        "-M", "fm",
        "-s", "200000",
        "-r", "48000",
        "-",
    ]
    pipe_cmd = ["sox", "-t", "raw", "-r", "48000", "-e", "signed", "-b", "16", "-c", "1",
                "-", out_file, "trim", "0", str(duration_sec)]

    try:
        rtl_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        sox_proc = subprocess.Popen(pipe_cmd, stdin=rtl_proc.stdout, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        rtl_proc.stdout.close()
        time.sleep(duration_sec + 2)
        rtl_proc.terminate()
        sox_proc.wait(timeout=5)
    except FileNotFoundError as exc:
        return f"Required tool not found: {exc}.  Install: sudo apt install rtl-sdr sox"
    except Exception as exc:
        return f"Error during FM capture: {exc}"

    if os.path.exists(out_file):
        size_kb = os.path.getsize(out_file) // 1024
        return json.dumps({
            "status": "captured",
            "file": out_file,
            "freq_mhz": freq_mhz,
            "duration_sec": duration_sec,
            "size_kb": size_kb,
        }, indent=2)
    return "FM capture failed — check device index and frequency."
