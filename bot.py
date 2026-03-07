import os
import re
import json
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

CHECK_INTERVAL = 180

# =====================
# AREA
# =====================

TOP_LAT = 37.98
BOTTOM_LAT = 31.57
LEFT_LON = 23.73
RIGHT_LON = 35.55

STATIONS = ["P","H","L","S","I"]

KEYWORDS = [
"DRIFTING",
"MISSING",
"EXERCISE",
"ROCKET",
"MISSILE",
"BUOY",
"DANGER",
"CABLE",
"PIPELINE"
]

# =====================
# CACHE
# =====================

CACHE_FILE = "cache.json"

def load_cache():

    if not os.path.exists(CACHE_FILE):
        return {"gov":[], "navtex":[]}

    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):

    with open(CACHE_FILE,"w") as f:
        json.dump(data,f)

cache = load_cache()

# =====================
# GOV
# =====================

GOV_API="https://www.gov.il/he/departments/dynamicCollectors/notice-to-mariners/api/getPage?skip=0&take=5"

def check_gov():

    try:
        r=requests.get(GOV_API,timeout=20)
        data=r.json()
    except:
        return

    for item in data.get("results",[]):

        nid=item["id"]

        if nid in cache["gov"]:
            continue

        number=item.get("number")
        title=item.get("title")
        valid_from=item.get("publishDate")
        valid_until=item.get("expireDate")

        link_number=number.replace(" / ","_")

        link=f"https://www.gov.il/en/pages/mariners_{link_number}"

        text=f"""
NAVTEX Notice

Number: {number}
Subject: {title}

Valid From: {valid_from}
Valid Until: {valid_until}

Open notice:
{link}
"""

        bot.send_message(CHAT_ID,text)

        cache["gov"].append(nid)

        save_cache(cache)

# =====================
# COORD PARSER
# =====================

coord_regex = re.compile(
r"(\d{2})[- ]?(\d{2}\.?\d*)\s*([NS])[\s,]+(\d{2,3})[- ]?(\d{2}\.?\d*)\s*([EW])"
)

def parse_coord(m):

    lat_deg=int(m.group(1))
    lat_min=float(m.group(2))
    lat_dir=m.group(3)

    lon_deg=int(m.group(4))
    lon_min=float(m.group(5))
    lon_dir=m.group(6)

    lat=lat_deg+lat_min/60
    lon=lon_deg+lon_min/60

    if lat_dir=="S":
        lat=-lat

    if lon_dir=="W":
        lon=-lon

    return lat,lon

def in_area(lat,lon):

    return (
        BOTTOM_LAT<=lat<=TOP_LAT and
        LEFT_LON<=lon<=RIGHT_LON
    )

# =====================
# NAVTEX
# =====================

NAVTEX_URL="https://navtex.net/navtex-archive.html"

def important(msg):

    for k in KEYWORDS:
        if k in msg.upper():
            return True

    return False

def check_navtex():

    try:
        r=requests.get(NAVTEX_URL,timeout=20)
    except:
        return

    soup=BeautifulSoup(r.text,"html.parser")

    text=soup.get_text()

    messages=text.split("ZCZC")

    for msg in messages:

        msg=msg.strip()

        if len(msg)<20:
            continue

        station=msg[0]

        if station not in STATIONS:
            continue

        mid=str(hash(msg))

        if mid in cache["navtex"]:
            continue

        coord=coord_regex.search(msg)

        if not coord:
            continue

        lat,lon=parse_coord(coord)

        if not in_area(lat,lon):
            continue

        warn="⚠ NAVTEX WARNING\n\n" if important(msg) else "NAVTEX\n\n"

        map_link=f"https://maps.google.com/?q={lat},{lon}"

        text_msg=f"""{warn}{msg[:900]}

📍 Position
{map_link}
"""

        bot.send_message(CHAT_ID,text_msg)

        bot.send_location(CHAT_ID,lat,lon)

        cache["navtex"].append(mid)

        save_cache(cache)

# =====================
# COMMANDS
# =====================

def start(update:Update,context:CallbackContext):

    keyboard=[
        [InlineKeyboardButton("TEST",callback_data="test")],
        [InlineKeyboardButton("LAST",callback_data="last")]
    ]

    update.message.reply_text(
        "NAVTEX MONITOR READY",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def test(update:Update,context:CallbackContext):

    update.message.reply_text("✅ Bot running")

def last(update:Update,context:CallbackContext):

    update.message.reply_text("Searching last NAVTEX...")

    try:
        r=requests.get(NAVTEX_URL,timeout=20)
    except:
        return

    soup=BeautifulSoup(r.text,"html.parser")

    text=soup.get_text()

    messages=text.split("ZCZC")

    found=0

    for msg in messages:

        coord=coord_regex.search(msg)

        if not coord:
            continue

        lat,lon=parse_coord(coord)

        if not in_area(lat,lon):
            continue

        update.message.reply_text(msg[:900])

        bot.send_location(update.effective_chat.id,lat,lon)

        found+=1

        if found>=5:
            break

# =====================
# MAIN
# =====================

def main():

    updater=Updater(TOKEN)

    dp=updater.dispatcher

    dp.add_handler(CommandHandler("start",start))
    dp.add_handler(CommandHandler("test",test))
    dp.add_handler(CommandHandler("last",last))

    updater.start_polling()

    print("NAVTEX BOT STARTED")

    while True:

        try:

            check_gov()
            check_navtex()

        except Exception as e:

            print("ERROR:",e)

        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()