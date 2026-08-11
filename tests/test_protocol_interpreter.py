"""
Tests for protocol_interpreter.py — no hardware required.
"""
import json
import pytest
from sdr_mcp.protocol_interpreter import (
    interpret_adsb,
    interpret_ais,
    interpret_acars,
    interpret_pocsag,
    interpret_meshtastic,
    explain_hex,
    identify_frequency,
)


# ── ADS-B ────────────────────────────────────────────────────────────────

def test_interpret_adsb_identification():
    # Real ADS-B identification frame
    result = json.loads(interpret_adsb("8D4840D6202CC371C32CE0576098"))
    assert result["downlink_format"] == 17
    assert "icao_address" in result
    assert result["icao_address"] == "4840D6"
    assert result["type_code"] == 4

def test_interpret_adsb_short_frame():
    result = json.loads(interpret_adsb("A0001910"))
    assert "downlink_format" in result

def test_interpret_adsb_invalid():
    result = json.loads(interpret_adsb("ZZZZ"))
    assert "error" in result or "Invalid" in str(result)

def test_interpret_adsb_with_asterisk():
    # dump1090 sometimes prepends *
    result = json.loads(interpret_adsb("*8D4840D6202CC371C32CE0576098;"))
    assert result["icao_address"] == "4840D6"


# ── AIS ──────────────────────────────────────────────────────────────────

def test_interpret_ais_type1():
    sentence = "!AIVDM,1,1,,B,15M67N0000G?Uf6E`FepT@0N0<0f,0*23"
    result = json.loads(interpret_ais(sentence))
    assert result["message_type"] == 1
    assert "mmsi" in result
    assert "nav_status" in result

def test_interpret_ais_not_sentence():
    result = json.loads(interpret_ais("not a sentence"))
    assert "error" in result

def test_interpret_ais_mmsi_type():
    sentence = "!AIVDM,1,1,,B,15M67N0000G?Uf6E`FepT@0N0<0f,0*23"
    result = json.loads(interpret_ais(sentence))
    assert "mmsi_type" in result


# ── ACARS ────────────────────────────────────────────────────────────────

def test_interpret_acars_label():
    msg = "Mode: 2\nLabel: H1\nAircraft reg: N12345\nFlight id: AA1234\nMsg: POSITION REPORT"
    result = json.loads(interpret_acars(msg))
    assert result.get("label") == "H1"
    assert "ACARS" in result.get("label_description", "")
    assert result.get("registration") == "N12345"
    assert result.get("flight_id") == "AA1234"

def test_interpret_acars_cpdlc_detection():
    msg = "Label: 35\nMsg: CPDLC CLRNCE REQUEST"
    result = json.loads(interpret_acars(msg))
    assert "CPDLC" in result.get("label_description", "") or \
           "CPDLC" in result.get("protocol", "")

def test_interpret_acars_unknown_label():
    msg = "Label: ZZ\nMsg: test"
    result = json.loads(interpret_acars(msg))
    assert "ZZ" in str(result)


# ── POCSAG ───────────────────────────────────────────────────────────────

def test_interpret_pocsag_alpha():
    line = "POCSAG1200: Address:  1234567  Function: 3  Alpha:   Hello World"
    result = json.loads(interpret_pocsag(line))
    assert result["baud_rate"] == 1200
    assert result["cap_code"] == 1234567
    assert result["function_type"] == "Alphanumeric"
    assert result["message"] == "Hello World"

def test_interpret_pocsag_numeric():
    line = "POCSAG512: Address:  9999999  Function: 0  Numeric:   12345"
    result = json.loads(interpret_pocsag(line))
    assert result["baud_rate"] == 512
    assert "numeric_message" in result

def test_interpret_pocsag_empty():
    result = json.loads(interpret_pocsag("not a pocsag line"))
    assert isinstance(result, dict)


# ── Meshtastic ───────────────────────────────────────────────────────────

def test_interpret_meshtastic_text():
    pkt = json.dumps({
        "from": 0x12345678,
        "to": 0xFFFFFFFF,
        "portnum": 1,
        "snr": 8.5,
        "hopLimit": 3,
        "decoded": {"text": "Hello mesh"},
    })
    result = json.loads(interpret_meshtastic(pkt))
    assert result["to"] == "BROADCAST (all nodes)"
    assert result["application"] == "TEXT_MESSAGE_APP"
    assert result["signal_quality"] == "Strong"
    assert result["text_message"] == "Hello mesh"

def test_interpret_meshtastic_position():
    pkt = json.dumps({
        "from": 0xABCDEF01,
        "portnum": 3,
        "snr": 2.0,
        "decoded": {"position": {"latitudeI": 374200000, "longitudeI": -1220800000, "altitude": 50}},
    })
    result = json.loads(interpret_meshtastic(pkt))
    assert "position" in result
    pos = result["position"]
    assert abs(pos["latitude"] - 37.42) < 0.01
    assert abs(pos["longitude"] - (-122.08)) < 0.01

def test_interpret_meshtastic_invalid_json():
    result = json.loads(interpret_meshtastic("not json"))
    assert "error" in result


# ── explain_hex ───────────────────────────────────────────────────────────

def test_explain_hex_basic():
    result = json.loads(explain_hex("48656C6C6F"))
    assert result["ascii_printable"] == "Hello"
    assert result["length_bytes"] == 5

def test_explain_hex_adsb_autodetect():
    result = json.loads(explain_hex("8D4840D6202CC371C32CE0576098"))
    # Should auto-detect as ADS-B
    assert result.get("downlink_format") == 17 or "icao_address" in result

def test_explain_hex_invalid():
    result = json.loads(explain_hex("ZZZZ"))
    assert "error" in result

def test_explain_hex_protocol_hint():
    result = json.loads(explain_hex("8D4840D6202CC371C32CE0576098", "adsb"))
    assert "downlink_format" in result


# ── identify_frequency ────────────────────────────────────────────────────

def test_identify_fm_band():
    result = json.loads(identify_frequency(98.1))
    assert result["freq_mhz"] == 98.1
    assert any("FM" in m["band"] or "fm" in m["description"].lower()
               for m in result["band_matches"])

def test_identify_adsb():
    result = json.loads(identify_frequency(1090.0))
    assert any("ADS-B" in m["band"] or "ADS-B" in m["description"]
               for m in result["band_matches"])
    assert "dump1090" in str(result.get("suggested_tools", []))

def test_identify_meshtastic():
    result = json.loads(identify_frequency(906.875))
    assert any("915" in m["band"] or "Meshtastic" in m["description"]
               for m in result["band_matches"])

def test_identify_unknown():
    result = json.loads(identify_frequency(4999.0))
    assert "freq_mhz" in result
    assert "band_matches" in result
