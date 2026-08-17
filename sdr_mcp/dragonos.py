"""
DragonOS tool integrations for sdr-mcp.
Wraps: meshtastic-sniffer, dump1090, grgsm_scanner, GNU Radio
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime


def _run(cmd: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"


# Maps device type names to meshtastic-sniffer CLI flags
_DEVICE_FLAGS: dict[str, str] = {
    "hackrf":  "--hackrf",
    "rtlsdr":  "--rtlsdr",
    "airspy":  "--airspy",
    "bladerf": "--bladerf",
    "sdrplay": "--sdrplay",
    "usrp":    "--usrp",
}


def detect_radios() -> list[dict]:
    """
    Probe for connected SDR hardware.
    Returns a list of dicts: {type, driver_flag, description}.
    Checks HackRF, RTL-SDR (all indices), and Airspy.
    """
    found = []

    # HackRF — hackrf_info exits 0 only when a device is present
    if shutil.which("hackrf_info"):
        rc, out, _ = _run(["hackrf_info"], timeout=5)
        if rc == 0:
            serial = next(
                (ln.split(":", 1)[-1].strip() for ln in out.splitlines() if "Serial number" in ln),
                "unknown",
            )
            found.append({
                "type": "hackrf",
                "driver_flag": "--hackrf",
                "description": f"HackRF One (S/N {serial})",
            })

    # RTL-SDR — rtl_test -t exits rc=1 even when devices exist; parse both streams
    if shutil.which("rtl_test"):
        _, out, err = _run(["rtl_test", "-t"], timeout=10)
        for line in (out + err).splitlines():
            s = line.strip()
            if s and s[0].isdigit() and ":" in s:
                try:
                    idx, desc = s.split(":", 1)
                    found.append({
                        "type": "rtlsdr",
                        "driver_flag": "--rtlsdr",
                        "description": f"RTL-SDR #{idx.strip()}: {desc.strip()}",
                        "index": int(idx.strip()),
                    })
                except (ValueError, IndexError):
                    pass

    # Airspy — airspy_info exits 0 when a device is attached
    if shutil.which("airspy_info"):
        rc, out, _ = _run(["airspy_info"], timeout=5)
        if rc == 0 and "airspy" in out.lower():
            found.append({
                "type": "airspy",
                "driver_flag": "--airspy",
                "description": "Airspy",
            })

    return found


def signal_identify(freq_mhz: float, bandwidth_khz: float = 200.0) -> str:
    """
    Heuristic signal identification based on frequency.
    For best results, tune GQRX to the frequency first and observe the waterfall.
    """
    freq = freq_mhz

    candidates = []

    # Known frequency bands
    if 87.5 <= freq <= 108.0:
        candidates.append({"protocol": "FM Broadcast", "confidence": "high",
                           "tool": "SDRAngel / GQRX (WFM mode)", "bandwidth_khz": 200})
    if 108.0 <= freq <= 118.0:
        candidates.append({"protocol": "VOR/ILS Aviation Nav", "confidence": "medium",
                           "tool": "SDRAngel (AM mode)"})
    if 118.0 <= freq <= 137.0:
        candidates.append({"protocol": "Aviation Voice (AM)", "confidence": "high",
                           "tool": "SDRAngel / GQRX (AM mode)"})
    if 137.0 <= freq <= 138.0:
        candidates.append({"protocol": "NOAA Weather Satellite (APT)", "confidence": "high",
                           "tool": "SatDump", "note": "Passes overhead ~4x/day"})
    if 156.0 <= freq <= 174.0:
        candidates.append({"protocol": "VHF Marine / Public Safety", "confidence": "medium",
                           "tool": "SDRAngel (NFM mode)"})
    if 433.0 <= freq <= 434.8:
        candidates.append({"protocol": "ISM 433 MHz — likely OOK/FSK device", "confidence": "high",
                           "tool": "Universal Radio Hacker (URH)"})
    if 902.0 <= freq <= 928.0:
        candidates.append({"protocol": "Meshtastic LoRa (US 915 ISM)", "confidence": "high",
                           "tool": "meshtastic-sniffer or Meshtastic_SDR",
                           "note": "Default channel: 906.875 MHz LongFast"})
    if 1090.0 <= freq <= 1090.5:
        candidates.append({"protocol": "ADS-B Aircraft Transponders", "confidence": "very high",
                           "tool": "dump1090 --interactive --net"})
    if 1616.0 <= freq <= 1627.0:
        candidates.append({"protocol": "Iridium Satellite", "confidence": "high",
                           "tool": "gr-iridium"})
    if 2400.0 <= freq <= 2500.0:
        candidates.append({"protocol": "WiFi 2.4 GHz / Bluetooth / ZigBee", "confidence": "medium",
                           "tool": "GNU Radio + gr-ieee802-11 or gr-bluetooth"})

    if not candidates:
        candidates.append({"protocol": "Unknown — manual investigation needed",
                           "confidence": "none",
                           "suggestion": "Tune GQRX to this frequency and observe the waterfall. Use URH to capture and analyze."})

    return json.dumps({
        "freq_mhz": freq_mhz,
        "bandwidth_khz": bandwidth_khz,
        "candidates": candidates,
    }, indent=2)


def meshtastic_sniff(freq_mhz: float = 906.875, duration_sec: int = 60, device: str = "auto") -> str:
    """
    Listen for Meshtastic LoRa packets using meshtastic-sniffer.
    device="auto" probes for connected SDRs; if exactly one found it is used
    automatically; if multiple, returns a list so the caller can ask the user.
    Streams packets + stats heartbeats in real-time. No hard duration cap.
    """
    sniffer = shutil.which("meshtastic-sniffer")
    if not sniffer:
        # Try common build locations
        for path in [
            os.path.expanduser("~/meshtastic-sniffer/build/meshtastic-sniffer"),
            "/usr/local/bin/meshtastic-sniffer",
        ]:
            if os.path.isfile(path):
                sniffer = path
                break

    if not sniffer:
        return json.dumps({
            "status": "tool_not_found",
            "message": "meshtastic-sniffer not installed.",
            "install": [
                "git clone https://github.com/alphafox02/meshtastic-sniffer",
                "cd meshtastic-sniffer && mkdir build && cd build",
                "cmake .. && make -j$(nproc)",
            ],
            "alternative": "Use Meshtastic_SDR with GNU Radio — see exercise ex11_meshtastic_rx",
        }, indent=2)

    # ── Resolve which SDR to use ──────────────────────────────────────────
    if device == "auto":
        radios = detect_radios()
        if not radios:
            return json.dumps({
                "status": "no_radio",
                "message": "No SDR device detected. Check USB connections and try again.",
            }, indent=2)
        if len(radios) > 1:
            return json.dumps({
                "status": "multiple_radios",
                "message": (
                    "Multiple SDR devices found. Re-call meshtastic_sniff with "
                    "device= set to your choice (e.g. 'hackrf', 'rtlsdr')."
                ),
                "devices": [{"id": r["type"], "description": r["description"]} for r in radios],
            }, indent=2)
        driver_flag = radios[0]["driver_flag"]
        print(f"\n  [auto] Using {radios[0]['description']}", flush=True)
    else:
        base = device.split(":")[0].lower()
        driver_flag = _DEVICE_FLAGS.get(base, f"--{base}")

    freq_hz = int(freq_mhz * 1e6)
    # meshtastic-sniffer (alphafox02) correct flags:
    #   --hackrf / --rtlsdr / --airspy  select SDR backend
    #   --center=<hz>   center frequency in Hz
    #   --rate=<sps>    sample rate (2 MSPS for single LongFast channel)
    #   --keys=default  decrypt with built-in default keys
    # JSON packets stream to stdout; [stats] heartbeat every 5s to stderr.
    # No --timeout flag — we terminate the process after duration_sec.
    cmd = [sniffer,
           driver_flag,
           f"--center={freq_hz}",
           "--rate=2000000",
           "--keys=default"]

    start = time.time()
    packets = []
    done = False

    import select

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while not done and time.time() - start < duration_sec:
            ready, _, _ = select.select([proc.stdout, proc.stderr], [], [], 5.0)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            for fd in ready:
                line = fd.readline()
                if not line:
                    if proc.poll() is not None:
                        done = True
                    continue
                line = line.strip()
                if fd is proc.stdout and line.startswith("{"):
                    try:
                        pkt = json.loads(line)
                        packets.append(pkt)
                        elapsed = int(time.time() - start)
                        print(f"\n  [meshtastic +{elapsed}s] {json.dumps(pkt, separators=(',', ':'))[:160]}",
                              flush=True)
                    except json.JSONDecodeError:
                        pass
                elif fd is proc.stderr and line:
                    # Stats heartbeat every 5s — shows HackRF is live and decoding
                    elapsed = int(time.time() - start)
                    print(f"\n  [+{elapsed}s] {line}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return json.dumps({
        "status": "complete",
        "freq_mhz": freq_mhz,
        "duration_sec": duration_sec,
        "packets_decoded": len(packets),
        "packets": packets[:20],
    }, indent=2)


def adsb_scan(duration_sec: int = 30) -> str:
    dump1090 = shutil.which("dump1090")
    if not dump1090:
        return json.dumps({
            "status": "tool_not_found",
            "message": "dump1090 not found. On DragonOS it should be pre-installed.",
            "install": "sudo apt install dump1090-mutability",
        }, indent=2)

    cmd = [dump1090, "--quiet", "--json", "--lat", "0", "--lon", "0"]
    start = time.time()
    aircraft = {}

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while time.time() - start < duration_sec:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                frame = json.loads(line)
                icao = frame.get("hex", "")
                if icao:
                    aircraft[icao] = frame
            except json.JSONDecodeError:
                pass
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    return json.dumps({
        "status": "complete",
        "freq_mhz": 1090.0,
        "duration_sec": duration_sec,
        "aircraft_seen": len(aircraft),
        "aircraft": list(aircraft.values())[:30],
    }, indent=2)


def gsm_scan(band: str = "GSM850") -> str:
    scanner = shutil.which("grgsm_scanner")
    if not scanner:
        return json.dumps({
            "status": "tool_not_found",
            "message": "grgsm_scanner not found.",
            "install": "sudo apt install gr-gsm",
        }, indent=2)

    rc, out, err = _run([scanner, "-b", band], timeout=90)

    # Parse ARFCN output
    arfcns = []
    for line in out.splitlines():
        if "ARFCN" in line or "MHz" in line:
            arfcns.append(line.strip())

    return json.dumps({
        "status": "complete",
        "band": band,
        "arfcns_found": len(arfcns),
        "output": arfcns or out.strip().splitlines()[:20],
        "note": "Use grgsm_livemon_headless --freq=<Hz> --gain=40 to monitor a found channel",
    }, indent=2)


def flowgraph_run(grc_file: str, timeout_sec: int = 30) -> str:
    if not os.path.exists(grc_file):
        return f"File not found: {grc_file}"

    if grc_file.endswith(".grc"):
        # Compile to Python first
        py_file = grc_file.replace(".grc", ".py")
        rc, out, err = _run(["grcc", "-d", os.path.dirname(grc_file) or ".", grc_file], timeout=30)
        if rc != 0:
            return f"grcc compile failed:\n{err}"
        run_file = py_file
    else:
        run_file = grc_file

    rc, out, err = _run(["python3", run_file], timeout=timeout_sec)

    return json.dumps({
        "status": "complete" if rc == 0 else "error",
        "file": grc_file,
        "returncode": rc,
        "stdout": out[:1000] if out else None,
        "stderr": err[:500] if err else None,
    }, indent=2)
