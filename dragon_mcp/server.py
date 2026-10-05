"""
dragon_mcp — FastMCP server with SSE transport for DragonOS / Raspberry Pi 5.

Exposes Dragon hardware tools (HackRF, RTL-SDR, GQRX, protocol decoders)
as MCP tools callable from Claude Code or Claude.ai over LAN.

Transport: SSE (Server-Sent Events)  |  Port: 8765
Start:     python -m dragon_mcp.server   (or via dragon-mcp.service)
"""

import json
import socket

from fastmcp import FastMCP

from .streams import StreamManager
from .tools.hackrf import hackrf_info, hackrf_sweep, hackrf_transmit
from .tools.rtlsdr import rtlsdr_info, rtlsdr_power, rtlsdr_fm
from .tools.gqrx import (
    gqrx_get_status,
    gqrx_tune,
    gqrx_set_squelch,
    gqrx_start,
    gqrx_stop,
    gqrx_stream,
)
from .tools.decoders import (
    dump1090_start,
    dump1090_stop,
    rtl433_start,
    rtl433_stop,
    multimon_start,
    multimon_stop,
)

mcp = FastMCP("dragon-mcp", version="0.1.0")

# Shared stream manager — all tools that need streams share this instance
_streams = StreamManager()


# ── Stream management tools ────────────────────────────────────────────────

@mcp.tool()
def list_streams() -> str:
    """List all currently active web-view streams and their browser URLs.

    Returns a JSON object mapping tool name → {mode, url} for every live stream.
    """
    return json.dumps(_streams.list_streams(), indent=2)


@mcp.tool()
def start_stream(tool: str, mode: str = "mjpeg", pi_ip: str | None = None) -> str:
    """Start a live web-view stream for a running SDR tool and return the browser URL.

    Args:
        tool: Tool name — one of "gqrx", "dump1090", "rtl433", "multimon".
        mode: Stream mode —
              "mjpeg"  pure browser MJPEG (Flask + scrot screenshot loop, no plugins),
              "hls"    ffmpeg x11grab to HLS segments (requires ffmpeg + libx264),
              "audio"  ffmpeg PulseAudio to Icecast MP3 stream (requires Icecast2).
        pi_ip: LAN IP of this Pi to embed in the URL (auto-detected if omitted).

    Returns JSON with the browser URL.  An existing stream for the same tool is
    stopped and restarted in the new mode.
    """
    ip = pi_ip or _detect_local_ip()
    url = _streams.start(tool, mode, ip)  # type: ignore[arg-type]
    return json.dumps({"status": "streaming", "tool": tool, "mode": mode, "url": url}, indent=2)


@mcp.tool()
def stop_stream(tool: str) -> str:
    """Stop the live stream for the given tool.

    Args:
        tool: Tool name — one of "gqrx", "dump1090", "rtl433", "multimon".
    """
    return _streams.stop(tool)


# ── HackRF tools ───────────────────────────────────────────────────────────

@mcp.tool()
def hackrf_info_tool() -> str:
    """Return hardware info for the connected HackRF One (serial, firmware, board rev).

    Fails gracefully if the HackRF is held by GQRX — call gqrx_stop_tool first.
    """
    return hackrf_info()


@mcp.tool()
def hackrf_sweep_tool(
    freq_min_mhz: float,
    freq_max_mhz: float,
    gain: int = 32,
    bin_width_hz: int = 1_000_000,
) -> str:
    """Sweep a frequency range with the HackRF and return the top signals.

    Args:
        freq_min_mhz: Start frequency in MHz (e.g. 100).
        freq_max_mhz: End frequency in MHz (e.g. 1000).  HackRF covers up to 6000 MHz.
        gain: LNA gain in dB (0–40, default 32).
        bin_width_hz: Resolution per bin in Hz (default 1 MHz).

    Returns JSON with noise_floor_dbm and top_signals sorted by power.
    HackRF must not be in use by GQRX — call gqrx_stop_tool first if needed.
    """
    return hackrf_sweep(freq_min_mhz, freq_max_mhz, gain, bin_width_hz)


