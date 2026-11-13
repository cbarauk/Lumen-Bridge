"""
Neewer BLE protocol: pattern engine, name parsing, light-type mapping.

Pure logic, no Bluetooth or GUI imports, so it can be unit-tested anywhere.
Port of CommandPatternParser.swift, NeewerLightConstant.swift and the
command helpers in NeewerLight.swift.
"""

import json
import os

# ---------------------------------------------------------------- config ----
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lights.json")

# ------------------------------------------------------------- constants ----
SERVICE_UUID = "69400001-B5A3-F393-E0A9-E50E24DCCA99"
CTL_UUID = "69400002-B5A3-F393-E0A9-E50E24DCCA99"    # app -> light (write)
GATT_UUID = "69400003-B5A3-F393-E0A9-E50E24DCCA99"   # light -> app (read/notify)

PREFIX_TAG = 0x78
POWER_TAG = 0x81
RGB_TAG = 0x86        # hsitag
CCT_TAG = 0x87        # ccttag
SCENE_TAG = 0x88      # fxtag
SCE_SUB_TAG = 0x8B    # fxsubtag
SCE_DATA_TAG = 0x91   # fxdatatag

READ_REQUEST = bytes([PREFIX_TAG, 0x84, 0x00, 0xFC])

_TAG_BYTES = {
    "cmdtag": PREFIX_TAG,
    "powertag": POWER_TAG,
    "ccttag": CCT_TAG,
    "hsitag": RGB_TAG,
    "fxtag": SCENE_TAG,
    "fxsubtag": SCE_SUB_TAG,
    "fxdatatag": SCE_DATA_TAG,
}

# Scene names used by the URL API (same ids as Command.swift scene()).
SCENE_IDS = {
    "squadcar": 1, "ambulance": 2, "fireengine": 3, "fireworks": 4, "party": 5,
    "candlelight": 6, "lighting": 7, "paparazzi": 8, "screen": 9,
}


# -------------------------------------------------------- pattern engine ----
def tokenize(pattern):
    """Split a pattern on spaces, but not inside {braces}."""
    tokens, cur, inside = [], "", False
    for ch in pattern:
        if ch == "{":
            inside = True
            cur += ch
        elif ch == "}":
            inside = False
            cur += ch
        elif ch == " " and not inside:
            if cur:
                tokens.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    return tokens


def _to_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_enum(extra):
    body = extra.replace("enum(", "").replace(")", "")
    result = {}
    for pair in body.split(","):
        parts = pair.split("=")
        if len(parts) == 2:
            try:
                result[parts[1].strip()] = int(parts[0].strip())
            except ValueError:
                pass
    return result


def _parse_range(extra):
    body = extra.replace("range(", "").replace(")", "")
    parts = body.split(",")
    if len(parts) == 2:
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            pass
    return -(2 ** 31), 2 ** 31 - 1


def _encode_value(value, ftype, extra):
    """Encode one variable. Returns bytes, or None if the value is invalid."""
    if extra and extra.startswith("enum("):
        mapping = _parse_enum(extra)
        if isinstance(value, int) and value in mapping.values():
            return bytes([value])
        if isinstance(value, str) and value in mapping:
            return bytes([mapping[value]])
        return None

    if extra and extra.startswith("range("):
        lo, hi = _parse_range(extra)
        iv = _to_int(value)
        if iv is None:
            return None
        if ftype == "uint8" and not (0 <= iv <= 255):
            return None
        iv = min(max(iv, lo), hi)
    else:
        if ftype == "hex" and isinstance(value, str):
            try:
                return bytes([int(value, 16) & 0xFF])
            except ValueError:
                return None
        iv = _to_int(value)
        if iv is None:
            return None

    if ftype == "uint16_le":
        return bytes([iv & 0xFF, (iv >> 8) & 0xFF])
    if ftype == "uint16_be":
        return bytes([(iv >> 8) & 0xFF, iv & 0xFF])
    return bytes([iv & 0xFF])  # uint8 and anything else


