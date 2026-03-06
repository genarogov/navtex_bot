import os
import time
import re
import requests
import telegram
from datetime import datetime

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telegram.Bot(token=TOKEN)

last_id_file = "last_id.txt"

try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

API_URL = "https://www.gov.il/he/departments/dynamicCollectors/notice-to-mariners/api/getPage?skip=0&take=5"

def get_links():
    try:
        r = requests.get(API_URL, timeout=20)
        r.raise_for_status()
        data = r.json().get("items", [])
    except Exception as e:
        print("ERROR fetching notices:", e)
        return []

    links = []
    for item in data:
        msg_id = str(item.get("id"))
        title = item.get("title", "No title")
        url = "https://www.gov.il" + item.get("url", "")
        valid_until = item.get("validUntil", "")
        links.append((msg_id, url, title, valid_until))
    return links

def parse_notice(msg_id, url, title, valid_until):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        text = r.text
    except:
        text = title

    coords = re.findall(r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])', text)
    maps_link = ""
    if coords:
        def dms_to_dd(dms):
            deg, rest = dms.split("°")
            mins = float(rest[:-1])
            sign = 1 if rest[-1] in "NE" else -1
            return sign * (float(deg) + mins/60)
        lat, lon = coords[0]
        lat_dd = dms_to_dd(lat)
        lon_dd = dms_to_dd(lon)
        maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    message = f"📝 {title}\nValid Until: {valid_until}\n{url}\n\n{text[:3000]}"
    if maps_link:
        message += f"\n\n📍 {lat} {lon}\n{maps_link}"
    return message

def is_valid(valid_until):
    try:
        dt = datetime.strptime(valid_until, "%Y-%m-%dT%H:%M:%S")
        return dt >= datetime.utcnow()
    except:
        return True  # если нет даты — считаем валидным

# -------------------------------
# Для команды /last отправляем просто последние 5 без проверки даты
# -------------------------------
def send_last():
    links = get_links()
    for msg_id, url, title, valid_until in reversed(links):
        message = parse_notice(msg_id, url, title, valid_until)
        bot.send_message(chat_id=CHAT_ID, text=message)
        time.sleep(1)

def check_navtex():
    global last_id
    links = get_links()
    new_messages = []
    for msg_id, url, title, valid_until in links:
        if msg_id == last_id:
            break
        if not is_valid(valid_until):
            continue
        new_messages.append((msg_id, url, title, valid_until))

    for msg_id, url, title, valid_until in reversed(new_messages):
        message = parse_notice(msg_id, url, title, valid_until)
        bot.send_message(chat_id=CHAT_ID, text=message)
        with open(last_id_file, "w") as f:
            f.write(msg_id)
        last_id = msg_id
        time.sleep(1)

offset = None
def check_commands():
    global offset
    updates = bot.get_updates(offset=offset, timeout=10)
    for update in updates:
        offset = update.update_id + 1
        if not update.message:
            continue
        text = update.message.text
        if text == "/status":
            bot.send_message(chat_id=update.message.chat.id, text="✅ NAVTEX bot is running")
        elif text == "/last":
            bot.send_message(chat_id=update.message.chat.id, text="📡 Sending last 5 NAVTEX (even if expired)")
            send_last()

print("NAVTEX BOT STARTED")
first_run = True

while True:
    try:
        if first_run:
            send_last()
            links = get_links()
            if links:
                with open(last_id_file, "w") as f:
                    f.write(links[0][0])
                last_id = links[0][0]
            first_run = False

        check_navtex()
        check_commands()
        time.sleep(120)
    except Exception as e:
        print("ERROR:", e)
        time.sleep(60)