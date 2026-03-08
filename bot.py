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

DIRECTIONS = {
    "NORTH": "N",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTH": "S",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "EAST": "E",
    "WEST": "W",
}


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


def shorten_direction(text):

    for full, short in DIRECTIONS.items():
        text = text.replace(full, short)

    text = text.replace("NORTH NORTHEAST", "NNE")
    text = text.replace("NORTH NORTHWEST", "NNW")
    text = text.replace("SOUTH SOUTHEAST", "SSE")
    text = text.replace("SOUTH SOUTHWEST", "SSW")

    return text


def clean_line(line):

    line = line.upper()
    line = shorten_direction(line)

    line = re.sub(r"\s+", " ", line)

    return line.strip()


def parse_metarea(text):

    lines = [clean_line(l) for l in text.split("\n") if l.strip()]

    results = {}

    current_area = None

    for line in lines:

        if line in AREAS:
            current_area = line
            results[current_area] = []
            continue

        if current_area:
            results[current_area].append(line)

            if len(results[current_area]) >= 2:
                current_area = None

    message = "🌊 METAREA III\n"

    for area in AREAS:

        if area not in results:
            continue

        message += f"\n📍 {area}\n"

        forecast = " ".join(results[area])

        wind_match = re.search(r"(N|S|E|W|NE|NW|SE|SW|NNE|NNW|SSE|SSW)\s*\d", forecast)

        if wind_match:
            message += f"🌬 Wind: {wind_match.group(0)}\n"

        message += f"{forecast}\n"

    if len(message) < 20:
        return "No forecast for TAURUS / DELTA / CRUSADE"

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