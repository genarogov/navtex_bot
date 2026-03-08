import os
import json
import time
import feedparser
import requests
import re
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
CHECK_INTERVAL = 300

RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"

CACHE_FILE = "cache.json"
AREAS = ["TAURUS", "DELTA", "CRUSADE"]

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

# ---------------- GOV.il ----------------
def format_gov(entry):
    title = entry.get("title", "")
    link = entry.get("link", "")
    published = entry.get("published", "")
    return f"""⚓ GOV.il Notice

{title}

Published: {published}

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

# ---------------- METAREA ----------------
def parse_metarea(text):
    # Останавливаемся на KASTELLORIZO SEA
    if "KASTELLORIZO SEA" in text:
        text = text.split("KASTELLORIZO SEA")[0]

    # Время выпуска
    issued_match = re.search(r"(\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC)", text)
    issued_time = issued_match.group(1) if issued_match else "N/A"

    result_blocks = [f"🕒 Issued: {issued_time}"]

    for area in AREAS:
        # Блок зоны
        pattern_area = re.compile(f"{area}(.*?)(?={'|'.join(AREAS)}|$)", re.IGNORECASE)
        match_area = pattern_area.search(text)
        if not match_area:
            continue
        block_text = match_area.group(1).strip()

        # Предложения, содержащие направление ветра
        sentences = [s.strip() for s in re.split(r"\. ", block_text) if re.search(r"\b(NORTH|SOUTH|EAST|WEST|VARIABLE|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST)\b", s, re.IGNORECASE)]
        
        for sentence in sentences:
            wind_match = re.findall(r"((?:NORTH|SOUTH|EAST|WEST|VARIABLE|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST)[^\.,]*)", sentence, re.IGNORECASE)
            wind_str = ", ".join([w.strip() for w in wind_match]) if wind_match else "N/A"
            
            sea_match = re.findall(r"(SMOOTH|SLIGHT|MODERATE|ROUGH|CHANCE OF THUNDERSTORM)", sentence, re.IGNORECASE)
            sea_str = ", ".join([s.strip() for s in sea_match]) if sea_match else "N/A"

            result_blocks.append(f"📍 {area}\n🌬 Wind: {wind_str}\n🌊 Sea: {sea_str}")

    if len(result_blocks) == 1:
        return "No forecast available for TAURUS / DELTA / CRUSADE"

    return "\n\n".join(result_blocks)

def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"Error loading METAREA: {e}"

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()
    parsed = parse_metarea(text)
    return parsed[:4000]

def check_metarea():
    text = get_metarea()
    if text == cache["metarea"]:
        return
    cache["metarea"] = text
    save_cache(cache)
    header = "⚠️ GALE WARNING\n\n" if "GALE" in text or "STORM" in text else "🌊 METAREA III FORECAST\n\n"
    bot.send_message(CHAT_ID, header + text)

# ---------------- COMMANDS ----------------
def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

def lastgov(update: Update, context: CallbackContext):
    update.message.reply_text("Loading GOV notices...")
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

def metarea(update: Update, context: CallbackContext):
    update.message.reply_text("Loading forecast...")
    text = get_metarea()
    update.message.reply_text(text)

# ---------------- MAIN ----------------
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
            check_metarea()
        except Exception as e:
            print("Error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()