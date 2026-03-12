import os
import re
import json
import time
import imaplib
import email
from email.header import decode_header
import threading

from telegram.ext import Updater, CommandHandler
from docx import Document
from pdf2image import convert_from_path

# ---------------- ENV ----------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
SENDER = "benzviy.mot.gov.il@send.vpcontact.com"

CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 минут

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gmail": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)

cache = load_cache()

# ---------------- COORDINATES ----------------
def convert_coords(match):
    lat_deg = float(match.group(1))
    lat_min = float(match.group(2))
    lat_dir = match.group(3)
    lon_deg = float(match.group(4))
    lon_min = float(match.group(5))
    lon_dir = match.group(6)

    lat = lat_deg + lat_min / 60
    lon = lon_deg + lon_min / 60
    if lat_dir.upper() == "S":
        lat = -lat
    if lon_dir.upper() == "W":
        lon = -lon
    link = f"https://maps.google.com/?q={lat},{lon}"
    return f'<a href="{link}">{match.group(0)}</a>'

def make_clickable(text):
    pattern = re.compile(
        r'(\d{1,3})[^\d]+(\d{1,2}\.?\d*)\s*([NS])[^\d]+(\d{1,3})[^\d]+(\d{1,2}\.?\d*)\s*([EW])',
        re.I
    )
    return pattern.sub(convert_coords, text)

# ---------------- GMAIL ----------------
def check_gmail():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, data = mail.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-50:]  # последние 50 писем

        for i in ids[::-1]:
            status, msg_data = mail.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            subject, enc = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(enc or "utf-8")

            if SENDER not in msg["From"]:
                continue
            if "notice to mariner" not in subject.lower():
                continue

            mid = msg["Message-ID"]
            if mid in cache["gmail"]:
                continue

            for part in msg.walk():
                filename = part.get_filename()
                if not filename:
                    continue
                if not filename.lower().endswith(".docx"):
                    continue

                data = part.get_payload(decode=True)
                with open(filename, "wb") as f:
                    f.write(data)

                # читаем Word
                doc = Document(filename)
                text = "\n".join([p.text for p in doc.paragraphs])
                text = make_clickable(text)

                # PDF → скрин
                pdf = filename.replace(".docx", ".pdf")
                os.system(f'libreoffice --headless --convert-to pdf "{filename}"')
                if os.path.exists(pdf):
                    images = convert_from_path(pdf)
                    img = filename.replace(".docx", ".png")
                    images[0].save(img)
                    return subject, text, img, mid
    except Exception as e:
        print("GMAIL ERROR:", e)

    return None, None, None, None

# ---------------- COMMANDS ----------------
def checkgovil(update, context):
    subject, text, img, mid = check_gmail()
    if not subject:
        update.message.reply_text("No new messages")
        return

    context.bot.send_message(
        CHAT_ID,
        f"{subject}\n\n{text}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    context.bot.send_photo(CHAT_ID, photo=open(img, "rb"))

    cache["gmail"].append(mid)
    save_cache()

def clearcache(update, context):
    global cache
    cache["gmail"] = []
    save_cache()
    update.message.reply_text("Cache cleared")  # ← теперь ответ точно приходит

# ---------------- AUTO CHECK ----------------
def auto_check(updater):
    while True:
        try:
            subject, text, img, mid = check_gmail()
            if subject:
                updater.bot.send_message(
                    CHAT_ID,
                    f"{subject}\n\n{text}",
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                updater.bot.send_photo(CHAT_ID, photo=open(img, "rb"))
                cache["gmail"].append(mid)
                save_cache()
        except Exception as e:
            print("AUTO ERROR:", e)
        time.sleep(CHECK_INTERVAL)

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("checkgovil", checkgovil))
    dp.add_handler(CommandHandler("clearcache", clearcache))

    updater.start_polling()
    threading.Thread(target=auto_check, args=(updater,), daemon=True).start()
    updater.idle()

if __name__ == "__main__":
    main()