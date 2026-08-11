"""
Protocol interpretation layer for sdr-mcp.

Takes raw decoded output (hex frames, NMEA sentences, JSON blobs) and
returns human-readable explanations of what each field means.

This is the layer between "bytes received" and "understanding what happened."
No hardware access — all pure Python interpretation.
"""

import json
import re
import struct
from datetime import datetime, timezone


# ── ADS-B / Mode S ────────────────────────────────────────────────────────

# Type codes → description
ADSB_TC = {
    range(1,  5):  "Aircraft identification (callsign)",
    range(5,  9):  "Surface position",
    range(9,  19): "Airborne position (barometric altitude)",
    range(19, 23): "Airborne velocity",
    range(20, 23): "Airborne position (GNSS altitude)",
    range(23, 28): "Reserved",
    range(28, 29): "Aircraft status",
    range(29, 31): "Target state and status",
    range(31, 32): "Aircraft operation status",
}

WAKE_TURBULENCE = {
    0: "No category info",
    1: "Light (<7,000kg)",
    2: "Small (7,000–34,000kg)",
    3: "Large (34,000–136,000kg)",
    4: "High vortex large",
    5: "Heavy (>136,000kg)",
    6: "High performance (>5g, >400kt)",
    7: "Rotorcraft",
}

CALLSIGN_CHARS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"


def _crc24(data: bytes) -> int:
    crc = 0xFFF409
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0xFFF409
    return crc & 0xFFFFFF


