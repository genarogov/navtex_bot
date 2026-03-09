import os
import time
import json
import hashlib
import re
import requests
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
import feedparser
from bs4 import BeautifulSoup

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

CHECK_INTERVAL = 900  # 15 минут

RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"

CACHE_FILE = "cache.json"

ZONES = ["TAURUS", "DELTA", "CRUSADE"]

# ---------------- CACHE ----------------

def load_cache():

    if not os.path.exists(CACHE_FILE):
        return {
            "gov": [],
            "metarea": "",
            "navtex_sent": []
        }

    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):

    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- UTILS ----------------

def hash_msg(text):
    return hashlib.sha1(text.encode()).hexdigest()

def extract_coords(text):

    coords = re.findall(r"(\d{2}-\d{2}[NS])\s*(\d{3}-\d{2}[EW])", text)

    out=[]

    for lat, lon in coords:

        latd, latm = int(lat[:2]), int(lat[3:5])
        lond, lonm = int(lon[:3]), int(lon[4:6])

        if "S" in lat:
            latd *= -1

        if "W" in lon:
            lond *= -1

        latf = latd + latm/60
        lonf = lond + lonm/60

        out.append((latf, lonf))

    return out

# ---------------- GOV ----------------

def format_gov(entry):

    title = entry.get("title","")
    link = entry.get("link","")
    date = entry.get("published","")

    return f"⚓ GOV.il Notice\n\n{title}\n{date}\n{link}"

def check_gov():

    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries[:5]:

        nid = entry.get("link")

        if nid in cache["gov"]:
            continue

        bot.send_message(CHAT_ID, format_gov(entry))

        cache["gov"].append(nid)

        save_cache(cache)

def lastgov(update: Update, context: CallbackContext):

    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries[:5]:
        update.message.reply_text(format_gov(entry))

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

        messages=[]

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

            if len(clean) > 40:
                messages.append(clean)

        return messages

    except Exception as e:
        print(e)
        return []

def send_map(coords):

    if len(coords) == 1:

        lat, lon = coords[0]

        bot.send_location(CHAT_ID, lat, lon)

    elif len(coords) > 1:

        url = "https://staticmap.openstreetmap.de/staticmap.php?"

        path = "|".join([f"{lat},{lon}" for lat,lon in coords])

        url += f"size=800x600&path={path}&markers={path}"

        bot.send_photo(CHAT_ID, url)

def check_navtex():

    msgs = fetch_sealagom_navtex()

    for m in msgs:

        h = hash_msg(m)

        if h in cache["navtex_sent"]:
            continue

        bot.send_message(CHAT_ID, "⚠️ NAVAREA WARNING\n\n" + m)

        coords = extract_coords(m)

        if coords:
            send_map(coords)

        cache["navtex_sent"].append(h)

        save_cache(cache)

def last(update: Update, context: CallbackContext):

    msgs = fetch_sealagom_navtex()[:5]

    for m in msgs:

        update.message.reply_text(m)

        coords = extract_coords(m)

        if coords:

            if len(coords)==1:

                update.message.reply_location(coords[0][0], coords[0][1])

            else:

                path="|".join([f"{lat},{lon}" for lat,lon in coords])

                url=f"https://staticmap.openstreetmap.de/staticmap.php?size=800x600&path={path}&markers={path}"

                update.message.reply_photo(url)

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

    while True:

        try:

            check_gov()
            check_metarea()
            check_navtex()

        except Exception as e:

            print(e)

        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()