def _parse_mac(mac_str):
    out = [0] * 6
    if mac_str:
        for i, part in enumerate(mac_str.split(":")[:6]):
            try:
                out[i] = int(part, 16) & 0xFF
            except ValueError:
                pass
    return bytes(out)


def build_command(pattern, values):
    """
    Turn a command template such as
        "{cmdtag} {ccttag} {size} {brr:uint8:range(0,100)} {cct:uint8:range(32,56)} 0x32 0x00 0x00 {checksum}"
    plus a dict of values into the raw BLE packet bytes.
    Returns None when the pattern is malformed or a value is invalid.
    """
    tokens = tokenize(pattern)
    out = bytearray()
    size_idx = None
    size16 = False
    after_size = False
    payload = 0

    for tok in tokens:
        if tok.startswith("{") and tok.endswith("}"):
            parts = tok[1:-1].split(":", 2)
            field = parts[0]
            ftype = parts[1] if len(parts) > 1 else "uint8"
            extra = parts[2] if len(parts) > 2 else None

            if field == "checksum":
                continue  # appended at the end
            if field in _TAG_BYTES:
                out.append(_TAG_BYTES[field])
                payload += 1 if after_size else 0
            elif field == "mac":
                out += _parse_mac(values.get("mac", ""))
                payload += 6 if after_size else 0
            elif field in ("size", "size16"):
                size16 = field == "size16"
                size_idx = len(out)
                out += b"\x00\x00" if size16 else b"\x00"
                after_size = True
            elif field in values:
                encoded = _encode_value(values[field], ftype, extra)
                if encoded is None:
                    return None
                out += encoded
                payload += len(encoded) if after_size else 0
            else:
                return None  # unsupported / missing variable
        elif tok.startswith("0x"):
            try:
                byte = int(tok[2:], 16)
            except ValueError:
                return None
            out.append(byte & 0xFF)
            payload += 1 if after_size else 0
        else:
            return None

    if size_idx is not None:
        if size16:
            out[size_idx:size_idx + 2] = payload.to_bytes(2, "big")
        else:
            out[size_idx] = payload & 0xFF

    if tokens and tokens[-1].startswith("{") and "checksum" in tokens[-1]:
        out.append(sum(out) & 0xFF)

    return bytes(out)


# ----------------------------------------------------------- name parsing ----
# Keyed by the numeric code that appears in raw names like "NW-20200015&...".
PROJECT_BY_CODE = {
    "20200015": "RGB1", "20200037": "SL90", "20240073": "SL90",
    "20200049": "RGB1200", "20210006": "Apollo 150D", "20210007": "RGB C80",
    "20210012": "CB60 RGB", "20210018": "BH-30S RGB", "20210034": "MS60B",
    "20210035": "MS60C", "20210036": "TL60 RGB", "20210037": "CB200B",
    "20220014": "CB60B", "20220016": "PL60C", "20220035": "MS150B",
    "20220041": "AS600B", "20220043": "FS150B", "20220046": "RP19C",
    "20220051": "CB100C", "20220055": "CB300B", "20220057": "SL90 Pro",
    "20230021": "BH-30S RGB", "20230025": "RGB1200", "20230031": "TL120C",
    "20230042": "HS60C", "20230050": "FS230 5600K", "20230051": "FS230B",
    "20230052": "FS150 5600K", "20230064": "TL60 RGB", "20230112": "CB120B",
    "20240063": "BH20C",
}

RGB_OTHER = {"RGB480", "RGB530", "RGB660", "RGB530 PRO", "RGB660 PRO",
             "RGB-P200", "RGB450", "RGB650"}