def interpret_adsb(hex_frame: str) -> str:
    """
    Interpret a raw ADS-B / Mode S hex frame.
    Decodes ICAO address, downlink format, type code, and payload fields.
    """
    hex_frame = hex_frame.strip().replace(" ", "").upper()
    if hex_frame.startswith("*"):
        hex_frame = hex_frame[1:]
    if hex_frame.endswith(";"):
        hex_frame = hex_frame[:-1]

    try:
        data = bytes.fromhex(hex_frame)
    except ValueError:
        return f"Invalid hex frame: {hex_frame}"

    result = {"raw": hex_frame, "length_bytes": len(data)}

    if len(data) < 3:
        return json.dumps({"error": "Frame too short", "raw": hex_frame}, indent=2)

    df = (data[0] >> 3) & 0x1F
    result["downlink_format"] = df

    df_names = {
        0:  "Short Air-to-Air Surveillance",
        4:  "Surveillance Altitude Reply",
        5:  "Surveillance Identity Reply",
        11: "All-Call Reply",
        16: "Long Air-to-Air Surveillance",
        17: "ADS-B Message (Extended Squitter)",
        18: "TIS-B Message",
        19: "Military Extended Squitter",
        20: "Comm-B Altitude Reply",
        21: "Comm-B Identity Reply",
        24: "Comm-D Extended Length Message",
    }
    result["downlink_format_name"] = df_names.get(df, f"Reserved/Unknown ({df})")

    if len(data) >= 4:
        icao = (data[1] << 16) | (data[2] << 8) | data[3]
        result["icao_address"] = f"{icao:06X}"

    if df == 17 and len(data) == 14:
        # Extended squitter — full ADS-B decode
        me = data[4:11]
        tc = (me[0] >> 3) & 0x1F
        result["type_code"] = tc

        tc_desc = "Unknown"
        for r, desc in ADSB_TC.items():
            if tc in r:
                tc_desc = desc
                break
        result["type_code_description"] = tc_desc

        if 1 <= tc <= 4:
            # Identification — callsign
            ec = (me[0] & 0x07)
            result["emitter_category"] = WAKE_TURBULENCE.get(ec, f"Category {ec}")
            cs = ""
            for i in range(8):
                idx = (me[1 + i // 2] >> (4 if i % 2 == 0 else 0)) & 0x3F
                if i == 0:
                    idx = (me[1] >> 2) & 0x3F
                elif i == 1:
                    idx = ((me[1] & 0x03) << 4) | ((me[2] >> 4) & 0x0F)
                elif i == 2:
                    idx = ((me[2] & 0x0F) << 2) | ((me[3] >> 6) & 0x03)
                elif i == 3:
                    idx = me[3] & 0x3F
                elif i == 4:
                    idx = (me[4] >> 2) & 0x3F
                elif i == 5:
                    idx = ((me[4] & 0x03) << 4) | ((me[5] >> 4) & 0x0F)
                elif i == 6:
                    idx = ((me[5] & 0x0F) << 2) | ((me[6] >> 6) & 0x03)
                elif i == 7:
                    idx = me[6] & 0x3F
                if idx < len(CALLSIGN_CHARS):
                    cs += CALLSIGN_CHARS[idx]
            result["callsign"] = cs.strip()

        elif 9 <= tc <= 18:
            # Airborne position
            alt_code = ((me[1] << 4) | (me[2] >> 4)) & 0x1FFF
            q_bit = (alt_code >> 4) & 1
            if q_bit:
                n = ((alt_code & 0x1F80) >> 2) | (alt_code & 0x3F)
                result["altitude_ft"] = n * 25 - 1000
            else:
                result["altitude_ft"] = "Gillham coded (complex decode)"
            f_flag = (me[2] >> 2) & 1
            result["cpr_frame"] = "odd" if f_flag else "even"
            result["cpr_note"] = "Two consecutive frames (odd+even) needed to compute position"

        elif tc == 19:
            # Velocity
            subtype = me[0] & 0x07
            if subtype in (1, 2):
                dew = ((me[1] & 0x03) << 8) | me[2]
                dns = ((me[3] & 0x7F) << 3) | (me[4] >> 5)
                vr  = ((me[4] & 0x1F) << 4) | (me[5] >> 4)
                result["ground_speed_kt"] = dew
                result["vertical_rate_fpm"] = vr * 64
                result["velocity_type"] = "ground speed" if subtype == 1 else "airspeed"

        # CRC check
        recv_crc = (data[11] << 16) | (data[12] << 8) | data[13]
        calc_crc = _crc24(data[:11])
        result["crc"] = "valid" if recv_crc == calc_crc else f"invalid (got {recv_crc:06X}, expected {calc_crc:06X})"

    return json.dumps(result, indent=2)


# ── AIS ───────────────────────────────────────────────────────────────────

AIS_MSG_TYPES = {
    1:  "Position Report Class A",
    2:  "Position Report Class A (Assigned)",
    3:  "Position Report Class A (Response to interrogation)",
    4:  "Base Station Report",
    5:  "Static and Voyage Related Data",
    6:  "Binary Addressed Message",
    7:  "Binary Acknowledge",
    8:  "Binary Broadcast Message",
    9:  "Standard SAR Aircraft Position Report",
    14: "Safety Related Broadcast Message",
    18: "Standard Class B CS Position Report",
    21: "Aid-to-Navigation Report",
    24: "Class B CS Static Data Report",
}

NAV_STATUS = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuverability",
    4: "Constrained by her draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in Fishing",
    8: "Under way sailing",
    15: "Not defined",
}

SHIP_TYPES = {
    range(20, 30): "Wing in ground",
    range(30, 40): "Fishing",
    range(40, 50): "Towing",
    range(50, 60): "Diving/dredging/underwater",
    range(60, 70): "Passenger ship",
    range(70, 80): "Cargo ship",
    range(80, 90): "Tanker",
    range(90, 100): "Other",
}


def _ais_bits(payload: str) -> str:
    bits = ""
    for ch in payload:
        v = ord(ch) - 48
        if v > 39:
            v -= 8
        bits += format(v, "06b")
    return bits


def _ais_int(bits: str, start: int, length: int, signed: bool = False) -> int:
    val = int(bits[start:start + length], 2)
    if signed and bits[start] == "1":
        val -= (1 << length)
    return val


def _ais_str(bits: str, start: int, length: int) -> str:
    s = ""
    for i in range(0, length, 6):
        v = int(bits[start + i:start + i + 6], 2)
        s += "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"[v]
    return s.strip().rstrip("@")


def interpret_ais(nmea_sentence: str) -> str:
    """
    Interpret a raw AIS NMEA sentence (!AIVDM or !AIVDO).
    Decodes message type, MMSI, position, vessel name, status.
    """
    nmea_sentence = nmea_sentence.strip()
    if not nmea_sentence.startswith("!"):
        return json.dumps({"error": "Not an AIS sentence", "input": nmea_sentence}, indent=2)

    parts = nmea_sentence.split(",")
    if len(parts) < 6:
        return json.dumps({"error": "Malformed AIS sentence"}, indent=2)

    payload = parts[5].split("!")[0]
    if not payload:
        return json.dumps({"error": "Empty payload"}, indent=2)

    try:
        bits = _ais_bits(payload)
    except Exception as e:
        return json.dumps({"error": f"Payload decode failed: {e}"}, indent=2)

    if len(bits) < 28:
        return json.dumps({"error": "Payload too short"}, indent=2)

    msg_type = _ais_int(bits, 0, 6)
    mmsi     = _ais_int(bits, 8, 30)

    result = {
        "sentence": nmea_sentence,
        "message_type": msg_type,
        "message_type_description": AIS_MSG_TYPES.get(msg_type, f"Type {msg_type}"),
        "mmsi": str(mmsi).zfill(9),
    }

    # MMSI type decode
    mmsi_s = str(mmsi).zfill(9)
    if mmsi_s.startswith("00"):
        result["mmsi_type"] = "Group / coast station"
    elif mmsi_s.startswith("0"):
        result["mmsi_type"] = "Coast / ground station"
    elif mmsi_s.startswith("8"):
        result["mmsi_type"] = "Handheld transceiver"
    elif mmsi_s.startswith("111"):
        result["mmsi_type"] = "SAR aircraft"
    else:
        result["mmsi_type"] = "Ship"
        result["country_mid"] = mmsi_s[:3]  # Maritime Identification Digit

    if msg_type in (1, 2, 3) and len(bits) >= 168:
        status = _ais_int(bits, 38, 4)
        result["nav_status"] = NAV_STATUS.get(status, f"Status {status}")
        sog = _ais_int(bits, 50, 10) / 10.0
        result["speed_over_ground_kt"] = sog if sog < 102.2 else "not available"
        lat = _ais_int(bits, 89, 27, signed=True) / 600000.0
        lon = _ais_int(bits, 61, 28, signed=True) / 600000.0
        result["latitude"] = round(lat, 6) if abs(lat) <= 90 else "not available"
        result["longitude"] = round(lon, 6) if abs(lon) <= 180 else "not available"
        cog = _ais_int(bits, 116, 12) / 10.0
        result["course_over_ground_deg"] = cog if cog < 360 else "not available"
        hdg = _ais_int(bits, 128, 9)
        result["true_heading_deg"] = hdg if hdg < 360 else "not available"

    elif msg_type == 5 and len(bits) >= 420:
        result["imo_number"] = _ais_int(bits, 40, 30)
        result["callsign"] = _ais_str(bits, 70, 42)
        result["vessel_name"] = _ais_str(bits, 112, 120)
        ship_type = _ais_int(bits, 232, 8)
        ship_type_desc = f"Type {ship_type}"
        for r, desc in SHIP_TYPES.items():
            if ship_type in r:
                ship_type_desc = desc
                break
        result["ship_type"] = ship_type_desc
        result["destination"] = _ais_str(bits, 302, 120)
        eta_month = _ais_int(bits, 274, 4)
        eta_day   = _ais_int(bits, 278, 5)
        eta_hour  = _ais_int(bits, 283, 5)
        eta_min   = _ais_int(bits, 288, 6)
        result["eta"] = f"Month {eta_month} Day {eta_day} {eta_hour:02d}:{eta_min:02d} UTC"
        draught = _ais_int(bits, 294, 8) / 10.0
        result["draught_m"] = draught

    elif msg_type == 18 and len(bits) >= 168:
        sog = _ais_int(bits, 46, 10) / 10.0
        result["speed_over_ground_kt"] = sog if sog < 102.2 else "not available"
        lat = _ais_int(bits, 85, 27, signed=True) / 600000.0
        lon = _ais_int(bits, 57, 28, signed=True) / 600000.0
        result["latitude"] = round(lat, 6) if abs(lat) <= 90 else "not available"
        result["longitude"] = round(lon, 6) if abs(lon) <= 180 else "not available"
        result["vessel_class"] = "Class B"

    return json.dumps(result, indent=2)


# ── ACARS ─────────────────────────────────────────────────────────────────

ACARS_LABELS = {
    "AA": "Acknowledged (positive)",
    "BA": "Negative acknowledge",
    "B1": "Oceanic ADS-C position report",
    "B2": "Oceanic ADS-C demand contract",
    "B6": "D-ATIS (digital ATIS)",
    "10": "Datalink logon/logoff",
    "13": "Engine data",
    "20": "Fuel data",
    "21": "Fuel prediction",
    "30": "Oceanic clearance",
    "35": "CPDLC (Controller-Pilot Datalink Communications)",
    "44": "Cabin crew call",
    "4A": "ATIS request",
    "4T": "Weather data",
    "5U": "Departure clearance",
    "5X": "Pre-departure clearance",
    "80": "Aircraft position report (ADS)",
    "83": "Height monitoring",
    "H1": "ACARS standard message",
    "Q0": "New aircraft logon",
    "QA": "ACARS logon acknowledgment",
    "QD": "ACARS logoff",
    "QN": "ACARS network status",
    "QS": "ACARS sublabel message",
    "QT": "ACARS test/heartbeat",
    "QU": "ACARS unit test",
    "QX": "ACARS extended address",
    "RA": "ATC safety message (CPDLC)",
    "S1": "Navigation data",
    "SA": "Weather request",
    "SQ": "Squawk code assignment",
    "W0": "Weather observation (PIREP)",
    "_d": "Position report (lat/lon encoded)",
}


def interpret_acars(raw_message: str) -> str:
    """
    Interpret a raw ACARS message string.
    Decodes label, registration, flight ID, and message content.
    """
    raw_message = raw_message.strip()
    result = {"raw": raw_message}

    # Try to parse structured ACARS format
    # Common format: Mode/Label/Block-ID/Ack/Reg/Flight/Content
    lines = raw_message.splitlines()

    for line in lines:
        line = line.strip()
        if line.startswith("Aircraft reg:") or "Aircraft reg:" in line:
            result["registration"] = line.split("Aircraft reg:")[-1].strip().split()[0]
        if line.startswith("Flight id:") or "Flight id:" in line:
            result["flight_id"] = line.split("Flight id:")[-1].strip().split()[0]
        if line.startswith("Label:") or "Label:" in line:
            label = line.split("Label:")[-1].strip().split()[0]
            result["label"] = label
            result["label_description"] = ACARS_LABELS.get(label, f"Unknown label ({label})")
        if line.startswith("Mode:") or "Mode:" in line:
            result["mode"] = line.split("Mode:")[-1].strip().split()[0]
        if line.startswith("Msg:") or "Message:" in line:
            result["message_content"] = line.split(":", 1)[-1].strip()

    # Look for position data in content
    pos_match = re.search(r"(\d{2,4}[NS]\d{3,5}[EW])", raw_message)
    if pos_match:
        result["position_encoded"] = pos_match.group(1)
        result["position_note"] = "Encoded position — use ACARS decoder for lat/lon"

    # CPDLC detection
    if any(kw in raw_message.upper() for kw in ["CPDLC", "CLRNCE", "DWNLNK", "ATLANT", "PACOT"]):
        result["protocol"] = "CPDLC (Controller-Pilot Datalink)"
        result["cpdlc_note"] = "Air Traffic Control datalink message"

    if "label" not in result and "label_description" not in result:
        result["parse_note"] = "Could not parse structured fields — try acarsdec JSON output format"

    return json.dumps(result, indent=2)


# ── POCSAG pager ──────────────────────────────────────────────────────────

# Common cap code ranges and known assignments
POCSAG_KNOWN = {
    range(0, 8):          "Test/calibration",
    range(1000000, 1999999): "Typical commercial pager range",
    range(2000000, 2999999): "Hospital/medical pager range (common)",
    range(3000000, 3999999): "Emergency services range (common)",
}


def interpret_pocsag(decoded_line: str) -> str:
    """
    Interpret a multimon-ng POCSAG decoded line.
    Explains cap code, baud rate, function bits, and message content.
    """
    decoded_line = decoded_line.strip()
    result = {"raw": decoded_line}

    # Parse multimon-ng output format:
    # POCSAG1200: Address: 1234567  Function: 3  Alpha:   Hello World
    baud_match = re.search(r"POCSAG(\d+)", decoded_line)
    if baud_match:
        result["baud_rate"] = int(baud_match.group(1))

    addr_match = re.search(r"Address:\s*(\d+)", decoded_line)
    if addr_match:
        cap_code = int(addr_match.group(1))
        result["cap_code"] = cap_code
        result["cap_code_hex"] = hex(cap_code)

        # Known range lookup
        known = f"Unknown range ({cap_code})"
        for r, desc in POCSAG_KNOWN.items():
            if cap_code in r:
                known = desc
                break
        result["cap_code_range"] = known

    func_match = re.search(r"Function:\s*(\d)", decoded_line)
    if func_match:
        func = int(func_match.group(1))
        func_names = {0: "Numeric only", 1: "Numeric only", 2: "Tone only", 3: "Alphanumeric"}
        result["function"] = func
        result["function_type"] = func_names.get(func, f"Function {func}")

    alpha_match = re.search(r"Alpha:\s*(.*)", decoded_line)
    if alpha_match:
        result["message"] = alpha_match.group(1).strip()

    numeric_match = re.search(r"Numeric:\s*([\d\-]+)", decoded_line)
    if numeric_match:
        result["numeric_message"] = numeric_match.group(1)

    return json.dumps(result, indent=2)


# ── Meshtastic / LoRa packet ───────────────────────────────────────────────

MESHTASTIC_PORT_NUMS = {
    0:   "UNKNOWN_APP",
    1:   "TEXT_MESSAGE_APP",
    2:   "REMOTE_HARDWARE_APP",
    3:   "POSITION_APP",
    4:   "NODEINFO_APP",
    5:   "ROUTING_APP",
    6:   "ADMIN_APP",
    7:   "TEXT_MESSAGE_COMPRESSED_APP",
    8:   "WAYPOINT_APP",
    9:   "AUDIO_APP",
    32:  "DETECTION_SENSOR_APP",
    34:  "REPLY_APP",
    35:  "IP_TUNNEL_APP",
    67:  "PAXCOUNTER_APP",
    256: "SERIAL_APP",
    257: "STORE_AND_FORWARD_APP",
    258: "RANGE_TEST_APP",
    259: "TELEMETRY_APP",
    260: "ZPS_APP",
    261: "SIMULATOR_APP",
    262: "TRACEROUTE_APP",
    263: "NEIGHBORINFO_APP",
    264: "ATAK_PLUGIN",
    300: "MAP_REPORT_APP",
}

MESHTASTIC_CHANNELS = {
    "LongFast":   {"freq_mhz": 906.875, "sf": 11, "bw_khz": 250, "cr": "4/5"},
    "LongSlow":   {"freq_mhz": 906.875, "sf": 12, "bw_khz": 125, "cr": "4/8"},
    "MedFast":    {"freq_mhz": 906.875, "sf": 10, "bw_khz": 250, "cr": "4/5"},
    "MedSlow":    {"freq_mhz": 906.875, "sf": 10, "bw_khz": 125, "cr": "4/5"},
    "ShortFast":  {"freq_mhz": 906.875, "sf": 8,  "bw_khz": 250, "cr": "4/5"},
    "ShortSlow":  {"freq_mhz": 906.875, "sf": 9,  "bw_khz": 125, "cr": "4/5"},
}


def interpret_meshtastic(packet_json: str) -> str:
    """
    Interpret a Meshtastic packet from meshtastic-sniffer JSON output.
    Explains node IDs, port numbers, hop counts, SNR, and message content.
    """
    try:
        if isinstance(packet_json, str):
            pkt = json.loads(packet_json)
        else:
            pkt = packet_json
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"JSON parse failed: {e}", "raw": packet_json}, indent=2)

    result = {}

    # Node IDs
    if "from" in pkt:
        node_id = pkt["from"]
        result["from_node_id"] = f"!{node_id:08x}" if isinstance(node_id, int) else str(node_id)
    if "to" in pkt:
        to_id = pkt["to"]
        if isinstance(to_id, int) and to_id == 0xFFFFFFFF:
            result["to"] = "BROADCAST (all nodes)"
        else:
            result["to"] = f"!{to_id:08x}" if isinstance(to_id, int) else str(to_id)

    # Port / app
    port = pkt.get("portnum") or pkt.get("port")
    if port is not None:
        result["portnum"] = port
        result["application"] = MESHTASTIC_PORT_NUMS.get(port, f"Unknown port {port}")

    # RF metrics
    if "snr" in pkt:
        snr = pkt["snr"]
        result["snr_db"] = snr
        if snr > 5:
            result["signal_quality"] = "Strong"
        elif snr > 0:
            result["signal_quality"] = "Good"
        elif snr > -5:
            result["signal_quality"] = "Marginal"
        else:
            result["signal_quality"] = "Weak (may have errors)"

    if "rxRssi" in pkt or "rssi" in pkt:
        result["rssi_dbm"] = pkt.get("rxRssi") or pkt.get("rssi")

    # Hop info
    if "hopLimit" in pkt:
        result["hop_limit"] = pkt["hopLimit"]
        result["hop_note"] = f"Up to {pkt['hopLimit']} more hops allowed"
    if "hopStart" in pkt and "hopLimit" in pkt:
        hops_used = pkt["hopStart"] - pkt["hopLimit"]
        result["hops_used"] = hops_used

    # Message content by port
    payload = pkt.get("decoded") or pkt.get("payload") or {}
    if port == 1 and payload:
        result["text_message"] = payload.get("text") or str(payload)
    elif port == 3 and payload:
        pos = payload.get("position") or payload
        result["position"] = {
            "latitude":  pos.get("latitudeI", 0) / 1e7 if "latitudeI" in pos else pos.get("latitude"),
            "longitude": pos.get("longitudeI", 0) / 1e7 if "longitudeI" in pos else pos.get("longitude"),
            "altitude_m": pos.get("altitude"),
            "speed_m_s":  pos.get("groundSpeed"),
        }
    elif port == 4 and payload:
        ni = payload.get("user") or payload
        result["node_info"] = {
            "long_name":  ni.get("longName"),
            "short_name": ni.get("shortName"),
            "hw_model":   ni.get("hwModel"),
            "is_licensed": ni.get("isLicensed", False),
        }

    # Channel info if present
    if "channel" in pkt:
        ch_name = pkt["channel"]
        result["channel"] = ch_name
        if ch_name in MESHTASTIC_CHANNELS:
            result["channel_params"] = MESHTASTIC_CHANNELS[ch_name]

    result["raw_packet"] = pkt
    return json.dumps(result, indent=2)


