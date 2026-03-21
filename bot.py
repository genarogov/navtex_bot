import os
import re
import time
import json
import imaplib
import email
import tempfile
import threading
import html
from io import BytesIO
from datetime import datetime, date, timezone, timedelta
from email.header import decode_header
from zoneinfo import ZoneInfo

import requests
from telegram import ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from docx import Document

# ---------------- ENV ----------------
TOKEN = os.getenv("BOT_TOKEN")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMS_API_TOKEN = os.getenv("IMS_API_TOKEN", "").strip()

# ---------------- CACHE ----------------
CACHE_FILE = "cache.json"
TAIL_SCAN_LIMIT = 40

# ---------------- GMAIL FILTERS ----------------
SENDER_KEYWORD = "mot.gov.il"
SUBJECT_KEYWORD = "notice to mariner"

# ---------------- METAREA ----------------
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset_download/3/json"

# ---------------- SEALAGOM / NAVAREA III ----------------
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/"
NAVAREA_BOX_LAT_MIN = 30.0
NAVAREA_BOX_LAT_MAX = 38.5
NAVAREA_BOX_LON_MIN = 26.0
NAVAREA_BOX_LON_MAX = 36.5

# ---------------- CAMERI BUOYS ----------------
HAIFA_BUOY_BUTTON = "🛟 Haifa buoy"
ASHDOD_BUOY_BUTTON = "🛟 Ashdod buoy"

CAMERI_QUERY_URL = "https://adva.cameri-eng.com/api/ds/query"
CAMERI_DATASOURCE_UID = "d4fb9d12-3057-41b7-9ce2-36c7320d8a58"
CAMERI_BUOY_LOCATIONS = {
    HAIFA_BUOY_BUTTON: {"name": "Haifa buoy", "location_id": 1},
    ASHDOD_BUOY_BUTTON: {"name": "Ashdod buoy", "location_id": 2},
}

# ---------------- IMS WEATHER ----------------
IMS_API_BASE = "https://api.ims.gov.il/v1/Envista"
IMS_STATION_CACHE_TTL = 6 * 60 * 60
IMS_STATION_INFO_CACHE = {}
IMS_STATIONS_CACHE = {
    "fetched_at": 0,
    "stations": [],
}
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

IMS_STATIONS = {
    "🌤 Haifa Port": "HAIFA PORT",
    "🌤 En Karmel": "EN KARMEL",
    "🌤 Hadera Port": "HADERA PORT",
    "🌤 Tel Aviv Coast": "TEL AVIV COAST",
    "🌤 Ashdod Port": "ASHDOD PORT",
    "🌤 Ashqelon Port": "ASHQELON PORT",
}

IMS_PRESSURE_STATIONS = {
    "HAIFA PORT": "AFEQ",
    "EN KARMEL": "AFEQ",
    "HADERA PORT": "BET DAGAN",
    "TEL AVIV COAST": "BET DAGAN",
    "ASHDOD PORT": "BET DAGAN",
    "ASHQELON PORT": "BET DAGAN",
}

IMS_CHANNEL_ALIASES = {
    "TD": [
        "TD", "TEMP", "TEMPERATURE", "AIR TEMPERATURE", "DRY TEMPERATURE",
        "DRY BULB", "DRYBULB", "TA"
    ],
    "RH": [
        "RH", "HUMIDITY", "RELATIVE HUMIDITY", "RELATIVEHUMIDITY"
    ],
    "BP": [
        "BP", "PRESSURE", "BAROMETRIC PRESSURE", "STATION PRESSURE",
        "SEA LEVEL PRESSURE", "BAROMETER"
    ],
    "Rain": [
        "RAIN", "RAINFALL", "PRECIPITATION", "RR"
    ],
    "WS": [
        "WS", "WIND SPEED", "WINDSPEED", "WIND VELOCITY", "WINDVELOCITY",
        "FF", "VELOCITY"
    ],
    "WD": [
        "WD", "WIND DIRECTION", "WINDDIRECTION", "DD", "DIRECTION"
    ],
    "WSmax": [
        "WSMAX", "MAX WIND SPEED", "MAXWINDSPEED", "MAX GUST", "GUST",
        "WIND GUST", "WINDGUST", "WG", "MAX VELOCITY"
    ],
    "WDmax": [
        "WDMAX", "MAX WIND DIRECTION", "MAXWINDDIRECTION",
        "GUST DIRECTION", "DIRECTION OF MAX GUST"
    ],
}

# ---------------- WEATHER / FORECAST BUTTONS ----------------
FORECAST_BUTTON = "🌤 Forecast Taurus Delta Crusade"
GOV_BUTTON = "📜 gov.il"
NAVAREA_BUTTON = "📜 Navarea III"

WEATHER_BUTTONS = [
    GOV_BUTTON,
    NAVAREA_BUTTON,
    FORECAST_BUTTON,
    HAIFA_BUOY_BUTTON,
    ASHDOD_BUOY_BUTTON,
    "🌤 Haifa Port",
    "🌤 En Karmel",
    "🌤 Hadera Port",
    "🌤 Tel Aviv Coast",
    "🌤 Ashdod Port",
    "🌤 Ashqelon Port",
]

WEATHER_KEYBOARD = [
    [GOV_BUTTON, NAVAREA_BUTTON],
    [FORECAST_BUTTON],
    ["🌤 Haifa Port", HAIFA_BUOY_BUTTON],
    ["🌤 En Karmel", "🌤 Hadera Port"],
    ["🌤 Tel Aviv Coast", "🌤 Ashqelon Port"],
    ["🌤 Ashdod Port", ASHDOD_BUOY_BUTTON],
]