@mcp.tool()
def hackrf_transmit_tool(
    iq_file: str,
    freq_mhz: float,
    sample_rate_msps: float = 8.0,
    tx_gain: int = 20,
) -> str:
    """Replay a captured IQ file from the HackRF transmitter.

    Args:
        iq_file: Path to raw 8-bit signed IQ file (.iq / .cs8).
        freq_mhz: Transmit center frequency in MHz.
        sample_rate_msps: Sample rate in mega-samples/sec (default 8.0).
        tx_gain: TX VGA gain in dB (0–47, default 20).

    CAUTION: Transmitting on licensed frequencies without authorization is illegal.
    """
    return hackrf_transmit(iq_file, freq_mhz, sample_rate_msps, tx_gain)


# ── RTL-SDR tools ──────────────────────────────────────────────────────────

@mcp.tool()
def rtlsdr_info_tool() -> str:
    """List all connected RTL-SDR devices with their index, name, and serial number."""
    return rtlsdr_info()


@mcp.tool()
def rtlsdr_power_tool(
    freq_min_mhz: float,
    freq_max_mhz: float,
    device_index: int = 0,
    gain: int = 40,
    integration_sec: int = 5,
) -> str:
    """Scan a frequency range with an RTL-SDR dongle and return signal power per bin.

    Args:
        freq_min_mhz: Start frequency in MHz.
        freq_max_mhz: End frequency in MHz (RTL-SDR max ≈ 1766 MHz).
        device_index: RTL-SDR device index (0 = first dongle).
        gain: Tuner gain in dB (0–49; 0 = auto-gain).
        integration_sec: Integration time in seconds (default 5).

    Can run concurrently with HackRF tools — RTL-SDR is a separate device (GROUP_B).
    """
    return rtlsdr_power(freq_min_mhz, freq_max_mhz, device_index, gain, integration_sec)


@mcp.tool()
def rtlsdr_fm_tool(freq_mhz: float, device_index: int = 0, duration_sec: int = 10) -> str:
    """Capture FM audio from a given frequency and save it as a WAV file.

    Args:
        freq_mhz: FM station frequency in MHz (e.g. 98.7).
        device_index: RTL-SDR device index (default 0).
        duration_sec: Seconds of audio to capture (default 10).

    Returns the path to the saved WAV file.  Requires sox.
    """
    return rtlsdr_fm(freq_mhz, device_index, duration_sec)


# ── GQRX tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def gqrx_status_tool() -> str:
    """Get the current GQRX frequency, demodulation mode, and signal level.

    Requires GQRX running with remote control enabled (Tools → Remote control → Start).
    """
    return gqrx_get_status()


@mcp.tool()
def gqrx_tune_tool(freq_mhz: float, mode: str | None = None) -> str:
    """Tune GQRX to a frequency and optionally change the demodulation mode.

    Args:
        freq_mhz: Target frequency in MHz (e.g. 162.55 for NOAA weather).
        mode: Demodulation mode — FM, WFM, AM, USB, LSB, CW, RTTY (optional).
    """
    return gqrx_tune(freq_mhz, mode)


@mcp.tool()
def gqrx_squelch_tool(level_dbm: float) -> str:
    """Set the GQRX squelch threshold.

    Args:
        level_dbm: Squelch level in dBm (e.g. -60.0).  Signals below are muted.
    """
    return gqrx_set_squelch(level_dbm)


@mcp.tool()
def gqrx_start_tool() -> str:
    """Start GQRX, pre-patching its config so remote control port 7356 opens automatically.

    Finds a live X display automatically — works from SSH, VNC, and HDMI sessions.
    """
    return gqrx_start()


@mcp.tool()
def gqrx_stop_tool() -> str:
    """Stop GQRX and release the HackRF so sweep/capture tools can use it.

    Stops the headless systemd service and kills any desktop GQRX process.
    """
    return gqrx_stop()


