import os
import time
import json
import requests
import re
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
CHECK_INTERVAL = 300
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
CACHE_FILE = "cache.json"

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"metarea": ""}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- METAREA ----------------
def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"Error loading METAREA: {e}"

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()

    # Берём только от TAURUS до KASTELLORIZO SEA
    start = text.find("TAURUS")
    end = text.find("KASTELLORIZO SEA")
    if start == -1 or end == -1:
        return "Forecast not found"
    forecast_text = text[start:end].strip()

    # Ищем дату и время выпуска (Issued)
    issued_match = re.search(r"(\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC)", text)
    issued_time = issued_match.group(1) if issued_match else "N/A"

    full_text = f"🕒 Issued: {issued_time}\n\n{forecast_text}"
    return full_text[:4000]  # ограничение длины Telegram

def check_metarea():
    text = get_metarea()
    if text == cache["metarea"]:
        return
    cache["metarea"] = text
    save_cache(cache)
    bot.send_message(CHAT_ID, "🌊 METAREA III FORECAST\n\n" + text)

# ---------------- COMMAND ----------------
def metarea(update: Update, context: CallbackContext):
    update.message.reply_text("Loading forecast...")
    text = get_metarea()
    update.message.reply_text(text)

def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("metarea", metarea))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            check_metarea()
        except Exception as e:
            print("Error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()