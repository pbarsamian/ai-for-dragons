#!/usr/bin/env python3
"""
Smoke test for sdr-mcp — validates all 18 tools load and fail gracefully
when hardware (HackRF, GQRX) is not present.

Run: python3 test_tools.py
"""

import sys
import json
from sdr_mcp.tools import TOOL_REGISTRY, execute_tool

PASS = "✓"
FAIL = "✗"
SKIP = "○"

results = {"pass": 0, "fail": 0, "skip": 0}

print("\n── sdr-mcp smoke test ──────────────────────────────\n")

# Verify all 18 tools are registered
expected = 18
actual   = len(TOOL_REGISTRY)
if actual == expected:
    print(f"{PASS} Tool registry: {actual} tools registered")
    results["pass"] += 1
else:
    print(f"{FAIL} Tool registry: expected {expected}, found {actual}")
    results["fail"] += 1

# Verify each tool has required fields
for name, spec in TOOL_REGISTRY.items():
    missing = [f for f in ("description", "schema", "fn") if f not in spec]
    if missing:
        print(f"{FAIL} {name}: missing {missing}")
        results["fail"] += 1
    else:
        results["pass"] += 1

print(f"\n── Tool execution (hardware not required) ──────────\n")

# Tests that don't need hardware
SAFE_TESTS = [
    ("exercise_list", {}),
    ("exercise_list", {"level": "Beginner"}),
    ("exercise_get",  {"exercise_id": "ex01_fm"}),
    ("exercise_get",  {"exercise_id": "ex11_meshtastic_rx"}),
    ("signal_identify", {"freq_mhz": 906.875}),
    ("signal_identify", {"freq_mhz": 98.1}),
    ("signal_identify", {"freq_mhz": 1090.0}),
]

for tool_name, args in SAFE_TESTS:
    try:
        result = execute_tool(tool_name, args)
        assert isinstance(result, str) and len(result) > 0
        print(f"{PASS} {tool_name}({args}) → {len(result)} chars")
        results["pass"] += 1
    except Exception as e:
        print(f"{FAIL} {tool_name}({args}) → {e}")
        results["fail"] += 1

# Tests that require hardware — verify they fail gracefully (not crash)
print(f"\n── Hardware tools (expected: graceful failure) ─────\n")

HARDWARE_TESTS = [
    ("hackrf_info",  {}),
    ("hackrf_sweep", {"freq_min_mhz": 88, "freq_max_mhz": 108}),
    ("gqrx_status",  {}),
    ("meshtastic_sniff", {"duration_sec": 2}),
    ("adsb_scan",    {"duration_sec": 2}),
    ("gsm_scan",     {}),
]

for tool_name, args in HARDWARE_TESTS:
    try:
        result = execute_tool(tool_name, args)
        # Should return a string (error message or result), not raise
        assert isinstance(result, str)
        # Should mention the issue, not crash silently
        if any(kw in result.lower() for kw in ("not found", "not connected", "error", "failed", "not running")):
            print(f"{PASS} {tool_name} → graceful failure: {result[:60].strip()!r}")
        else:
            print(f"{SKIP} {tool_name} → hardware may be connected: {result[:60].strip()!r}")
        results["pass"] += 1
    except Exception as e:
        # Any exception is a failure — tools must not crash
        print(f"{FAIL} {tool_name} → CRASH: {e}")
        results["fail"] += 1

# Summary
print(f"\n── Results ─────────────────────────────────────────\n")
total = results["pass"] + results["fail"]
print(f"  Passed: {results['pass']}/{total}")
if results["fail"] > 0:
    print(f"  Failed: {results['fail']}")
    sys.exit(1)
else:
    print(f"  All tests passed.")
    sys.exit(0)