# ---------------- LOCK ----------------
MAIL_LOCK = threading.Lock()


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        WEATHER_KEYBOARD,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {
            "gmail": [],
            "gmail_initialized": False,
            "sealagom": [],
            "sealagom_initialized": False,
        }

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "gmail" not in data or not isinstance(data["gmail"], list):
            data["gmail"] = []

        if "gmail_initialized" not in data:
            data["gmail_initialized"] = False

        if "sealagom" not in data or not isinstance(data["sealagom"], list):
            data["sealagom"] = []

        if "sealagom_initialized" not in data:
            data["sealagom_initialized"] = False

        return data
    except Exception:
        return {
            "gmail": [],
            "gmail_initialized": False,
            "sealagom": [],
            "sealagom_initialized": False,
        }


def save_cache(data):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


cache = load_cache()


# ---------------- HELPERS ----------------
def decode_mime_words(value):
    if not value:
        return ""

    decoded = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            decoded.append(part)

    return "".join(decoded).strip()


def normalize_message_id(msg):
    raw = (msg.get("Message-ID") or "").strip()
    return raw.strip("<>").strip().lower()


def html_escape(text):
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def split_html_message(text, limit=4000):
    parts = []
    text = text or ""

    while text:
        if len(text) <= limit:
            parts.append(text)
            break

        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit

        parts.append(text[:cut])
        text = text[cut:].lstrip()

    return parts or [""]


def split_plain_message(text, limit=4000):
    parts = []
    text = text or ""

    while text:
        if len(text) <= limit:
            parts.append(text)
            break

        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = text.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit

        parts.append(text[:cut])
        text = text[cut:].lstrip()

    return parts or [""]


def deg_to_compass(deg):
    if deg in (None, "", "N/A"):
        return "N/A"
    try:
        deg = float(deg)
    except Exception:
        return "N/A"

    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) / 45.0) % 8]


def ms_to_knots(value):
    if value in (None, "", "N/A"):
        return None
    try:
        return round(float(value) * 1.94384, 1)
    except Exception:
        return None


def format_direction_with_degrees(deg_value):
    if deg_value in (None, "", "N/A"):
        return "N/A"

    try:
        deg_float = float(deg_value)
        deg_int = int(round(deg_float))
        return f"{deg_to_compass(deg_float)} ({deg_int}°)"
    except Exception:
        return "N/A"


def format_float(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return "N/A"


def normalize_key(text):
    return re.sub(r"[^A-Z0-9]+", "", str(text or "").upper())


def first_not_none(*values):
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def safe_float(value):
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def utc_to_israel_local(dt_utc):
    if not dt_utc:
        return None
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(ISRAEL_TZ)


def format_full_datetime_with_isr(dt_utc):
    if not dt_utc:
        return "N/A"

    dt_isr = utc_to_israel_local(dt_utc)
    return (
        f"Updated: {dt_utc.strftime('%d %B %Y').upper()}\n"
        f"{dt_utc.strftime('%H:%M')} UTC / {dt_isr.strftime('%H:%M')} LT"
    )


# ---------------- COORDINATES ----------------
def dms_to_decimal(deg, minutes, seconds, direction):
    value = float(deg) + float(minutes) / 60 + float(seconds) / 3600
    if direction.upper() in ["S", "W"]:
        value = -value
    return value


def dm_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes) / 60
    if direction.upper() in ["S", "W"]:
        value = -value
    return value


def decimal_signed(value, direction):
    value = float(value)
    if direction.upper() in ["S", "W"]:
        return -abs(value)
    return abs(value)


def replace_coordinates(text, pattern, parser):
    matches = list(pattern.finditer(text))
    replacements = []

    for m in matches:
        try:
            lat, lon = parser(m)
            url = f"https://maps.google.com/?q={lat},{lon}"
            original = m.group(0)
            html_link = f'<a href="{url}">{original}</a>'
            replacements.append((m.start(), m.end(), html_link))
        except Exception:
            pass

    for start, end, html_link in reversed(replacements):
        text = text[:start] + html_link + text[end:]

    return text