# ── Generic hex field explainer ────────────────────────────────────────────

def explain_hex(hex_data: str, protocol_hint: str = "") -> str:
    """
    Explain a raw hex string — attempts protocol auto-detection then
    returns a byte-by-byte breakdown with ASCII interpretation.
    Useful as a fallback when the specific protocol decoder doesn't apply.
    """
    hex_data = hex_data.strip().replace(" ", "").replace(":", "")
    try:
        data = bytes.fromhex(hex_data)
    except ValueError:
        return json.dumps({"error": f"Invalid hex: {hex_data}"}, indent=2)

    result = {
        "hex": hex_data,
        "length_bytes": len(data),
        "bytes_decimal": list(data),
        "ascii_printable": "".join(chr(b) if 32 <= b < 127 else "." for b in data),
    }

    # Auto-detect protocol
    protocol_hint = protocol_hint.lower()
    if not protocol_hint:
        if len(data) in (7, 14) and ((data[0] >> 3) & 0x1F) == 17:
            protocol_hint = "adsb"
        elif hex_data.startswith("2A41") or hex_data.startswith("2A42"):
            protocol_hint = "ais"

    if protocol_hint == "adsb":
        return interpret_adsb(hex_data)
    elif protocol_hint == "ais":
        return interpret_ais(hex_data)

    # Bit field breakdown for unknown protocols
    fields = []
    for i, byte in enumerate(data):
        fields.append({
            "offset": i,
            "hex": f"{byte:02X}",
            "decimal": byte,
            "binary": f"{byte:08b}",
            "ascii": chr(byte) if 32 <= byte < 127 else None,
        })
    result["byte_fields"] = fields

    return json.dumps(result, indent=2)


