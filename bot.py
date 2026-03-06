import requests
import time
import os
from telegram import Bot

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

URL = "https://www.gov.il/he/departments/dynamicCollectors/notice-to-mariners/api/getPage?skip=0&take=5"

bot = Bot(token=TOKEN)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.gov.il/en/departments/dynamicCollectors/notice-to-mariners",
}

session = requests.Session()
session.headers.update(HEADERS)

sent_ids = {}

CHECK_INTERVAL = 60  # 1 минута


def fetch_notices():
    try:
        response = session.get(URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
    except Exception as e:
        print("ERROR fetching notices:", e)
        return []


def format_notice(notice, outdated=False):
    title = notice.get("title", "No title")
    number = notice.get("number", "")
    date = notice.get("publishDate", "")

    mark = "❌ " if outdated else ""

    text = f"{mark}NAVTEX Notice\n\n"
    text += f"Number: {number}\n"
    text += f"Title: {title}\n"
    text += f"Date: {date}"

    return text


def check_updates():
    global sent_ids

    notices = fetch_notices()

    if not notices:
        return

    current_ids = set()

    for notice in notices:
        nid = notice.get("id")
        current_ids.add(nid)

        if nid not in sent_ids:
            text = format_notice(notice)
            bot.send_message(chat_id=CHAT_ID, text=text)
            sent_ids[nid] = notice

    # помечаем устаревшие
    for nid in list(sent_ids.keys()):
        if nid not in current_ids:
            old_notice = sent_ids[nid]
            text = format_notice(old_notice, outdated=True)
            bot.send_message(chat_id=CHAT_ID, text=text)
            del sent_ids[nid]


print("NAVTEX BOT STARTED")

while True:
    check_updates()
    time.sleep(CHECK_INTERVAL)