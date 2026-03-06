import requests
from bs4 import BeautifulSoup
import telegram
import os
import re
import time
from datetime import datetime

# =============================
# Telegram настройки
# =============================
TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
bot = telegram.Bot(token=TOKEN)

# =============================
# Файл для хранения последнего ID
# =============================
last_id_file = "last_id.txt"
try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

offset = None

# =============================
# Заголовки, чтобы сайт не блокировал
# =============================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# =============================
# Функции
# =============================

def get_links():
    """Получаем последние 5 ссылок на уведомления"""
    url = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
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

    # уникальные id
    unique = []
    ids = set()
    for i in links:
        if i[0] not in ids:
            unique.append(i)
            ids.add(i[0])

    return unique[:5]  # последние 5

def dms_to_dd(dms):
    """Преобразование координат из DMS в десятичные"""
    deg, rest = dms.split("°")
    mins = float(rest[:-1])
    sign = 1 if rest[-1] in "NE" else -1
    return sign * (float(deg) + mins / 60)

def parse_notice(link):
    """Парсим уведомление, получаем текст, координаты и дату Valid Until"""
    try:
        page = requests.get(link, headers=HEADERS, timeout=20)
        page.raise_for_status()
    except Exception as e:
        print("ERROR fetching notice:", e)
        return None

    soup = BeautifulSoup(page.text, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    # Координаты
    coords = re.findall(r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])', text)
    maps_link = ""
    if coords:
        lat, lon = coords[0]
        lat_dd = dms_to_dd(lat)
        lon_dd = dms_to_dd(lon)
        maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    # Valid Until
    valid_until_match = re.search(r'Valid Until[:\s]+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})', text)
    expired = False
    valid_text = ""
    if valid_until_match:
        valid_str = valid_until_match.group(1)
        try:
            valid_date = datetime.strptime(valid_str, "%d/%m/%Y")
            if valid_date < datetime.now():
                expired = True
                valid_text = f"❌ Expired: {valid_str}"
            else:
                valid_text = f"Valid Until: {valid_str}"
        except:
            valid_text = f"Valid Until: {valid_str}"

    # Сообщение
    message = text[:3000]
    if maps_link:
        message += f"\n\n📍 {lat} {lon}\n{maps_link}"
    if valid_text:
        message += f"\n\n{valid_text}"

    return message

def send_last():
    """Отправляем последние 5 уведомлений в любом случае"""
    links = get_links()
    for msg_id, link in reversed(links):
        message = parse_notice(link)
        if message:
            bot.send_message(chat_id=CHAT_ID, text=message)
            time.sleep(1)

def check_navtex():
    """Проверяем новые уведомления и отправляем"""
    global last_id
    links = get_links()
    if not links:
        return

    new_messages = []
    for msg_id, link in links:
        if msg_id == last_id:
            break
        new_messages.append((msg_id, link))

    for msg_id, link in reversed(new_messages):
        message = parse_notice(link)
        if message:
            bot.send_message(chat_id=CHAT_ID, text=message)
            with open(last_id_file, "w") as f:
                f.write(msg_id)
            last_id = msg_id
            time.sleep(1)

def check_commands():
    """Обрабатываем команды Telegram"""
    global offset
    updates = bot.get_updates(offset=offset, timeout=10)
    for update in updates:
        offset = update.update_id + 1
        if not update.message:
            continue
        text = update.message.text
        if text == "/status":
            bot.send_message(chat_id=update.message.chat_id, text="✅ NAVTEX bot is running")
        elif text == "/last":
            bot.send_message(chat_id=update.message.chat_id, text="📡 Sending last 5 NAVTEX")
            send_last()

# =============================
# Запуск
# =============================
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