import os
import time
import json
import re
import requests
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)

CHECK_INTERVAL = 300  # 5 мин
GOV_INTERVAL = 900    # 15 мин

CACHE_FILE = "cache.json"
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
GOV_INDEX_URL = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners?skip=0"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": "", "navtex": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- universal coord parser ----------------
def convert_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes)/60
    if direction in ["S","W"]:
        value = -value
    return value

def add_coordinate_links(text):
    coord_pattern = re.compile(
        r'(\d{1,3})[°\-\s]+(\d{1,2}\.\d+)\s*([NSEW])', re.IGNORECASE
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

# ---------------- GOV ----------------

def fetch_gov_index():
    try:
        r = requests.get(GOV_INDEX_URL, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        # ищем ссылки на notice
        links = soup.find_all("a", href=re.compile(r"/en/pages/mariners-"))
        results = []
        for a in links:
            href = a.get("href")
            if not href.startswith("http"):
                href = "https://www.gov.il" + href
            # номер определяем из текста или href
            num_match = re.search(r"(\d{1,4}/\d{4})", a.text)
            notice_number = num_match.group(0) if num_match else href
            results.append((notice_number, href))
        # убрать дубли и оставить 5
        seen = set()
        out = []
        for num, link in results:
            if num not in seen:
                seen.add(num)
                out.append((num, link))
            if len(out)>=5:
                break
        return out
    except Exception as e:
        print("Error fetching GOV index:", e)
        return []

def fetch_gov_notice_page(link):
    try:
        r2 = requests.get(link, headers=headers, timeout=20)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        # основной текст
        content_div = soup2.find("div", class_=re.compile("govil-page-content"))
        text = content_div.get_text("\n").strip() if content_div else ""
        # subject
        subject = ""
        sub = soup2.find(string=re.compile("Subject", re.IGNORECASE))
        if sub:
            subject = sub.strip()
        # valid from / until
        valid = ""
        vf = soup2.find(string=re.compile("Valid from", re.IGNORECASE))
        if vf:
            valid = vf.strip()
        return subject, valid, text
    except Exception as e:
        print("Error fetching notice page:", e)
        return "", "", ""

def check_gov():
    notices = fetch_gov_index()
    for notice_number, link in notices:
        if notice_number in cache.get("gov", []):
            continue
        subject, valid, body = fetch_gov_notice_page(link)
        full_text = f'⚓ <a href="{link}">Notice {notice_number}</a>\nSubject: {subject}\n{valid}\n\n{body}'
        full_text = add_coordinate_links(full_text)
        bot.send_message(CHAT_ID, full_text, parse_mode="HTML", disable_web_page_preview=True)
        cache.setdefault("gov", []).append(notice_number)
        save_cache(cache)

def lastgov(update: Update, context: CallbackContext):
    notices = fetch_gov_index()
    if not notices:
        update.message.reply_text("No GOV notices found")
        return
    for notice_number, link in notices:
        subject, valid, body = fetch_gov_notice_page(link)
        full_text = f'⚓ <a href="{link}">Notice {notice_number}</a>\nSubject: {subject}\n{valid}\n\n{body}'
        full_text = add_coordinate_links(full_text)
        update.message.reply_text(full_text, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- NAVTEX as before ----------------
def fetch_sealagom_navtex():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text,"html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r"\n(?=\d{4}/\d{2})", text)
        messages=[]
        for m in raw_msgs:
            date_match = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC",m)
            if not date_match:
                continue
            start = date_match.start()
            end_match = re.search(r"\bDetails\b",m)
            if not end_match:
                continue
            end = end_match.start()
            clean = m[start:end].strip()
            if len(clean)>30:
                messages.append(clean)
        return messages[:5]
    except: return []
def send_navtex():
    messages = fetch_sealagom_navtex()
    new_msgs = [m for m in messages if m not in cache["navtex"]]
    if not new_msgs: return
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

# ---------------- MAIN ----------------
def test(update: Update, context: CallbackContext):
    update.message.reply_text("Bot running")

def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("lastgov", lastgov))
    dp.add_handler(CommandHandler("last", last))
    updater.start_polling()
    print("BOT STARTED")
    last_gov_check = 0
    while True:
        try:
            if time.time() - last_gov_check >= GOV_INTERVAL:
                check_gov()
                last_gov_check = time.time()
            send_navtex()
        except Exception as e:
            print("Error:",e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()