@mcp.tool()
def gqrx_stream_tool(mode: str = "mjpeg", pi_ip: str | None = None) -> str:
    """Start a live view stream of the GQRX waterfall/spectrum and return the browser URL.

    Args:
        mode: "mjpeg" (pure browser), "hls" (ffmpeg x11grab), or "audio" (Icecast MP3).
        pi_ip: LAN IP of this Pi (auto-detected if omitted).
    """
    ip = pi_ip or _detect_local_ip()
    url = _streams.start("gqrx", mode, ip)  # type: ignore[arg-type]
    return json.dumps({"status": "streaming", "mode": mode, "url": url}, indent=2)


# ── Decoder tools ──────────────────────────────────────────────────────────

@mcp.tool()
def dump1090_start_tool(device_index: int = 0, gain: int = 40, stream: bool = False, pi_ip: str | None = None) -> str:
    """Start dump1090 to decode ADS-B aircraft transponders on 1090 MHz.

    Args:
        device_index: RTL-SDR dongle index (default 0).
        gain: Tuner gain in dB (0–49, default 40; 0 = auto).
        stream: If True, also start a MJPEG web view stream and include the URL.
        pi_ip: LAN IP for the stream URL (auto-detected if omitted).

    Returns the dump1090 HTTP map URL (port 8080) and optionally a stream URL.
    """
    return dump1090_start(device_index, gain, stream, pi_ip)


@mcp.tool()
def dump1090_stop_tool() -> str:
    """Stop the background dump1090 ADS-B decoder and release the RTL-SDR device."""
    return dump1090_stop()


@mcp.tool()
def rtl433_start_tool(
    freq_mhz: float = 433.92,
    device_index: int = 0,
    stream: bool = False,
    pi_ip: str | None = None,
) -> str:
    """Start rtl_433 to decode ISM-band sensors (weather stations, tire pressure, doorbells).

    Args:
        freq_mhz: Center frequency in MHz (default 433.92 EU ISM; use 915.0 for US).
        device_index: RTL-SDR dongle index (default 0).
        stream: If True, start a MJPEG web view and include the URL.
        pi_ip: LAN IP for the stream URL (auto-detected if omitted).

    Decoded packets are appended as JSON lines to ~/sdr-captures/rtl433.jsonl.
    """
    return rtl433_start(freq_mhz, device_index, stream, pi_ip)


@mcp.tool()
def rtl433_stop_tool() -> str:
    """Stop the background rtl_433 ISM-band sensor decoder."""
    return rtl433_stop()


@mcp.tool()
def multimon_start_tool(
    freq_mhz: float,
    modes: list[str] | None = None,
    device_index: int = 0,
    stream: bool = False,
    pi_ip: str | None = None,
) -> str:
    """Start multimon-ng to decode digital protocols (POCSAG, FLEX, DTMF, EAS, AFSK, etc.).

    Args:
        freq_mhz: Center frequency in MHz to monitor.
        modes: Decoder list (e.g. ["POCSAG512", "FLEX", "DTMF"]).
               Defaults to ["POCSAG512", "POCSAG1200", "FLEX"] when omitted.
        device_index: RTL-SDR dongle index (default 0).
        stream: If True, start a MJPEG web view and include the URL.
        pi_ip: LAN IP for the stream URL (auto-detected if omitted).

    Decoded messages are appended to ~/sdr-captures/multimon.log.
    """
    return multimon_start(freq_mhz, modes, device_index, stream, pi_ip)


@mcp.tool()
def multimon_stop_tool() -> str:
    """Stop the background multimon-ng decoder and its rtl_fm feed."""
    return multimon_stop()


# ── Helpers ────────────────────────────────────────────────────────────────

def _detect_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"


# ── Entry point ────────────────────────────────────────────────────────────

def run() -> None:
    mcp.run(transport="sse", host="0.0.0.0", port=8765)


if __name__ == "__main__":
    run()
