"""
HackRF tools for dragon_mcp FastMCP server.

Hardware exclusivity: HackRF is a single-receiver device — only one process
can hold it at a time.  GQRX (GROUP_A) and HackRF CLI tools (GROUP_A) share
the same exclusive slot.  RTL-SDR devices (GROUP_B) are independent and can
run simultaneously with each other, but not alongside a HackRF tool.

GROUP_A  — HackRF One (hackrf_sweep, hackrf_transfer, GQRX when using HackRF)
GROUP_B  — RTL-SDR dongles (rtl_sdr, rtl_power, rtl_fm — independent per dongle)

Before running any GROUP_A tool, call _check_hackrf_free() which returns None
if the device is available, or an error string if it is held.
"""

import json
import os
import subprocess
import time
from datetime import datetime

CAPTURE_DIR = os.path.expanduser("~/sdr-captures")


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"


def _check_hackrf_free() -> str | None:
    """Return None if HackRF is free, or an error string if it is held."""
    try:
        proc = subprocess.Popen(
            ["hackrf_info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return (
                "HackRF timed out — likely held by GQRX or another app. "
                "Stop GQRX (click ■ in GQRX or call gqrx_stop), then retry."
            )
        rc = proc.returncode if proc.returncode is not None else -1
        if rc == 0:
            return None
        combined = (stderr + stdout).lower()
        if any(kw in combined for kw in ("busy", "in use", "claimed", "resource")):
            return (
                "HackRF is held by another process (likely GQRX). "
                "Click ■ in GQRX or call gqrx_stop, then retry."
            )
        if any(kw in combined for kw in ("not found", "no hackrf", "unable")):
            return f"HackRF not detected — check USB connection. Detail: {stderr.strip()}"
        return None
    except FileNotFoundError:
        return "hackrf_info not found — install: sudo apt install hackrf"


def hackrf_info() -> str:
    """Return hardware info for the connected HackRF One (serial, firmware, board rev)."""
    busy = _check_hackrf_free()
    if busy:
        return busy
    rc, out, err = _run(["hackrf_info"])
    if rc != 0 or not out:
        return f"HackRF not found or not connected.\n{err}"
    return out.strip()


def hackrf_sweep(
    freq_min_mhz: float,
    freq_max_mhz: float,
    gain: int = 32,
    bin_width_hz: int = 1_000_000,
) -> str:
    """Sweep a frequency range with the HackRF and return the top signals as JSON.

    Args:
        freq_min_mhz: Start frequency in MHz (e.g. 100).
        freq_max_mhz: End frequency in MHz (e.g. 1000).  Max 6000.
        gain: LNA gain in dB (0–40, default 32).
        bin_width_hz: Resolution bandwidth per bin in Hz (default 1 MHz).

    Returns JSON with noise_floor_dbm and top_signals list.
    HackRF must not be in use by GQRX — call gqrx_stop first if needed.
    """
    busy = _check_hackrf_free()
    if busy:
        return json.dumps({"status": "busy", "message": busy}, indent=2)

    SWEEP_DURATION = 8
    cmd = [
        "hackrf_sweep",
        "-f", f"{int(freq_min_mhz)}:{int(freq_max_mhz)}",
        "-g", str(gain),
        "-l", str(gain),
        "-w", str(bin_width_hz),
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = proc.communicate(timeout=SWEEP_DURATION)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
    except FileNotFoundError:
        return "hackrf_sweep not found — install: sudo apt install hackrf"

    if not stdout and stderr:
        return json.dumps({"status": "error", "stderr": stderr[:400]}, indent=2)

    bin_max: dict[float, float] = {}
    for line in stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            center = round((float(parts[2]) + float(parts[3])) / 2 / 1e6, 3)
            dbm_vals = [float(x) for x in parts[6:] if x.strip()]
            if dbm_vals:
                peak = max(dbm_vals)
                if center not in bin_max or peak > bin_max[center]:
                    bin_max[center] = peak
        except (ValueError, IndexError):
            continue

    if not bin_max:
        return json.dumps({"status": "no_data", "raw": stdout[:300]}, indent=2)

    all_powers = sorted(bin_max.values())
    noise_floor = all_powers[len(all_powers) // 2]
    peaks = sorted(bin_max.items(), key=lambda x: x[1], reverse=True)

    return json.dumps({
        "sweep_range_mhz": f"{freq_min_mhz}–{freq_max_mhz}",
        "bin_width_mhz": bin_width_hz / 1e6,
        "noise_floor_dbm": round(noise_floor, 1),
        "top_signals": [
            {"freq_mhz": f, "power_dbm": round(p, 1), "above_noise_db": round(p - noise_floor, 1)}
            for f, p in peaks[:10]
        ],
    }, indent=2)


def hackrf_transmit(iq_file: str, freq_mhz: float, sample_rate_msps: float = 8.0, tx_gain: int = 20) -> str:
    """Replay a captured IQ file from the HackRF transmitter (RF replay / signal replay).

    Args:
        iq_file: Path to raw 8-bit signed IQ file (.iq / .cs8).
        freq_mhz: Transmit center frequency in MHz.
        sample_rate_msps: Sample rate in mega-samples/sec (default 8.0).
        tx_gain: TX VGA gain in dB (0–47, default 20).  Use low values indoors.

    CAUTION: Transmitting on licensed frequencies without authorization is illegal.
    Ensure you operate within your jurisdiction and on authorized frequencies.
    """
    if not os.path.exists(iq_file):
        return f"File not found: {iq_file}"
    busy = _check_hackrf_free()
    if busy:
        return busy

    freq_hz = int(freq_mhz * 1e6)
    samp_hz = int(sample_rate_msps * 1e6)
    size_mb = os.path.getsize(iq_file) / 1e6
    est_sec = size_mb / (samp_hz * 2 / 1e6)

    cmd = ["hackrf_transfer", "-t", iq_file, "-f", str(freq_hz),
           "-s", str(samp_hz), "-x", str(tx_gain)]
    rc, out, err = _run(cmd, timeout=int(est_sec) + 30)

    return json.dumps({
        "status": "complete" if rc == 0 else "error",
        "file": iq_file,
        "freq_mhz": freq_mhz,
        "tx_gain_db": tx_gain,
        "est_duration_sec": round(est_sec, 1),
        "stderr": err[:200] if err else None,
    }, indent=2)
