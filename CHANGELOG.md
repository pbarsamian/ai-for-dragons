# Changelog

All notable changes to ai-for-dragons are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `hackrf_sweep` / `_check_hackrf_free()` hang when HackRF USB device is in kernel D-state — replaced `subprocess.run(timeout=)` with a bounded `Popen` pattern so both `communicate()` calls have explicit timeouts; the hang occurred because `subprocess.run` drains pipes without a timeout after its internal kill, blocking forever if the device is uninterruptible

## [0.3.0] — 2025

### Added
- 71 tools total across 6 categories
- `protocol_interpreter.py` — new interpretation layer:
  - `interpret_adsb` — full ADS-B/Mode S hex frame decoder with CRC validation
  - `interpret_ais` — AIS NMEA sentence decoder (Types 1/2/3/5/18)
  - `interpret_acars` — ACARS label code lookup and CPDLC detection
  - `interpret_pocsag` — POCSAG pager cap code and message interpreter
  - `interpret_meshtastic` — Meshtastic sniffer JSON packet decoder
  - `explain_hex` — byte-by-byte hex breakdown with auto protocol detection
  - `identify_frequency` — band table lookup with tool recommendations
- `app_manager.py` — unified GUI app lifecycle management:
  - Hardware exclusivity groups (GROUP A/B) with automatic conflict resolution
  - `gqrx_stop/start`, `sdrangel_start/stop`, `sdrpp_start/stop`
  - `cubicsdr_start/stop`, `qspectrumanalyzer_start/stop`
  - `gnuradio_open/stop`, `inspectrum_open/stop`, `urh_open/stop`
  - `satdump_open/stop`, `sigdigger_open/stop`, `openwebrx_start/stop`
  - `wireshark_open/stop`, `kismet_start/stop`, `wsjtx_start/stop`
  - `gpredict_start/stop`, `qsstv_start/stop`, `fldigi_start/stop`
  - `rtl433_start`, `multimon_decode`, `dsdfme_decode`
  - `dump1090_start/stop`, `jaero_start/stop`
  - `rtlais_start`, `dumphfdl_start`, `dumpvdl2_start`
  - `app_status` — unified status for all managed apps + port checks
- Autostart setup script (`ai-for-dragons-autostart.sh`):
  - Systemd user services for Ollama, SoapySDR, GQRX headless, Xvfb
  - `loginctl enable-linger` for boot persistence
  - Desktop/SSH coexistence via session detection
- Installer improvements:
  - idempotent re-run (skip already-done steps)
  - `/usr/local/bin` symlinks for system-wide PATH
  - PATH written to `.bashrc`, `.profile`, `.bash_profile`
  - `--fix-broken` apt before package installs
  - Direct venv copy (no setuptools build step)
  - `netcat-openbsd`, `lsof`, `usbutils` added to apt installs

### Fixed
- `hackrf_sweep` hang — uses `Popen` with `communicate(timeout=8)` instead of `-n` flag
- `_check_hackrf_free()` hang — removed `lsof /dev/hackrf0` (path doesn't exist on DragonOS)
- `nc -q1` → `nc -w2` throughout (openbsd netcat flag difference)
- GQRX `configparser.DuplicateOptionError` — opened with `strict=False`
- `qwen3:1.7b` model name resolution — fuzzy prefix matching in `check_ollama()`
- Spinner and 120s timeout in `ollama_agent` chat loop

## [0.2.0] — 2025

### Added
- 20 initial tools: HackRF, GQRX, DragonOS protocols, exercises
- MCP stdio server (`server.py`)
- Ollama offline agent (`ollama_agent.py`)
- Raspberry Pi 5 installer script

## [0.1.0] — 2025

### Added
- Initial proof of concept
