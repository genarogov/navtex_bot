import os
import re
import time
import json
import imaplib
import email
import tempfile
import textwrap
from email.header import decode_header

from telegram.ext import Updater, CommandHandler
from docx import Document
from PIL import Image, ImageDraw, ImageFont

# ---------------- ENV ----------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# ---------------- CACHE ----------------
CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 min

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {
            "gmail": [],
            "gmail_initialized": False
        }

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "gmail" not in data or not isinstance(data["gmail"], list):
            data["gmail"] = []

        if "gmail_initialized" not in data:
            data["gmail_initialized"] = False

        return data
    except Exception:
        return {
            "gmail": [],
            "gmail_initialized": False
        }

def save_cache(data):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

cache = load_cache()

# ---------------- GMAIL SETTINGS ----------------
SENDER = "benzviy.mot.gov.il@send.vpcontact.com"
SUBJECT_KEYWORD = "notice to mariner"

# ---------------- HELPERS ----------------
def html_escape(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

def decode_mime_words(value):
    if not value:
        return ""

    decoded_parts = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            decoded_parts.append(part)

    return "".join(decoded_parts).strip()

def normalize_message_id(msg):
    raw = (msg.get("Message-ID") or "").strip()
    return raw.strip("<>").strip().lower()

def split_html_message(text, limit=3500):
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break

        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit

        parts.append(text[:cut])
        text = text[cut:].lstrip()

    return parts

# ---------------- COORDINATES ----------------
def dms_to_decimal(deg, minutes, seconds, direction):
    value = float(deg) + float(minutes) / 60.0 + float(seconds) / 3600.0
    if direction.upper() in ("S", "W"):
        value = -value
    return value

def dm_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes) / 60.0
    if direction.upper() in ("S", "W"):
        value = -value
    return value

def decimal_signed(value, direction):
    val = float(value)
    if direction.upper() in ("S", "W"):
        return -abs(val)
    return abs(val)

def replace_coordinate_pairs(text, pattern, parser):
    matches = list(pattern.finditer(text))
    replacements = []

    for m in matches:
        try:
            lat, lon = parser(m)
            original = m.group(0)
            url = f"https://maps.google.com/?q={lat},{lon}"
            repl = f'<a href="{url}">{original}</a>'
            replacements.append((m.start(), m.end(), repl))
        except Exception:
            continue

    for start, end, repl in reversed(replacements):
        text = text[:start] + repl + text[end:]

    return text

def add_coordinate_links(text):
    if not text:
        return ""

    safe = html_escape(text)

    # 1) DMS
    # 32 58 10 N 034 00 00 E
    # 32°58'10"N 034°00'00"E
    # 32 58 10N, 034 00 00E
    pattern_dms = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*'
        r'(?P<lat_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″]|sec|s)?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*'
        r'(?P<lon_min>\d{1,2})\s*[\'′]?\s*'
        r'(?P<lon_sec>\d{1,2}(?:\.\d+)?)\s*(?:["″]|sec|s)?\s*'
        r'(?P<lon_dir>[EW])',
        re.IGNORECASE
    )

    def parse_dms(m):
        lat = dms_to_decimal(
            m.group("lat_deg"),
            m.group("lat_min"),
            m.group("lat_sec"),
            m.group("lat_dir")
        )
        lon = dms_to_decimal(
            m.group("lon_deg"),
            m.group("lon_min"),
            m.group("lon_sec"),
            m.group("lon_dir")
        )
        return lat, lon

    safe = replace_coordinate_pairs(safe, pattern_dms, parse_dms)

    # 2) DM
    # 32-15.4N 034-55.1E
    # 32 15.4 N 034 55.1 E
    # 32°15.4'N 034°55.1'E
    pattern_dm = re.compile(
        r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lat_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
        r'(?P<lon_deg>\d{1,3})\s*[°º]?\s*[-–—:/,\s]?\s*'
        r'(?P<lon_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*'
        r'(?P<lon_dir>[EW])',
        re.IGNORECASE
    )

    def parse_dm(m):
        lat = dm_to_decimal(
            m.group("lat_deg"),
            m.group("lat_min"),
            m.group("lat_dir")
        )
        lon = dm_to_decimal(
            m.group("lon_deg"),
            m.group("lon_min"),
            m.group("lon_dir")
        )
        return lat, lon

    safe = replace_coordinate_pairs(safe, pattern_dm, parse_dm)

    # 3) Compact DM
    # 3215.4N 03455.1E
    pattern_compact_dm = re.compile(
        r'(?P<lat_deg>\d{2})(?P<lat_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
        r'(?P<lon_deg>\d{3})(?P<lon_min>\d{2}(?:\.\d+)?)\s*'
        r'(?P<lon_dir>[EW])',
        re.IGNORECASE
    )

    safe = replace_coordinate_pairs(safe, pattern_compact_dm, parse_dm)

    # 4) Decimal with letters
    # 32.256N 34.817E
    # 32 N 034 E
    pattern_decimal = re.compile(
        r'(?P<lat>\d{1,2}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lat_dir>[NS])'
        r'[\s,;/:-]*'
        r'(?P<lon>\d{1,3}(?:\.\d+)?)\s*[°º]?\s*'
        r'(?P<lon_dir>[EW])',
        re.IGNORECASE
    )

    def parse_decimal(m):
        lat = decimal_signed(m.group("lat"), m.group("lat_dir"))
        lon = decimal_signed(m.group("lon"), m.group("lon_dir"))
        return lat, lon

    safe = replace_coordinate_pairs(safe, pattern_decimal, parse_decimal)

    return safe

