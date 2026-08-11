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


def gqrx_start() -> str:
    """
    Start GQRX (headless service) after a sweep/capture is complete.
    Waits for remote control port 7356 to be ready before returning.
    """
    import socket
    import time

    # Start the headless service
    r = subprocess.run(
        ["systemctl", "--user", "start", "sdr-gqrx-headless"],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        # Service may not be installed — try launching GQRX directly
        env = {**os.environ, "DISPLAY": ":99"}
        subprocess.Popen(
            ["gqrx"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

    # Wait up to 20 seconds for remote control port to open
    for i in range(10):
        time.sleep(2)
        try:
            with socket.create_connection(("127.0.0.1", 7356), timeout=2):
                return "GQRX started — remote control ready on port 7356."
        except (ConnectionRefusedError, OSError):
            continue

    return (
        "GQRX started but remote control port 7356 not yet open. "
        "In GQRX: Tools → Remote control → Start, then retry."
    )
