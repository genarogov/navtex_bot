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

    return f"""
⚓ GOV.il Notice

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


def clean_metarea_text(text):

    text = text.replace("\n", " ")

    # добавляем переносы перед районами
    for area in AREAS:
        text = text.replace(area, f"\n{area}")

    blocks = []

    for area in AREAS:

        pattern = area + r"(.*?)(?=\n[A-Z ]{3,}|$)"

        match = re.search(pattern, text)

        if match:
            block = match.group(0)

            block = block.replace("  ", " ")

            blocks.append(block.strip())

    if not blocks:
        return "No forecast for TAURUS / DELTA / CRUSADE"

    return "\n\n".join(blocks)


def get_metarea():

    try:
        r = requests.get(METAREA_URL, timeout=20)
    except:
        return "Error loading METAREA"

    soup = BeautifulSoup(r.text, "html.parser")

    text = soup.get_text()

    cleaned = clean_metarea_text(text)

    return cleaned[:3500]


def check_metarea():

    text = get_metarea()

    if text == cache["metarea"]:
        return

    cache["metarea"] = text

    save_cache(cache)

    if "GALE" in text or "STORM" in text:

        bot.send_message(CHAT_ID, "⚠️ GALE WARNING\n\n" + text)

    else:

        bot.send_message(CHAT_ID, "🌊 METAREA III FORECAST\n\n" + text)


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