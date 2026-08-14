"""
DragonOS GUI application manager for sdr-mcp.

Handles start/stop of GUI tools that need a display (real or virtual),
and manages HackRF hardware exclusivity between them.

Hardware exclusivity groups:
  GROUP A — hold HackRF exclusively while running (only one can run at a time):
    gqrx, sdrangel, gnuradio (when flowgraph active), dump1090

  GROUP B — read IQ files only, no hardware conflict, can run any time:
    inspectrum, urh (file mode), satdump (file mode)

  GROUP C — need HackRF only during active recording/scan:
    urh (record mode), satdump (live mode)
    These conflict with GROUP A but not with each other if sequential.
"""

import os
import subprocess
import socket
import time
from typing import Optional


# ── Display helpers ────────────────────────────────────────────────────────

def _display() -> str:
    """Return best available display: real desktop if active, else Xvfb :99."""
    real = os.environ.get("DISPLAY", "")
    if real and real != ":99":
        # Check if a real X server is running on this display
        try:
            r = subprocess.run(
                ["xdpyinfo", "-display", real],
                capture_output=True, timeout=3
            )
            if r.returncode == 0:
                return real
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return ":99"


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"


def _is_running(process_name: str) -> bool:
    r = subprocess.run(["pgrep", "-x", process_name], capture_output=True)
    return r.returncode == 0


def _kill(process_name: str) -> bool:
    r = subprocess.run(["pkill", "-x", process_name], capture_output=True)
    return r.returncode == 0


