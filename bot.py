import os
import re
import time
import json
import imaplib
import email
from email.header import decode_header
import requests
from bs4 import BeautifulSoup
from telegram.ext import Updater, CommandHandler
from docx import Document
from pdf2image import convert_from_path
from PIL import Image

# ---------------- ENV ----------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# ---------------- CACHE ----------------
CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 мин

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"sealagom": [], "gov": {"last_number": "019", "year": "2026", "last_format": "_"}, "gmail": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- COORDINATES ----------------
def convert_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes)/60
    if direction in ["S","W"]:
        value = -value
    return value

def add_coordinate_links(text):
    coord_pattern = re.compile(r'(\d{1,3})[°\-\s]+(\d{1,2}\.\d+)\s*([NSEW])', re.IGNORECASE)
    coords = list(coord_pattern.finditer(text))
    replacements = []
    i = 0
    while i < len(coords)-1:
        lat = coords[i]
        lon = coords[i+1]
        if lat.group(3).upper() in ["N","S"] and lon.group(3).upper() in ["E","W"]:
            lat_val = convert_to_decimal(lat.group(1), lat.group(2), lat.group(3).upper())
            lon_val = convert_to_decimal(lon.group(1), lon.group(2), lon.group(3).upper())
            start = lat.start()
            end = lon.end()
            original = text[start:end]
            link = f"https://maps.google.com/?q={lat_val},{lon_val}"
            html = f'<a href="{link}">{original}</a>'
            replacements.append((start,end,html))
            i += 2
        else:
            i += 1
    for start,end,html in reversed(replacements):
        text = text[:start]+html+text[end:]
    return text

# ---------------- SEALAGOM ----------------
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
def fetch_sealagom():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text,"html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r"(?=NAVAREA III - \d{4}/\d{2})", text)
        messages = []
        for m in raw_msgs:
            m = m.strip()
            if m and len(m) > 30:
                messages.append(m)
        return messages[:5]
    except Exception as e:
        print("Sealagom fetch error:", e)
        return []

def send_sealagom(updater):
    messages = fetch_sealagom()
    new_msgs = [m for m in messages if m not in cache["sealagom"]]
    for m in new_msgs:
        msg = add_coordinate_links(m[:3500])
        if CHAT_ID:
            updater.bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
        cache["sealagom"].append(m)
    if new_msgs:
        save_cache(cache)

# ---------------- GOV ----------------
GOV_LAST_NUMBER = "019"
GOV_YEAR = "2026"
GOV_LAST_FORMAT = "_"

def page_exists(url):
    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200
    except:
        return False

def find_latest_gov(last_number, year, fmt):
    low = int(last_number)
    high = 300
    latest = low
    while low <= high:
        mid = (low + high) // 2
        num = f"{mid:03d}"
        url = f"https://www.gov.il/en/pages/mariners{fmt}{num}{fmt}{year}"
        if page_exists(url):
            latest = mid
            low = mid + 1
        else:
            high = mid - 1
    return latest

def send_gov_site(updater):
    last_number = cache["gov"]["last_number"]
    year = cache["gov"]["year"]
    fmt = cache["gov"]["last_format"]
    latest = find_latest_gov(last_number, year, fmt)
    url = f"https://www.gov.il/en/pages/mariners{fmt}{latest:03d}{fmt}{year}"
    if CHAT_ID:
        updater.bot.send_message(CHAT_ID, f"Last message from GOV.il:\n{url}")

# ---------------- METAREA ----------------
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
ZONES = ["TAURUS","DELTA","CRUSADE"]

def get_metarea():
    r = requests.get(METAREA_URL,timeout=20)
    soup = BeautifulSoup(r.text,"html.parser")
    text = soup.get_text()
    issued = re.search(r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC",text)
    issued = issued.group(0) if issued else "N/A"
    start = text.find("TAURUS")
    end = text.find("KASTELLORIZO SEA")
    forecast = text[start:end]
    blocks=[]
    for i,zone in enumerate(ZONES):
        s = forecast.find(zone)
        if s==-1:
            continue
        nxt=[forecast.find(z,s+1) for z in ZONES[i+1:]]
        nxt=[n for n in nxt if n!=-1]
        e=min(nxt) if nxt else len(forecast)
        txt=forecast[s:e].strip()
        if txt.startswith(zone):
            txt=txt[len(zone):].lstrip()
        blocks.append(f"📍 {zone}\n{txt}")
    msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)
    return msg[:4000]

def send_metarea(updater):
    if CHAT_ID:
        updater.bot.send_message(CHAT_ID, get_metarea())

# ---------------- GMAIL Gov.il Word ----------------
SENDER = "benzviy.mot.gov.il@send.vpcontact.com"
SUBJECT_KEYWORD = "notice to mariner"

def process_gmail(updater):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        result, data = mail.search(None, "ALL")
        mail_ids = data[0].split()
        for num in mail_ids[-10:]:  # последние 10 писем
            result, msg_data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")
            if SENDER.lower() not in msg["From"].lower():
                continue
            if SUBJECT_KEYWORD.lower() not in subject.lower():
                continue
            if msg["Message-ID"] in cache["gmail"]:
                continue
            # Найдём attachment .docx
            for part in msg.walk():
                if part.get_content_type() == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                    filename = part.get_filename()
                    content = part.get_payload(decode=True)
                    with open(filename, "wb") as f:
                        f.write(content)
                    # парсим текст
                    doc = Document(filename)
                    full_text = "\n".join([p.text for p in doc.paragraphs])
                    full_text = add_coordinate_links(full_text)
                    # скрин через pdf2image
                    # сначала конвертируем docx в pdf с помощью LibreOffice в будущем, но сейчас просто png через PIL
                    img = filename.replace(".docx",".png")
                    img_obj = Image.new("RGB",(800,1000),(255,255,255))
                    img_obj.save(img)
                    # Отправка
                    if CHAT_ID:
                        updater.bot.send_message(CHAT_ID, f"{subject}\n\n{full_text}")
                        updater.bot.send_photo(CHAT_ID, photo=open(img,"rb"))
            cache["gmail"].append(msg["Message-ID"])
        save_cache(cache)
    except Exception as e:
        print("Gmail error:", e)

# ---------------- TEST ----------------
def test(update, context):
    messages = fetch_sealagom()
    if not messages:
        update.message.reply_text("No Sealagom messages")
        return
    for m in messages:
        msg = add_coordinate_links(m[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

def testbot(update, context):
    update.message.reply_text("✅ Bot running")

def testgov(update, context):
    last_number = cache["gov"]["last_number"]
    year = cache["gov"]["year"]
    fmt = cache["gov"]["last_format"]
    latest = find_latest_gov(last_number, year, fmt)
    url = f"https://www.gov.il/en/pages/mariners{fmt}{latest:03d}{fmt}{year}"
    update.message.reply_text(f"Last message from GOV.il:\n{url}")

def metarea(update,context):
    update.message.reply_text(get_metarea())

# ---------------- GET CHAT ID ----------------
def get_chat_id_cmd(update, context):
    update.message.reply_text(f"Chat ID: {update.message.chat.id}")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Handlers
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("testgov", testgov))
    dp.add_handler(CommandHandler("metarea", metarea))
    dp.add_handler(CommandHandler("getid", get_chat_id_cmd))  # временно

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            send_sealagom(updater)
            send_gov_site(updater)
            send_metarea(updater)
            process_gmail(updater)
        except Exception as e:
            print("Auto check error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()