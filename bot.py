import os
import re
import time
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from telegram.ext import Updater, CommandHandler

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800  # 30 минут

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
GOV_NTM_URLS = ["https://www.gov.il/en/pages/mariners_019_2026"]
RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"

ZONES = ["TAURUS","DELTA","CRUSADE"]

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"sealagom": [], "gov": {"last_number": "019", "year": "2026"}, "rss": []}
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

# ---------------- SEALAGOM NAVTEX ----------------
def fetch_sealagom():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text,"html.parser")
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
        print("Sealagom fetch error:", e)
        return []

def send_sealagom(updater):
    messages = fetch_sealagom()
    new_msgs = [m for m in messages if m not in cache["sealagom"]]
    for m in new_msgs:
        msg = add_coordinate_links(m[:3500])
        updater.bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
        cache["sealagom"].append(m)
    if new_msgs:
        save_cache(cache)

def test(update, context):
    messages = fetch_sealagom()
    if not messages:
        update.message.reply_text("No Sealagom messages")
        return
    for m in messages:
        msg = add_coordinate_links(m[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- GOV IL ----------------
def get_gov_notices():
    notices = []
    for url in GOV_NTM_URLS:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.find("h1")
            number = h1.get_text().strip() if h1 else "No number"
            notices.append({"number": number, "link": url})
        except Exception as e:
            print("GOV fetch error:", e)
    return notices

def send_gov(updater):
    notices = get_gov_notices()
    last_number = cache["gov"]["last_number"]
    year = cache["gov"]["year"]
    for n in notices:
        # Простейшая проверка нового номера
        if last_number not in n["number"]:
            msg = f"Новое сообщение: ⚓ <a href='{n['link']}'>{n['number']}</a>"
            updater.bot.send_message(CHAT_ID, msg, parse_mode="HTML")
            # Обновляем кэш
            cache["gov"]["last_number"] = n["number"].split()[-1]
            save_cache(cache)

def testgov(update, context):
    notices = get_gov_notices()
    if not notices:
        update.message.reply_text("No GOV notices found")
        return
    for n in notices:
        msg = f"Новое сообщение: ⚓ <a href='{n['link']}'>{n['number']}</a>"
        update.message.reply_text(msg, parse_mode="HTML")

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

def metarea(update,context):
    update.message.reply_text(get_metarea())

# ---------------- RSS ----------------
def send_rss(updater):
    feed = feedparser.parse(RSS_URL)
    new_entries = [e for e in feed.entries if e.link not in cache["rss"]]
    for e in new_entries:
        msg = f"RSS GOV IL: {e.title}\n{e.link}"
        updater.bot.send_message(CHAT_ID, msg)
        cache["rss"].append(e.link)
    if new_entries:
        save_cache(cache)

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

    # Авто-проверка каждые 30 минут
    while True:
        try:
            send_sealagom(updater)
            send_gov(updater)
            send_rss(updater)
        except Exception as e:
            print("Auto check error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()