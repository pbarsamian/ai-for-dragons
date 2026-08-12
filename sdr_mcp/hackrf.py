"""
HackRF CLI wrappers for sdr-mcp.
Wraps: hackrf_info, hackrf_sweep, hackrf_transfer

IMPORTANT — Hardware exclusivity:
  The HackRF is a single-receiver device. Only one process can hold it at a time.
  If GQRX (or any other SDR app) is running and has the HackRF open, hackrf_sweep
  and hackrf_transfer will fail or hang trying to acquire the device.

  These functions detect that situation and return a clear error rather than hanging.
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
        return -1, "", f"Command not found: {cmd[0]} — is HackRF tools installed?"
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"


def _check_hackrf_free() -> str | None:
    """
    Check whether the HackRF is accessible.
    Returns None if free, or an error string if not.
    Uses Popen (not subprocess.run) so we can bound both communicate() calls.
    subprocess.run drains pipes without a timeout after its kill, which can hang
    indefinitely if the USB device is stuck in kernel D-state.
    """
    try:
        proc = subprocess.Popen(
            ["hackrf_info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = "", ""
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass  # D-state — abandon, return timeout error below
            return (
                "HackRF timed out during check — likely held by GQRX or another app.\n"
                "Click the GQRX stop button (■) to release the hardware, then retry."
            )

        rc = proc.returncode if proc.returncode is not None else -1
        if rc == 0:
            return None  # free and responding
        combined = (stderr + stdout).lower()
        if any(kw in combined for kw in ("busy", "in use", "claimed", "resource")):
            return (
                "HackRF is held by another application (likely GQRX).\n"
                "Click the GQRX stop button (■) to release the hardware, then retry."
            )
        if any(kw in combined for kw in ("not found", "no hackrf", "unable")):
            return (
                "HackRF not detected — check it is plugged into a blue USB 3.0 port.\n"
                f"Detail: {stderr.strip() or stdout.strip()}"
            )
        # Unknown non-zero exit — don't block, let the actual command report the error
        return None
    except FileNotFoundError:
        return "hackrf_info not found — try: sudo apt install hackrf"


def hackrf_info() -> str:
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
    busy = _check_hackrf_free()
    if busy:
        return busy

    freq_range = f"{int(freq_min_mhz)}:{int(freq_max_mhz)}"

    # hackrf_sweep runs forever by default — use a timed subprocess
    # rather than -n (not supported on all versions) to guarantee termination.
    # We collect 8 seconds of sweep data then kill it.
    SWEEP_DURATION = 8
    cmd = [
        "hackrf_sweep",
        "-f", freq_range,
        "-g", str(gain),
        "-l", str(gain),
        "-w", str(bin_width_hz),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = "", ""
        try:
            stdout, stderr = proc.communicate(timeout=SWEEP_DURATION)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                pass  # D-state — use whatever we have (empty strings)

        out = stdout
        err = stderr
        rc  = proc.returncode if proc.returncode is not None else 0

    except FileNotFoundError:
        return "hackrf_sweep not found — try: sudo apt install hackrf"

    # Check for device-busy errors in the output
    if not out and err:
        if any(kw in err.lower() for kw in ("busy", "in use", "claimed", "access")):
            return (
                "HackRF became busy during sweep — another app grabbed the device.\n"
                "Click the GQRX stop button (■), then retry the sweep."
            )
        return f"hackrf_sweep failed:\n{err}"

    # Parse CSV output → find top signals
    # Split on comma and strip each field to handle both "a, b" and "a,b" formats
    peaks = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:  # date, time, hz_low, hz_high, step, n_samples, 1+ dBm
            continue
        try:
            hz_low   = float(parts[2])
            hz_high  = float(parts[3])
            center   = (hz_low + hz_high) / 2
            dbm_vals = [float(x) for x in parts[6:] if x.strip()]
            if dbm_vals:
                peaks.append((center / 1e6, max(dbm_vals)))
        except (ValueError, IndexError):
            continue

    if not peaks:
        return (
            f"Sweep complete ({freq_min_mhz}–{freq_max_mhz} MHz) — no data parsed.\n"
            f"Raw output:\n{out[:500]}"
        )

    peaks.sort(key=lambda x: x[1], reverse=True)
    return json.dumps({
        "sweep_range_mhz": f"{freq_min_mhz}–{freq_max_mhz}",
        "bin_width_mhz": bin_width_hz / 1e6,
        "top_signals": [
            {"freq_mhz": round(f, 3), "power_dbm": round(p, 1)}
            for f, p in peaks[:10]
        ],
        "note": "Sweep uses HackRF directly. GQRX must be stopped or paused first.",
    }, indent=2)


def hackrf_capture(
    freq_mhz: float,
    sample_rate_msps: float = 8.0,
    duration_sec: float = 10.0,
    output_path: str | None = None,
) -> str:
    busy = _check_hackrf_free()
    if busy:
        return busy

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(CAPTURE_DIR, f"capture_{int(freq_mhz)}mhz_{ts}.iq")

    freq_hz = int(freq_mhz * 1e6)
    samp_hz = int(sample_rate_msps * 1e6)
    n_samps = int(duration_sec * samp_hz)

    cmd = ["hackrf_transfer",
           "-r", output_path,
           "-f", str(freq_hz),
           "-s", str(samp_hz),
           "-n", str(n_samps)]

    rc, out, err = _run(cmd, timeout=int(duration_sec) + 15)

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / 1e6
        return json.dumps({
            "status": "captured",
            "file": output_path,
            "size_mb": round(size_mb, 1),
            "freq_mhz": freq_mhz,
            "sample_rate_msps": sample_rate_msps,
            "duration_sec": duration_sec,
            "note": "Use hackrf_analyze to inspect this file.",
        }, indent=2)
    return f"Capture failed (rc={rc}):\n{err}"


def hackrf_analyze(iq_file: str) -> str:
    if not os.path.exists(iq_file):
        return f"File not found: {iq_file}"

    try:
        import numpy as np
        data = np.fromfile(iq_file, dtype=np.int8)
        if len(data) < 2:
            return "File too short to analyze"
        iq = data[0::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)

        N = min(len(iq), 65536)
        spectrum  = np.abs(np.fft.fftshift(np.fft.fft(iq[:N]))) ** 2
        power_db  = 10 * np.log10(spectrum + 1e-10)
        peak_idx  = np.argmax(power_db)
        peak_db   = float(power_db[peak_idx])
        noise_floor = float(np.percentile(power_db, 10))

        return json.dumps({
            "file": iq_file,
            "samples": len(iq),
            "peak_power_db": round(peak_db, 1),
            "noise_floor_db": round(noise_floor, 1),
            "dynamic_range_db": round(peak_db - noise_floor, 1),
            "peak_bin": int(peak_idx),
            "note": "Frequency axis depends on capture sample rate and center frequency.",
        }, indent=2)
    except ImportError:
        size_mb = os.path.getsize(iq_file) / 1e6
        return json.dumps({
            "file": iq_file,
            "size_mb": round(size_mb, 1),
            "note": "numpy not available — install it for spectral analysis",
        }, indent=2)


def hackrf_replay(
    iq_file: str,
    freq_mhz: float,
    sample_rate_msps: float = 8.0,
    tx_gain: int = 20,
) -> str:
    if not os.path.exists(iq_file):
        return f"File not found: {iq_file}"

    busy = _check_hackrf_free()
    if busy:
        return busy

    freq_hz = int(freq_mhz * 1e6)
    samp_hz = int(sample_rate_msps * 1e6)
    size_mb = os.path.getsize(iq_file) / 1e6
    est_sec = size_mb / (samp_hz * 2 / 1e6)

    cmd = ["hackrf_transfer",
           "-t", iq_file,
           "-f", str(freq_hz),
           "-s", str(samp_hz),
           "-x", str(tx_gain)]

    rc, out, err = _run(cmd, timeout=int(est_sec) + 30)

    return json.dumps({
        "status": "complete" if rc == 0 else "error",
        "file": iq_file,
        "freq_mhz": freq_mhz,
        "tx_gain_db": tx_gain,
        "est_duration_sec": round(est_sec, 1),
        "stderr": err[:200] if err else None,
    }, indent=2)
