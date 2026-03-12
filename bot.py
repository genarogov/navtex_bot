import os
import re
import time
import json
import requests
import imaplib
import email
from bs4 import BeautifulSoup
from telegram.ext import Updater, CommandHandler
from telegram import Bot
from datetime import datetime
from docx import Document
from pdf2image import convert_from_path

# ---------------- CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = "imap.gmail.com"

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"

SENDER = "benzviy.mot.gov.il@send.vpcontact.com"
SUBJECT_KEYWORD = "notice to mariner"

ZONES = ["TAURUS","DELTA","CRUSADE"]
CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 минут

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"sealagom": [], "gov": {"last_number": "019","year":"2026","last_format":"_"},
                "gov_mail": []}
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
def fetch_sealagom_full():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text,"html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r'\n(?=NAVAREA III - \d{4}/\d{2})', text)
        messages = []
        for m in raw_msgs:
            header_match = re.match(r'NAVAREA III - (\d{4}/\d{2})\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC)', m)
            if not header_match:
                continue
            number = header_match.group(1)
            date_str = header_match.group(2)
            try:
                msg_date = datetime.strptime(date_str, "%d %B %Y %H:%M UTC")
            except:
                msg_date = datetime.min
            clean = m.strip()
            if len(clean) > 30:
                messages.append((number, clean, msg_date))
        messages_sorted = sorted(messages, key=lambda x: x[2], reverse=True)
        return messages_sorted
    except Exception as e:
        print("Sealagom full fetch error:", e)
        return []

def send_new_sealagom(updater):
    fetched = fetch_sealagom_full()
    new_messages = []
    for number, msg_text, msg_date in fetched:
        if number not in cache["sealagom"]:
            msg_with_links = add_coordinate_links(msg_text[:3500])
            updater.bot.send_message(CHAT_ID, msg_with_links, parse_mode="HTML", disable_web_page_preview=True)
            cache["sealagom"].append(number)
            new_messages.append(number)
    if new_messages:
        save_cache(cache)

def test(update, context):
    messages = fetch_sealagom_full()
    if not messages:
        update.message.reply_text("No Sealagom messages")
        return
    for number, msg_text, msg_date in messages[:5]:
        msg = add_coordinate_links(msg_text[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- GOV ----------------
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

def testgov(update, context):
    last_number = cache["gov"]["last_number"]
    year = cache["gov"]["year"]
    fmt = cache["gov"]["last_format"]
    latest = find_latest_gov(last_number, year, fmt)
    url = f"https://www.gov.il/en/pages/mariners{fmt}{latest:03d}{fmt}{year}"
    update.message.reply_text(f"Last message from GOV.il:\n{url}")

# ---------------- METAREA ----------------
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
        if s==-1: continue
        nxt=[forecast.find(z,s+1) for z in ZONES[i+1:] if forecast.find(z,s+1)!=-1]
        e=min(nxt) if nxt else len(forecast)
        txt=forecast[s:e].strip()
        if txt.startswith(zone): txt=txt[len(zone):].lstrip()
        blocks.append(f"📍 {zone}\n{txt}")
    msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)
    return msg[:4000]

def metarea(update,context):
    update.message.reply_text(get_metarea())

# ---------------- GOV MAIL ----------------
def fetch_gov_emails():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")
    result, data = mail.search(None, f'(FROM "{SENDER}" SUBJECT "{SUBJECT_KEYWORD}")')
    mail_ids = data[0].split()
    downloaded_docs = []
    for num in mail_ids:
        if num.decode() in cache["gov_mail"]:  # уже обработано
            continue
        result, msg_data = mail.fetch(num, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        if msg.get_content_maintype() != 'multipart': continue
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart': continue
            if part.get("Content-Disposition") is None: continue
            filename = part.get_filename()
            if filename and filename.lower().endswith((".doc", ".docx")):
                save_path = os.path.join("downloads", filename)
                os.makedirs("downloads", exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(part.get_payload(decode=True))
                downloaded_docs.append((num.decode(), save_path))
    mail.logout()
    return downloaded_docs

def parse_gov_word(doc_path):
    doc = Document(doc_path)
    full_text = "\n".join([p.text for p in doc.paragraphs])
    number_match = re.search(r'Number[:\s]+(\S+)', full_text, re.IGNORECASE)
    start_match = re.search(r'Start[:\s]+([\d/-]+)', full_text, re.IGNORECASE)
    valid_match = re.search(r'Valid[:\s]+([\d/-]+)', full_text, re.IGNORECASE)
    number = number_match.group(1) if number_match else "N/A"
    start = start_match.group(1) if start_match else "N/A"
    valid = valid_match.group(1) if valid_match else "N/A"
    text_with_links = add_coordinate_links(full_text)
    return number, start, valid, text_with_links

def word_to_images(doc_path, output_dir="screenshots"):
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    pdf_path = os.path.join(output_dir, os.path.basename(doc_path).replace(".docx",".pdf").replace(".doc",".pdf"))
    os.system(f'libreoffice --headless --convert-to pdf "{doc_path}" --outdir "{output_dir}"')
    pages = convert_from_path(pdf_path, dpi=200)
    img_paths = []
    for i, page in enumerate(pages):
        img_file = os.path.join(output_dir, f"page_{i+1}.png")
        page.save(img_file, "PNG")
        img_paths.append(img_file)
    return img_paths

def send_gov_mail(updater):
    docs = fetch_gov_emails()
    for mail_id, doc_path in docs:
        number, start, valid, text = parse_gov_word(doc_path)
        updater.bot.send_message(CHAT_ID, f"Notice to mariner {number}\nStart: {start}\nValid: {valid}\n\n{text}", parse_mode="HTML")
        images = word_to_images(doc_path)
        for img in images:
            updater.bot.send_photo(CHAT_ID, photo=open(img,"rb"))
        cache["gov_mail"].append(mail_id)
    if docs: save_cache(cache)

# ---------------- TESTBOT ----------------
def testbot(update, context):
    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("testgov", testgov))
    dp.add_handler(CommandHandler("metarea", metarea))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            send_new_sealagom(updater)
            send_gov_mail(updater)
        except Exception as e:
            print("Auto check error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()