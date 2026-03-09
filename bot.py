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

CHECK_INTERVAL = 300

RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"

CACHE_FILE = "cache.json"

ZONES = ["TAURUS", "DELTA", "CRUSADE"]

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": ""}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

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
    if not feed.entries:
        update.message.reply_text("No GOV notices found")
        return
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

def last(update: Update, context: CallbackContext):

    msgs = fetch_sealagom_navtex()

    if not msgs:
        update.message.reply_text("No NAVTEX messages")
        return

    for m in msgs:
        update.message.reply_text(m[:3500])

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

        except Exception as e:
            print(e)

        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()