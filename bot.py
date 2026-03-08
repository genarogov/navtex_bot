import os
import json
import time
import re
import feedparser
import requests
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

CHECK_INTERVAL = 300

RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"
METAREA_HTML_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"

CACHE_FILE = "cache.json"
AREAS = ["TAURUS", "DELTA", "CRUSADE"]

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": ""}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- GOV.il ----------------

def format_gov(entry):
    title = entry.get("title", "")
    link = entry.get("link", "")
    published = entry.get("published", "")
    return f"""⚓ GOV.il Notice

{title}

📅 {published}

{link}
"""

def check_gov():
    try:
        feed = feedparser.parse(RSS_URL)
    except:
        return
    for entry in feed.entries[:5]:
        nid = entry.get("link")
        if nid in cache["gov"]:
            continue
        bot.send_message(CHAT_ID, format_gov(entry))
        cache["gov"].append(nid)
        save_cache(cache)

def lastgov(update: Update, context: CallbackContext):
    update.message.reply_text("Loading GOV IL notices...")
    try:
        feed = feedparser.parse(RSS_URL)
    except:
        update.message.reply_text("Error loading GOV")
        return
    if not feed.entries:
        update.message.reply_text("No entries found")
        return
    for entry in feed.entries[:5]:
        update.message.reply_text(format_gov(entry))

# ---------------- METAREA via Saildocs ----------------

def extract_wmo_code(html_text):
    """Ищем WMO‑код вида FQ.. .."""
    # Примеры: FQMQ54 LFPW, FQME22 LGAT, FQMQ54 LFPW
    match = re.search(r"(FQ[A-Z0-9]{2,4})\s+([A-Z]{4})", html_text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return None

def get_metarea_text():
    try:
        r = requests.get(METAREA_HTML_URL, timeout=20)
        r.raise_for_status()
    except:
        return "Error loading METAREA HTML"

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()

    code = extract_wmo_code(text)

    if not code:
        return "WMO code not found"

    # запрос Saildocs
    saildocs_url = f"https://api.saildocs.com/text?query={code}"
    try:
        r2 = requests.get(saildocs_url, timeout=20)
        r2.raise_for_status()
    except:
        return "Error loading METAREA via Saildocs"

    return r2.text

def parse_saildocs_metarea(text):
    lines = text.splitlines()
    results = {}
    current_area = None

    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        uc = clean.upper()
        if uc in AREAS:
            current_area = uc
            results[current_area] = []
            continue
        if current_area:
            results[current_area].append(clean)

    if not results:
        return "No forecast found"

    # Форматируем красиво
    msg = "🌊 METAREA III FORECAST\n"
    for area in AREAS:
        if area in results:
            msg += f"\n📍 {area}\n"
            for row in results[area]:
                msg += row + "\n"
    return msg

def metarea(update: Update, context: CallbackContext):
    update.message.reply_text("Loading METAREA III forecast...")
    text = get_metarea_text()
    update.message.reply_text(text[:4000])

# ----------------- MAIN -----------------

def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("lastgov", lastgov))
    dp.add_handler(CommandHandler("metarea", metarea))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            check_gov()
        except Exception as e:
            print("GOV error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()