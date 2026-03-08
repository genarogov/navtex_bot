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

# Сокращение направления ветра
def shorten_direction(text):
    text = text.upper()
    text = text.replace("NORTH NORTHEAST", "NNE")
    text = text.replace("NORTH NORTHWEST", "NNW")
    text = text.replace("SOUTH SOUTHEAST", "SSE")
    text = text.replace("SOUTH SOUTHWEST", "SSW")
    text = text.replace("NORTH", "N")
    text = text.replace("SOUTH", "S")
    text = text.replace("EAST", "E")
    text = text.replace("WEST", "W")
    return text

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": ""}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

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

# Исправленный стабильный парсер METAREA
def parse_metarea(text):
    text = text.upper()
    # вставляем перенос строки перед каждой зоной, чтобы разделить слипшиеся названия
    for area in AREAS:
        text = re.sub(area, f"\n{area}", text)

    message = "🌊 METAREA III FORECAST\n"

    for area in AREAS:
        pattern = rf"{area}\n(.*?)(?=\nTAURUS|\nDELTA|\nCRUSADE|$)"
        match = re.search(pattern, text, re.S)
        if not match:
            continue
        block = match.group(1)
        block = re.sub(r"\s+", " ", block).strip()
        block = shorten_direction(block)
        message += f"\n📍 {area}\n"
        message += f"{block[:400]}...\n"  # обрезаем слишком длинные строки

    if message == "🌊 METAREA III FORECAST\n":
        return "No forecast found"

    return message

def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
    except:
        return "Error loading METAREA"
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()
    return parse_metarea(text)

def check_metarea():
    text = get_metarea()
    if text == cache["metarea"]:
        return
    cache["metarea"] = text
    save_cache(cache)
    bot.send_message(CHAT_ID, text)

# Команды Telegram
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
    update.message.reply_text("Loading METAREA forecast...")
    text = get_metarea()
    update.message.reply_text(text)

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
            print(e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()