# Checked in order, first match wins (same order as the Swift source).
_NON_RGB_RULES = [
    ("ZY", 23), ("ER1", 23), ("DL200", 35), ("X2", 27), ("CB200B", 28),
    ("Apollo 150D", 24), ("MS60C", 25), ("MS60B", 30), ("CB60B", 31),
    ("RGB62", 40), ("GM16", 36), ("FS150B", 37), ("MS150B", 38), ("DL300", 41),
    ("T100C", 44), ("A19C 220V", 45), ("A19C(E26)", 46),
    # CB300 with a specific raw name is type 81; checked before this list in get_light_type().
    ("CB300B", 47), ("R360", 48), ("CB100C", 49), ("TL120C", 50), ("RL45B", 52),
    ("FS230 5600K", 53), ("FS150 5600K", 54), ("FS230B", 55), ("20220041", 58),
    ("PL60C", 60), ("BH40C", 61), ("GR18C", 62), ("RP19C", 63), ("VL67C", 65),
    ("TL97C", 64), ("HS60C", 87), ("HS60B", 66), ("TL40", 67), ("Q200", 68),
    ("TL21C", 69), ("MS150C", 73), ("CB200C", 74), ("FS150C", 75), ("MS60", 78),
    ("MS150", 79), ("CB300C", 82), ("CB120B", 70), ("AP150C-2", 83),
]


def project_name_from_code(code):
    return PROJECT_BY_CODE.get(code, "")


def get_light_names(raw_name, identifier):
    """Raw BLE advertised name -> (nick_name, project_name)."""
    name = raw_name
    suffix = "" if identifier == "" else "-" + identifier[-6:]

    if name.upper().startswith("NH-"):
        # NEEWER Home (0x7A protocol) lights are not supported on Windows yet.
        stripped = name[3:]
        if "&" in stripped:
            stripped = stripped[:stripped.index("&")]
        project = stripped
    elif name.startswith("NWR"):
        project = name[4:]
    elif name.startswith("NEEWER"):
        project = name[7:]
    elif not name.startswith("NW") or "&" not in name:
        project = name[3:] if name.startswith("NW") else name
    else:
        substring = name[3:name.rindex("&")]
        if substring.lstrip("-").isdigit():
            project = project_name_from_code(substring)
        else:
            project = substring

    return f"{project}{suffix}", project


def get_light_type(nick, raw, project):
    """Map names to a database light type id (0 = unknown)."""
    ru, nu, pu = raw.upper(), nick.upper(), project.upper()
    haystack = "\n".join([ru, nu, pu])

    if "HS60C PRO" in haystack or "HS60C-PRO" in haystack or "HS60CPRO" in haystack:
        return 88
    if "HS60 PRO LITE" in haystack or "HS60-PRO-LITE" in haystack or "HS60PROLITE" in haystack:
        return 87
    if "HS60C" in ru or ru.startswith("NW-20230042"):
        return 87
    if raw.startswith("NW-20240073") or raw.startswith("NW-20200037"):
        return 71
    if "BH20C" in ru or ru.startswith("NW-20240063"):
        return 89
    if "SRP" in nick or "RP18-P" in nick:
        return 1
    if "RP18B PRO" in nick:
        return 51

    if "SNL" in nick or "NL" in nick:
        if "SNL" in nick:
            if "SNL960" in nick or "SNL1320" in nick or "SNL1920" in nick:
                return 13
            return 7
        return 2

    if "GL1" in nick:
        if "GL1 PRO" in nick:
            return 33
        if "GL1C" in nick:
            return 39
        return 4

    if "ZK-RY" in nick:
        return 17

    if "RGB" not in nick and "SL" not in nick:
        if "CB300" in nick and ("20230111" in raw or raw == "NW-CB300"):
            return 81
        for needle, type_id in _NON_RGB_RULES:
            if needle in nick:
                return type_id
        return 0

    if "RGB" in nick:
        lt = 8
        if project == "RGB1" or "RGB1-A" in nick:
            lt = 8
        elif "RGB176" in nick:
            lt = 20 if "RGB176 A1" in nick else 5
        elif "RGB18(II)" in nick:
            lt = 57
        else:
            if "RGB18" in nick:
                lt = 9
            elif "RGB190" in nick:
                lt = 11
            elif "RGB960" in nick or "RGB1320" in nick or "RGB1920" in nick:
                lt = 12
            elif "RGB140" in nick:
                lt = 15
            elif "RGB168" in nick:
                lt = 16

            if "RGB1200" in nick:
                lt = 43 if "20230025" in nick else 18
            elif "CL124 RGB(II)" in nick:
                lt = 56
            else:
                if "CL124-RGB" in nick:
                    lt = 19
                elif "RGB C80" in nick or "RGBC80" in nick:
                    lt = 21
                elif "CB60 RGB" in nick:
                    lt = 22
                elif "RGB-P280" in nick:
                    lt = 29

                if "BH-30S RGB" in nick:
                    lt = 42 if "20230021" in raw else 26
                elif "TL60 RGB" in nick:
                    lt = 59 if "20230064" in raw else 32
                elif "RGB62" in nick:
                    lt = 40
                elif project.upper() in RGB_OTHER:
                    lt = 3
        return lt

    if "SL90 Pro" in nick:
        return 34
    if "SL90" in nick:
        return 14
    return 6


