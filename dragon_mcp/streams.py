"""
StreamManager — lifecycle manager for live web-viewable streams.

Supports MJPEG (Flask + scrot screenshot loop), HLS (ffmpeg x11grab),
and Audio (ffmpeg PulseAudio → Icecast) per stream tool.

Each stream is identified by a tool name (e.g. "gqrx", "dump1090").
Only one stream per tool name runs at a time.
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

StreamMode = Literal["mjpeg", "hls", "audio"]

# Ports allocated per stream tool
_MJPEG_BASE_PORT = 9000  # gqrx→9000, dump1090→9001, rtl433→9002, multimon→9003
_HLS_PORT = 9010
_ICECAST_PORT = 8000

_TOOL_MJPEG_PORTS = {
    "gqrx": 9000,
    "dump1090": 9001,
    "rtl433": 9002,
    "multimon": 9003,
}

HLS_ROOT = os.path.expanduser("~/dragon-streams/hls")
MJPEG_SCRIPT = os.path.join(os.path.dirname(__file__), "_mjpeg_server.py")


@dataclass
class _StreamEntry:
    tool: str
    mode: StreamMode
    url: str
    procs: list[subprocess.Popen] = field(default_factory=list)


class StreamManager:
    """Thread-safe registry of running streams.  One stream per tool name."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, _StreamEntry] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, tool: str, mode: StreamMode, pi_ip: str = "0.0.0.0") -> str:
        """Start a stream for *tool* in *mode*.  Returns the browser URL.

        If a stream for that tool is already running it is stopped first.
        pi_ip is the LAN address to embed in returned URLs (default shows the
        Pi's address as seen by the caller).
        """
        with self._lock:
            if tool in self._streams:
                self._stop_locked(tool)
            entry = self._launch(tool, mode, pi_ip)
            self._streams[tool] = entry
            return entry.url

    def stop(self, tool: str) -> str:
        """Stop the stream for *tool*.  Returns a status message."""
        with self._lock:
            if tool not in self._streams:
                return f"No stream running for '{tool}'"
            self._stop_locked(tool)
            return f"Stream for '{tool}' stopped."

    def list_streams(self) -> dict[str, dict]:
        """Return {tool: {mode, url}} for all active streams."""
        with self._lock:
            return {
                name: {"mode": e.mode, "url": e.url}
                for name, e in self._streams.items()
            }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _stop_locked(self, tool: str) -> None:
        entry = self._streams.pop(tool)
        for proc in entry.procs:
            try:
                proc.terminate()
            except Exception:
                pass
        # Give processes a moment to die before releasing the tool name
        time.sleep(0.5)

    def _launch(self, tool: str, mode: StreamMode, pi_ip: str) -> _StreamEntry:
        if mode == "mjpeg":
            return self._launch_mjpeg(tool, pi_ip)
        elif mode == "hls":
            return self._launch_hls(tool, pi_ip)
        elif mode == "audio":
            return self._launch_audio(tool, pi_ip)
        else:
            raise ValueError(f"Unknown stream mode: {mode!r}")

    # ── MJPEG (Flask + scrot screenshot loop) ─────────────────────────────

    def _launch_mjpeg(self, tool: str, pi_ip: str) -> _StreamEntry:
        port = _TOOL_MJPEG_PORTS.get(tool, _MJPEG_BASE_PORT)
        # Write the minimal Flask MJPEG server inline rather than depending on
        # a separate script file — keeps dragon_mcp self-contained.
        script = _MJPEG_SCRIPT_CONTENT.format(port=port)
        script_path = f"/tmp/dragon_mjpeg_{tool}.py"
        with open(script_path, "w") as f:
            f.write(script)

        proc = subprocess.Popen(
            ["python3", script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://{pi_ip}:{port}/stream"
        return _StreamEntry(tool=tool, mode="mjpeg", url=url, procs=[proc])

    # ── HLS (ffmpeg x11grab) ──────────────────────────────────────────────

    def _launch_hls(self, tool: str, pi_ip: str) -> _StreamEntry:
        os.makedirs(HLS_ROOT, exist_ok=True)
        seg_dir = os.path.join(HLS_ROOT, tool)
        os.makedirs(seg_dir, exist_ok=True)
        playlist = os.path.join(seg_dir, "index.m3u8")

        display = os.environ.get("DISPLAY", ":0")
        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-r", "5",
            "-s", "1280x800",
            "-i", display,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments",
            playlist,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Serve HLS segments via a minimal HTTP server on _HLS_PORT
        serve_script = _HLS_SERVER_CONTENT.format(root=HLS_ROOT, port=_HLS_PORT)
        serve_path = f"/tmp/dragon_hls_server.py"
        with open(serve_path, "w") as f:
            f.write(serve_script)
        srv_proc = subprocess.Popen(
            ["python3", serve_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        url = f"http://{pi_ip}:{_HLS_PORT}/{tool}/index.m3u8"
        return _StreamEntry(tool=tool, mode="hls", url=url, procs=[proc, srv_proc])

    # ── Audio (ffmpeg PulseAudio → Icecast) ───────────────────────────────

    def _launch_audio(self, tool: str, pi_ip: str) -> _StreamEntry:
        mountpoint = f"/{tool}.mp3"
        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse",
            "-i", "default",
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            "-f", "mp3",
            f"icecast://source:hackme@localhost:{_ICECAST_PORT}{mountpoint}",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://{pi_ip}:{_ICECAST_PORT}{mountpoint}"
        return _StreamEntry(tool=tool, mode="audio", url=url, procs=[proc])


# ── Embedded MJPEG server script ──────────────────────────────────────────

_MJPEG_SCRIPT_CONTENT = """\
import subprocess, time, threading
from flask import Flask, Response

app = Flask(__name__)
_frame = b""
_lock = threading.Lock()

def _capture_loop():
    global _frame
    while True:
        r = subprocess.run(["scrot", "-", "--format", "jpg"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            with _lock:
                _frame = r.stdout
        time.sleep(0.2)

threading.Thread(target=_capture_loop, daemon=True).start()

def _generate():
    while True:
        with _lock:
            frame = _frame
        if frame:
            yield (b"--frame\\r\\nContent-Type: image/jpeg\\r\\n\\r\\n" + frame + b"\\r\\n")
        time.sleep(0.2)

@app.route("/stream")
def stream():
    return Response(_generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/")
def index():
    return '<html><body><img src="/stream" style="max-width:100%"></body></html>'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port={port}, threaded=True)
"""

# ── Embedded HLS static file server ───────────────────────────────────────

_HLS_SERVER_CONTENT = """\
import http.server, os
os.chdir("{root}")
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()
http.server.HTTPServer(("0.0.0.0", {port}), H).serve_forever()
"""
