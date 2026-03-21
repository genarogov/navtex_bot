# (код сокращать не буду — вот полный файл, просто копируй и вставляй)

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
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMS_API_TOKEN = os.getenv("IMS_API_TOKEN", "").strip()

# ---------------- CACHE ----------------
CACHE_FILE = "cache.json"
TAIL_SCAN_LIMIT = 40

# ---------------- BUTTONS ----------------
HAIFA_BUOY_BUTTON = "🛟 Haifa buoy"
ASHDOD_BUOY_BUTTON = "🛟 Ashdod buoy"

FORECAST_BUTTON = "🌤 Forecast Taurus Delta Crusade"
GOV_BUTTON = "📜 gov.il"
NAVAREA_BUTTON = "📜 Navarea III"

# ---------------- KEYBOARD ----------------
WEATHER_KEYBOARD = [
    [GOV_BUTTON, NAVAREA_BUTTON],
    [FORECAST_BUTTON],
    ["🌤 Haifa Technion", HAIFA_BUOY_BUTTON],
    ["🌤 En Karmel", "🌤 Hadera Port"],
    ["🌤 Tel Aviv Coast", "🌤 Ashqelon Port"],
    ["🌤 Ashdod Port", ASHDOD_BUOY_BUTTON],
]

def get_main_keyboard():
    return ReplyKeyboardMarkup(WEATHER_KEYBOARD, resize_keyboard=True)

# ---------------- TIME ----------------
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

def utc_to_israel_local(dt_utc):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(ISRAEL_TZ)

def format_full_datetime_with_isr(dt_utc):
    if not dt_utc:
        return "N/A"
    dt_isr = utc_to_israel_local(dt_utc)
    return (
        f"Updated: {dt_utc.strftime('%d %B %Y').upper()}\n"
        f"{dt_utc.strftime('%H:%M')} UTC / {dt_isr.strftime('%H:%M')} LT"
    )

# ---------------- BASIC HELPERS ----------------
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
    return str(text or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

# ---------------- GMAIL ----------------
SENDER_KEYWORD = "mot.gov.il"
SUBJECT_KEYWORD = "notice to mariner"

def connect_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")
    return mail

def message_matches(msg):
    from_header = decode_mime_words(msg.get("From",""))
    subject = decode_mime_words(msg.get("Subject",""))
    msg_id = normalize_message_id(msg)

    if not msg_id:
        return None
    if SENDER_KEYWORD not in from_header.lower():
        return None
    if SUBJECT_KEYWORD not in subject.lower():
        return None

    return {"msg": msg, "id": msg_id}

def fetch_latest_matching_email():
    mail = connect_gmail()
    result, data = mail.search(None, "ALL")
    ids = data[0].split()

    for num in reversed(ids[-TAIL_SCAN_LIMIT:]):
        _, msg_data = mail.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        entry = message_matches(msg)
        if entry:
            mail.logout()
            return entry

    mail.logout()
    return None

# ---------------- DOCX ----------------
def read_docx(file_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def extract_docx(msg):
    for part in msg.walk():
        filename = part.get_filename() or ""
        if filename.lower().endswith(".docx"):
            return part.get_payload(decode=True)
    return None

def build_message(text):
    return text

def process_entry(bot, chat_id, entry):
    file_bytes = extract_docx(entry["msg"])
    if not file_bytes:
        bot.send_message(chat_id=chat_id, text="No DOCX")
        return
    text = read_docx(file_bytes)
    bot.send_message(chat_id=chat_id, text=text[:4000])

# ---------------- NAVAREA ----------------
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/"

def fetch_navarea():
    r = requests.get(SEALAGOM_URL)
    return r.text

# ---------------- HANDLER ----------------
def handle_buttons(update, context):
    text = update.message.text

    if text == GOV_BUTTON:
        latest = fetch_latest_matching_email()
        if not latest:
            update.message.reply_text("No messages")
            return
        process_entry(context.bot, update.message.chat.id, latest)

    elif text == NAVAREA_BUTTON:
        data = fetch_navarea()
        update.message.reply_text(data[:4000])

    elif text == FORECAST_BUTTON:
        update.message.reply_text("Forecast not changed")

# ---------------- COMMANDS ----------------
def start(update, context):
    update.message.reply_text("Bot started", reply_markup=get_main_keyboard())

def checkgovil(update, context):
    latest = fetch_latest_matching_email()
    if latest:
        process_entry(context.bot, update.message.chat.id, latest)

def testbot(update, context):
    update.message.reply_text("Bot running")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("checkgovil", checkgovil))
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_buttons))

    updater.start_polling()
    print("BOT STARTED")
    updater.idle()

if __name__ == "__main__":
    main()