def cct_range(light_type, project):
    """Slider range in Neewer units (Kelvin / 100)."""
    if light_type == 6:
        if "SL140" in project:
            return 25, 90
        return 25, 85
    return 32, 56


# ------------------------------------------------------------- database ----
def load_database(path=DB_PATH):
    """Load lights.json and return the parsed dict. Keeps only usable lights."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    lights = {}
    for entry in data.get("lights", []):
        lights[entry["type"]] = entry
    return {"lights": lights, "fxPresets": data.get("fxPresets", {})}


def scene_list(db, light_type):
    """[(id, name)] for scene effects of a light type (empty if none)."""
    entry = db["lights"].get(light_type)
    if not entry:
        return []
    preset = db["fxPresets"].get(entry.get("fxPreset", ""), [])
    return [(p["id"], p["name"]) for p in preset]


def scene_pattern(db, light_type, scene_id):
    entry = db["lights"].get(light_type)
    if not entry:
        return None
    for p in db["fxPresets"].get(entry.get("fxPreset", ""), []):
        if p["id"] == scene_id:
            return p["cmd"]
    return None


# ------------------------------------------------- high-level command set ----
def power_command(db, light_type, mac, on):
    pattern = _pattern(db, light_type, "power")
    return build_command(pattern, {"state": "on" if on else "off", "mac": mac}) if pattern else None


def cct_command(db, light_type, mac, brightness, cct_kelvin, gm=0):
    """brightness 0-100, cct_kelvin like 5600, gm -50..50."""
    pattern = _pattern(db, light_type, "cct")
    if not pattern:
        return None
    values = {
        "brr": int(brightness),
        "cct": int(round(cct_kelvin / 100.0)),
        "gm": int(gm) + 50,
        "mac": mac,
    }
    return build_command(pattern, values)


def hsi_command(db, light_type, mac, hue_deg, saturation_pct, brightness):
    """hue 0-360, saturation 0-100, brightness 0-100."""
    pattern = _pattern(db, light_type, "hsi")
    if not pattern:
        return None
    values = {
        "hue": int(hue_deg) % 361,
        "sat": int(saturation_pct),
        "brr": int(brightness),
        "mac": mac,
    }
    return build_command(pattern, values)


def scene_command(db, light_type, mac, scene_id, brightness):
    pattern = scene_pattern(db, light_type, scene_id)
    if not pattern:
        return None
    values = {
        "brr": int(brightness), "brr2": int(brightness),
        "cct": 50, "gm": 50, "speed": 5, "color": 0, "sparks": 5,
        "hue": 0, "sat": 100, "mac": mac,
    }
    return build_command(pattern, values)


def _pattern(db, light_type, name):
    entry = db["lights"].get(light_type)
    if not entry:
        return None
    return entry.get("commandPatterns", {}).get(name)
