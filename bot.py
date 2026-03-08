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
    """
    Парсим METAREA III прогноз:
    - разделяем по зонам TAURUS / DELTA / CRUSADE
    - извлекаем время, ветер, состояние моря
    - возвращаем читаемые NAVTEX-блоки
    """
    blocks = []
    text = text.replace("\n", " ")

    for area in AREAS:
        # Ищем блок по зоне до следующей зоны или конца
        pattern = area + r"(.*?)(?=" + "|".join(AREAS) + "|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        block_text = match.group(0).strip()

        # Извлекаем время (пример: "FORECAST UP TO 09 MARCH 04 UTC")
        time_match = re.search(r"(FORECAST UP TO|VALID FROM|VALID UNTIL)\s*(\d{1,2}\s+[A-Z]+\s+\d{2,4}\s*\d{0,2}\s*UTC)?", block_text, re.IGNORECASE)
        time_str = time_match.group(2).strip() if time_match else "N/A"

        # Извлекаем ветер (пример: "NORTH NORTHEAST 3 UP TO 5")
        wind_match = re.search(r"([NSEW]+(?:\s+[NSEW]+)?)\s*(\d{1,2}(?:\s*OR\s*\d{1,2})?)", block_text, re.IGNORECASE)
        wind_str = wind_match.group(0).strip() if wind_match else "N/A"

        # Извлекаем море (пример: "VARIABLE 3 OR 4. SMOOTH OR SLIGHT")
        sea_match = re.search(r"(SMOOTH|SLIGHT|MODERATE|ROUGH|CHANCE OF THUNDERSTORM)", block_text, re.IGNORECASE)
        sea_str = sea_match.group(0).strip() if sea_match else "N/A"

        # Формируем читаемый блок
        block_formatted = f"📍 {area}\n🕒 Valid: {time_str}\n🌬 Wind: {wind_str}\n🌊 Sea: {sea_str}"
        blocks.append(block_formatted)

    if not blocks:
        return "No forecast available for TAURUS / DELTA / CRUSADE"

    return "\n\n".join(blocks)


def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"Error loading METAREA: {e}"

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()
    parsed = parse_metarea(text)
    return parsed[:4000]  # Telegram limit


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