def _launch_gui(cmd: list[str], display: Optional[str] = None) -> subprocess.Popen:
    """Launch a GUI app on the best available display."""
    env = {**os.environ, "DISPLAY": display or _display()}
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _wait_for_port(port: int, timeout_sec: int = 20) -> bool:
    """Wait until a TCP port is open. Returns True if it opens in time."""
    for _ in range(timeout_sec // 2):
        time.sleep(2)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except (ConnectionRefusedError, OSError):
            continue
    return False


# ── Hardware exclusivity ───────────────────────────────────────────────────

GROUP_A = {
    "gqrx":              ["gqrx"],
    "sdrangel":          ["sdrangel"],
    "sdrpp":             ["sdrpp", "SDRPlusPlus"],
    "cubicsdr":          ["CubicSDR", "cubicsdr"],
    "qspectrumanalyzer": ["qspectrumanalyzer", "QSpectrumAnalyzer"],
    "openwebrx":         ["openwebrx"],
    "dump1090":          ["dump1090", "dump1090-mutability", "dump1090-fa"],
    "gnuradio":          ["gnuradio-companion"],
}

def _stop_hardware_holders(exclude: str = "") -> list[str]:
    """
    Stop all GROUP A apps that currently hold the HackRF.
    exclude: app name to skip (e.g. don't stop GQRX if we're starting GQRX).
    Returns list of what was stopped.
    """
    stopped = []
    for app, processes in GROUP_A.items():
        if app == exclude:
            continue
        for proc in processes:
            if _is_running(proc):
                _kill(proc)
                stopped.append(proc)
    if stopped:
        time.sleep(2)  # brief wait for USB device to be released
    return stopped


def _hardware_status() -> dict:
    """Return which GROUP A apps are currently running."""
    running = {}
    for app, processes in GROUP_A.items():
        for proc in processes:
            if _is_running(proc):
                running[app] = proc
                break
    return running


# ── GQRX ──────────────────────────────────────────────────────────────────

def gqrx_stop() -> str:
    stopped = []

    # Stop headless systemd service
    r = subprocess.run(
        ["systemctl", "--user", "stop", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=8
    )
    if r.returncode == 0:
        stopped.append("sdr-gqrx-headless service")

    if _kill("gqrx"):
        stopped.append("gqrx process")

    if not stopped:
        return "GQRX was not running — HackRF is free."

    time.sleep(2)
    return f"GQRX stopped ({', '.join(stopped)}) — HackRF is free for sweep/capture."


def gqrx_start() -> str:
    # Stop anything else that might hold the HackRF
    others = _stop_hardware_holders(exclude="gqrx")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    # Start headless service
    r = subprocess.run(
        ["systemctl", "--user", "start", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        _launch_gui(["gqrx"])

    if _wait_for_port(7356, timeout_sec=6):
        return f"GQRX started{msg_others} — remote control ready on port 7356."
    return (
        f"GQRX started{msg_others}. "
        "Remote control port 7356 is not active — enable it in GQRX: Tools → Remote control → Start. "
        "Workflow complete."
    )


# ── SDRAngel ──────────────────────────────────────────────────────────────

def sdrangel_stop() -> str:
    if _kill("sdrangel"):
        time.sleep(2)
        return "SDRAngel stopped — HackRF is free."
    return "SDRAngel was not running."


def sdrangel_start() -> str:
    others = _stop_hardware_holders(exclude="sdrangel")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    if _is_running("sdrangel"):
        return f"SDRAngel is already running{msg_others}."

    _launch_gui(["sdrangel"])
    time.sleep(3)

    if _is_running("sdrangel"):
        return f"SDRAngel started{msg_others} — GUI is open on the display."
    return "SDRAngel launch attempted — check the display."


def sdrangel_status() -> str:
    if _is_running("sdrangel"):
        return "SDRAngel is running."
    return "SDRAngel is not running."


# ── GNU Radio Companion ────────────────────────────────────────────────────

def gnuradio_open(grc_file: str = "") -> str:
    """Open GNU Radio Companion, optionally with a specific .grc file."""
    cmd = ["gnuradio-companion"]
    if grc_file:
        if not os.path.exists(grc_file):
            return f"File not found: {grc_file}"
        cmd.append(grc_file)

    _launch_gui(cmd)
    time.sleep(2)

    if _is_running("gnuradio-companion"):
        return f"GNU Radio Companion opened{' with ' + grc_file if grc_file else ''}."
    return "GNU Radio Companion launch attempted — check the display."


def gnuradio_stop() -> str:
    if _kill("gnuradio-companion"):
        time.sleep(1)
        return "GNU Radio Companion closed."
    return "GNU Radio Companion was not running."


# ── inspectrum ─────────────────────────────────────────────────────────────

def inspectrum_open(iq_file: str) -> str:
    """Open inspectrum with an IQ file. No hardware conflict — reads files only."""
    if not os.path.exists(iq_file):
        return f"File not found: {iq_file}"

    _launch_gui(["inspectrum", iq_file])
    time.sleep(2)

    if _is_running("inspectrum"):
        return f"inspectrum opened with {iq_file}."
    return f"inspectrum launch attempted with {iq_file} — check the display."


def inspectrum_stop() -> str:
    if _kill("inspectrum"):
        return "inspectrum closed."
    return "inspectrum was not running."


# ── Universal Radio Hacker (URH) ───────────────────────────────────────────

def urh_open(iq_file: str = "") -> str:
    """Open URH, optionally with an IQ file. No hardware conflict in file mode."""
    cmd = ["urh"]
    if iq_file:
        if not os.path.exists(iq_file):
            return f"File not found: {iq_file}"
        cmd.append(iq_file)

    _launch_gui(cmd)
    time.sleep(2)

    if _is_running("urh"):
        return (
            f"URH opened{' with ' + iq_file if iq_file else ''}. "
            "Note: if you use URH's Record function it will need exclusive HackRF access — "
            "stop GQRX first if it is running."
        )
    return "URH launch attempted — check the display."


def urh_stop() -> str:
    if _kill("urh"):
        return "URH closed."
    return "URH was not running."


# ── SatDump ────────────────────────────────────────────────────────────────

def satdump_open(mode: str = "gui") -> str:
    """
    Open SatDump.
    mode='gui'  — opens the GUI, no hardware conflict until a live pipeline is started
    mode='live' — starts a live SDR pipeline (needs exclusive HackRF access)
    """
    if mode == "live":
        others = _stop_hardware_holders(exclude="satdump")
        msg_others = f" (stopped: {', '.join(others)})" if others else ""
    else:
        msg_others = ""

    cmd = ["satdump-ui"] if mode == "gui" else ["satdump"]
    _launch_gui(cmd)
    time.sleep(2)

    proc = "satdump-ui" if mode == "gui" else "satdump"
    if _is_running(proc):
        return f"SatDump started ({mode} mode){msg_others}."
    return f"SatDump launch attempted ({mode} mode) — check the display."


def satdump_stop() -> str:
    stopped = []
    for proc in ["satdump-ui", "satdump"]:
        if _kill(proc):
            stopped.append(proc)
    if stopped:
        time.sleep(1)
        return f"SatDump stopped ({', '.join(stopped)})."
    return "SatDump was not running."


# ── dump1090 ──────────────────────────────────────────────────────────────

def dump1090_start(duration_sec: int = 60, device: str = "hackrf") -> str:
    """
    Start dump1090 for ADS-B aircraft decoding.
    Opens web interface on http://localhost:8080.
    device="hackrf" — uses HackRF (dump1090 doesn't natively support HackRF well).
    device="rtlsdr" or "rtlsdr:N" — uses RTL-SDR device index N (recommended).
    """
    import shutil as _shutil

    dev_idx = None
    msg_others = ""
    if device.startswith("rtlsdr"):
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0
    else:
        # HackRF path — stop any hardware holders first and warn
        others = _stop_hardware_holders(exclude="dump1090")
        msg_others = f" (stopped: {', '.join(others)})" if others else ""

    if _is_running("dump1090"):
        return "dump1090 already running. Web interface: http://localhost:8080"

    for binary in ["dump1090", "dump1090-mutability", "dump1090-fa"]:
        if not _shutil.which(binary):
            continue
        cmd = [binary, "--interactive", "--net", "--gain", "-10"]
        if dev_idx is not None:
            cmd += ["--device-index", str(dev_idx)]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        if _is_running(binary):
            if dev_idx is not None:
                extra = f" (RTL-SDR device {dev_idx})"
            else:
                extra = (
                    " Note: dump1090 does not natively support HackRF — "
                    "use device='rtlsdr:N' for best results."
                )
            return (
                f"dump1090 started{msg_others}{extra}. "
                "Web map: http://localhost:8080  "
                "Terminal: run 'dump1090 --interactive' in a separate window."
            )

    return "dump1090 not found — try: sudo apt install dump1090-mutability"


def dump1090_stop() -> str:
    stopped = []
    for proc in ["dump1090", "dump1090-mutability", "dump1090-fa"]:
        if _kill(proc):
            stopped.append(proc)
    if stopped:
        time.sleep(2)
        return f"dump1090 stopped — HackRF is free."
    return "dump1090 was not running."


# ── System-wide status ────────────────────────────────────────────────────

def app_status() -> str:
    """Return status of all managed GUI apps and hardware state."""
    import json

    hw = _hardware_status()
    status = {
        "hardware_holder": hw if hw else "none — HackRF is free",
        "apps": {
            "gqrx":              _is_running("gqrx"),
            "sdrangel":          _is_running("sdrangel"),
            "sdrpp":             any(_is_running(p) for p in ["sdrpp", "SDRPlusPlus"]),
            "cubicsdr":          any(_is_running(p) for p in ["CubicSDR", "cubicsdr"]),
            "qspectrumanalyzer": any(_is_running(p) for p in ["qspectrumanalyzer", "QSpectrumAnalyzer"]),
            "gnuradio":          _is_running("gnuradio-companion"),
            "sigdigger":         _is_running("SigDigger"),
            "inspectrum":        _is_running("inspectrum"),
            "urh":               _is_running("urh"),
            "satdump":           any(_is_running(p) for p in ["satdump-ui", "satdump"]),
            "dump1090":          any(_is_running(p) for p in ["dump1090", "dump1090-mutability", "dump1090-fa"]),
            "fldigi":            _is_running("fldigi"),
            "wsjtx":             any(_is_running(p) for p in ["wsjtx", "WSJTX"]),
            "qsstv":             _is_running("qsstv"),
            "wireshark":         _is_running("wireshark"),
            "kismet":            _wait_for_port(2501, timeout_sec=2),
            "gpredict":          _is_running("gpredict"),
            "openwebrx":         _wait_for_port(8073, timeout_sec=2),
        },
        "ports": {
            "gqrx_remote_7356":       _wait_for_port(7356, timeout_sec=2) if _is_running("gqrx") else False,
            "openwebrx_8073":         _wait_for_port(8073, timeout_sec=2),
            "dump1090_web_8080":      _wait_for_port(8080, timeout_sec=2),
            "ollama_11434":           _wait_for_port(11434, timeout_sec=2),
        },
    }
    return json.dumps(status, indent=2)


# ── QSpectrumAnalyzer ─────────────────────────────────────────────────────

def qspectrumanalyzer_start() -> str:
    """
    Start QSpectrumAnalyzer — persistent waterfall using hackrf_sweep backend.
    Better than GQRX for wideband survey work. Needs exclusive HackRF access.
    """
    others = _stop_hardware_holders(exclude="qspectrumanalyzer")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    if _is_running("qspectrumanalyzer"):
        return f"QSpectrumAnalyzer is already running{msg_others}."

    for binary in ["qspectrumanalyzer", "QSpectrumAnalyzer"]:
        rc, _, _ = _run(["which", binary])
        if rc == 0:
            _launch_gui([binary])
            time.sleep(2)
            if _is_running(binary):
                return (
                    f"QSpectrumAnalyzer started{msg_others}. "
                    "Set backend to hackrf_sweep in Settings → Backend."
                )

    return "QSpectrumAnalyzer not found — try: sudo apt install qspectrumanalyzer"


def qspectrumanalyzer_stop() -> str:
    for proc in ["qspectrumanalyzer", "QSpectrumAnalyzer"]:
        if _kill(proc):
            time.sleep(2)
            return "QSpectrumAnalyzer stopped — HackRF is free."
    return "QSpectrumAnalyzer was not running."


# ── CubicSDR ──────────────────────────────────────────────────────────────

def cubicsdr_start() -> str:
    """
    Start CubicSDR — multi-channel SDR with per-channel demodulators.
    Good for monitoring multiple signals simultaneously. Needs exclusive HackRF.
    """
    others = _stop_hardware_holders(exclude="cubicsdr")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    if _is_running("CubicSDR"):
        return f"CubicSDR is already running{msg_others}."

    for binary in ["CubicSDR", "cubicsdr"]:
        rc, _, _ = _run(["which", binary])
        if rc == 0:
            _launch_gui([binary])
            time.sleep(3)
            if _is_running("CubicSDR") or _is_running("cubicsdr"):
                return f"CubicSDR started{msg_others}."

    return "CubicSDR not found — try: sudo apt install cubicsdr"


def cubicsdr_stop() -> str:
    for proc in ["CubicSDR", "cubicsdr"]:
        if _kill(proc):
            time.sleep(2)
            return "CubicSDR stopped — HackRF is free."
    return "CubicSDR was not running."


# ── SigDigger ─────────────────────────────────────────────────────────────

def sigdigger_open(iq_file: str = "") -> str:
    """
    Open SigDigger for deep signal analysis — symbol rate detection,
    protocol demodulation without prior knowledge, gap/burst analysis.
    Can open IQ files (no hardware conflict) or live SDR source (exclusive).
    """
    if iq_file:
        if not os.path.exists(iq_file):
            return f"File not found: {iq_file}"
        _launch_gui(["SigDigger", iq_file])
    else:
        others = _stop_hardware_holders(exclude="sigdigger")
        _launch_gui(["SigDigger"])

    time.sleep(3)
    if _is_running("SigDigger"):
        note = f" with {iq_file}" if iq_file else " — select HackRF as source"
        return f"SigDigger opened{note}."

    return (
        "SigDigger not found or failed to start.\n"
        "Build from source:\n"
        "  git clone --recurse-submodules https://github.com/BatchDrake/SigDigger\n"
        "  cd SigDigger && qmake SigDigger.pro && make -j$(nproc) && sudo make install"
    )


def sigdigger_stop() -> str:
    if _kill("SigDigger"):
        return "SigDigger closed."
    return "SigDigger was not running."


# ── OpenWebRX+ ────────────────────────────────────────────────────────────

def openwebrx_start() -> str:
    """
    Start OpenWebRX+ — browser-based SDR accessible from any device on the network.
    Ideal for SSH workflows: open http://<pi-ip>:8073 in any browser to see
    the waterfall and demodulate signals without needing a desktop.
    Needs exclusive HackRF access.
    """
    import socket as _socket

    # Check if already running on port 8073
    try:
        with _socket.create_connection(("127.0.0.1", 8073), timeout=2):
            return "OpenWebRX-plus is already running — open http://localhost:8073 in a browser."
    except (ConnectionRefusedError, OSError):
        pass

    others = _stop_hardware_holders(exclude="openwebrx")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    # Try systemd service first (if installed that way)
    r = subprocess.run(
        ["sudo", "systemctl", "start", "openwebrx"],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode == 0:
        if _wait_for_port(8073, timeout_sec=15):
            return f"OpenWebRX-plus started{msg_others} — open http://localhost:8073"

    # Try running directly
    for binary in ["openwebrx", "openwebrx.py"]:
        rc, path, _ = _run(["which", binary])
        if rc == 0:
            subprocess.Popen(
                [path.strip()],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if _wait_for_port(8073, timeout_sec=15):
                return f"OpenWebRX-plus started{msg_others} — open http://localhost:8073"

    return (
        "OpenWebRX-plus not found or failed to start.\n"
        "Install: https://www.radiosrs.net/installing_sdr_software_debian.html\n"
        "Or via Docker: docker run -d -p 8073:8073 jketterl/openwebrx-hackrf"
    )


def openwebrx_stop() -> str:
    r = subprocess.run(
        ["sudo", "systemctl", "stop", "openwebrx"],
        capture_output=True, text=True, timeout=8
    )
    if r.returncode == 0:
        return "OpenWebRX-plus stopped."

    for proc in ["openwebrx", "openwebrx.py"]:
        if _kill(proc):
            return "OpenWebRX+ stopped — HackRF is free."

    return "OpenWebRX-plus was not running."


# ── fldigi ────────────────────────────────────────────────────────────────

def fldigi_start() -> str:
    """
    Start fldigi for ham radio digital modes (PSK31, RTTY, Olivia, CW, MFSK etc).
    Audio-based only — NO hardware conflict with GQRX or any other SDR app.
    """
    if _is_running("fldigi"):
        return "fldigi is already running."

    rc, _, _ = _run(["which", "fldigi"])
    if rc != 0:
        return (
            "fldigi not found. Install with: sudo apt install fldigi\n"
            "Note: fldigi is audio-based and runs alongside GQRX with no conflict."
        )

    _launch_gui(["fldigi"])
    time.sleep(3)

    if _is_running("fldigi"):
        return (
            "fldigi started. No hardware conflict — runs alongside GQRX.\n"
            "In GQRX: set audio output to a PulseAudio monitor/virtual sink.\n"
            "In fldigi: Op Mode → select digital mode (e.g. PSK31, RTTY).\n"
            "Tune GQRX to a digital signal and watch fldigi decode it."
        )
    return "fldigi failed to start."


def fldigi_stop() -> str:
    if _kill("fldigi"):
        return "fldigi closed."
    return "fldigi was not running."


# ── Wireshark ─────────────────────────────────────────────────────────────

def wireshark_open(capture_file: str = "") -> str:
    """
    Open Wireshark for packet/protocol analysis.
    No HackRF conflict — analyzes capture files or live network interfaces.
    Useful for analyzing decoded GSM, AIS, and other protocol data piped
    from gr-gsm, rtl-ais, kismet etc.
    """
    cmd = ["wireshark"]
    if capture_file:
        if not os.path.exists(capture_file):
            return f"File not found: {capture_file}"
        cmd += ["-r", capture_file]

    _launch_gui(cmd)
    time.sleep(2)
    if _is_running("wireshark"):
        note = f" with {capture_file}" if capture_file else ""
        return (
            f"Wireshark opened{note}. No hardware conflict — runs alongside any SDR app.\\n"
            "Tip: gr-gsm pipes decoded GSM frames to a local UDP socket "
            "that Wireshark can capture on loopback (lo)."
        )
    return "Wireshark not found — try: sudo apt install wireshark"


def wireshark_stop() -> str:
    if _kill("wireshark"):
        return "Wireshark closed."
    return "Wireshark was not running."


# ── Kismet ────────────────────────────────────────────────────────────────

def kismet_start() -> str:
    """
    Start Kismet — passive WiFi/Bluetooth scanner with web UI at http://localhost:2501
    No HackRF conflict — uses WiFi adapter, not the HackRF.
    Can run simultaneously with any SDR app.
    """
    import socket as _socket
    try:
        with _socket.create_connection(("127.0.0.1", 2501), timeout=2):
            return "Kismet is already running — web UI: http://localhost:2501"
    except (ConnectionRefusedError, OSError):
        pass

    rc, _, _ = _run(["which", "kismet"])
    if rc != 0:
        return "Kismet not found — try: sudo apt install kismet"

    subprocess.Popen(
        ["kismet", "--no-ncurses"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if _wait_for_port(2501, timeout_sec=15):
        return (
            "Kismet started — web UI: http://localhost:2501\\n"
            "No HackRF conflict — runs alongside GQRX or any SDR app.\\n"
            "Default credentials set on first login."
        )
    return "Kismet started but web UI not responding — check: sudo systemctl status kismet"


def kismet_stop() -> str:
    r = subprocess.run(
        ["sudo", "systemctl", "stop", "kismet"],
        capture_output=True, text=True, timeout=8
    )
    if r.returncode == 0:
        return "Kismet stopped."
    if _kill("kismet"):
        return "Kismet stopped."
    return "Kismet was not running."


# ── WSJT-X ───────────────────────────────────────────────────────────────

def wsjtx_start() -> str:
    """
    Start WSJT-X for weak signal digital modes: FT8, FT4, WSPR, JT65, MSK144.
    Audio-based — NO HackRF conflict, runs alongside GQRX.
    Tune GQRX to 14.074 MHz USB for FT8 (the most active digital mode globally).
    WSPR beacons on 14.0956 MHz are receivable worldwide with minimal setup.
    """
    if _is_running("wsjtx"):
        return "WSJT-X is already running."

    for binary in ["wsjtx", "WSJTX"]:
        rc, _, _ = _run(["which", binary])
        if rc == 0:
            _launch_gui([binary])
            time.sleep(3)
            if _is_running("wsjtx") or _is_running("WSJTX"):
                return (
                    "WSJT-X started. No hardware conflict — runs alongside GQRX.\\n"
                    "Tune GQRX to 14.074 MHz USB for FT8 (most active globally).\\n"
                    "Set GQRX audio output to PulseAudio monitor as WSJT-X input."
                )

    return "WSJT-X not found — try: sudo apt install wsjtx"


def wsjtx_stop() -> str:
    for proc in ["wsjtx", "WSJTX"]:
        if _kill(proc):
            return "WSJT-X closed."
    return "WSJT-X was not running."


# ── Gpredict ─────────────────────────────────────────────────────────────

def gpredict_start() -> str:
    """
    Start Gpredict — satellite pass prediction and real-time tracking.
    No hardware conflict — purely computational/display.
    Shows when satellites (NOAA, ISS, Iridium etc) pass overhead so you
    know when to point the antenna and start a capture.
    """
    if _is_running("gpredict"):
        return "Gpredict is already running."

    rc, _, _ = _run(["which", "gpredict"])
    if rc != 0:
        return "Gpredict not found — try: sudo apt install gpredict"

    _launch_gui(["gpredict"])
    time.sleep(2)
    if _is_running("gpredict"):
        return (
            "Gpredict started. No hardware conflict.\\n"
            "Edit > Update TLE data to get current satellite positions.\\n"
            "Right-click a satellite to see next pass time and elevation."
        )
    return "Gpredict failed to start."


def gpredict_stop() -> str:
    if _kill("gpredict"):
        return "Gpredict closed."
    return "Gpredict was not running."


# ── rtl_433 ──────────────────────────────────────────────────────────────

def rtl433_start(freq_mhz: float = 433.92, duration_sec: int = 30, device: str = "hackrf") -> str:
    """
    Run rtl_433 to automatically decode 433/868/915 MHz ISM band sensors:
    weather stations, tire pressure monitors, doorbells, power meters etc.
    device="hackrf" — uses HackRF via SoapySDR (-d driver=hackrf).
    device="rtlsdr" or "rtlsdr:N" — uses RTL-SDR device index N (-d N).
    """
    import shutil as _shutil

    dev_idx = None
    if device.startswith("rtlsdr"):
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0
    else:
        busy = None
        try:
            from .hackrf import _check_hackrf_free
            busy = _check_hackrf_free()
        except Exception:
            pass
        if busy:
            return busy
        _stop_hardware_holders(exclude="rtl_433")

    if not _shutil.which("rtl_433"):
        return "rtl_433 not found — try: sudo apt install rtl-433"

    freq_hz = int(freq_mhz * 1e6)
    driver_arg = str(dev_idx) if dev_idx is not None else "driver=hackrf"
    cmd = ["rtl_433", "-d", driver_arg,
           "-f", str(freq_hz), "-F", "json", "-T", str(duration_sec)]

    rc2, out, err = _run(cmd, timeout=duration_sec + 10)

    import json as _json
    decoded = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                decoded.append(_json.loads(line))
            except Exception:
                pass

    if decoded:
        return _json.dumps({
            "freq_mhz": freq_mhz,
            "duration_sec": duration_sec,
            "devices_decoded": len(decoded),
            "packets": decoded[:20],
        }, indent=2)

    return (
        f"rtl_433 ran for {duration_sec}s on {freq_mhz} MHz — no devices decoded.\\n"
        "Try: 433.92 MHz (EU/AU), 315.0 MHz (US garage/tire), 868.35 MHz (EU meters).\\n"
        f"Raw output: {out[:300] or err[:300]}"
    )


# ── multimon-ng ───────────────────────────────────────────────────────────

def multimon_decode(audio_file: str = "", modes: list = None) -> str:
    """
    Decode digital modes from an audio file or stdin using multimon-ng.
    Supports: POCSAG pagers, AFSK1200, DTMF, EAS, FLEX, SSTV sync, SCOPE.
    Audio-based — NO HackRF conflict.
    Pipe GQRX NFM audio to multimon-ng for live pager decoding.
    """
    if not modes:
        modes = ["POCSAG512", "POCSAG1200", "POCSAG2400", "AFSK1200", "DTMF", "EAS"]

    rc, _, _ = _run(["which", "multimon-ng"])
    if rc != 0:
        return "multimon-ng not found — try: sudo apt install multimon-ng"

    mode_args = []
    for m in modes:
        mode_args += ["-a", m]

    if audio_file:
        if not os.path.exists(audio_file):
            return f"File not found: {audio_file}"
        # Convert to raw audio if needed
        cmd = ["multimon-ng", "-t", "wav"] + mode_args + [audio_file]
        rc2, out, err = _run(cmd, timeout=60)
    else:
        return (
            "multimon-ng ready. To decode live pager traffic:\\n"
            "  In GQRX: set NFM, tune to a pager frequency (e.g. 152.24 MHz)\\n"
            "  Run: gqrx audio | sox -t raw -r 48000 -e signed -b 16 -c 1 - -t wav - | multimon-ng -a POCSAG1200 -t wav -\\n"
            "Or capture audio to a WAV file and call this tool with the file path."
        )

    decoded = [l for l in out.splitlines() if not l.startswith("multimon-ng") and l.strip()]
    return (
        f"multimon-ng decoded {len(decoded)} messages:\\n" + "\\n".join(decoded[:30])
        if decoded else
        f"No messages decoded from {audio_file}.\\nRaw: {out[:200] or err[:200]}"
    )


# ── QSSTV ─────────────────────────────────────────────────────────────────

def qsstv_start() -> str:
    """
    Start QSSTV for Slow Scan Television (SSTV) image decoding and transmission.
    Audio-based — NO HackRF conflict, runs alongside GQRX.
    ISS transmits SSTV images on 145.800 MHz FM during special events.
    """
    if _is_running("qsstv"):
        return "QSSTV is already running."

    rc, _, _ = _run(["which", "qsstv"])
    if rc != 0:
        return "QSSTV not found — try: sudo apt install qsstv"

    _launch_gui(["qsstv"])
    time.sleep(2)
    if _is_running("qsstv"):
        return (
            "QSSTV started. No hardware conflict — runs alongside GQRX.\\n"
            "Tune GQRX to 145.800 MHz FM for ISS SSTV (during events).\\n"
            "Set GQRX audio output to PulseAudio monitor as QSSTV input."
        )
    return "QSSTV failed to start."


def qsstv_stop() -> str:
    if _kill("qsstv"):
        return "QSSTV closed."
    return "QSSTV was not running."


# ── SDR++ ─────────────────────────────────────────────────────────────────

def sdrpp_start() -> str:
    """
    Start SDR++ — modern, lightweight alternative to GQRX.
    Lower CPU usage on ARM64, better for Pi 5.
    Needs exclusive HackRF access — stops GQRX first.
    """
    others = _stop_hardware_holders(exclude="sdrpp")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    if _is_running("sdrpp"):
        return f"SDR++ is already running{msg_others}."

    for binary in ["sdrpp", "SDRPlusPlus"]:
        rc, _, _ = _run(["which", binary])
        if rc == 0:
            _launch_gui([binary])
            time.sleep(3)
            if _is_running(binary):
                return f"SDR++ started{msg_others}."

    return "SDR++ not found — try: sudo apt install sdrpp"


def sdrpp_stop() -> str:
    for proc in ["sdrpp", "SDRPlusPlus"]:
        if _kill(proc):
            time.sleep(2)
            return "SDR++ stopped — HackRF is free."
    return "SDR++ was not running."


# ── DSD-FME ──────────────────────────────────────────────────────────────

def dsdfme_decode(audio_file: str = "", mode: str = "auto") -> str:
    """
    Decode digital voice protocols using DSD-FME: P25 Phase 1/2, DMR, NXDN,
    D-STAR, ProVoice, X2-TDMA, EDACS.
    Audio-based — NO HackRF conflict. Pipe GQRX NFM audio for live decode.
    """
    rc, _, _ = _run(["which", "dsd-fme"])
    if rc != 0:
        # Try alternate binary names
        for alt in ["dsd", "dsdfme"]:
            rc2, _, _ = _run(["which", alt])
            if rc2 == 0:
                break
        else:
            return "DSD-FME not found — check /usr/src/ on DragonOS for the binary."

    if not audio_file:
        return (
            "DSD-FME ready for digital voice decoding.\\n"
            "Supported protocols: P25 P1/P2, DMR, NXDN, D-STAR, ProVoice\\n"
            "For live decode from GQRX:\\n"
            "  Tune GQRX to a digital voice channel (NFM, 12.5 kHz BW)\\n"
            "  Run: dsd-fme -i <pulseaudio_source> -o /dev/null\\n"
            "Or record audio to a WAV file and call this tool with the file path."
        )

    if not os.path.exists(audio_file):
        return f"File not found: {audio_file}"

    cmd = ["dsd-fme", "-i", audio_file]
    if mode != "auto":
        cmd += ["-f", mode]

    rc2, out, err = _run(cmd, timeout=120)
    output = out or err
    return output[:1000] if output else "No output from DSD-FME — check audio format."


# ── JAERO ─────────────────────────────────────────────────────────────────

def jaero_start() -> str:
    """
    Start JAERO for decoding ACARS messages from Inmarsat satellites.
    Covers aircraft over oceans where ADS-B ground stations don't reach.
    Audio-based — NO HackRF conflict, runs alongside GQRX or SDRAngel.

    Typical Inmarsat frequencies (tune GQRX to these, USB mode):
      1545.000 MHz — Inmarsat C-band (primary ACARS)
      1546.000 MHz — alternate
      10500.0  kHz (HF) — via SDRAngel HF reception

    Note: Inmarsat L-band is above HackRF's 6 GHz limit so you need
    a downconverter or use JAERO with an HF SDR for the 10 MHz ACARS band.
    JAERO decodes the audio demodulated by GQRX/SDRAngel.
    """
    if _is_running("JAERO"):
        return "JAERO is already running."

    for binary in ["JAERO", "jaero"]:
        rc, _, _ = _run(["which", binary])
        if rc == 0:
            _launch_gui([binary])
            time.sleep(3)
            if _is_running(binary):
                return (
                    "JAERO started. Audio-based — no HackRF conflict.\\n"
                    "Tune GQRX to an Inmarsat frequency in USB mode, then\\n"
                    "set GQRX audio output as JAERO audio input.\\n"
                    "Common frequencies: 1545.000 MHz, 1546.000 MHz (L-band).\\n"
                    "Note: L-band requires a downconverter with the HackRF."
                )

    return (
        "JAERO not found in PATH — check /usr/src/ on DragonOS.\\n"
        "It may need to be launched from its install directory.\\n"
        "Try: find /usr/src -name 'JAERO' -type f 2>/dev/null"
    )


def jaero_stop() -> str:
    for proc in ["JAERO", "jaero"]:
        if _kill(proc):
            return "JAERO closed."
    return "JAERO was not running."


# ── rtl-ais (marine vessel tracking) ─────────────────────────────────────

def rtlais_start(duration_sec: int = 60, device: str = "hackrf") -> str:
    """
    Decode AIS (Automatic Identification System) marine vessel transponders.
    AIS operates on VHF channels 87B (161.975 MHz) and 88B (162.025 MHz).
    Returns vessel positions, names, MMSI numbers, speed, heading.
    device="hackrf" — uses HackRF via SoapySDR (-d driver=hackrf).
    device="rtlsdr" or "rtlsdr:N" — uses RTL-SDR device index N (-d N).

    AIS is the maritime equivalent of ADS-B — all commercial vessels
    over 300 gross tons are required to broadcast it.
    """
    import shutil as _shutil

    dev_idx = None
    if device.startswith("rtlsdr"):
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0
    else:
        busy = None
        try:
            from .hackrf import _check_hackrf_free
            busy = _check_hackrf_free()
        except Exception:
            pass
        if busy:
            return busy
        _stop_hardware_holders(exclude="rtl-ais")

    others = []
    msg_others = ""

    import json as _json

    driver_arg = str(dev_idx) if dev_idx is not None else "driver=hackrf"
    for binary in ["rtl-ais", "rtl_ais"]:
        if not _shutil.which(binary):
            continue
        cmd = [binary,
               "-d", driver_arg,
               "-T", str(duration_sec)]

        rc2, out, err = _run(cmd, timeout=duration_sec + 10)

        vessels = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("!AIVDM") or line.startswith("!AIVDO"):
                vessels.append(line)

        if vessels:
            return _json.dumps({
                "status": "complete",
                "freq_mhz": "161.975 / 162.025 (dual channel)",
                "duration_sec": duration_sec,
                "sentences_decoded": len(vessels),
                "nmea_sentences": vessels[:30],
                "note": "Pipe output to AIS decoder (e.g. gpsd) for vessel details",
            }, indent=2)

        return (
            f"rtl-ais ran for {duration_sec}s{msg_others} — no AIS sentences decoded.\n"
            "AIS requires line of sight to vessels — works best near coastlines/harbours.\n"
            f"Raw output: {out[:300] or err[:300]}"
        )

    # Fallback: try via GNU Radio AIS flowgraph if rtl-ais not found
    return (
        "rtl-ais not found. Alternatives on DragonOS:\n"
        "  gr-ais GNU Radio flowgraph in /usr/src/\n"
        "  OpenCPN with network AIS input from another decoder\n"
        "Install: sudo apt install rtl-ais"
    )


# ── DumpHFDL (HF datalink) ────────────────────────────────────────────────

def dumphfdl_start(
    freq_list: list = None,
    duration_sec: int = 60,
) -> str:
    """
    Decode HFDL (High Frequency Data Link) aircraft messages on HF frequencies.
    HFDL carries ACARS-like data on shortwave — covers polar routes and remote
    areas not reachable by VHF ADS-B or Inmarsat.

    Common HFDL frequencies (USB mode, kHz):
      2998, 4681, 5652, 6532, 8825, 10081, 11384, 13270, 17901 kHz

    HackRF can receive HF directly (1 MHz+) but sensitivity is poor without
    an upconverter or HF antenna. Best results with a long wire antenna.
    Needs exclusive HackRF access.
    """
    if freq_list is None:
        freq_list = [8825, 11384, 13270]  # most active globally

    busy = None
    try:
        from .hackrf import _check_hackrf_free
        busy = _check_hackrf_free()
    except Exception:
        pass
    if busy:
        return busy

    others = _stop_hardware_holders(exclude="dumphfdl")
    msg_others = f" (stopped: {', '.join(others)})" if others else ""

    rc, _, _ = _run(["which", "dumphfdl"])
    if rc != 0:
        return (
            "dumphfdl not found — check DragonOS /usr/src/.\\n"
            "Install: https://github.com/szpajder/dumphfdl\\n"
            "Note: HFDL requires a good HF antenna (long wire, 10-20m)."
        )

    import json as _json

    freq_args = []
    for f in freq_list:
        freq_args += ["--freq", str(f)]

    cmd = ["dumphfdl",
           "--soapysdr", "driver=hackrf",
           "--output", "decoded:json:file:-",
           "--output-queue-hwm", "100"] + freq_args

    rc2, out, err = _run(cmd, timeout=duration_sec + 10)

    messages = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                import json
                messages.append(json.loads(line))
            except Exception:
                pass

    if messages:
        return _json.dumps({
            "status": "complete",
            "frequencies_khz": freq_list,
            "duration_sec": duration_sec,
            "messages_decoded": len(messages),
            "messages": messages[:20],
        }, indent=2)

    return (
        f"dumphfdl ran for {duration_sec}s on {freq_list} kHz{msg_others}\\n"
        "No messages decoded — HF propagation is variable.\\n"
        "Try different frequencies or times of day.\\n"
        f"Output: {out[:200] or err[:200]}"
    )


# ── DumpVDL2 (VHF datalink) ───────────────────────────────────────────────

def dumpvdl2_start(duration_sec: int = 60, device: str = "hackrf") -> str:
    """
    Decode VDL Mode 2 aircraft digital datalink messages on 136-137 MHz.
    VDL2 is the VHF equivalent of HFDL — ACARS over VHF digital radio.
    Much more active than HFDL near airports.

    Primary frequencies: 136.900, 136.925, 136.975 MHz
    device="hackrf" — uses HackRF via SoapySDR (--soapysdr driver=hackrf).
    device="rtlsdr" or "rtlsdr:N" — uses RTL-SDR (--soapysdr driver=rtlsdr,rtlsdr=N).
    """
    import shutil as _shutil

    dev_idx = None
    if device.startswith("rtlsdr"):
        parts = device.split(":", 1)
        dev_idx = int(parts[1]) if len(parts) > 1 else 0
    else:
        busy = None
        try:
            from .hackrf import _check_hackrf_free
            busy = _check_hackrf_free()
        except Exception:
            pass
        if busy:
            return busy
        _stop_hardware_holders(exclude="dumpvdl2")

    msg_others = ""

    if not _shutil.which("dumpvdl2"):
        return (
            "dumpvdl2 not found — check DragonOS /usr/src/.\n"
            "Install: https://github.com/szpajder/dumpvdl2"
        )

    import json as _json

    if dev_idx is not None:
        soapy_arg = f"driver=rtlsdr,rtlsdr={dev_idx}"
    else:
        soapy_arg = "driver=hackrf"

    cmd = ["dumpvdl2",
           "--soapysdr", soapy_arg,
           "--output", "decoded:json:file:-",
           "136900000", "136925000", "136975000"]

    rc2, out, err = _run(cmd, timeout=duration_sec + 10)

    messages = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                import json
                messages.append(json.loads(line))
            except Exception:
                pass

    if messages:
        return _json.dumps({
            "status": "complete",
            "frequencies_mhz": [136.9, 136.925, 136.975],
            "duration_sec": duration_sec,
            "messages_decoded": len(messages),
            "messages": messages[:20],
        }, indent=2)

    return (
        f"dumpvdl2 ran for {duration_sec}s{msg_others} — no VDL2 messages decoded.\\n"
        "Works best within ~200km of a major airport.\\n"
        f"Output: {out[:200] or err[:200]}"
    )
