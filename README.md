# ai-for-dragons

AI-powered SDR tool bridge for [DragonOS Pi64](https://sourceforge.net/projects/dragonos-pi64/) + HackRF One.

Connects Claude Code (cloud) and local Ollama models to 70+ DragonOS tools via the [Model Context Protocol](https://modelcontextprotocol.io/).

```
"Sweep 88–108 MHz, find the strongest signal, open it in GQRX"
→ dragon-agent handles the full stop/sweep/start/tune sequence automatically
```

## What it does

- **MCP server** — exposes DragonOS tools to Claude Code as callable functions
- **Offline agent** — local AI assistant using Ollama (qwen3:4b default, works without internet)
- **71 tools** across 6 categories: HackRF, GQRX, app management, aviation/maritime decoding, protocol interpretation, and exercises
- **Hardware exclusivity management** — automatically stops/starts apps around sweeps so you never fight over the HackRF

## Hardware

| Component | Notes |
|---|---|
| Raspberry Pi 5 (16 GB) | 8 GB minimum |
| HackRF One | Plug into blue USB 3.0 port |
| Active cooler | Required — installer checks temperature |
| 32 GB+ SD card (A2-rated) | 128 GB recommended |
| DragonOS Pi64 | Pre-flashed on SD card |

## Quick start

```bash
# On the Pi after flashing DragonOS Pi64:
sudo raspi-config --expand-rootfs && sudo reboot

# Copy the release zip to the Pi, then:
unzip ai-for-dragons-pi5.zip && cd ai-for-dragons-pi5-bundle
bash ai-for-dragons-install-pi5.sh      # installs everything (~15 min first run)

# Start the AI agent
dragon-agent

# Or with a faster/smarter model
dragon-agent --model qwen3:1.7b
dragon-agent --model qwen3:8b
```

## Install from PyPI (advanced)

```bash
pip install ai-for-dragons
```

Requires DragonOS Pi64 or equivalent DragonOS environment with HackRF tools installed.

## Tool categories

### HackRF (5 tools)
`hackrf_info` `hackrf_sweep` `hackrf_capture` `hackrf_analyze` `hackrf_replay`

### GQRX control (8 tools)
`gqrx_status` `gqrx_tune` `gqrx_set_frequency` `gqrx_set_mode` `gqrx_set_squelch` `gqrx_record` `gqrx_stop` `gqrx_start`

### App management (30+ tools)
SDRAngel, SDR++, CubicSDR, GNU Radio, QSpectrumAnalyzer, inspectrum, URH, SatDump, SigDigger, OpenWebRX+, Wireshark, Kismet, WSJT-X, Gpredict, fldigi, QSSTV, dump1090, rtl_433, multimon-ng, DSD-FME, JAERO, rtl-ais, dumpvdl2, dumphfdl, ...

### Protocol interpretation (7 tools)
`interpret_adsb` `interpret_ais` `interpret_acars` `interpret_pocsag` `interpret_meshtastic` `explain_hex` `identify_frequency`

### DragonOS tools (5 tools)
`signal_identify` `meshtastic_sniff` `adsb_scan` `gsm_scan` `flowgraph_run`

### Exercises (2 tools)
`exercise_list` `exercise_get` — 9 built-in SDR learning exercises (Beginner → Advanced)

## Using with Claude Code

After install, the MCP server is configured automatically. In Claude Code:

```
/mcp          ← confirm "sdr" appears in the list
```

Then just talk naturally:
```
"What aircraft are overhead right now?"
"Sweep the ISM band and identify any active sensors"
"Decode this hex frame: 8D4840D6202CC371C32CE0576098"
"Walk me through exercise ex03_adsb"
```

## Architecture

```
Claude Code / Ollama
       │
   ai-for-dragons (MCP stdio server)
       │
   ┌───┴──────────────────────────┐
   │  hackrf.py    gqrx.py        │
   │  dragonos.py  app_manager.py │
   │  protocol_interpreter.py     │
   └───────────────────────────────┘
       │
   DragonOS Pi64 tools
   (GQRX, dump1090, GNU Radio, etc.)
```

## Development

```bash
git clone https://github.com/pbarsamian/ai-for-dragons
cd ai-for-dragons
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python test_tools.py
```

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

Areas where contributions are most useful:
- New protocol interpreters (DMR, P25, APRS, RDS)
- Additional exercise scenarios
- Testing on hardware other than HackRF One
- OpenWebRX+ and other tool integrations

## Legal

Always verify local regulations before transmitting. In the US, FCC Part 15 governs unlicensed ISM band operation. An amateur radio Technician license significantly expands legal operating options.

This tool is for educational and research use. Do not use for unlicensed interception of private communications.

## License

MIT — see [LICENSE](LICENSE)
