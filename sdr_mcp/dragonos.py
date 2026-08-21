"""
DragonOS tool integrations for sdr-mcp.
Wraps: meshtastic-sniffer, dump1090, grgsm_scanner, GNU Radio
"""

import json
import os
import re
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


# Known RTL-SDR USB vendor:product IDs
_RTL_USB_IDS = {"0bda:2832", "0bda:2838", "0bda:2820", "0bda:2840", "1d19:1101"}


def _detect_rtlsdr() -> list[dict]:
    """
    Detect RTL-SDR devices.
    Method 1: rtl_test (gives device index + description)
    Method 2: lsusb USB-ID match (fallback when rtl_test not installed)
    """
    # Method 1 — try both common binary names
    rtl_bin = shutil.which("rtl_test") or shutil.which("rtl-test")
    if rtl_bin:
        _, out, err = _run([rtl_bin, "-t"], timeout=10)
        devices: list[dict] = []
        for line in (out + err).splitlines():
            s = line.strip()
            # rtl_test prints device list as "  0:  Realtek..." — after strip: "0:  Realtek..."
            if s and s[0].isdigit() and ":" in s:
                try:
                    raw_idx, desc = s.split(":", 1)
                    raw_idx = raw_idx.strip()
                    if raw_idx.isdigit():
                        devices.append({
                            "type": "rtlsdr",
                            "driver_flag": "--rtlsdr",
                            "description": f"RTL-SDR #{raw_idx}: {desc.strip()}",
                            "index": int(raw_idx),
                        })
                except (ValueError, IndexError):
                    pass
        if devices:
            return devices

    # Method 2 — lsusb: works even when rtl-sdr tools are not installed
    if shutil.which("lsusb"):
        _, out, _ = _run(["lsusb"], timeout=5)
        devices = []
        idx = 0
        for line in out.splitlines():
            lower = line.lower()
            if any(uid in lower for uid in _RTL_USB_IDS) or "rtl283" in lower:
                # Line format: "Bus NNN Device NNN: ID xxxx:xxxx Description"
                m = re.search(r"\bID\s+[\da-f]+:[\da-f]+\s+(.+)", line, re.IGNORECASE)
                desc = m.group(1).strip() if m else "RTL-SDR"
                devices.append({
                    "type": "rtlsdr",
                    "driver_flag": "--rtlsdr",
                    "description": f"RTL-SDR #{idx}: {desc}",
                    "index": idx,
                })
                idx += 1
        if devices:
            return devices

    return []


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

    # RTL-SDR — try rtl_test first, then lsusb as fallback
    found.extend(_detect_rtlsdr())

    # Airspy — airspy_info prints "airspy" in its version text even with no device.
    # Only count it as connected if a serial number appears in the output.
    if shutil.which("airspy_info"):
        rc, out, _ = _run(["airspy_info"], timeout=5)
        if rc == 0 and "serial" in out.lower():
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


