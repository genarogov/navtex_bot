import os
import time
import json
import re
import requests
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)

CHECK_INTERVAL = 300  # 5 минут, но GOV проверяется каждые 15 минут

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
GOV_URL = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners?skip=0"
GOV_INTERVAL = 900  # 15 минут

CACHE_FILE = "cache.json"
ZONES = ["TAURUS", "DELTA", "CRUSADE"]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
}

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": "", "navtex": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- UNIVERSAL COORDINATE PARSER ----------------
def convert_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes)/60
    if direction in ["S","W"]:
        value = -value
    return value

def add_coordinate_links(text):
    coord_pattern = re.compile(
        r'(\d{1,3})[°\-\s]+(\d{1,2}\.\d+)\s*([NSEW])',
        re.IGNORECASE
    )
    coords = list(coord_pattern.finditer(text))
    replacements = []
    i = 0
    while i < len(coords) - 1:
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
        text = text[:start] + html + text[end:]
    return text

# ---------------- METAREA ----------------
def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text()
        issued = re.search(r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC", text)
        issued = issued.group(0) if issued else "N/A"
        start = text.find("TAURUS")
        end = text.find("KASTELLORIZO SEA")
        if start == -1 or end == -1:
            return "Forecast not found"
        forecast = text[start:end]
        blocks=[]
        for i, zone in enumerate(ZONES):
            s = forecast.find(zone)
            if s == -1:
                continue
            nxt = [forecast.find(z, s+1) for z in ZONES[i+1:]]
            nxt = [n for n in nxt if n!=-1]
            e = min(nxt) if nxt else len(forecast)
            txt = forecast[s:e].strip()
            if txt.startswith(zone):
                txt = txt[len(zone):].lstrip()
            blocks.append(f"📍 {zone}\n{txt}")
        msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)
        return msg[:4000]
    except:
        return "Error loading METAREA"

def check_metarea():
    text = get_metarea()
    if text == cache["metarea"]:
        return
    cache["metarea"] = text
    save_cache(cache)
    bot.send_message(CHAT_ID, "🌊 METAREA III FORECAST\n\n" + text)

def metarea(update: Update, context: CallbackContext):
    update.message.reply_text(get_metarea())

# ---------------- NAVTEX ----------------
def fetch_sealagom_navtex():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r"\n(?=\d{4}/\d{2})", text)
        messages = []
        for m in raw_msgs:
            date_match = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC", m)
            if not date_match:
                continue
            start = date_match.start()
            end_match = re.search(r"\bDetails\b", m)
            if not end_match:
                continue
            end = end_match.start()
            clean = m[start:end].strip()
            if len(clean) > 30:
                messages.append(clean)
        return messages[:5]
    except Exception as e:
        print(e)
        return []

def send_navtex():
    messages = fetch_sealagom_navtex()
    new_msgs = [m for m in messages if m not in cache["navtex"]]
    if not new_msgs:
        return
    for m in new_msgs:
        msg = add_coordinate_links(m[:3500])
        bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
        cache["navtex"].append(m)
    save_cache(cache)

def last(update: Update, context: CallbackContext):
    msgs = fetch_sealagom_navtex()
    if not msgs:
        update.message.reply_text("No NAVTEX messages")
        return
    for m in msgs:
        msg = add_coordinate_links(m[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- GOV ----------------
def fetch_gov_notices():
    try:
        r = requests.get(GOV_URL, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        blocks = soup.find_all("div", class_="notice-collector-block")
        notices = []
        for block in blocks[:10]:
            # Notice number
            num_tag = block.find(string=re.compile(r"\d{1,4}\s*/\s*\d{4}"))
            if not num_tag:
                continue
            notice_number = num_tag.strip().replace(" ", "")
            # Link
            link_tag = block.find("a", string=re.compile("Link to notice"))
            if not link_tag:
                continue
            link = link_tag.get("href")
            if not link.startswith("http"):
                link = "https://www.gov.il" + link
            # Заходим на страницу notice
            r2 = requests.get(link, headers=headers, timeout=20)
            soup2 = BeautifulSoup(r2.text, "html.parser")
            content_div = soup2.find("div", class_=re.compile("govil-page-content"))
            text = content_div.get_text("\n").strip() if content_div else ""
            # Subject и valid from/until
            subject_tag = block.find(string=re.compile(r"Subject"))
            subject = subject_tag.strip() if subject_tag else ""
            valid_tag = block.find(string=re.compile(r"Valid from"))
            valid = valid_tag.strip() if valid_tag else ""
            # Полный текст
            full_text = f'⚓ <a href="{link}">Notice {notice_number}</a>\nSubject: {subject}\n{valid}\n\n{text}'
            notices.append((notice_number, full_text))
        return notices[:5]
    except Exception as e:
        print("Error fetching GOV notices:", e)
        return []

def check_gov():
    notices = fetch_gov_notices()
    for notice_number, full_text in notices:
        if notice_number in cache.get("gov", []):
            continue
        full_text = add_coordinate_links(full_text)
        bot.send_message(CHAT_ID, full_text, parse_mode="HTML", disable_web_page_preview=True)
        cache.setdefault("gov", []).append(notice_number)
        save_cache(cache)

def lastgov(update: Update, context: CallbackContext):
    notices = fetch_gov_notices()
    if not notices:
        update.message.reply_text("No GOV notices found")
        return
    for notice_number, full_text in notices:
        full_text = add_coordinate_links(full_text)
        update.message.reply_text(full_text, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- COMMANDS ----------------
def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("metarea", metarea))
    dp.add_handler(CommandHandler("lastgov", lastgov))
    dp.add_handler(CommandHandler("last", last))
    updater.start_polling()
    print("BOT STARTED")
    last_gov_check = 0
    while True:
        try:
            # Проверка GOV каждые 15 минут
            if time.time() - last_gov_check >= GOV_INTERVAL:
                check_gov()
                last_gov_check = time.time()
            check_metarea()
            send_navtex()
        except Exception as e:
            print(e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()