# ---------------- DOCX ----------------
def read_docx_text_from_bytes(file_bytes):
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

    return "\n".join(lines).strip(), tmp_path

def find_field(text, labels):
    for label in labels:
        pattern = re.compile(rf'(?im)^\s*{label}\s*[:\-]?\s*(.+?)\s*$')
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return ""

def extract_notice_payload(doc_text):
    notice_no = find_field(doc_text, [
        r'Notice\s+to\s+mariner(?:s)?\s*No',
        r'Notice\s*No',
        r'Notice\s+number'
    ])

    start = find_field(doc_text, [
        r'Start',
        r'From'
    ])

    valid = find_field(doc_text, [
        r'Valid',
        r'Until',
        r'Valid\s+until'
    ])

    body_lines = []
    for line in doc_text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        lower = line_clean.lower()

        if re.match(r'^notice\s+to\s+mariner(?:s)?\s*no\s*[:\-]?', lower):
            continue
        if re.match(r'^notice\s*no\s*[:\-]?', lower):
            continue
        if re.match(r'^notice\s+number\s*[:\-]?', lower):
            continue
        if re.match(r'^start\s*[:\-]?', lower):
            continue
        if re.match(r'^from\s*[:\-]?', lower):
            continue
        if re.match(r'^valid\s*[:\-]?', lower):
            continue
        if re.match(r'^until\s*[:\-]?', lower):
            continue
        if re.match(r'^valid\s+until\s*[:\-]?', lower):
            continue

        body_lines.append(line_clean)

    body = "\n".join(body_lines).strip()

    return {
        "notice_no": notice_no or "N/A",
        "start": start or "N/A",
        "valid": valid or "N/A",
        "body": body or "N/A"
    }

def render_text_image(payload):
    text = (
        f"Notice to mariner No: {payload['notice_no']}\n"
        f"Start: {payload['start']}\n"
        f"Valid: {payload['valid']}\n\n"
        f"{payload['body']}"
    )

    wrapped_lines = []
    for block in text.split("\n"):
        if not block.strip():
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(block, width=55) or [""])

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    line_height = 36
    top_pad = 40
    bottom_pad = 40
    width = 1400
    height = max(900, top_pad + bottom_pad + (len(wrapped_lines) + 2) * line_height)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = 30
    draw.text((40, y), "Notice to mariner", fill="black", font=title_font)
    y += 55

    for line in wrapped_lines:
        draw.text((40, y), line, fill="black", font=font)
        y += line_height

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    image.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name

def build_html_message(payload):
    notice_no = html_escape(payload["notice_no"])
    start = html_escape(payload["start"])
    valid = html_escape(payload["valid"])
    body = add_coordinate_links(payload["body"])

    return (
        f"<b>Notice to mariner No:</b> {notice_no}\n"
        f"<b>Start:</b> {start}\n"
        f"<b>Valid:</b> {valid}\n\n"
        f"{body}"
    )

# ---------------- GMAIL CORE ----------------
def connect_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")
    return mail