# ── Frequency / band identifier ────────────────────────────────────────────

BAND_TABLE = [
    (0.003,   0.03,    "ELF/VLF", "Submarine comms, navigation beacons"),
    (0.03,    0.3,     "LF",      "AM longwave broadcasting, LORAN navigation"),
    (0.3,     3.0,     "MF",      "AM broadcasting, maritime MF, AMSB"),
    (3.0,     30.0,    "HF",      "Shortwave, amateur radio, HFDL aircraft, international broadcast"),
    (30.0,    50.0,    "VHF low", "Low-band public safety, military"),
    (54.0,    88.0,    "VHF TV",  "TV channels 2-6 (legacy)"),
    (88.0,    108.0,   "FM",      "FM broadcast band"),
    (108.0,   137.0,   "VHF air", "Aviation voice (AM), VOR/ILS/DME navigation"),
    (137.0,   138.0,   "SAT",     "NOAA/Meteor weather satellite APT/LRPT downlink"),
    (138.0,   144.0,   "VHF mil", "Military aviation"),
    (144.0,   148.0,   "2m ham",  "Amateur VHF — FM voice, APRS, Meshtastic testing, SSB weak signal"),
    (148.0,   174.0,   "VHF hi",  "Public safety, marine, business radio"),
    (156.0,   174.0,   "Marine",  "VHF marine (ch 16 = 156.800 MHz distress/calling), AIS 162 MHz"),
    (174.0,   216.0,   "VHF TV",  "TV channels 7-13 (legacy), DAB radio"),
    (216.0,   222.0,   "1.25m",   "Amateur 222 MHz band"),
    (225.0,   400.0,   "UHF mil", "Military aircraft voice (AM)"),
    (400.0,   406.0,   "Met",     "Meteorological aids, radiosondes"),
    (406.0,   420.0,   "UHF gov", "Government, search and rescue beacons (406 MHz ELT/EPIRB)"),
    (420.0,   450.0,   "70cm ham","Amateur UHF — ATV, FM voice, satellites"),
    (433.0,   435.0,   "ISM 433", "License-free IoT/sensors (EU/AU), LoRa, OOK remotes"),
    (450.0,   470.0,   "UHF bus", "Business radio, land mobile"),
    (470.0,   698.0,   "UHF TV",  "TV channels 14-51"),
    (698.0,   806.0,   "LTE",     "4G/5G cellular, FirstNet public safety LTE"),
    (806.0,   902.0,   "800 MHz", "Cellular, public safety P25, iDEN"),
    (902.0,   928.0,   "ISM 915", "Meshtastic LoRa (US), Z-Wave, 802.15.4, tire pressure sensors"),
    (928.0,   960.0,   "UHF",     "Cellular, paging"),
    (960.0,   1215.0,  "ADSB",    "ADS-B 1090 MHz, DME aviation, TACAN"),
    (1090.0,  1090.5,  "ADS-B",   "ADS-B Mode S aircraft transponders (1090 MHz)"),
    (1176.0,  1186.0,  "GPS L5",  "GPS L5 signal"),
    (1215.0,  1300.0,  "L-band",  "GPS L2, GLONASS"),
    (1525.0,  1559.0,  "Inmarsat","Inmarsat L-band satellite (ACARS via JAERO)"),
    (1559.0,  1610.0,  "GPS L1",  "GPS L1 C/A at 1575.42 MHz, GLONASS, Galileo"),
    (1616.0,  1627.0,  "Iridium", "Iridium satellite constellation"),
    (2400.0,  2500.0,  "S-band",  "WiFi 2.4 GHz, Bluetooth, ZigBee, microwave ovens"),
    (5150.0,  5850.0,  "C-band",  "WiFi 5 GHz, weather radar"),
]