def _adsb_dump1090(duration_sec: int, dev_idx: int = 0, dev_serial: str = "") -> str:
    """ADS-B via readsb/dump1090 + RTL-SDR. Writes aircraft.json every ~1s, polls it."""
    import tempfile
    import shutil as _shutil

    dump1090 = (shutil.which("readsb")
                or shutil.which("dump1090")
                or shutil.which("dump1090-fa")
                or shutil.which("dump1090-mutability"))
    if not dump1090:
        return json.dumps({
            "status": "tool_not_found",
            "message": "ADS-B decoder not found.",
            "install": "sudo apt install readsb",
        }, indent=2)

    bin_name = os.path.basename(dump1090)
    tmpdir = tempfile.mkdtemp(prefix="dump1090_")
    aircraft_file = os.path.join(tmpdir, "aircraft.json")

    # readsb (wiedehopf build) uses --device-type to select SDR type and
    # --rtlsdr-device to select which RTL-SDR by index or serial name.
    # --device and --device-index are aliases for --device-type and don't
    # accept a numeric index.
    if "readsb" in bin_name:
        rtl_dev = dev_serial if dev_serial else str(dev_idx)
        cmd = [dump1090,
               "--device-type", "rtlsdr",
               "--rtlsdr-device", rtl_dev,
               "--gain", "49.6",
               "--quiet",
               "--write-json", tmpdir,
               "--auto-exit", str(duration_sec)]
        start = time.time()
        aircraft: dict = {}
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            while time.time() - start < duration_sec + 5:
                if proc.poll() is not None:
                    # Normal exit via --auto-exit, or error
                    err = (proc.stderr.read() or "").strip()[:400]
                    if proc.returncode == 0:
                        break  # clean exit — read final aircraft.json below
                    _shutil.rmtree(tmpdir, ignore_errors=True)
                    return json.dumps({
                        "status": "error",
                        "message": "readsb exited early — RTL-SDR may be busy or unplugged.",
                        "detail": err,
                        "hint": "Run: readsb --device-type rtlsdr --help 2>&1 | head -30",
                    }, indent=2)
                if os.path.exists(aircraft_file):
                    try:
                        with open(aircraft_file) as f:
                            data = json.load(f)
                        for ac in data.get("aircraft", []):
                            icao = ac.get("hex", "")
                            if icao:
                                aircraft[icao] = ac
                        elapsed = int(time.time() - start)
                        print(f"\n  [adsb +{elapsed}s] {len(aircraft)} aircraft", flush=True)
                    except (json.JSONDecodeError, OSError):
                        pass
                time.sleep(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            # Final read of aircraft.json before cleanup
            if os.path.exists(aircraft_file):
                try:
                    with open(aircraft_file) as f:
                        data = json.load(f)
                    for ac in data.get("aircraft", []):
                        icao = ac.get("hex", "")
                        if icao:
                            aircraft[icao] = ac
                except (json.JSONDecodeError, OSError):
                    pass
            _shutil.rmtree(tmpdir, ignore_errors=True)

        return json.dumps({
            "status": "complete",
            "backend": "readsb+rtlsdr",
            "freq_mhz": 1090.0,
            "duration_sec": duration_sec,
            "aircraft_seen": len(aircraft),
            "aircraft": list(aircraft.values())[:30],
        }, indent=2)

    # dump1090-fa and other variants: use standard device-selection flags
    cmd = [dump1090, "--quiet", "--net", "--write-json", tmpdir]
    if dev_serial:
        cmd += ["--device-serial", dev_serial]
    else:
        cmd += ["--device-index", str(dev_idx)]

    start = time.time()
    aircraft: dict = {}
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        while time.time() - start < duration_sec:
            if proc.poll() is not None:
                err = (proc.stderr.read() or "").strip()[:400]
                _shutil.rmtree(tmpdir, ignore_errors=True)
                return json.dumps({
                    "status": "error",
                    "message": "ADS-B decoder exited early — RTL-SDR may be busy or unplugged.",
                    "detail": err,
                }, indent=2)
            if os.path.exists(aircraft_file):
                try:
                    with open(aircraft_file) as f:
                        data = json.load(f)
                    for ac in data.get("aircraft", []):
                        icao = ac.get("hex", "")
                        if icao:
                            aircraft[icao] = ac
                    elapsed = int(time.time() - start)
                    print(f"\n  [adsb +{elapsed}s] {len(aircraft)} aircraft ({bin_name})", flush=True)
                except (json.JSONDecodeError, OSError):
                    pass
            time.sleep(2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _shutil.rmtree(tmpdir, ignore_errors=True)

    return json.dumps({
        "status": "complete",
        "backend": f"rtlsdr+{bin_name}",
        "freq_mhz": 1090.0,
        "duration_sec": duration_sec,
        "aircraft_seen": len(aircraft),
        "aircraft": list(aircraft.values())[:30],
    }, indent=2)


def _adsb_modes_rx(duration_sec: int) -> str:
    """ADS-B via gr-air-modes (modes_rx) + HackRF through osmosdr."""
    import re
    import select as _select

    modes_rx = shutil.which("modes_rx")
    if not modes_rx:
        return json.dumps({
            "status": "tool_not_found",
            "message": "modes_rx (gr-air-modes) not found.",
            "install": "sudo apt install gr-air-modes",
            "note": "gr-air-modes decodes ADS-B using HackRF via GNU Radio osmosdr.",
        }, indent=2)

    # modes_rx with osmosdr source — supports HackRF, RTL-SDR, Airspy, etc.
    # --args "hackrf=0" pins to HackRF when multiple SDRs are connected.
    # Gain 40 is a safe starting point; HackRF has IF+BB gain stages.
    cmd = [modes_rx, "-s", "osmocom", "--args", "hackrf=0", "-g", "40"]

    # Regex patterns for modes_rx text output (robust across versions)
    ICAO_RE = re.compile(r'\bAA[=:\s]+([0-9a-fA-F]{6})\b', re.I)
    ALT_RE  = re.compile(r'\bAlt(?:itude)?[=:\s]+(-?\d+)', re.I)
    LAT_RE  = re.compile(r'\bLat(?:itude)?[=:\s]+([-\d.]+)', re.I)
    LON_RE  = re.compile(r'\bLon(?:gitude)?[=:\s]+([-\d.]+)', re.I)
    CALL_RE = re.compile(r'\b(?:Callsign|Ident)[=:\s]+([A-Z0-9]{3,8})\b', re.I)

    start = time.time()
    aircraft: dict = {}

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while time.time() - start < duration_sec:
            ready, _, _ = _select.select([proc.stdout, proc.stderr], [], [], 5.0)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            for fd in ready:
                line = fd.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                line = line.strip()
                if not line:
                    continue

                m_icao = ICAO_RE.search(line)
                if m_icao:
                    icao = m_icao.group(1).lower()
                    ac = aircraft.setdefault(icao, {"hex": icao})
                    m = ALT_RE.search(line)
                    if m:
                        ac["altitude"] = int(m.group(1))
                    m = LAT_RE.search(line)
                    if m:
                        ac["lat"] = float(m.group(1))
                    m = LON_RE.search(line)
                    if m:
                        ac["lon"] = float(m.group(1))
                    m = CALL_RE.search(line)
                    if m:
                        ac["flight"] = m.group(1)
                    elapsed = int(time.time() - start)
                    print(
                        f"\n  [adsb +{elapsed}s] {icao}"
                        f" {ac.get('flight', '')}"
                        f" alt={ac.get('altitude', '?')}",
                        flush=True,
                    )
                elif fd is proc.stderr and line:
                    # Show stderr startup messages (e.g. gain applied, device opened)
                    elapsed = int(time.time() - start)
                    print(f"\n  [modes_rx +{elapsed}s] {line}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return json.dumps({
        "status": "complete",
        "backend": "hackrf+modes_rx",
        "freq_mhz": 1090.0,
        "duration_sec": duration_sec,
        "aircraft_seen": len(aircraft),
        "aircraft": list(aircraft.values())[:30],
    }, indent=2)


def _adsb_rtl_adsb(duration_sec: int, dev_idx: int = 0, dev_serial: str = "") -> str:
    """ADS-B via rtl_adsb (rtl-sdr package). Reads RTL-SDR directly, outputs *hex; frames."""
    rtl_adsb_bin = shutil.which("rtl_adsb")
    if not rtl_adsb_bin:
        return json.dumps({"status": "tool_not_found", "message": "rtl_adsb not found."}, indent=2)

    # rtl_adsb -d accepts index OR serial string (uses librtlsdr device selection)
    rtl_dev = dev_serial if dev_serial else str(dev_idx)
    cmd = [rtl_adsb_bin, "-d", rtl_dev, "-g", "0"]

    start = time.time()
    aircraft: dict = {}
    frame_count = 0

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        while time.time() - start < duration_sec:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            # ADS-B frames arrive as: *<UPPERCASE_HEX>;
            if not (line.startswith("*") and line.endswith(";")):
                continue
            hex_data = line[1:-1]
            frame_count += 1
            # DF17 (ADS-B extended squitter) = 14 bytes = 28 hex chars
            # Top 5 bits of byte 0 = 17 (10001x) → first byte 0x88-0x8F
            if len(hex_data) == 28:
                try:
                    df = int(hex_data[:2], 16) >> 3
                except ValueError:
                    continue
                if df == 17:
                    icao = hex_data[2:8].lower()
                    ac = aircraft.setdefault(icao, {"hex": icao})
                    try:
                        tc = int(hex_data[8:10], 16) >> 3  # type code from ME field
                        if 1 <= tc <= 4:
                            ac["has_callsign"] = True
                        elif 9 <= tc <= 22:
                            ac["has_position"] = True
                    except ValueError:
                        pass
                    elapsed = int(time.time() - start)
                    print(f"\n  [adsb +{elapsed}s] {icao}  ({len(aircraft)} unique aircraft)", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not aircraft:
        return json.dumps({
            "status": "no_aircraft",
            "frames_decoded": frame_count,
            "duration_sec": duration_sec,
            "message": "No ADS-B aircraft detected. Ensure antenna is connected and aircraft are within ~100-200 nm.",
        }, indent=2)

    return json.dumps({
        "status": "complete",
        "backend": "rtl_adsb",
        "freq_mhz": 1090.0,
        "duration_sec": duration_sec,
        "frames_decoded": frame_count,
        "aircraft_seen": len(aircraft),
        "aircraft": list(aircraft.values()),
    }, indent=2)


def adsb_scan(duration_sec: int = 30, device: str = "auto") -> str:
    """
    Decode ADS-B aircraft transponders at 1090 MHz.

    device="auto"      — detect connected radios; prefer RTL-SDR+dump1090
                         (purpose-built for ADS-B), fall back to HackRF+modes_rx.
                         If only one radio is attached it is used automatically.
                         If multiple radios with multiple viable backends are
                         found, returns a list so the caller can ask the user.
    device="rtlsdr"    — force RTL-SDR device 0
    device="rtlsdr:N"  — force RTL-SDR device index N
    device="hackrf"    — force HackRF via gr-air-modes (modes_rx)
    """
    if device == "auto":
        radios = detect_radios()
        if not radios:
            return json.dumps({
                "status": "no_radio",
                "message": "No SDR device detected. Check USB connections.",
            }, indent=2)

        rtlsdrs = [r for r in radios if r["type"] == "rtlsdr"]
        hackrfs  = [r for r in radios if r["type"] == "hackrf"]

        # Decoder priority for RTL-SDR ADS-B:
        #   1. dump1090 / dump1090-fa / dump1090-mutability — proper --device-serial support
        #   2. rtl_adsb (rtl-sdr package) — drives RTL-SDR directly, outputs *hex; frames
        #   3. readsb — last resort (DragonOS build lacks RTL-SDR driver support)
        dump1090_bin = (shutil.which("dump1090")
                        or shutil.which("dump1090-fa")
                        or shutil.which("dump1090-mutability"))
        readsb_bin   = shutil.which("readsb")
        rtl_adsb_bin = shutil.which("rtl_adsb")
        modes_rx_bin = shutil.which("modes_rx")

        if rtlsdrs and (dump1090_bin or rtl_adsb_bin or readsb_bin):
            # Prefer stratux:1090 (pre-filtered for 1090 MHz ADS-B)
            preferred = next(
                (r for r in rtlsdrs if "1090" in r.get("description", "").lower()),
                rtlsdrs[0]
            )
            serial = ""
            desc = preferred.get("description", "")
            if "SN:" in desc:
                serial = desc.split("SN:")[-1].strip()

            if dump1090_bin:
                print(f"\n  [auto] Using {preferred['description']} with {os.path.basename(dump1090_bin)}", flush=True)
                return _adsb_dump1090(duration_sec, dev_idx=preferred.get("index", 0), dev_serial=serial)
            elif rtl_adsb_bin:
                print(f"\n  [auto] Using {preferred['description']} with rtl_adsb", flush=True)
                return _adsb_rtl_adsb(duration_sec, dev_idx=preferred.get("index", 0), dev_serial=serial)
            else:
                print(f"\n  [auto] Using {preferred['description']} with readsb", flush=True)
                return _adsb_dump1090(duration_sec, dev_idx=preferred.get("index", 0), dev_serial=serial)

        if hackrfs and modes_rx_bin:
            print(f"\n  [auto] Using HackRF with modes_rx", flush=True)
            return _adsb_modes_rx(duration_sec)

        # Hardware present but missing software
        missing = []
        if rtlsdrs and not any([dump1090_bin, rtl_adsb_bin, readsb_bin]):
            missing.append("RTL-SDR detected but no ADS-B decoder found (sudo apt install rtl-sdr)")
        if hackrfs and not modes_rx_bin:
            missing.append("HackRF detected but modes_rx missing (sudo apt install gr-air-modes)")
        if not rtlsdrs and not hackrfs:
            missing.append("No compatible SDR radio detected")
        return json.dumps({
            "status": "tool_not_found",
            "message": "Required decoder software not found.",
            "details": missing,
        }, indent=2)

    # Explicit device selection
    if device.startswith("rtlsdr"):
        parts = device.split(":", 1)
        idx = int(parts[1]) if len(parts) > 1 else 0
        return _adsb_dump1090(duration_sec, dev_idx=idx)

    if device == "hackrf":
        return _adsb_modes_rx(duration_sec)

    return json.dumps({"status": "error", "message": f"Unknown device '{device}'. Use 'auto', 'rtlsdr', 'rtlsdr:N', or 'hackrf'."}, indent=2)


def uat_scan(duration_sec: int = 30, device: str = "auto") -> str:
    """
    Decode 978 MHz UAT (Universal Access Transceiver) traffic using the stratux:978 RTL-SDR.
    Captures ADS-B Out, FIS-B weather, and TIS-B traffic — US only.

    Requires dump978-fa (build with: bash install-dump978.sh).
    Best with a stratux:978 dongle (pre-filtered for 978 MHz).

    device="auto"      — prefer stratux:978, fall back to first RTL-SDR available
    device="rtlsdr:N"  — use RTL-SDR device index N explicitly
    """
    dump978_bin = shutil.which("dump978-fa") or shutil.which("dump978")
    rtl_sdr_bin = shutil.which("rtl_sdr")

    if not dump978_bin:
        return json.dumps({
            "status": "tool_not_found",
            "message": "dump978-fa not installed. Build with: bash install-dump978.sh",
        }, indent=2)
    if not rtl_sdr_bin:
        return json.dumps({
            "status": "tool_not_found",
            "message": "rtl_sdr not found. Install: sudo apt install rtl-sdr",
        }, indent=2)

    if device == "auto":
        rtlsdrs = _detect_rtlsdr()
        if not rtlsdrs:
            return json.dumps({"status": "no_radio", "message": "No RTL-SDR found for UAT decoding."}, indent=2)
        # Prefer device with "978" in description (stratux:978)
        dev = next(
            (r for r in rtlsdrs if "978" in r.get("description", "").lower()),
            rtlsdrs[0]
        )
        dev_idx = dev.get("index", 0)
        print(f"\n  [auto] Using {dev['description']} for UAT 978 MHz", flush=True)
    else:
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0

    start = time.time()
    messages = []

    # Pipe rtl_sdr raw IQ → dump978-fa for decoding
    # 978 MHz, 2.083334 MSPS (required sample rate for UAT), gain 48
    rtlsdr_cmd = [rtl_sdr_bin, "-f", "978000000", "-s", "2083334",
                  "-g", "48", "-d", str(dev_idx), "-"]
    dump978_flags = ["--raw-stdin"]
    if "dump978-fa" in os.path.basename(dump978_bin):
        dump978_flags.append("--json-stdout")
    dump978_cmd = [dump978_bin] + dump978_flags

    import select as _select
    proc_rtl = subprocess.Popen(rtlsdr_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    proc_dump = subprocess.Popen(
        dump978_cmd, stdin=proc_rtl.stdout,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    proc_rtl.stdout.close()

    try:
        while time.time() - start < duration_sec:
            ready, _, _ = _select.select([proc_dump.stdout], [], [], 2.0)
            if not ready:
                if proc_dump.poll() is not None:
                    break
                continue
            line = proc_dump.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line:
                messages.append(line)
                elapsed = int(time.time() - start)
                print(f"\n  [uat978 +{elapsed}s] {line[:120]}", flush=True)
    finally:
        for p in (proc_rtl, proc_dump):
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    if not messages:
        return json.dumps({
            "status": "no_traffic",
            "duration_sec": duration_sec,
            "message": "No UAT traffic detected on 978 MHz. UAT is US-only. Aircraft must be equipped and within ~100 nm.",
        }, indent=2)

    return json.dumps({
        "status": "ok",
        "duration_sec": duration_sec,
        "message_count": len(messages),
        "messages": messages[:50],
        "backend": f"rtlsdr+{os.path.basename(dump978_bin)}",
        "note": "Messages include ADS-B position, FIS-B weather, and TIS-B traffic. Use interpret_adsb to decode individual frames.",
    }, indent=2)


def gsm_scan(band: str = "GSM850", device: str = "auto") -> str:
    """
    Scan for GSM base stations using grgsm_scanner (gr-gsm).
    RTL-SDR only: grgsm_scanner uses the rtlsdr osmosdr source.
    HackRF cannot be used for this tool.

    device="auto"      — detect RTL-SDR automatically; error if none found.
    device="rtlsdr:N"  — use RTL-SDR device index N when multiple dongles present.
    """
    scanner = shutil.which("grgsm_scanner")
    if not scanner:
        return json.dumps({
            "status": "tool_not_found",
            "message": "grgsm_scanner not found.",
            "install": "sudo apt install gr-gsm",
        }, indent=2)

    # Detect radio
    if device == "auto":
        radios = detect_radios()
        rtlsdrs = [r for r in radios if r["type"] == "rtlsdr"]
        hackrfs  = [r for r in radios if r["type"] == "hackrf"]

        if not radios:
            return json.dumps({"status": "no_radio",
                "message": "No SDR device detected."}, indent=2)

        if not rtlsdrs:
            return json.dumps({
                "status": "no_compatible_radio",
                "message": (
                    "gsm_scan requires an RTL-SDR dongle (grgsm_scanner uses the rtlsdr driver). "
                    + ("A HackRF is connected but cannot be used for this tool." if hackrfs else "")
                ),
                "try_instead": {
                    "tool": "hackrf_sweep",
                    "args": {"freq_min_mhz": 869, "freq_max_mhz": 960},
                    "reason": (
                        "HackRF can show signal presence in the GSM850 band (869-960 MHz) "
                        "but cannot decode GSM — use this to confirm whether base stations are active, "
                        "then attach an RTL-SDR to run gsm_scan for full ARFCN decoding."
                    ),
                } if hackrfs else None,
            }, indent=2)

        if len(rtlsdrs) > 1:
            return json.dumps({
                "status": "multiple_radios",
                "message": "Multiple RTL-SDR devices found. Re-call with device='rtlsdr:N'.",
                "devices": [{"id": f"rtlsdr:{r.get('index', 0)}",
                              "description": r["description"]} for r in rtlsdrs],
            }, indent=2)

        dev_idx = rtlsdrs[0].get("index", 0)
        print(f"\n  [auto] Using {rtlsdrs[0]['description']}", flush=True)
    else:
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0

    # grgsm_scanner uses osmosdr args via -a; rtlsdr=N selects device index
    args_str = f"rtlsdr={dev_idx}"
    cmd = [scanner, "-b", band, "-a", args_str]
    rc, out, err = _run(cmd, timeout=90)

    arfcns = []
    for line in out.splitlines():
        if "ARFCN" in line or "MHz" in line:
            arfcns.append(line.strip())

    return json.dumps({
        "status": "complete",
        "band": band,
        "device": f"rtlsdr:{dev_idx}",
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
