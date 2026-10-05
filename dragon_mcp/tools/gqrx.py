"""
GQRX remote control and stream launcher for dragon_mcp FastMCP server.

GQRX exposes a Hamlib rigctld-compatible TCP interface on port 7356.
Enable it in GQRX: Tools → Remote control → Start.

Stream modes (via StreamManager):
  mjpeg  — Flask + scrot screenshot loop, viewable in any browser
  hls    — ffmpeg x11grab → HLS segments served over HTTP
  audio  — ffmpeg PulseAudio → Icecast MP3 stream
"""

import json
import os
import re
import socket
import subprocess
import time

GQRX_HOST = "127.0.0.1"
GQRX_PORT = 7356
TIMEOUT = 5.0


class GqrxError(RuntimeError):
    pass


class GqrxClient:
    """Thin client for the GQRX Hamlib rigctld TCP interface."""

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
                "GQRX remote control not reachable. "
                "In GQRX: Tools → Remote control → Start"
            )
        except socket.timeout:
            raise GqrxError("GQRX remote control timed out.")

    def get_frequency(self) -> int | None:
        resp = self._cmd("f")
        try:
            return int(resp.split()[0])
        except (ValueError, IndexError):
            return None

    def set_frequency(self, freq_hz: int) -> None:
        self._cmd(f"F {freq_hz}")

    def get_mode(self) -> str | None:
        resp = self._cmd("m")
        lines = resp.strip().splitlines()
        return lines[0].strip() if lines else None

    def set_mode(self, mode: str) -> None:
        self._cmd(f"M {mode.upper()} 0")

    def get_signal_level(self) -> float | None:
        resp = self._cmd("l STRENGTH")
        try:
            return float(resp.split()[0])
        except (ValueError, IndexError):
            return None

    def set_squelch(self, level_dbm: float) -> None:
        self._cmd(f"L SQL {level_dbm}")


def _gqrx_client() -> GqrxClient:
    return GqrxClient()


def gqrx_get_status() -> str:
    """Get the current GQRX frequency, demodulation mode, and signal level.

    Requires GQRX to be running with remote control enabled on port 7356.
    In GQRX: Tools → Remote control → Start.
    """
    try:
        c = _gqrx_client()
        freq = c.get_frequency()
        mode = c.get_mode()
        level = c.get_signal_level()
        return json.dumps({
            "freq_hz": freq,
            "freq_mhz": round(freq / 1e6, 6) if freq else None,
            "mode": mode,
            "signal_level_dbm": level,
        }, indent=2)
    except GqrxError as exc:
        return json.dumps({"error": str(exc)}, indent=2)


def gqrx_tune(freq_mhz: float, mode: str | None = None) -> str:
    """Tune GQRX to a frequency and optionally change the demodulation mode.

    Args:
        freq_mhz: Target frequency in MHz (e.g. 162.55 for NOAA weather radio).
        mode: Demodulation mode — FM, WFM, AM, USB, LSB, CW, CWR, RTTY (optional).

    Requires GQRX running with remote control on port 7356.
    """
    try:
        c = _gqrx_client()
        c.set_frequency(int(freq_mhz * 1e6))
        if mode:
            c.set_mode(mode)
        freq = c.get_frequency()
        return json.dumps({
            "status": "tuned",
            "freq_hz": freq,
            "freq_mhz": round(freq / 1e6, 6) if freq else None,
            "mode": mode or "unchanged",
        }, indent=2)
    except GqrxError as exc:
        return json.dumps({"error": str(exc)}, indent=2)


def gqrx_set_squelch(level_dbm: float) -> str:
    """Set the GQRX squelch threshold.

    Args:
        level_dbm: Squelch level in dBm (e.g. -60.0).  Signals below this level are muted.
    """
    try:
        _gqrx_client().set_squelch(level_dbm)
        return json.dumps({"status": "squelch_set", "level_dbm": level_dbm}, indent=2)
    except GqrxError as exc:
        return json.dumps({"error": str(exc)}, indent=2)