def fetch_recent_matching_emails(limit=100):
    mail = connect_gmail()
    result, data = mail.search(None, "ALL")

    if result != "OK":
        mail.logout()
        return []

    ids = data[0].split()
    if not ids:
        mail.logout()
        return []

    matched = []

    for num in reversed(ids[-limit:]):
        result, msg_data = mail.fetch(num, "(RFC822)")
        if result != "OK" or not msg_data or not msg_data[0]:
            continue

        raw_bytes = msg_data[0][1]
        if not raw_bytes:
            continue

        msg = email.message_from_bytes(raw_bytes)

        from_header = decode_mime_words(msg.get("From", ""))
        subject = decode_mime_words(msg.get("Subject", ""))
        msg_id = normalize_message_id(msg)

        if not msg_id:
            continue

        if SENDER.lower() not in from_header.lower():
            continue

        if SUBJECT_KEYWORD.lower() not in subject.lower():
            continue

        matched.append({
            "imap_num": num,
            "msg": msg,
            "msg_id": msg_id,
            "subject": subject,
            "from": from_header,
        })

    mail.logout()
    return matched

def extract_docx_attachment_bytes(msg):
    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition", "")).lower()
        filename = part.get_filename()
        filename = decode_mime_words(filename) if filename else ""
        content_type = (part.get_content_type() or "").lower()

        if "attachment" in content_disposition or filename:
            if (
                filename.lower().endswith(".docx")
                or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                or (content_type == "application/octet-stream" and filename.lower().endswith(".docx"))
            ):
                file_bytes = part.get_payload(decode=True)
                if file_bytes:
                    return file_bytes, filename or "notice_to_mariner.docx"

    return None, None

def get_latest_matching_email():
    matched = fetch_recent_matching_emails(limit=150)
    if not matched:
        return None
    return matched[0]

def send_notice_to_chat(bot, chat_id, payload, image_path=None):
    html_msg = build_html_message(payload)

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as img:
            bot.send_photo(chat_id=chat_id, photo=img)

    for chunk in split_html_message(html_msg):
        bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

def process_message_entry(bot, chat_id, entry):
    msg = entry["msg"]
    file_bytes, filename = extract_docx_attachment_bytes(msg)

    if not file_bytes:
        bot.send_message(chat_id=chat_id, text="DOCX attachment not found in latest matching email.")
        return False

    doc_text, _tmp_docx_path = read_docx_text_from_bytes(file_bytes)
    payload = extract_notice_payload(doc_text)
    image_path = render_text_image(payload)

    send_notice_to_chat(bot, chat_id, payload, image_path=image_path)
    return True

def initialize_gmail_cache_silently():
    if cache.get("gmail_initialized"):
        return

    latest = get_latest_matching_email()
    if latest and latest["msg_id"] not in cache["gmail"]:
        cache["gmail"].append(latest["msg_id"])

    cache["gmail_initialized"] = True
    save_cache(cache)

def auto_check_gmail(updater):
    try:
        initialize_gmail_cache_silently()

        matched = fetch_recent_matching_emails(limit=150)
        if not matched:
            return

        new_entries = []
        seen_ids = set(cache["gmail"])

        for entry in reversed(matched):
            if entry["msg_id"] not in seen_ids:
                new_entries.append(entry)

        for entry in new_entries:
            if not CHAT_ID:
                continue

            ok = process_message_entry(updater.bot, CHAT_ID, entry)
            cache["gmail"].append(entry["msg_id"])

            if ok:
                save_cache(cache)

        if new_entries:
            save_cache(cache)

    except Exception as e:
        print("Gmail auto-check error:", e)

# ---------------- COMMANDS ----------------
def testbot(update, context):
    update.message.reply_text("✅ Bot running")

def get_chat_id_cmd(update, context):
    update.message.reply_text(f"Chat ID: {update.message.chat.id}")

def clearcache(update, context):
    cache["gmail"] = []
    cache["gmail_initialized"] = False
    save_cache(cache)
    update.message.reply_text("✅ Gmail cache cleared")

def checkgovil(update, context):
    try:
        latest = get_latest_matching_email()
        if not latest:
            update.message.reply_text("No matching emails found.")
            return

        ok = process_message_entry(context.bot, update.message.chat.id, latest)
        if not ok:
            return

    except Exception as e:
        print("checkgovil error:", e)
        update.message.reply_text(f"Error: {e}")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("getid", get_chat_id_cmd))
    dp.add_handler(CommandHandler("clearcache", clearcache))
    dp.add_handler(CommandHandler("checkgovil", checkgovil))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            auto_check_gmail(updater)
        except Exception as e:
            print("Auto check error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()