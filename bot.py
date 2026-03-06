import requests
from bs4 import BeautifulSoup
import telegram
import os
import re

# =============================
# 1️⃣ Telegram настройки
# =============================
TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telegram.Bot(token=TOKEN)

# =============================
# 2️⃣ Файл для хранения последнего ID
# =============================
last_id_file = "last_id.txt"
try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

# =============================
# 3️⃣ Главная страница NAVTEX Израиля
# =============================
url = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners"
r = requests.get(url)
soup = BeautifulSoup(r.text, "html.parser")

# =============================
# 4️⃣ Находим ссылки на новые сообщения
# =============================
links = []
for a in soup.find_all("a"):
    href = a.get("href")
    if href and "notice-to-mariners" in href:
        full_url = "https://www.gov.il" + href
        # используем часть ссылки как ID
        msg_id = href.split("/")[-1]
        if msg_id != last_id:
            links.append((msg_id, full_url))

# =============================
# 5️⃣ Проходим по ссылкам и отправляем новые сообщения
# =============================
for msg_id, link in links[::-1]:  # старые сначала
    page = requests.get(link)
    soup2 = BeautifulSoup(page.text, "html.parser")
    text = soup2.get_text(separator="\n", strip=True)

    # =============================
    # Ищем координаты
    # =============================
    coords = re.findall(r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])', text)
    maps_link = ""
    if coords:
        lat, lon = coords[0]
        def dms_to_dd(dms):
            deg, rest = dms.split("°")
            mins = float(rest[:-1])
            sign = 1 if rest[-1] in "N E" else -1
            return sign * (float(deg) + mins/60)
        lat_dd = dms_to_dd(lat)
        lon_dd = dms_to_dd(lon)
        maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    # =============================
    # Формируем текст для Telegram
    # =============================
    message = text[:3000]
    if maps_link:
        message += f"\n\n📍 {lat} {lon}\n{maps_link}"

    # =============================
    # Отправка в Telegram
    # =============================
    bot.send_message(chat_id=CHAT_ID, text=message)

    # =============================
    # Сохраняем последний ID
    # =============================
    with open(last_id_file, "w") as f:
        f.write(msg_id)