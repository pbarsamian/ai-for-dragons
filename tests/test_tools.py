"""
Tests for tool registry structure and graceful failure without hardware.
"""
import json
import pytest
from sdr_mcp.tools import TOOL_REGISTRY, execute_tool


REQUIRED_KEYS = {"description", "schema", "fn"}
SAFE_TOOLS = [
    ("exercise_list", {}),
    ("exercise_list", {"level": "Beginner"}),
    ("exercise_get",  {"exercise_id": "ex01_fm"}),
    ("exercise_get",  {"exercise_id": "ex11_meshtastic_rx"}),
    ("signal_identify", {"freq_mhz": 906.875}),
    ("signal_identify", {"freq_mhz": 98.1}),
    ("signal_identify", {"freq_mhz": 1090.0}),
    ("identify_frequency", {"freq_mhz": 98.1}),
    ("identify_frequency", {"freq_mhz": 1090.0}),
    ("interpret_adsb", {"hex_frame": "8D4840D6202CC371C32CE0576098"}),
    ("interpret_ais",  {"nmea_sentence": "!AIVDM,1,1,,B,15M67N0000G?Uf6E`FepT@0N0<0f,0*23"}),
    ("explain_hex",    {"hex_data": "48656C6C6F"}),
]
HARDWARE_TOOLS = [
    ("hackrf_info",  {}),
    ("gqrx_status",  {}),
    ("meshtastic_sniff", {"duration_sec": 2}),
    ("adsb_scan",    {"duration_sec": 2}),
]


def test_tool_count():
    """At least 60 tools should be registered."""
    assert len(TOOL_REGISTRY) >= 60, f"Only {len(TOOL_REGISTRY)} tools registered"


def test_all_tools_have_required_keys():
    for name, spec in TOOL_REGISTRY.items():
        missing = REQUIRED_KEYS - set(spec.keys())
        assert not missing, f"Tool '{name}' missing: {missing}"


def test_all_tools_have_callable_fn():
    for name, spec in TOOL_REGISTRY.items():
        assert callable(spec["fn"]), f"Tool '{name}' fn is not callable"


def test_all_tools_have_nonempty_description():
    for name, spec in TOOL_REGISTRY.items():
        assert spec["description"].strip(), f"Tool '{name}' has empty description"
        assert len(spec["description"]) >= 20, f"Tool '{name}' description too short"


def test_all_tools_have_valid_schema():
    for name, spec in TOOL_REGISTRY.items():
        schema = spec["schema"]
        assert isinstance(schema, dict), f"Tool '{name}' schema is not a dict"
        assert schema.get("type") == "object", f"Tool '{name}' schema type must be 'object'"
        assert "properties" in schema, f"Tool '{name}' schema missing 'properties'"
        assert "required" in schema, f"Tool '{name}' schema missing 'required'"


@pytest.mark.parametrize("tool_name,args", SAFE_TOOLS)
def test_safe_tool_returns_string(tool_name, args):
    result = execute_tool(tool_name, args)
    assert isinstance(result, str), f"{tool_name} returned {type(result)}"
    assert len(result) > 0, f"{tool_name} returned empty string"


@pytest.mark.parametrize("tool_name,args", HARDWARE_TOOLS)
def test_hardware_tool_fails_gracefully(tool_name, args):
    """Hardware tools must return a string, never raise, even without hardware."""
    try:
        result = execute_tool(tool_name, args)
        assert isinstance(result, str), f"{tool_name} returned {type(result)}"
    except Exception as e:
        pytest.fail(f"{tool_name} raised {type(e).__name__}: {e}")


def test_exercise_list_returns_json():
    result = execute_tool("exercise_list", {})
    exercises = json.loads(result)
    assert isinstance(exercises, list)
    assert len(exercises) > 0
    for ex in exercises:
        assert "id" in ex
        assert "title" in ex
        assert "level" in ex


def test_exercise_get_unknown_returns_helpful_message():
    result = execute_tool("exercise_get", {"exercise_id": "does_not_exist"})
    assert "not found" in result.lower() or "available" in result.lower()


def test_identify_frequency_returns_json():
    result = execute_tool("identify_frequency", {"freq_mhz": 433.92})
    data = json.loads(result)
    assert data["freq_mhz"] == 433.92
    assert "band_matches" in data
