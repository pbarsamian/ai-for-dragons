"""
RTL-SDR CLI wrappers for sdr-mcp.
Wraps: rtl_test (enumerate), rtl_sdr (capture IQ), rtl_power (spectrum survey)

RTL-SDR devices are independent USB receivers — they can run simultaneously
with HackRF One.  Each RTL-SDR is addressed by a device_index integer (0, 1, 2...).
Two tools cannot share the same device_index at the same time.
"""

import json
import os
import shutil
import subprocess
import tempfile
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


def rtlsdr_info(device_index=None) -> str:
    if not shutil.which("rtl_test"):
        return json.dumps({
            "status": "not_found",
            "message": "rtl_test not found — install: sudo apt install rtl-sdr",
        }, indent=2)

    # rtl_test -t exits rc=1 even when devices are present; parse both streams
    rc, out, err = _run(["rtl_test", "-t"], timeout=10)
    combined = out + err

    devices = []
    for line in combined.splitlines():
        stripped = line.strip()
        # Device lines look like: "  0:  Realtek, RTL2838UHIDIR, SN: 00000001"
        if stripped and stripped[0].isdigit() and ":" in stripped:
            parts = stripped.split(":", 1)
            try:
                idx = int(parts[0].strip())
                desc = parts[1].strip()
                devices.append({"index": idx, "description": desc})
            except (ValueError, IndexError):
                pass

    if device_index is not None:
        match = next((d for d in devices if d["index"] == int(device_index)), None)
        if match:
            return json.dumps({"status": "found", **match}, indent=2)
        return json.dumps({
            "status": "not_found",
            "device_index": device_index,
            "device_count": len(devices),
            "devices": devices,
        }, indent=2)

    return json.dumps({
        "status": "ok",
        "device_count": len(devices),
        "devices": devices,
    }, indent=2)


def rtlsdr_capture(
    freq_mhz: float,
    duration_sec: int = 10,
    device_index: int = 0,
    sample_rate_msps: float = 2.048,
    output_path: str | None = None,
) -> str:
    if not shutil.which("rtl_sdr"):
        return json.dumps({
            "status": "not_found",
            "message": "rtl_sdr not found — install: sudo apt install rtl-sdr",
        }, indent=2)

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(
            CAPTURE_DIR, f"rtlsdr{device_index}_{freq_mhz:.3f}MHz_{ts}.iq"
        )

    freq_hz = int(freq_mhz * 1_000_000)
    sample_rate_hz = int(sample_rate_msps * 1_000_000)
    num_samples = sample_rate_hz * duration_sec

    cmd = [
        "rtl_sdr",
        "-d", str(device_index),
        "-f", str(freq_hz),
        "-s", str(sample_rate_hz),
        "-n", str(num_samples),
        output_path,
    ]

    rc, out, err = _run(cmd, timeout=duration_sec + 15)

    if rc != 0 and not os.path.exists(output_path):
        return json.dumps({
            "status": "error",
            "device_index": device_index,
            "freq_mhz": freq_mhz,
            "error": (err or out)[:400],
        }, indent=2)

    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    return json.dumps({
        "status": "ok",
        "device_index": device_index,
        "freq_mhz": freq_mhz,
        "sample_rate_msps": sample_rate_msps,
        "duration_sec": duration_sec,
        "output_path": output_path,
        "file_size_bytes": file_size,
        "format": "uint8 interleaved IQ (RTL-SDR native — not float32)",
    }, indent=2)


def rtlsdr_power(
    freq_min_mhz: float,
    freq_max_mhz: float,
    device_index: int = 0,
    integration_sec: int = 10,
) -> str:
    if not shutil.which("rtl_power"):
        return json.dumps({
            "status": "not_found",
            "message": "rtl_power not found — install: sudo apt install rtl-sdr",
        }, indent=2)

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp.close()
    tmpfile = tmp.name

    try:
        freq_min_hz = int(freq_min_mhz * 1_000_000)
        freq_max_hz = int(freq_max_mhz * 1_000_000)
        cmd = [
            "rtl_power",
            "-d", str(device_index),
            "-f", f"{freq_min_hz}:{freq_max_hz}:125000",
            "-i", "1",
            "-e", f"{integration_sec}s",
            tmpfile,
        ]

        rc, out, err = _run(cmd, timeout=integration_sec + 20)

        signals = []
        try:
            with open(tmpfile, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(",")
                    # CSV columns: date, time, freq_low, freq_high, freq_step, samples, power...
                    if len(parts) < 7:
                        continue
                    try:
                        freq_low = float(parts[2])
                        freq_step = float(parts[4])
                        power_values = [float(x) for x in parts[6:] if x.strip()]
                        for i, pwr in enumerate(power_values):
                            freq_hz = freq_low + i * freq_step
                            signals.append({"freq_mhz": round(freq_hz / 1e6, 4), "power_db": round(pwr, 1)})
                    except (ValueError, IndexError):
                        pass
        except OSError:
            pass

        signals.sort(key=lambda s: s["power_db"], reverse=True)

        return json.dumps({
            "status": "ok",
            "device_index": device_index,
            "freq_range_mhz": [freq_min_mhz, freq_max_mhz],
            "integration_sec": integration_sec,
            "total_bins": len(signals),
            "top_signals": signals[:20],
        }, indent=2)

    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass
