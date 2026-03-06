import requests
from bs4 import BeautifulSoup
import telegram
import os
import re

# =============================
# Telegram настройки
# =============================
TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telegram.Bot(token=TOKEN)

# =============================
# Файл последнего ID
# =============================
last_id_file = "last_id.txt"

first_run = not os.path.exists(last_id_file)

try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

# =============================
# Headers для gov.il
# =============================
headers = {
    "User-Agent": "Mozilla/5.0"
}

# =============================
# Страница Notice to Mariners
# =============================
url = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners"

r = requests.get(url, headers=headers)
soup = BeautifulSoup(r.text, "html.parser")

# =============================
# Поиск ссылок
# =============================
links = []

for a in soup.find_all("a", href=True):

    href = a["href"]

    if "/en/departments/publications/" in href:

        msg_id = href.split("/")[-1]
        full_url = "https://www.gov.il" + href

        links.append((msg_id, full_url))

# =============================
# Функция извлечения координат
# =============================
def extract_coords(text):

    coords = re.findall(
        r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])',
        text
    )

    if not coords:
        return None

    lat, lon = coords[0]

    def dms_to_dd(dms):

        deg, rest = dms.split("°")
        mins = float(rest[:-1])
        direction = rest[-1]

        dd = float(deg) + mins / 60

        if direction in ["S", "W"]:
            dd *= -1

        return dd

    lat_dd = dms_to_dd(lat)
    lon_dd = dms_to_dd(lon)

    maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    return f"\n\n📍 {lat} {lon}\n{maps_link}"


# =============================
# Функция отправки notice
# =============================
def send_notice(link):

    page = requests.get(link, headers=headers)
    soup2 = BeautifulSoup(page.text, "html.parser")

    text = soup2.get_text("\n", strip=True)

    message = text[:3500]

    coords = extract_coords(text)

    if coords:
        message += coords

    bot.send_message(
        chat_id=CHAT_ID,
        text=message
    )


# =============================
# Первый запуск → отправляем 5 последних
# =============================
if first_run:

    for msg_id, link in links[:5]:
        send_notice(link)

    with open(last_id_file, "w") as f:
        f.write(links[0][0])

    print("First run: sent last 5 notices")
    exit()


# =============================
# Поиск новых сообщений
# =============================
new_links = []

for msg_id, link in links:

    if msg_id == last_id:
        break

    new_links.append((msg_id, link))


# =============================
# Отправка новых
# =============================
for msg_id, link in reversed(new_links):

    send_notice(link)

    with open(last_id_file, "w") as f:
        f.write(msg_id)

    print("Sent:", msg_id)


# =============================
# Telegram команды
# =============================
updates = bot.get_updates(timeout=30)

for update in updates:

    if not update.message:
        continue

    text = update.message.text
    chat_id = update.message.chat.id

    if text == "/last5":

        for msg_id, link in links[:5]:
            send_notice(link)

    if text == "/latest":

        msg_id, link = links[0]
        send_notice(link)