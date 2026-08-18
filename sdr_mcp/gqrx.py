"""
GQRX TCP remote control client.
Uses the Hamlib rigctld protocol that GQRX exposes on port 7356.

To enable in GQRX: Tools → Remote control → Start
Test manually:  echo "f" | nc -w2 localhost 7356
"""

import socket
import time


GQRX_HOST = "127.0.0.1"
GQRX_PORT = 7356
TIMEOUT    = 5.0


class GqrxError(RuntimeError):
    pass


class GqrxClient:
    def __init__(self, host: str = GQRX_HOST, port: int = GQRX_PORT):
        self.host = host
        self.port = port

    def _cmd(self, cmd: str) -> str:
        try:
            with socket.create_connection((self.host, self.port), timeout=TIMEOUT) as s:
                s.sendall((cmd + "\n").encode())
                time.sleep(0.1)
                data = b""
                s.settimeout(TIMEOUT)
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"RPRT" in data or b"\n" in data:
                        break
            return data.decode(errors="replace").strip()
        except ConnectionRefusedError:
            raise GqrxError(
                "GQRX remote control not running. "
                "In GQRX: Tools → Remote control → Start"
            )
        except socket.timeout:
            raise GqrxError("GQRX remote control timed out")

    def get_frequency(self) -> int | None:
        resp = self._cmd("f")
        try:
            return int(resp.split()[0])
        except (ValueError, IndexError):
            return None

    def set_frequency(self, freq_hz: int) -> None:
        resp = self._cmd(f"F {freq_hz}")
        if "RPRT 0" not in resp and resp:
            pass  # GQRX sometimes returns just the freq — treat as OK

    def get_mode(self) -> str | None:
        resp = self._cmd("m")
        lines = resp.strip().splitlines()
        return lines[0].strip() if lines else None

    def set_mode(self, mode: str) -> None:
        # Hamlib format: M <mode> <passband>
        # GQRX accepts 0 for default passband
        self._cmd(f"M {mode.upper()} 0")

    def get_signal_level(self) -> float | None:
        resp = self._cmd("l STRENGTH")
        try:
            return float(resp.split()[0])
        except (ValueError, IndexError):
            return None

    def set_squelch(self, level_dbm: float) -> None:
        self._cmd(f"L SQL {level_dbm}")

    def start_recording(self, directory: str = "") -> None:
        self._cmd("AOS")  # GQRX record start (custom extension)

    def stop_recording(self) -> None:
        self._cmd("LOS")  # GQRX record stop (custom extension)


# ── GQRX process management ────────────────────────────────────────────────
# These functions start and stop GQRX (headless service or desktop process)
# so the agent can manage the stop/start cycle around sweeps automatically.

import subprocess
import os


def gqrx_stop() -> str:
    """
    Stop GQRX so the HackRF is free for sweeps/captures.
    Tries the headless systemd service first, then kills any running GQRX process.
    Returns a status string.
    """
    stopped_anything = False

    # Stop headless systemd service
    r = subprocess.run(
        ["systemctl", "--user", "stop", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=8
    )
    if r.returncode == 0:
        stopped_anything = True

    # Also kill any desktop GQRX process
    r2 = subprocess.run(["pkill", "-x", "gqrx"], capture_output=True, text=True, timeout=5)
    if r2.returncode == 0:
        stopped_anything = True

    if not stopped_anything:
        return "GQRX was not running — HackRF is free for sweep/capture."

    # Wait briefly for the device to be released
    import time
    time.sleep(2)
    return "GQRX stopped — HackRF is now free. Run your sweep or capture, then call gqrx_start when done."


def _patch_gqrx_remote_control() -> None:
    """
    Write remote control settings to GQRX's config before launch so port 7356
    is open automatically without requiring manual menu interaction.
    Creates the config directory and file if they don't exist yet.
    """
    import configparser

    config_dir  = os.path.expanduser("~/.config/GQRX")
    config_path = os.path.join(config_dir, "default.conf")

    os.makedirs(config_dir, exist_ok=True)

    cfg = configparser.ConfigParser(strict=False)
    if os.path.exists(config_path):
        cfg.read(config_path)

    if not cfg.has_section("remote_control"):
        cfg.add_section("remote_control")
    cfg.set("remote_control", "enabled", "true")
    cfg.set("remote_control", "port",    "7356")

    try:
        with open(config_path, "w") as f:
            cfg.write(f)
    except OSError:
        pass  # read-only filesystem — GQRX may still open the port from saved state


def _find_display() -> str:
    """
    Return the best available X display for launching GQRX.
    Probes X11 sockets directly so it works from SSH sessions where
    $DISPLAY is not set.  Starts Xvfb :99 as a last resort.
    """
    import glob

    # Prefer the caller's session display if valid
    env_disp = os.environ.get("DISPLAY", "")
    if env_disp:
        sock_num = env_disp.lstrip(":").split(".")[0]
        if os.path.exists(f"/tmp/.X11-unix/X{sock_num}"):
            return env_disp

    # Probe for any live X server by checking socket files
    for sock in sorted(glob.glob("/tmp/.X11-unix/X*")):
        n = sock.rsplit("X", 1)[-1]
        if n.isdigit():
            return f":{n}"

    # Last resort: start Xvfb on :99 (headless virtual display)
    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x1024x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
    except FileNotFoundError:
        pass  # Xvfb not installed — GQRX may still work if display appears later
    return ":99"


def gqrx_start() -> str:
    """
    Start GQRX after a sweep/capture is complete.
    Pre-patches the GQRX config so remote control port 7356 opens automatically.
    Probes X11 sockets to find a running display even from SSH sessions.
    """
    import socket

    def _port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 7356), timeout=2):
                return True
        except (ConnectionRefusedError, OSError):
            return False

    # Early return if GQRX is already running with remote control active
    if _port_open():
        return "GQRX is already running — remote control ready on port 7356."

    # Ensure remote control is pre-configured before launch
    _patch_gqrx_remote_control()

    # Try the headless systemd service first (may have its own display configured)
    r = subprocess.run(
        ["systemctl", "--user", "start", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        for _ in range(8):
            time.sleep(2)
            if _port_open():
                return "GQRX started — remote control ready on port 7356."

    # Service unavailable or port didn't open — launch GQRX directly.
    # Find the best available X display (works from SSH, VNC, or HDMI sessions).
    display = _find_display()
    env = {**os.environ, "DISPLAY": display}
    subprocess.Popen(
        ["gqrx"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    # Wait up to 20 s for remote control port
    for _ in range(10):
        time.sleep(2)
        if _port_open():
            return f"GQRX started on display {display} — remote control ready on port 7356."

    return (
        f"GQRX launched on display {display} but remote control port 7356 did not open in 20 s. "
        "If the GQRX window is visible, go to Tools → Remote control → Start, then call gqrx_tune."
    )
