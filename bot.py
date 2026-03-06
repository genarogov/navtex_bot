import requests
from bs4 import BeautifulSoup
import telegram
import os
import re
import time
from datetime import datetime

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telegram.Bot(token=TOKEN)
last_id_file = "last_id.txt"

# читаем последний отправленный ID
try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

offset = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

URL = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners"

# =============================
# Получаем последние 5 уведомлений
# =============================
def get_links():
    try:
        r = requests.get(URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("ERROR fetching main page:", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/notice-to-mariners/" in href:
            full_url = "https://www.gov.il" + href
            msg_id = href.split("/")[-1]
            links.append((msg_id, full_url))

    # уникальные ссылки
    unique = []
    ids = set()
    for i in links:
        if i[0] not in ids:
            unique.append(i)
            ids.add(i[0])

    return unique[:5]  # последние 5

# =============================
# Координаты
# =============================
def dms_to_dd(dms):
    deg, rest = dms.split("°")
    mins = float(rest[:-1])
    sign = 1 if rest[-1] in "NE" else -1
    return sign * (float(deg) + mins/60)

# =============================
# Парсим уведомление
# =============================
def parse_notice(link):
    try:
        page = requests.get(link, headers=HEADERS, timeout=20)
        page.raise_for_status()
    except Exception as e:
        return f"❌ ERROR fetching notice: {e}"

    soup = BeautifulSoup(page.text, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    # ищем координаты
    coords = re.findall(
        r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])', text
    )
    maps_link = ""
    if coords:
        lat, lon = coords[0]
        lat_dd = dms_to_dd(lat)
        lon_dd = dms_to_dd(lon)
        maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    # ищем Valid Until
    valid_until = ""
    match = re.search(r"Valid Until[:\s]+([A-Za-z0-9 ,]+)", text)
    if match:
        valid_until = match.group(1)
        try:
            valid_date = datetime.strptime(valid_until, "%d %B %Y")
            if valid_date < datetime.now():
                valid_until += " ❌"  # помечаем устаревшее
        except:
            pass

    message = text[:3500]
    if maps_link:
        message += f"\n\n📍 {lat} {lon}\n{maps_link}"
    if valid_until:
        message += f"\n⏰ Valid Until: {valid_until}"

    return message

# =============================
# Отправка последних 5 сообщений
# =============================
def send_last():
    links = get_links()
    for msg_id, link in reversed(links):
        message = parse_notice(link)
        bot.send_message(chat_id=CHAT_ID, text=message)
        time.sleep(1)

# =============================
# Проверка новых сообщений
# =============================
def check_navtex():
    global last_id
    links = get_links()
    new_messages = []

    for msg_id, link in links:
        if msg_id == last_id:
            break
        new_messages.append((msg_id, link))

    for msg_id, link in reversed(new_messages):
        message = parse_notice(link)
        bot.send_message(chat_id=CHAT_ID, text=message)
        with open(last_id_file, "w") as f:
            f.write(msg_id)
        last_id = msg_id
        time.sleep(1)

# =============================
# Проверка команд
# =============================
def check_commands():
    global offset
    updates = bot.get_updates(offset=offset, timeout=5)
    for update in updates:
        offset = update.update_id + 1
        if not update.message:
            continue
        text = update.message.text
        if text == "/status":
            bot.send_message(chat_id=update.message.chat_id, text="✅ NAVTEX bot is running")
        elif text == "/last":
            bot.send_message(chat_id=update.message.chat_id, text="📡 Sending last 5 NAVTEX notices")
            send_last()
        elif text == "/test":
            bot.send_message(chat_id=update.message.chat_id, text="🧪 Test OK")

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

        time.sleep(15)  # проверяем каждые 15 секунд

    except Exception as e:
        print("ERROR:", e)
        time.sleep(10)