def gqrx_stop() -> str:
    """Stop GQRX and release the HackRF so it can be used by sweep/capture tools.

    Tries the headless systemd service first, then kills any running GQRX process.
    """
    stopped = False
    r = subprocess.run(
        ["systemctl", "--user", "stop", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=8
    )
    if r.returncode == 0:
        stopped = True

    r2 = subprocess.run(["pkill", "-x", "gqrx"], capture_output=True, text=True, timeout=5)
    if r2.returncode == 0:
        stopped = True

    if not stopped:
        return "GQRX was not running — HackRF is already free."
    time.sleep(2)
    return "GQRX stopped. HackRF is now free for sweep/capture tools."


def _patch_gqrx_remote_control() -> None:
    config_path = os.path.expanduser("~/.config/GQRX/default.conf")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    text = ""
    if os.path.exists(config_path):
        with open(config_path) as f:
            text = f.read()
        text = re.sub(r'\[remote_control\].*?(?=\n\[|\Z)', '', text,
                      flags=re.DOTALL | re.IGNORECASE).rstrip()
    text += "\n\n[remote_control]\nenabled=true\nport=7356\n"
    try:
        with open(config_path, "w") as f:
            f.write(text)
    except OSError:
        pass


def _find_display() -> str:
    import glob
    env_disp = os.environ.get("DISPLAY", "")
    if env_disp:
        sock = env_disp.lstrip(":").split(".")[0]
        if os.path.exists(f"/tmp/.X11-unix/X{sock}"):
            return env_disp
    for sock in sorted(glob.glob("/tmp/.X11-unix/X*")):
        n = sock.rsplit("X", 1)[-1]
        if n.isdigit():
            return f":{n}"
    try:
        subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x1024x24"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    except FileNotFoundError:
        pass
    return ":99"


def gqrx_start() -> str:
    """Start GQRX after a sweep/capture, pre-patching config so remote control opens.

    Probes X11 sockets to find a live display even from SSH sessions.
    Returns a message indicating whether remote control port 7356 is ready.
    """
    def _port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 7356), timeout=2):
                return True
        except (ConnectionRefusedError, OSError):
            return False

    if _port_open():
        return "GQRX is already running — remote control ready on port 7356."

    _patch_gqrx_remote_control()

    r = subprocess.run(["systemctl", "--user", "start", "sdr-gqrx-headless"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        for _ in range(8):
            time.sleep(2)
            if _port_open():
                return "GQRX started via systemd — remote control ready on port 7356."

    display = _find_display()
    env = {**os.environ, "DISPLAY": display}
    subprocess.Popen(["gqrx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    for _ in range(10):
        time.sleep(2)
        if _port_open():
            return f"GQRX started on display {display} — remote control ready on port 7356."

    return (
        f"GQRX launched on display {display} but port 7356 did not open in 20 s. "
        "If the GQRX window is visible, go to Tools → Remote control → Start."
    )


def gqrx_stream(mode: str = "mjpeg", pi_ip: str | None = None) -> str:
    """Start a live view stream of the GQRX waterfall/spectrum display.

    Args:
        mode: Stream mode — "mjpeg" (browser-native, no plugins), "hls" (ffmpeg
              x11grab → HLS segments), or "audio" (ffmpeg PulseAudio → Icecast MP3).
        pi_ip: LAN IP of the Pi to embed in the returned URL (auto-detected if omitted).

    Returns a JSON object with the browser URL for the live stream.
    Start GQRX first with gqrx_start.
    """
    from ..streams import StreamManager
    ip = pi_ip or _local_ip()
    mgr = _get_stream_manager()
    url = mgr.start("gqrx", mode, ip)  # type: ignore[arg-type]
    return json.dumps({"status": "streaming", "mode": mode, "url": url}, indent=2)


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# Stream manager singleton shared across tool calls
_stream_manager_instance = None


def _get_stream_manager():
    global _stream_manager_instance
    if _stream_manager_instance is None:
        from ..streams import StreamManager
        _stream_manager_instance = StreamManager()
    return _stream_manager_instance