def identify_frequency(freq_mhz: float) -> str:
    """
    Identify what services and protocols operate at a given frequency in MHz.
    Returns band name, typical users, and suggested decoder tools.
    """
    result = {
        "freq_mhz": freq_mhz,
        "freq_hz": int(freq_mhz * 1e6),
    }

    matched = []
    for low, high, band, desc in BAND_TABLE:
        if low <= freq_mhz <= high:
            matched.append({"band": band, "range_mhz": f"{low}–{high}", "description": desc})

    if matched:
        result["band_matches"] = matched
    else:
        result["band_matches"] = [{"band": "Unknown", "description": "No standard allocation found"}]

    # Suggest tools
    tools = []
    if 88 <= freq_mhz <= 108:
        tools = ["GQRX (WFM mode)", "SDRAngel", "SDR++"]
    elif 108 <= freq_mhz <= 137:
        tools = ["GQRX (AM mode)", "dump1090 (1090 MHz only)"]
    elif freq_mhz == 1090.0:
        tools = ["dump1090", "SDRAngel ADS-B plugin"]
    elif 136 <= freq_mhz <= 137:
        tools = ["dumpvdl2", "GQRX (NFM 25kHz)"]
    elif 156 <= freq_mhz <= 163:
        tools = ["rtl-ais (162 MHz)", "GQRX (NFM marine channels)"]
    elif 162 <= freq_mhz <= 163:
        tools = ["rtl-ais", "SDRAngel AIS plugin"]
    elif 902 <= freq_mhz <= 928:
        tools = ["meshtastic-sniffer", "rtl_433", "SDRAngel LoRa plugin"]
    elif 433 <= freq_mhz <= 435:
        tools = ["rtl_433", "URH (signal reverse engineering)", "GQRX (NFM)"]
    elif 1525 <= freq_mhz <= 1560:
        tools = ["JAERO (requires downconverter for HackRF)"]
    elif 1616 <= freq_mhz <= 1627:
        tools = ["gr-iridium", "Iridium Toolkit"]
    elif freq_mhz < 30:
        tools = ["GQRX (USB/LSB)", "SDRAngel", "dumphfdl (HFDL freqs)", "fldigi (digital modes)"]

    if tools:
        result["suggested_tools"] = tools

    return json.dumps(result, indent=2)
