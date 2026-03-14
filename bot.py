import os
import re
import time
import json
import imaplib
import email
import tempfile
import threading
from io import BytesIO
from datetime import datetime, date
from email.header import decode_header

import requests
from telegram.ext import Updater, CommandHandler
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

# ---------------- LOCK / DUPLICATE GUARD ----------------
MAIL_LOCK = threading.Lock()
RECENT_SENT_IDS = set()


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gmail": [], "gmail_initialized": False}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "gmail" not in data or not isinstance(data["gmail"], list):
            data["gmail"] = []

        if "gmail_initialized" not in data:
            data["gmail_initialized"] = False

        return data
    except Exception:
        return {"gmail": [], "gmail_initialized": False}


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
            html = f'<a href="{url}">{original}</a>'
            replacements.append((m.start(), m.end(), html))
        except Exception:
            pass

    for start, end, html in reversed(replacements):
        text = text[:start] + html + text[end:]

    return text


def add_coordinate_links(text):
    safe = html_escape(text or "")

    # 0) LAT N LONG E without letters in each line
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

    # 1) DMS
    pattern_dms = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*'
        r'(?P<lat_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″])?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
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

    # 2) DM
    pattern_dm = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lat_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
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

    # 3) Compact DM
    pattern_compact_dm = re.compile(
        r'(?P<lat_deg>\d{2})(?P<lat_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
        r'(?P<lon_deg>\d{3})(?P<lon_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lon_dir>[EW])',
        re.I
    )

    safe = replace_coordinates(safe, pattern_compact_dm, parse_dm)

    # 4) Decimal
    pattern_decimal = re.compile(
        r'(?P<lat>\d{1,2}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
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
            return f"🕒 Issued: {issued}\n\nMETAREA zone markers not found."

        msg = (
            f"🕒 Issued: {issued}\n\n"
            f"{format_zone_lines('TAURUS', zone_blocks['TAURUS'])}\n\n"
            f"{format_zone_lines('DELTA', zone_blocks['DELTA'])}\n\n"
            f"{format_zone_lines('CRUSADE', zone_blocks['CRUSADE'])}"
        )

        return msg[:4000]

    except Exception as e:
        print("METAREA JSON error:", e)
        return f"METAREA JSON error: {e}"


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

    except Exception as e:
        print("Gmail error:", e)

    finally:
        MAIL_LOCK.release()


# ---------------- COMMANDS ----------------
def checkgovil(update, context):
    with MAIL_LOCK:
        latest = fetch_latest_matching_email()

        if not latest:
            update.message.reply_text("No messages")
            return

        ok = process_entry(context.bot, update.message.chat.id, latest)

        RECENT_SENT_IDS.add(latest["id"])

        if latest["id"] not in cache["gmail"]:
            cache["gmail"].append(latest["id"])
            save_cache(cache)

        if not ok:
            return


def testbot(update, context):
    update.message.reply_text("Bot running")


def clearcache(update, context):
    with MAIL_LOCK:
        cache["gmail"] = []
        cache["gmail_initialized"] = False
        RECENT_SENT_IDS.clear()
        save_cache(cache)
    update.message.reply_text("Cache cleared")


def metarea(update, context):
    msg = get_metarea()
    for chunk in split_html_message(msg, limit=4000):
        update.message.reply_text(chunk)


# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("checkgovil", checkgovil))
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("clearcache", clearcache))
    dp.add_handler(CommandHandler("metarea", metarea))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        auto_check(updater)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()