def add_coordinate_links(text):
    safe = html_escape(text or "")

    pattern_latn_longe = re.compile(
        r'(?:(?:^)|(?:\b\d+\.\s*))'
        r'(?P<lat_deg>\d{1,2})\s+'
        r'(?P<lat_min>\d{2})\s+'
        r'(?P<lat_sec>\d{2}(?:\.\d+)?)\s+'
        r'(?P<lon_deg>\d{3})\s+'
        r'(?P<lon_min>\d{2})\s+'
        r'(?P<lon_sec>\d{2}(?:\.\d+)?)'
        r'(?:\s*\([^)]*\))?',
        re.I | re.M
    )

    def parse_latn_longe(m):
        lat = dms_to_decimal(
            m.group("lat_deg"),
            m.group("lat_min"),
            m.group("lat_sec"),
            "N"
        )
        lon = dms_to_decimal(
            m.group("lon_deg"),
            m.group("lon_min"),
            m.group("lon_sec"),
            "E"
        )
        return lat, lon

    safe = replace_coordinates(safe, pattern_latn_longe, parse_latn_longe)

    pattern_dms = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*'
        r'(?P<lat_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″])?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*'
        r'(?P<lon_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lon_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″])?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    def parse_dms(m):
        lat = dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), m.group("lat_dir"))
        lon = dms_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_sec"), m.group("lon_dir"))
        return lat, lon

    safe = replace_coordinates(safe, pattern_dms, parse_dms)

    pattern_dm = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lat_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lon_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    def parse_dm(m):
        lat = dm_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_dir"))
        lon = dm_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_dir"))
        return lat, lon

    safe = replace_coordinates(safe, pattern_dm, parse_dm)

    pattern_compact_dm = re.compile(
        r'(?P<lat_deg>\d{2})(?P<lat_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{3})(?P<lon_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    safe = replace_coordinates(safe, pattern_compact_dm, parse_dm)

    pattern_decimal = re.compile(
        r'(?P<lat>\d{1,2}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon>\d{1,3}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    def parse_decimal(m):
        lat = decimal_signed(m.group("lat"), m.group("lat_dir"))
        lon = decimal_signed(m.group("lon"), m.group("lon_dir"))
        return lat, lon

    safe = replace_coordinates(safe, pattern_decimal, parse_decimal)

    return safe


def extract_coordinates_for_filter(text):
    text = text or ""
    coords = []

    pattern_latn_longe = re.compile(
        r'(?:(?:^)|(?:\b\d+\.\s*))'
        r'(?P<lat_deg>\d{1,2})\s+'
        r'(?P<lat_min>\d{2})\s+'
        r'(?P<lat_sec>\d{2}(?:\.\d+)?)\s+'
        r'(?P<lon_deg>\d{3})\s+'
        r'(?P<lon_min>\d{2})\s+'
        r'(?P<lon_sec>\d{2}(?:\.\d+)?)'
        r'(?:\s*\([^)]*\))?',
        re.I | re.M
    )

    for m in pattern_latn_longe.finditer(text):
        try:
            lat = dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), "N")
            lon = dms_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_sec"), "E")
            coords.append((lat, lon))
        except Exception:
            pass

    pattern_dms = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*'
        r'(?P<lat_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″])?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*'
        r'(?P<lon_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lon_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″])?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    for m in pattern_dms.finditer(text):
        try:
            lat = dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), m.group("lat_dir"))
            lon = dms_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_sec"), m.group("lon_dir"))
            coords.append((lat, lon))
        except Exception:
            pass

    pattern_dm = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lat_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lon_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    for m in pattern_dm.finditer(text):
        try:
            lat = dm_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_dir"))
            lon = dm_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_dir"))
            coords.append((lat, lon))
        except Exception:
            pass

    pattern_compact_dm = re.compile(
        r'(?P<lat_deg>\d{2})(?P<lat_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon_deg>\d{3})(?P<lon_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    for m in pattern_compact_dm.finditer(text):
        try:
            lat = dm_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_dir"))
            lon = dm_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_dir"))
            coords.append((lat, lon))
        except Exception:
            pass

    pattern_decimal = re.compile(
        r'(?P<lat>\d{1,2}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:\-–—]*'
        r'(?P<lon>\d{1,3}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    for m in pattern_decimal.finditer(text):
        try:
            lat = decimal_signed(m.group("lat"), m.group("lat_dir"))
            lon = decimal_signed(m.group("lon"), m.group("lon_dir"))
            coords.append((lat, lon))
        except Exception:
            pass

    uniq = []
    seen = set()
    for lat, lon in coords:
        key = (round(lat, 6), round(lon, 6))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((lat, lon))

    return uniq


def in_navarea_box(lat, lon):
    return (
        NAVAREA_BOX_LAT_MIN <= lat <= NAVAREA_BOX_LAT_MAX
        and NAVAREA_BOX_LON_MIN <= lon <= NAVAREA_BOX_LON_MAX
    )


# ---------------- VALID STATUS ----------------
def get_status_icon(valid):
    if not valid or valid == "N/A":
        return "✅"

    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(valid.strip(), fmt).date()
            return "❌" if d < date.today() else "✅"
        except Exception:
            pass

    return "✅"


def is_valid_date_active(valid):
    if not valid or valid == "N/A":
        return False

    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(valid.strip(), fmt).date()
            return d >= date.today()
        except Exception:
            pass

    return False


# ---------------- DOCX ----------------
def read_docx(file_bytes):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        doc = Document(tmp_path)
        lines = []

        for p in doc.paragraphs:
            t = p.text.strip()
            if t:
                lines.append(t)

        for table in doc.tables:
            for row in table.rows:
                row_cells = []
                for cell in row.cells:
                    cell_text = " ".join(
                        p.text.strip() for p in cell.paragraphs if p.text.strip()
                    ).strip()
                    if cell_text:
                        row_cells.append(cell_text)
                if row_cells:
                    lines.append(" | ".join(row_cells))

        return "\n".join(lines)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def extract_notice(doc_text):
    notice = "N/A"
    start = "N/A"
    valid = "N/A"

    m = re.search(r'No\.\s*(\d+\s*/\s*\d+)', doc_text, re.I)
    if m:
        notice = m.group(1).strip()

    m = re.search(r'Start[:\s]*([\d/]+).*?VALID[:\s]*([\d/]+)', doc_text, re.I | re.S)
    if m:
        start = m.group(1).strip()
        valid = m.group(2).strip()
    else:
        m_start = re.search(r'Start[:\s]*([\d/]+)', doc_text, re.I)
        if m_start:
            start = m_start.group(1).strip()

        m_valid = re.search(r'Valid[:\s]*([\d/]+)', doc_text, re.I)
        if m_valid:
            valid = m_valid.group(1).strip()

    body = []
    skip_next_no = False

    for line in doc_text.splitlines():
        l = line.strip()
        if not l:
            continue

        compact = re.sub(r'\s+', '', l).lower()

        if "notice" in compact and "mariner" in compact:
            skip_next_no = True
            continue

        if skip_next_no and re.match(r'^no\.\s*\d+\s*/\s*\d+', l, re.I):
            skip_next_no = False
            continue

        if re.match(r'^start[:\s]', l, re.I):
            continue

        if "valid" in l.lower() and re.search(r'\d{2}/\d{2}/\d{4}', l):
            continue

        body.append(l)

    return {
        "notice": notice,
        "start": start,
        "valid": valid,
        "body": "\n".join(body).strip() or "N/A"
    }


def build_message(payload):
    icon = get_status_icon(payload["valid"])
    body = add_coordinate_links(payload["body"])

    return (
        f"{icon} <b>Notice to mariner No:</b> {html_escape(payload['notice'])}\n"
        f"<b>Start:</b> {html_escape(payload['start'])}\n"
        f"<b>Valid:</b> {html_escape(payload['valid'])}\n\n"
        f"{body}"
    )


# ---------------- METAREA JSON ----------------
def fetch_metarea_json():
    r = requests.get(METAREA_URL, timeout=20)
    r.raise_for_status()
    return r.json()


def get_east_forecast_bulletin(data):
    bulletins = data.get("bulletin", [])
    for b in bulletins:
        if b.get("label") == "EAST / HIGH SEAS FORECAST":
            return b
    return None


def ordered_content_lines(content_dict):
    pairs = []
    for k, v in content_dict.items():
        try:
            pairs.append((int(k), str(v).strip()))
        except Exception:
            continue

    pairs.sort(key=lambda x: x[0])
    return [v for _, v in pairs if v]


def extract_zone_blocks_from_lines(lines):
    zones = ["TAURUS", "DELTA", "CRUSADE", "KASTELLORIZO SEA"]

    start_idx = {}
    for i, line in enumerate(lines):
        line_up = line.strip().upper()
        if line_up in zones and line_up not in start_idx:
            start_idx[line_up] = i

    if not all(z in start_idx for z in zones):
        return None

    taurus_lines = lines[start_idx["TAURUS"] + 1:start_idx["DELTA"]]
    delta_lines = lines[start_idx["DELTA"] + 1:start_idx["CRUSADE"]]
    crusade_lines = lines[start_idx["CRUSADE"] + 1:start_idx["KASTELLORIZO SEA"]]

    return {
        "TAURUS": taurus_lines,
        "DELTA": delta_lines,
        "CRUSADE": crusade_lines,
    }


def format_zone_lines(zone_name, lines):
    text = " ".join([x.strip() for x in lines if x.strip()])
    text = re.sub(r"\.\s*", ".\n", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return f"📍 {zone_name}\n{text}"


def normalize_metarea_issued_line(issued_line):
    issued_line = (issued_line or "").strip()
    if not issued_line:
        return "Issued: ATHENS\nN/A"

    upper = issued_line.upper()

    if upper.startswith("ISSUED:"):
        upper = upper[len("ISSUED:"):].strip()

    parts = [x.strip() for x in upper.split(",", 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        city, dt_part = parts[0], parts[1]
        return f"Issued: {city}\n{dt_part}"

    return f"Issued: ATHENS\n{upper}"


def get_metarea():
    try:
        data = fetch_metarea_json()
        bulletin = get_east_forecast_bulletin(data)

        if not bulletin:
            return "METAREA EAST forecast not found."

        lines = ordered_content_lines(bulletin.get("content", {}))
        if not lines:
            return "METAREA EAST forecast content is empty."

        issued = "N/A"
        for line in lines:
            if re.search(r"\bUTC\b", line, re.I):
                issued = line.strip()
                break

        zone_blocks = extract_zone_blocks_from_lines(lines)
        if not zone_blocks:
            return f"{normalize_metarea_issued_line(issued)}\n\nMETAREA zone markers not found."

        msg = (
            f"{normalize_metarea_issued_line(issued)}\n\n"
            f"{format_zone_lines('TAURUS', zone_blocks['TAURUS'])}\n\n"
            f"{format_zone_lines('DELTA', zone_blocks['DELTA'])}\n\n"
            f"{format_zone_lines('CRUSADE', zone_blocks['CRUSADE'])}"
        )

        return msg[:4000]

    except Exception as e:
        print("METAREA JSON error:", e)
        return f"METAREA JSON error: {e}"


# ---------------- CAMERI BUOYS ----------------
def fetch_cameri_buoy_latest(location_id):
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - (3 * 24 * 60 * 60 * 1000)

    payload = {
        "queries": [{
            "refId": "A",
            "datasource": {
                "type": "grafana-postgresql-datasource",
                "uid": CAMERI_DATASOURCE_UID
            },
            "rawSql": (
                "SELECT\n"
                "  middle_time AS \"time\",\n"
                "  avg_hs AS \"Wave Height (m)\",\n"
                "  avg_tp AS \"Peak Period (s)\",\n"
                "  avg_temp AS \"Temperature (°C)\"\n"
                "FROM\n"
                "  backend_buoysmsr_2h_avg\n"
                f"WHERE\n  location_id = '{int(location_id)}'\n"
                "  AND $__timeFilter(middle_time)\n"
                "ORDER BY\n"
                "  middle_time DESC\n"
                "LIMIT 1;\n"
            ),
            "format": "table",
            "datasourceId": 10,
            "intervalMs": 120000,
            "maxDataPoints": 510,
        }],
        "from": str(from_ms),
        "to": str(now_ms),
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://adva.cameri-eng.com",
        "Referer": "https://adva.cameri-eng.com/",
    }

    r = requests.post(CAMERI_QUERY_URL, headers=headers, json=payload, timeout=25)
    r.raise_for_status()
    data = r.json()

    frames = (((data or {}).get("results") or {}).get("A") or {}).get("frames") or []
    if not frames:
        return None

    frame = frames[0]
    fields = [f.get("name") for f in frame.get("schema", {}).get("fields", [])]
    values = frame.get("data", {}).get("values", [])

    if not fields or not values:
        return None

    columns = {}
    for idx, field_name in enumerate(fields):
        column_values = values[idx] if idx < len(values) else []
        columns[field_name] = column_values

    if not columns.get("time"):
        return None

    time_value = columns["time"][0] if columns["time"] else None
    hs_value = columns.get("Wave Height (m)", [None])[0]
    tp_value = columns.get("Peak Period (s)", [None])[0]
    temp_value = columns.get("Temperature (°C)", [None])[0]

    dt_utc = None
    if isinstance(time_value, (int, float)):
        dt_utc = datetime.fromtimestamp(time_value / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    elif isinstance(time_value, str):
        try:
            dt_utc = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
            if dt_utc.tzinfo is not None:
                dt_utc = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            dt_utc = None

    return {
        "time": dt_utc,
        "hs": hs_value,
        "tp": tp_value,
        "temp": temp_value,
    }


def build_cameri_buoy_message(button_text):
    config = CAMERI_BUOY_LOCATIONS.get(button_text)
    if not config:
        return "Buoy config not found."

    latest = fetch_cameri_buoy_latest(config["location_id"])
    if not latest:
        return f"📍 {config['name']}\nNo data."

    updated = format_full_datetime_with_isr(latest["time"]) if latest.get("time") else "N/A"

    return (
        f"📍 {config['name']}\n"
        f"{updated}\n\n"
        f"Wave height: {format_float(latest.get('hs'), 2)} m\n"
        f"Peak period: {format_float(latest.get('tp'), 1)} s\n"
        f"Water temperature: {format_float(latest.get('temp'), 1)} °C"
    )


# ---------------- IMS WEATHER ----------------
def ims_headers():
    if not IMS_API_TOKEN:
        raise Exception("IMS_API_TOKEN is empty")
    return {
        "Authorization": f"ApiToken {IMS_API_TOKEN}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }


def ims_request_json(path):
    url = f"{IMS_API_BASE}{path}"
    r = requests.get(url, headers=ims_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def ims_extract_station_id(station):
    return first_not_none(
        station.get("stationId"),
        station.get("station_id"),
        station.get("id"),
        station.get("StationId"),
        station.get("StationID"),
    )


def ims_extract_station_name(station):
    return str(first_not_none(
        station.get("name"),
        station.get("stationName"),
        station.get("station_name"),
        station.get("title"),
        station.get("label"),
    ) or "").strip()


def parse_datetime_any(value):
    if not value:
        return None

    if isinstance(value, (int, float)):
        try:
            if value > 10**12:
                return datetime.utcfromtimestamp(value / 1000.0)
            return datetime.utcfromtimestamp(value)
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue

    return None


def normalize_station_lookup_name(name):
    return re.sub(r"\s+", " ", str(name or "").upper()).strip()


def fetch_ims_stations():
    now = time.time()
    if IMS_STATIONS_CACHE["stations"] and now - IMS_STATIONS_CACHE["fetched_at"] < IMS_STATION_CACHE_TTL:
        return IMS_STATIONS_CACHE["stations"]

    stations = ims_request_json("/stations")
    if not isinstance(stations, list):
        raise Exception("IMS stations response is not a list")

    IMS_STATIONS_CACHE["stations"] = stations
    IMS_STATIONS_CACHE["fetched_at"] = now
    return stations


def find_ims_station_by_name(target_name):
    wanted = normalize_station_lookup_name(target_name)
    stations = fetch_ims_stations()

    exact = []
    partial = []

    for station in stations:
        st_name = ims_extract_station_name(station)
        st_norm = normalize_station_lookup_name(st_name)
        if not st_norm:
            continue

        if st_norm == wanted:
            exact.append(station)
        elif wanted in st_norm or st_norm in wanted:
            partial.append(station)

    if exact:
        return exact[0]
    if partial:
        return partial[0]

    raise Exception(f"IMS station not found: {target_name}")


def fetch_ims_station_info(station_name):
    cache_key = normalize_station_lookup_name(station_name)
    cached = IMS_STATION_INFO_CACHE.get(cache_key)
    now = time.time()

    if cached and now - cached["fetched_at"] < IMS_STATION_CACHE_TTL:
        return cached["data"]

    station = find_ims_station_by_name(station_name)
    station_id = ims_extract_station_id(station)
    if station_id is None:
        raise Exception(f"IMS station id not found for: {station_name}")

    station_meta = ims_request_json(f"/stations/{station_id}")
    latest_data = ims_request_json(f"/stations/{station_id}/data/latest")

    data = {
        "station": station,
        "station_id": station_id,
        "station_meta": station_meta,
        "latest_data": latest_data,
    }
    IMS_STATION_INFO_CACHE[cache_key] = {
        "fetched_at": now,
        "data": data,
    }
    return data


def recursive_find_first_datetime(node):
    if isinstance(node, dict):
        for key, value in node.items():
            low = str(key).lower()
            if "time" in low or "date" in low:
                dt = parse_datetime_any(value)
                if dt:
                    return dt
        for value in node.values():
            dt = recursive_find_first_datetime(value)
            if dt:
                return dt

    elif isinstance(node, list):
        for item in node:
            dt = recursive_find_first_datetime(item)
            if dt:
                return dt

    return None


def collect_monitor_name_map(station_meta):
    result = {}

    def add_names(channel_id, *names):
        if channel_id in (None, "", "N/A"):
            return
        cid = str(channel_id).strip()
        if not cid:
            return
        bucket = result.setdefault(cid, set())
        for name in names:
            name = str(name or "").strip()
            if name:
                bucket.add(name)

    monitors = []
    if isinstance(station_meta, dict):
        monitors = first_not_none(
            station_meta.get("monitors"),
            station_meta.get("Monitors"),
            station_meta.get("channels"),
            station_meta.get("Channels"),
        ) or []

    if not isinstance(monitors, list):
        monitors = []

    for mon in monitors:
        if not isinstance(mon, dict):
            continue

        channel_id = first_not_none(
            mon.get("channelId"),
            mon.get("channel_id"),
            mon.get("id"),
            mon.get("monitorId"),
        )

        add_names(
            channel_id,
            mon.get("name"),
            mon.get("alias"),
            mon.get("description"),
            mon.get("shortName"),
            mon.get("title"),
            mon.get("symbol"),
        )
    return result


def collect_latest_channel_items(node):
    items = []

    def walk(obj):
        if isinstance(obj, list):
            for item in obj:
                walk(item)
            return

        if not isinstance(obj, dict):
            return

        has_channel_hint = any(
            key in obj for key in (
                "channelId", "channel_id", "monitorId", "id", "name", "alias",
                "description", "symbol", "title"
            )
        )
        has_value_hint = any(
            key in obj for key in (
                "value", "Value", "lastValue", "currentValue", "avg", "data"
            )
        )

        if has_channel_hint and has_value_hint:
            items.append(obj)

        for value in obj.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(node)
    return items


def extract_value_from_item(item):
    value = first_not_none(
        item.get("value"),
        item.get("Value"),
        item.get("lastValue"),
        item.get("currentValue"),
        item.get("avg"),
    )

    if isinstance(value, dict):
        value = first_not_none(
            value.get("value"),
            value.get("Value"),
            value.get("avg"),
            value.get("data"),
        )

    if isinstance(value, list):
        for v in reversed(value):
            if v not in (None, ""):
                return v
        return None

    return value


def extract_item_datetime(item):
    return first_not_none(
        parse_datetime_any(item.get("datetime")),
        parse_datetime_any(item.get("dateTime")),
        parse_datetime_any(item.get("time")),
        parse_datetime_any(item.get("Time")),
        parse_datetime_any(item.get("lastUpdate")),
        parse_datetime_any(item.get("updatedAt")),
        parse_datetime_any(item.get("createdAt")),
    )


def build_channel_value_map(station_meta, latest_data):
    name_map = collect_monitor_name_map(station_meta)
    items = collect_latest_channel_items(latest_data)
    channel_map = {}

    for item in items:
        channel_id = str(first_not_none(
            item.get("channelId"),
            item.get("channel_id"),
            item.get("monitorId"),
            item.get("id"),
        ) or "").strip()

        names = set()
        if channel_id and channel_id in name_map:
            names.update(name_map[channel_id])

        for key in ("name", "alias", "description", "symbol", "title", "shortName"):
            val = item.get(key)
            if val:
                names.add(str(val).strip())

        value = extract_value_from_item(item)
        item_dt = extract_item_datetime(item)

        entry = {
            "channel_id": channel_id,
            "names": list(names),
            "value": value,
            "datetime": item_dt,
        }

        keys = set()
        for name in names:
            keys.add(normalize_key(name))

        if channel_id:
            keys.add(normalize_key(channel_id))

        for key in keys:
            if not key:
                continue

            prev = channel_map.get(key)
            if prev is None:
                channel_map[key] = entry
                continue

            prev_dt = prev.get("datetime")
            new_dt = entry.get("datetime")

            if prev.get("value") in (None, "") and entry.get("value") not in (None, ""):
                channel_map[key] = entry
            elif prev_dt is None and new_dt is not None:
                channel_map[key] = entry
            elif prev_dt is not None and new_dt is not None and new_dt >= prev_dt:
                channel_map[key] = entry

    return channel_map


def find_channel_entry(channel_map, canonical_name):
    aliases = IMS_CHANNEL_ALIASES.get(canonical_name, [])
    for alias in aliases:
        entry = channel_map.get(normalize_key(alias))
        if entry is not None:
            return entry
    return None


def ims_api_time_to_utc(dt_naive):
    if not dt_naive:
        return None
    return dt_naive - timedelta(hours=2)


def build_ims_weather_message(station_name):
    obs = get_ims_station_weather(station_name)

    dt = obs.get("time_obs")
    updated_utc = ims_api_time_to_utc(dt) if dt else None
    updated = format_full_datetime_with_isr(updated_utc) if updated_utc else "N/A"

    pressure_value = obs.get("BP")
    pressure_station_name = IMS_PRESSURE_STATIONS.get(normalize_station_lookup_name(station_name))
    if pressure_station_name:
        try:
            pressure_obs = get_ims_station_weather(pressure_station_name)
            if pressure_obs and pressure_obs.get("BP") not in (None, ""):
                pressure_value = pressure_obs.get("BP")
        except Exception as e:
            print("IMS pressure fallback error:", e)

    wind_kn = ms_to_knots(obs.get("WS"))
    gust_kn = ms_to_knots(obs.get("WSmax"))

    wind_str = f"{wind_kn:.1f} kn, {format_direction_with_degrees(obs.get('WD'))}" if wind_kn is not None else "N/A"
    gust_str = f"{gust_kn:.1f} kn, {format_direction_with_degrees(obs.get('WDmax'))}" if gust_kn is not None else "N/A"

    pressure_text = "N/A"
    if pressure_value not in (None, ""):
        pressure_num = safe_float(pressure_value)
        if pressure_num is None:
            pressure_text = str(pressure_value)
        else:
            pressure_text = f"{pressure_num:.1f} hPa"

    rain_text = "N/A"
    if obs.get("Rain") not in (None, ""):
        rain_num = safe_float(obs.get("Rain"))
        rain_text = f"{rain_num:.1f} mm" if rain_num is not None else str(obs.get("Rain"))

    temp_text = "N/A"
    if obs.get("TD") not in (None, ""):
        temp_num = safe_float(obs.get("TD"))
        temp_text = f"{temp_num:.1f} °C" if temp_num is not None else f"{obs.get('TD')} °C"

    humidity_text = "N/A"
    if obs.get("RH") not in (None, ""):
        rh_num = safe_float(obs.get("RH"))
        humidity_text = f"{rh_num:.0f} %" if rh_num is not None else f"{obs.get('RH')} %"

    return (
        f"📍 {station_name.replace('🌤 ', '')}\n"
        f"{updated}\n\n"
        f"Air temperature: {temp_text}\n"
        f"Humidity: {humidity_text}\n"
        f"Pressure: {pressure_text}\n"
        f"Rain: {rain_text}\n"
        f"Wind: {wind_str}\n"
        f"Max gust: {gust_str}"
    )


def get_ims_station_weather(station_name):
    info = fetch_ims_station_info(station_name)
    station_meta = info["station_meta"]
    latest_data = info["latest_data"]

    channel_map = build_channel_value_map(station_meta, latest_data)

    obs = {
        "TD": None,
        "RH": None,
        "BP": None,
        "Rain": None,
        "WS": None,
        "WD": None,
        "WSmax": None,
        "WDmax": None,
        "time_obs": None,
    }

    for key in ("TD", "RH", "BP", "Rain", "WS", "WD", "WSmax", "WDmax"):
        entry = find_channel_entry(channel_map, key)
        if entry:
            obs[key] = entry.get("value")
            if obs["time_obs"] is None and entry.get("datetime") is not None:
                obs["time_obs"] = entry.get("datetime")

    if obs["time_obs"] is None:
        obs["time_obs"] = recursive_find_first_datetime(latest_data)

    obs["station_name"] = station_name
    obs["station_id"] = info["station_id"]
    return obs


# ---------------- GMAIL ----------------
def connect_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")
    return mail


def message_matches(msg):
    from_header = decode_mime_words(msg.get("From", ""))
    subject = decode_mime_words(msg.get("Subject", ""))
    msg_id = normalize_message_id(msg)

    if not msg_id:
        return None

    if SENDER_KEYWORD.lower() not in from_header.lower():
        return None

    if SUBJECT_KEYWORD.lower() not in subject.lower():
        return None

    return {
        "msg": msg,
        "id": msg_id,
        "from": from_header,
        "subject": subject
    }


def fetch_latest_matching_email():
    mail = connect_gmail()
    result, data = mail.search(None, "ALL")

    if result != "OK":
        mail.logout()
        return None

    ids = data[0].split()
    if not ids:
        mail.logout()
        return None

    tail_ids = ids[-TAIL_SCAN_LIMIT:]

    for num in reversed(tail_ids):
        result, msg_data = mail.fetch(num, "(RFC822)")
        if result != "OK" or not msg_data or not msg_data[0]:
            continue

        raw_bytes = msg_data[0][1]
        if not raw_bytes:
            continue

        msg = email.message_from_bytes(raw_bytes)
        entry = message_matches(msg)
        if entry:
            mail.logout()
            return entry

    mail.logout()
    return None


def fetch_recent_matching_emails():
    mail = connect_gmail()
    result, data = mail.search(None, "ALL")

    if result != "OK":
        mail.logout()
        return []

    ids = data[0].split()
    if not ids:
        mail.logout()
        return []

    tail_ids = ids[-TAIL_SCAN_LIMIT:]
    messages = []

    for num in reversed(tail_ids):
        result, msg_data = mail.fetch(num, "(RFC822)")
        if result != "OK" or not msg_data or not msg_data[0]:
            continue

        raw_bytes = msg_data[0][1]
        if not raw_bytes:
            continue

        msg = email.message_from_bytes(raw_bytes)
        entry = message_matches(msg)
        if entry:
            messages.append(entry)

    mail.logout()
    return messages


def extract_docx(msg):
    for part in msg.walk():
        filename = part.get_filename()
        filename = decode_mime_words(filename) if filename else ""
        content_type = (part.get_content_type() or "").lower()

        if (
            filename.lower().endswith(".docx")
            or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or (content_type == "application/octet-stream" and filename.lower().endswith(".docx"))
        ):
            file_bytes = part.get_payload(decode=True)
            if file_bytes:
                return file_bytes

    return None


def extract_pdf(msg):
    for part in msg.walk():
        filename = part.get_filename()
        filename = decode_mime_words(filename) if filename else ""
        content_type = (part.get_content_type() or "").lower()
        disposition = str(part.get("Content-Disposition", "")).lower()

        is_pdf = (
            filename.lower().endswith(".pdf")
            or "pdf" in content_type
            or ("attachment" in disposition and ".pdf" in filename.lower())
        )

        if is_pdf:
            file_bytes = part.get_payload(decode=True)
            if file_bytes:
                return file_bytes, (filename or "attachment.pdf")

    return None, None


def send_pdf_bytes(bot, chat_id, pdf_bytes, pdf_name):
    bio = BytesIO(pdf_bytes)
    bio.name = pdf_name or "attachment.pdf"
    bio.seek(0)
    bot.send_document(chat_id=chat_id, document=bio)


def extract_payload_from_entry(entry):
    msg = entry["msg"]
    file_bytes = extract_docx(msg)
    if not file_bytes:
        return None
    text = read_docx(file_bytes)
    return extract_notice(text)


def process_entry(bot, chat_id, entry):
    msg = entry["msg"]

    text_sent = False
    pdf_sent = False

    file_bytes = extract_docx(msg)
    if file_bytes:
        text = read_docx(file_bytes)
        payload = extract_notice(text)
        message = build_message(payload)

        for chunk in split_html_message(message):
            bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        text_sent = True

    pdf_bytes, pdf_name = extract_pdf(msg)
    if pdf_bytes:
        send_pdf_bytes(bot, chat_id, pdf_bytes, pdf_name)
        pdf_sent = True

    if not text_sent and not pdf_sent:
        bot.send_message(chat_id=chat_id, text="DOCX attachment not found.")
        return False

    return True


def fetch_active_gov_entries():
    messages = fetch_recent_matching_emails()
    active_entries = []

    for entry in messages:
        try:
            payload = extract_payload_from_entry(entry)
            if not payload:
                continue
            if is_valid_date_active(payload.get("valid")):
                active_entries.append(entry)
        except Exception as e:
            print("Active GOV parse error:", e)

    return active_entries


# ---------------- SEALAGOM / NAVAREA III ----------------
def fetch_sealagom_page_text():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(SEALAGOM_URL, headers=headers, timeout=25)
    r.raise_for_status()

    raw = r.text
    raw = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style.*?>.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p>", "\n", raw)
    raw = re.sub(r"(?i)</div>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)

    text = html.unescape(raw)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    return text


def extract_navarea_messages(page_text):
    page_text = page_text or ""

    m = re.search(r'NAVAREA\s+III\s*-\s*\d{4}/\d{2}', page_text, re.I)
    if not m:
        return []

    text = page_text[m.start():]
    parts = re.split(r'(?=NAVAREA\s+III\s*-\s*\d{4}/\d{2})', text, flags=re.I)
    messages = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        id_match = re.match(r'NAVAREA\s+III\s*-\s*(\d{4}/\d{2})', part, re.I)
        if not id_match:
            continue

        nav_id = id_match.group(1).strip()

        part = re.split(
            r'(?:Sign In|Register|Search Messages|View Messages|Download \(|Subscribe\b)',
            part,
            maxsplit=1,
            flags=re.I
        )[0].strip()

        messages.append({
            "id": nav_id,
            "title": f"NAVAREA III - {nav_id}",
            "text": part,
        })

    return messages


def navarea_message_matches_box(message_text):
    coords = extract_coordinates_for_filter(message_text)
    if not coords:
        return False, []

    hits = []
    for lat, lon in coords:
        if in_navarea_box(lat, lon):
            hits.append((lat, lon))

    return len(hits) > 0, hits


def fetch_recent_matching_navarea():
    page_text = fetch_sealagom_page_text()
    messages = extract_navarea_messages(page_text)

    matched = []
    for msg in messages:
        ok, hits = navarea_message_matches_box(msg["text"])
        if ok:
            msg["hits"] = hits
            matched.append(msg)

    return matched


def fetch_latest_matching_navarea():
    messages = fetch_recent_matching_navarea()
    return messages[0] if messages else None


def build_navarea_message(entry):
    linked_text = add_coordinate_links(entry["text"])
    return f"⚠️ <b>{html_escape(entry['title'])}</b>\n\n{linked_text}"


def process_navarea_entry(bot, chat_id, entry):
    message = build_navarea_message(entry)

    for chunk in split_html_message(message):
        bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    return True


# ---------------- WEATHER / GOV / NAVAREA BUTTONS ----------------
def handle_weather_button(update, context):
    text = (update.message.text or "").strip()

    if text == GOV_BUTTON:
        with MAIL_LOCK:
            try:
                active_entries = fetch_active_gov_entries()

                if not active_entries:
                    update.message.reply_text("No messages")
                    return

                sent_any = False
                for entry in reversed(active_entries):
                    ok = process_entry(context.bot, update.message.chat.id, entry)
                    if ok:
                        if entry["id"] not in cache["gmail"]:
                            cache["gmail"].append(entry["id"])
                            save_cache(cache)
                        sent_any = True

                if not sent_any:
                    update.message.reply_text("No messages")
            except Exception as e:
                update.message.reply_text(f"GOV.IL error: {e}")
        return

    if text == NAVAREA_BUTTON:
        with MAIL_LOCK:
            try:
                entries = fetch_recent_matching_navarea()

                if not entries:
                    update.message.reply_text("No messages")
                    return

                sent_any = False
                for entry in reversed(entries):
                    ok = process_navarea_entry(context.bot, update.message.chat.id, entry)
                    if ok:
                        if entry["id"] not in cache["sealagom"]:
                            cache["sealagom"].append(entry["id"])
                            save_cache(cache)
                        sent_any = True

                if not sent_any:
                    update.message.reply_text("No messages")
            except Exception as e:
                update.message.reply_text(f"NAVAREA III error: {e}")
        return

    if text == FORECAST_BUTTON:
        msg = get_metarea()
        for chunk in split_plain_message(msg, limit=4000):
            update.message.reply_text(chunk)
        return

    if text in CAMERI_BUOY_LOCATIONS:
        try:
            msg = build_cameri_buoy_message(text)
        except Exception as e:
            msg = f"CAMERI buoy error: {e}"

        for chunk in split_plain_message(msg, limit=4000):
            update.message.reply_text(chunk)
        return

    if text in IMS_STATIONS:
        try:
            msg = build_ims_weather_message(IMS_STATIONS[text])
        except Exception as e:
            msg = f"IMS weather error: {e}"

        for chunk in split_plain_message(msg, limit=4000):
            update.message.reply_text(chunk)
        return

    if text not in WEATHER_BUTTONS:
        return


# ---------------- COMMANDS ----------------
def start(update, context):
    update.message.reply_text("Bot started", reply_markup=get_main_keyboard())


def startbot(update, context):
    update.message.reply_text("Bot started", reply_markup=get_main_keyboard())


def checkgovil(update, context):
    with MAIL_LOCK:
        latest = fetch_latest_matching_email()

        if not latest:
            update.message.reply_text("No messages")
            return

        ok = process_entry(context.bot, update.message.chat.id, latest)

        if latest["id"] not in cache["gmail"]:
            cache["gmail"].append(latest["id"])
            save_cache(cache)

        if not ok:
            return


def testbot(update, context):
    update.message.reply_text("Bot running", reply_markup=get_main_keyboard())


# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("startbot", startbot))
    dp.add_handler(CommandHandler("checkgovil", checkgovil))
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_weather_button))

    updater.start_polling()
    print("BOT STARTED")
    updater.idle()


if __name__ == "__main__":
    main()