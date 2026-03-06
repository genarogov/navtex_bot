import requests
from bs4 import BeautifulSoup
import telegram
import os
import re
import time

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telegram.Bot(token=TOKEN)

last_id_file = "last_id.txt"

try:
    with open(last_id_file, "r") as f:
        last_id = f.read().strip()
except:
    last_id = ""

offset = None


def get_links():

    url = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners"

    r = requests.get(url, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    links = []

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if "/notice-to-mariners/" in href:

            full_url = "https://www.gov.il" + href
            msg_id = href.split("/")[-1]

            links.append((msg_id, full_url))

    unique = []
    ids = set()

    for i in links:

        if i[0] not in ids:

            unique.append(i)
            ids.add(i[0])

    return unique[:5]


def dms_to_dd(dms):

    deg, rest = dms.split("°")
    mins = float(rest[:-1])
    sign = 1 if rest[-1] in "NE" else -1

    return sign * (float(deg) + mins/60)


def parse_notice(link):

    page = requests.get(link, timeout=20)
    soup = BeautifulSoup(page.text, "html.parser")

    text = soup.get_text(separator="\n", strip=True)

    coords = re.findall(
        r'(\d{1,2}°\d{1,2}\.\d+[NS])\s*(\d{1,3}°\d{1,2}\.\d+[EW])', text
    )

    maps_link = ""

    if coords:

        lat, lon = coords[0]

        lat_dd = dms_to_dd(lat)
        lon_dd = dms_to_dd(lon)

        maps_link = f"https://maps.google.com/?q={lat_dd},{lon_dd}"

    message = text[:3500]

    if maps_link:

        message += f"\n\n📍 {lat} {lon}\n{maps_link}"

    return message


def send_last():

    links = get_links()

    for msg_id, link in reversed(links):

        message = parse_notice(link)

        bot.send_message(chat_id=CHAT_ID, text=message)

        time.sleep(1)


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


def check_commands():

    global offset

    updates = bot.get_updates(offset=offset, timeout=10)

    for update in updates:

        offset = update.update_id + 1

        if not update.message:
            continue

        text = update.message.text

        if text == "/status":

            bot.send_message(
                chat_id=update.message.chat_id,
                text="✅ NAVTEX bot is running",
            )

        if text == "/last":

            bot.send_message(
                chat_id=update.message.chat_id,
                text="📡 Sending last 5 NAVTEX",
            )

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