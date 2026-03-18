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
from datetime import datetime, date, timezone
from email.header import decode_header

import requests
import xml.etree.ElementTree as ET
from telegram import ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from docx import Document

# ---------------- ENV ----------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# ---------------- CACHE ----------------
CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 min
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

# ---------------- SDOT YAM BUOY ----------------
SDOT_YAM_BUTTON = "Sdot Yam buoy real time"
SDOT_YAM_URL = "https://www.wqdatalive.com/public/v3/2281/graphdatamultiple"

# ---------------- SHIKOMA / ISRAMAR ----------------
SHIKOMA_BUTTON = "Shikoma buoy real time"
SHIKOMA_WAVES_URL = "https://isramar.ocean.org.il/isramar2009/station/data/ShikBuoy_HS_Per.json"

# ---------------- IMS WEATHER ----------------
IMS_XML_URL = "https://ims.gov.il/sites/default/files/ims_data/xml_files/imslasthour.xml"

IMS_STATIONS = {
    "Haifa Technion weather": "HAIFA TECHNION",
    "En Karmel weather": "EN KARMEL",
    "Hadera Port weather": "HADERA PORT",
    "Tel Aviv Coast weather": "TEL AVIV COAST",
    "Ashdod Port weather": "ASHDOD PORT",
    "Ashqelon Port weather": "ASHQELON PORT",
}

IMS_PRESSURE_STATIONS = {
    "HAIFA TECHNION": "AFEQ",
    "EN KARMEL": "AFEQ",
    "HADERA PORT": "BET DAGAN",
    "TEL AVIV COAST": "BET DAGAN",
    "ASHDOD PORT": "BET DAGAN",
    "ASHQELON PORT": "BET DAGAN",
}

# ---------------- WEATHER / FORECAST BUTTONS ----------------
FORECAST_BUTTON = "Forecast Taurus, Delta, Crusade"

WEATHER_BUTTONS = [
    SHIKOMA_BUTTON,
    SDOT_YAM_BUTTON,
    "Haifa Technion weather",
    "En Karmel weather",
    "Hadera Port weather",
    "Tel Aviv Coast weather",
    "Ashdod Port weather",
    "Ashqelon Port weather",
]

WEATHER_KEYBOARD = [
    [FORECAST_BUTTON],
    [SHIKOMA_BUTTON],
    [SDOT_YAM_BUTTON],
    ["Haifa Technion weather", "En Karmel weather"],
    ["Hadera Port weather", "Tel Aviv Coast weather"],
    ["Ashdod Port weather", "Ashqelon Port weather"],
]

# ---------------- LOCK / DUPLICATE GUARD ----------------
MAIL_LOCK = threading.Lock()
RECENT_SENT_IDS = set()
RECENT_NAVAREA_SENT_IDS = set()


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

    dirs = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]
    return dirs[int((deg + 11.25) / 22.5) % 16]


def ms_to_knots(value):
    if value in (None, "", "N/A"):
        return None
    try:
        return round(float(value) * 1.94384, 1)
    except Exception:
        return None


def m_per_min_to_knots(value):
    try:
        return float(value) / 30.8666667
    except Exception:
        return None


def format_navstyle_datetime(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%d %B %Y / %H%M UTC").upper()


def format_direction_with_degrees(deg_value):
    if deg_value in (None, "", "N/A"):
        return "N/A"

    try:
        deg_float = float(deg_value)
        deg_int = int(round(deg_float))
        return f"{deg_to_compass(deg_float)} ({deg_int}°)"
    except Exception:
        return "N/A"


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


# ---------------- DOCX ----------------
def read_docx(file_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
        tmp.write(file_bytes)
        path = tmp.name

    doc = Document(path)
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


# ---------------- SDOT YAM BUOY ----------------
def get_last_valid_point(points):
    for item in reversed(points or []):
        try:
            ts, value = item[0], item[1]
        except Exception:
            continue

        if value is not None:
            return ts, value

    return None, None


def normalize_series_name(name):
    prefix = "Sdot Yam 10m : "
    if name.startswith(prefix):
        return name[len(prefix):].strip()
    return name.strip()


def format_value(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def fetch_sdot_yam_graph(param_ids, include_table_data=0):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.wqdatalive.com",
        "Referer": "https://www.wqdatalive.com/public/v3/2281?dashboardId=371&panels%5B0%5D%5Bid%5D=6076&panels%5B0%5D%5Btab%5D=data",
        "X-Requested-With": "XMLHttpRequest",
    }

    data = []
    for param_id in param_ids:
        data.append(("paramIds[]", str(param_id)))
    data.append(("timeRange", "last_day"))
    data.append(("includeTableData", str(include_table_data)))

    r = requests.post(SDOT_YAM_URL, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    payload = r.json()

    if payload.get("error"):
        raise Exception("Buoy API returned error=true")

    return payload


def fetch_sdot_yam_data():
    request_groups = [
        [117409, 117413],  # Wind Direction + Wind Velocity
        [117452, 117453],  # Current Velocity 2m + Current Direction 2m
    ]

    series_by_name = {}
    latest_ts = None

    for group in request_groups:
        payload = fetch_sdot_yam_graph(group)

        for series in payload.get("graphData", []):
            name = str(series.get("name", "")).strip()
            short_name = normalize_series_name(name)
            unit = str(series.get("unit", "")).strip()
            ts, value = get_last_valid_point(series.get("data", []))

            if not short_name:
                continue

            item = {
                "full_name": name,
                "short_name": short_name,
                "unit": unit,
                "param_id": series.get("paramId"),
                "timestamp_ms": ts,
                "value": value,
            }

            prev = series_by_name.get(short_name)
            if prev is None:
                series_by_name[short_name] = item
            else:
                prev_ts = prev.get("timestamp_ms")
                new_ts = item.get("timestamp_ms")

                if prev.get("value") is None and item.get("value") is not None:
                    series_by_name[short_name] = item
                elif prev_ts is None and new_ts is not None:
                    series_by_name[short_name] = item
                elif prev_ts is not None and new_ts is not None and new_ts >= prev_ts:
                    series_by_name[short_name] = item

            if ts is not None:
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts

    return {
        "series": series_by_name,
        "latest_ts": latest_ts,
    }


def find_series(series_by_name, *needles):
    needles = [n.lower() for n in needles if n]

    candidates = []
    for name, item in (series_by_name or {}).items():
        low = name.lower()
        if all(n in low for n in needles):
            candidates.append(item)

    if not candidates:
        return None

    valued = [x for x in candidates if x.get("value") is not None]
    if valued:
        valued.sort(key=lambda x: (x.get("timestamp_ms") or 0), reverse=True)
        return valued[0]

    candidates.sort(key=lambda x: (x.get("timestamp_ms") or 0), reverse=True)
    return candidates[0]


def build_sdot_yam_message():
    data = fetch_sdot_yam_data()
    series = data.get("series", {})

    lines = ["📍 Sdot Yam buoy"]

    latest_ts = data.get("latest_ts")
    if latest_ts:
        dt_utc = datetime.utcfromtimestamp(latest_ts / 1000.0)
        lines.append(f"Updated: {format_navstyle_datetime(dt_utc)}")
    else:
        lines.append("Updated: N/A")

    lines.append("")

    wind_velocity = (
        find_series(series, "wind", "velocity")
        or find_series(series, "wind", "speed")
    )
    wind_direction = find_series(series, "wind", "direction")

    current_velocity = (
        find_series(series, "current", "velocity", "2m")
        or find_series(series, "current", "speed", "2m")
        or find_series(series, "current", "velocity")
    )
    current_direction = (
        find_series(series, "current", "direction", "2m")
        or find_series(series, "current", "direction")
    )

    if (
        wind_velocity and wind_velocity.get("value") is not None and
        wind_direction and wind_direction.get("value") is not None
    ):
        wd = float(wind_direction["value"])
        lines.append(
            f"Wind: {format_value(wind_velocity['value'], 1)} {wind_velocity['unit']} "
            f"{deg_to_compass(wd)} ({int(round(wd))}°)"
        )
    elif wind_velocity and wind_velocity.get("value") is not None:
        lines.append(
            f"Wind: {format_value(wind_velocity['value'], 1)} {wind_velocity['unit']}"
        )
    else:
        lines.append("Wind: N/A")

    if current_velocity and current_velocity.get("value") is not None:
        current_knots = m_per_min_to_knots(current_velocity["value"])

        if current_direction and current_direction.get("value") is not None:
            cd = float(current_direction["value"])
            lines.append(
                f"Current 2m: {format_value(current_knots, 1)} knots "
                f"{deg_to_compass(cd)} ({int(round(cd))}°)"
            )
        else:
            lines.append(f"Current 2m: {format_value(current_knots, 1)} knots")
    else:
        lines.append("Current 2m: N/A")

    return "\n".join(lines)


# ---------------- SHIKOMA / ISRAMAR ----------------
def fetch_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def build_shikoma_message():
    try:
        payload = fetch_json(SHIKOMA_WAVES_URL)
    except Exception as e:
        return f"Shikoma buoy error: {e}"

    dt_raw = str(payload.get("datetime") or "").strip()
    params = payload.get("parameters") or []

    hs = None
    tp = None
    hmax = None

    for p in params:
        name = str(p.get("name") or "").strip().lower()
        units = str(p.get("units") or "").strip()
        values = p.get("values") or []

        value = None
        if isinstance(values, list) and values:
            value = values[0]

        if "significant wave height" in name:
            hs = (value, units)
        elif "peak wave period" in name:
            tp = (value, units)
        elif "maximal wave height" in name:
            hmax = (value, units)

    lines = ["📍 Shikoma buoy"]

    dt_out = "N/A"
    if dt_raw:
        try:
            dt_obj = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
            if dt_obj.tzinfo is not None:
                dt_obj = dt_obj.astimezone(timezone.utc).replace(tzinfo=None)
            dt_out = format_navstyle_datetime(dt_obj)
        except Exception:
            dt_out = dt_raw

    lines.append(f"Updated: {dt_out}")
    lines.append("")

    if hs and hs[0] is not None:
        lines.append(f"Significant wave height: {float(hs[0]):.2f} {hs[1]}")
    else:
        lines.append("Significant wave height: N/A")

    if tp and tp[0] is not None:
        lines.append(f"Peak wave period: {float(tp[0]):.1f} {tp[1]}")
    else:
        lines.append("Peak wave period: N/A")

    if hmax and hmax[0] is not None:
        lines.append(f"Maximal wave height: {float(hmax[0]):.2f} {hmax[1]}")
    else:
        lines.append("Maximal wave height: N/A")

    return "\n".join(lines)


# ---------------- IMS WEATHER ----------------
def safe_xml_text(node, tag):
    el = node.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text if text else None


def fetch_ims_observations():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/xml,text/xml,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(IMS_XML_URL, headers=headers, timeout=20)
    r.raise_for_status()

    root = ET.fromstring(r.content)
    observations = []

    for obs in root.findall("Observation"):
        observations.append({
            "stn_name": safe_xml_text(obs, "stn_name"),
            "stn_num": safe_xml_text(obs, "stn_num"),
            "time_obs": safe_xml_text(obs, "time_obs"),
            "TD": safe_xml_text(obs, "TD"),
            "RH": safe_xml_text(obs, "RH"),
            "BP": safe_xml_text(obs, "BP"),
            "Rain": safe_xml_text(obs, "Rain"),
            "WS": safe_xml_text(obs, "WS"),
            "WD": safe_xml_text(obs, "WD"),
            "WSmax": safe_xml_text(obs, "WSmax"),
            "WDmax": safe_xml_text(obs, "WDmax"),
        })

    return observations


def get_latest_observation_for_station(observations, station_name):
    latest_obs = None
    latest_dt = None
    wanted = (station_name or "").strip().upper()

    for obs in observations:
        current_name = (obs.get("stn_name") or "").strip().upper()
        if current_name != wanted:
            continue

        time_obs = obs.get("time_obs")
        if not time_obs:
            continue

        try:
            dt = datetime.fromisoformat(time_obs)
        except Exception:
            continue

        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_obs = obs

    return latest_obs


def get_latest_pressure_for_station(observations, pressure_station_name):
    latest_obs = None
    latest_dt = None
    wanted = (pressure_station_name or "").strip().upper()

    for obs in observations:
        current_name = (obs.get("stn_name") or "").strip().upper()
        if current_name != wanted:
            continue

        if not obs.get("BP"):
            continue

        time_obs = obs.get("time_obs")
        if not time_obs:
            continue

        try:
            dt = datetime.fromisoformat(time_obs)
        except Exception:
            continue

        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_obs = obs

    return latest_obs


def build_ims_weather_message(station_name):
    observations = fetch_ims_observations()
    obs = get_latest_observation_for_station(observations, station_name)

    if not obs:
        return f"📍 {station_name}\nNo data."

    try:
        dt = datetime.fromisoformat(obs.get("time_obs"))
        updated = format_navstyle_datetime(dt)
    except Exception:
        updated = "N/A"

    pressure_value = obs.get("BP")
    pressure_station_name = IMS_PRESSURE_STATIONS.get((station_name or "").strip().upper())
    if pressure_station_name:
        pressure_obs = get_latest_pressure_for_station(observations, pressure_station_name)
        if pressure_obs and pressure_obs.get("BP"):
            pressure_value = pressure_obs.get("BP")

    wind_kn = ms_to_knots(obs.get("WS"))
    gust_kn = ms_to_knots(obs.get("WSmax"))

    wind_str = f"{wind_kn:.1f} kn, {format_direction_with_degrees(obs.get('WD'))}" if wind_kn is not None else "N/A"
    gust_str = f"{gust_kn:.1f} kn, {format_direction_with_degrees(obs.get('WDmax'))}" if gust_kn is not None else "N/A"

    return (
        f"📍 {station_name}\n"
        f"Updated: {updated}\n\n"
        f"Air temperature: {obs.get('TD') or 'N/A'} °C\n"
        f"Humidity: {obs.get('RH') or 'N/A'} %\n"
        f"Pressure: {pressure_value or 'N/A'}\n"
        f"Rain: {obs.get('Rain') or 'N/A'} mm\n"
        f"Wind: {wind_str}\n"
        f"Max gust: {gust_str}"
    )


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


# ---------------- WEATHER / BUOY BUTTONS ----------------
def handle_weather_button(update, context):
    text = (update.message.text or "").strip()

    if text == FORECAST_BUTTON:
        msg = get_metarea()
        for i, chunk in enumerate(split_plain_message(msg, limit=4000)):
            if i == 0:
                update.message.reply_text(
                    chunk,
                    reply_markup=get_main_keyboard()
                )
            else:
                update.message.reply_text(chunk)
        return

    if text == SDOT_YAM_BUTTON:
        try:
            msg = build_sdot_yam_message()
        except Exception as e:
            msg = f"Sdot Yam buoy error: {e}"

        for i, chunk in enumerate(split_plain_message(msg, limit=4000)):
            if i == 0:
                update.message.reply_text(
                    chunk,
                    reply_markup=get_main_keyboard()
                )
            else:
                update.message.reply_text(chunk)
        return

    if text == SHIKOMA_BUTTON:
        try:
            msg = build_shikoma_message()
        except Exception as e:
            msg = f"Shikoma buoy error: {e}"

        for i, chunk in enumerate(split_plain_message(msg, limit=4000)):
            if i == 0:
                update.message.reply_text(
                    chunk,
                    reply_markup=get_main_keyboard()
                )
            else:
                update.message.reply_text(chunk)
        return

    if text in IMS_STATIONS:
        try:
            msg = build_ims_weather_message(IMS_STATIONS[text])
        except Exception as e:
            msg = f"IMS weather error: {e}"

        for i, chunk in enumerate(split_plain_message(msg, limit=4000)):
            if i == 0:
                update.message.reply_text(
                    chunk,
                    reply_markup=get_main_keyboard()
                )
            else:
                update.message.reply_text(chunk)
        return

    if text not in WEATHER_BUTTONS:
        return


# ---------------- AUTO CHECK ----------------
def initialize_gmail_cache_silently():
    if cache.get("gmail_initialized"):
        return

    messages = fetch_recent_matching_emails()
    for m in messages:
        if m["id"] not in cache["gmail"]:
            cache["gmail"].append(m["id"])

    cache["gmail_initialized"] = True
    save_cache(cache)


def initialize_sealagom_cache_silently():
    if cache.get("sealagom_initialized"):
        return

    try:
        messages = fetch_recent_matching_navarea()
        for m in messages:
            if m["id"] not in cache["sealagom"]:
                cache["sealagom"].append(m["id"])

        cache["sealagom_initialized"] = True
        save_cache(cache)
    except Exception as e:
        print("SeaLagom init error:", e)


def auto_check(updater):
    if not CHAT_ID:
        return

    if not MAIL_LOCK.acquire(blocking=False):
        return

    try:
        initialize_gmail_cache_silently()
        messages = fetch_recent_matching_emails()

        for m in reversed(messages):
            if m["id"] in cache["gmail"]:
                continue
            if m["id"] in RECENT_SENT_IDS:
                continue

            RECENT_SENT_IDS.add(m["id"])
            ok = process_entry(updater.bot, CHAT_ID, m)
            cache["gmail"].append(m["id"])

            if ok:
                save_cache(cache)

        initialize_sealagom_cache_silently()
        nav_messages = fetch_recent_matching_navarea()

        for m in reversed(nav_messages):
            if m["id"] in cache["sealagom"]:
                continue
            if m["id"] in RECENT_NAVAREA_SENT_IDS:
                continue

            RECENT_NAVAREA_SENT_IDS.add(m["id"])
            ok = process_navarea_entry(updater.bot, CHAT_ID, m)
            cache["sealagom"].append(m["id"])

            if ok:
                save_cache(cache)

    except Exception as e:
        print("Auto-check error:", e)

    finally:
        MAIL_LOCK.release()


# ---------------- COMMANDS ----------------
def start(update, context):
    update.message.reply_text(
        "Bot started",
        reply_markup=get_main_keyboard()
    )


def checkgovil(update, context):
    with MAIL_LOCK:
        latest = fetch_latest_matching_email()

        if not latest:
            update.message.reply_text("No messages", reply_markup=get_main_keyboard())
            return

        ok = process_entry(context.bot, update.message.chat.id, latest)

        RECENT_SENT_IDS.add(latest["id"])

        if latest["id"] not in cache["gmail"]:
            cache["gmail"].append(latest["id"])
            save_cache(cache)

        if not ok:
            return


def checknavarea(update, context):
    with MAIL_LOCK:
        latest = fetch_latest_matching_navarea()

        if not latest:
            update.message.reply_text("No NAVAREA III messages in box", reply_markup=get_main_keyboard())
            return

        ok = process_navarea_entry(context.bot, update.message.chat.id, latest)

        RECENT_NAVAREA_SENT_IDS.add(latest["id"])

        if latest["id"] not in cache["sealagom"]:
            cache["sealagom"].append(latest["id"])
            save_cache(cache)

        if not ok:
            return


def testbot(update, context):
    update.message.reply_text("Bot running", reply_markup=get_main_keyboard())


def clearcache(update, context):
    with MAIL_LOCK:
        cache["gmail"] = []
        cache["gmail_initialized"] = False
        cache["sealagom"] = []
        cache["sealagom_initialized"] = False
        RECENT_SENT_IDS.clear()
        RECENT_NAVAREA_SENT_IDS.clear()
        save_cache(cache)
    update.message.reply_text("Cache cleared", reply_markup=get_main_keyboard())


def metarea(update, context):
    msg = get_metarea()
    for i, chunk in enumerate(split_plain_message(msg, limit=4000)):
        if i == 0:
            update.message.reply_text(chunk, reply_markup=get_main_keyboard())
        else:
            update.message.reply_text(chunk)


# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("checkgovil", checkgovil))
    dp.add_handler(CommandHandler("checknavarea", checknavarea))
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("clearcache", clearcache))
    dp.add_handler(CommandHandler("metarea", metarea))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_weather_button))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        auto_check(updater)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()