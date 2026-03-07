import os
import json
import time
import feedparser
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
CHECK_INTERVAL = 180  # 3 минуты

# =====================
# CACHE
# =====================
CACHE_FILE = "cache.json"

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE,"w") as f:
        json.dump(data,f)

cache = load_cache()

# =====================
# GOV RSS
# =====================
RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"

def format_rss_item(entry):
    title = entry.get("title")
    link = entry.get("link")
    published = entry.get("published", "")
    return f"""
NAVTEX Notice

Title: {title}
Published: {published}

Open notice:
{link}
"""

def check_gov():
    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        print("GOV RSS ERROR:", e)
        return
    for entry in feed.entries[:5]:
        nid = entry.get("id", entry.get("link"))
        if nid in cache["gov"]:
            continue
        bot.send_message(CHAT_ID, format_rss_item(entry))
        cache["gov"].append(nid)
        save_cache(cache)

# =====================
# COMMANDS
# =====================
def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("TEST", callback_data="test")],
        [InlineKeyboardButton("LAST GOV", callback_data="lastgov")],
    ]
    update.message.reply_text("GOV.il NAVTEX MONITOR READY", reply_markup=InlineKeyboardMarkup(keyboard))

def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

def lastgov(update: Update, context: CallbackContext):
    update.message.reply_text("Loading last GOV notices...")
    try:
        feed = feedparser.parse(RSS_URL)
    except:
        update.message.reply_text("Error loading GOV notices")
        return
    for entry in feed.entries[:5]:
        update.message.reply_text(format_rss_item(entry))

# =====================
# MAIN
# =====================
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("lastgov", lastgov))

    updater.start_polling()
    print("GOV.il NAVTEX BOT STARTED")

    while True:
        try:
            check_gov()
        except Exception as e:
            print("MAIN LOOP ERROR:", e)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()