import os
import re
import time
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from telegram.ext import Updater, CommandHandler

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
CACHE_FILE = "cache.json"
CHECK_INTERVAL = 1800

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"

ZONES = ["TAURUS","DELTA","CRUSADE"]

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {
            "sealagom": [],
            "gov": {"last_number": "019", "year": "2026", "last_format": "_"},
            "rss": []
        }
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- COORDINATES ----------------
def convert_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes)/60
    if direction in ["S","W"]:
        value = -value
    return value

def add_coordinate_links(text):
    coord_pattern = re.compile(r'(\d{1,3})[°\-\s]+(\d{1,2}\.\d+)\s*([NSEW])', re.IGNORECASE)
    coords = list(coord_pattern.finditer(text))
    replacements = []
    i = 0
    while i < len(coords)-1:
        lat = coords[i]
        lon = coords[i+1]
        if lat.group(3).upper() in ["N","S"] and lon.group(3).upper() in ["E","W"]:
            lat_val = convert_to_decimal(lat.group(1), lat.group(2), lat.group(3).upper())
            lon_val = convert_to_decimal(lon.group(1), lon.group(2), lon.group(3).upper())
            start = lat.start()
            end = lon.end()
            original = text[start:end]
            link = f"https://maps.google.com/?q={lat_val},{lon_val}"
            html = f'<a href="{link}">{original}</a>'
            replacements.append((start,end,html))
            i += 2
        else:
            i += 1
    for start,end,html in reversed(replacements):
        text = text[:start]+html+text[end:]
    return text

# ---------------- SEALAGOM ----------------
def fetch_sealagom_full():
    """
    Возвращает список сообщений с основной страницы SEALAGOM.
    Каждый элемент — кортеж: (номер, текст)
    """
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")

        raw_msgs = re.split(r'\n(?=NAVAREA III - \d{4}/\d{2})', text)
        messages = []

        for m in raw_msgs:
            header_match = re.match(r'NAVAREA III - (\d{4}/\d{2})', m)
            if not header_match:
                continue
            number = header_match.group(1)
            clean = m.strip()
            if len(clean) > 30:
                messages.append((number, clean))

        messages.sort(key=lambda x: x[0])
        return messages

    except Exception as e:
        print("Sealagom full fetch error:", e)
        return []

def send_new_sealagom(updater):
    """
    Отправляет в чат только новые сообщения, которые ещё не были отправлены
    """
    fetched = fetch_sealagom_full()
    new_messages = []

    for number, msg_text in fetched:
        if number not in cache["sealagom"]:
            msg_with_links = add_coordinate_links(msg_text[:3500])
            updater.bot.send_message(
                CHAT_ID,
                msg_with_links,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            cache["sealagom"].append(number)
            new_messages.append(number)

    if new_messages:
        save_cache(cache)

def test(update, context):
    """
    Показывает 5 последних сообщений (без проверки кэша)
    """
    messages = fetch_sealagom_full()
    if not messages:
        update.message.reply_text("No Sealagom messages")
        return

    for number, msg_text in messages[-5:]:
        msg = add_coordinate_links(msg_text[:3500])
        update.message.reply_text(
            msg,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

# ---------------- GOV ----------------
def page_exists(url):
    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200
    except:
        return False

def find_latest_gov(last_number, year, fmt):
    low = int(last_number)
    high = 300
    latest = low
    while low <= high:
        mid = (low + high) // 2
        num = f"{mid:03d}"
        url = f"https://www.gov.il/en/pages/mariners{fmt}{num}{fmt}{year}"
        if page_exists(url):
            latest = mid
            low = mid + 1
        else:
            high = mid - 1
    return latest

def testgov(update, context):
    last_number = cache["gov"]["last_number"]
    year = cache["gov"]["year"]
    fmt = cache["gov"]["last_format"]
    latest = find_latest_gov(last_number, year, fmt)
    url = f"https://www.gov.il/en/pages/mariners{fmt}{latest:03d}{fmt}{year}"
    update.message.reply_text(f"Last message from GOV.il: {url}")

# ---------------- METAREA ----------------
def get_metarea():
    r = requests.get(METAREA_URL,timeout=20)
    soup = BeautifulSoup(r.text,"html.parser")
    text = soup.get_text()
    issued = re.search(r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC",text)
    issued = issued.group(0) if issued else "N/A"
    start = text.find("TAURUS")
    end = text.find("KASTELLORIZO SEA")
    forecast = text[start:end]
    blocks=[]
    for i,zone in enumerate(ZONES):
        s = forecast.find(zone)
        if s==-1:
            continue
        nxt=[forecast.find(z,s+1) for z in ZONES[i+1:]]
        nxt=[n for n in nxt if n!=-1]
        e=min(nxt) if nxt else len(forecast)
        txt=forecast[s:e].strip()
        if txt.startswith(zone):
            txt=txt[len(zone):].lstrip()
        blocks.append(f"📍 {zone}\n{txt}")
    msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)
    return msg[:4000]

def metarea(update,context):
    update.message.reply_text(get_metarea())

# ---------------- RSS ----------------
def send_rss(updater):
    feed = feedparser.parse(RSS_URL)
    new_entries = [e for e in feed.entries if e.link not in cache["rss"]]
    for e in new_entries:
        msg = f"RSS GOV IL: {e.title}\n{e.link}"
        updater.bot.send_message(CHAT_ID, msg)
        cache["rss"].append(e.link)
    if new_entries:
        save_cache(cache)

# ---------------- TESTBOT ----------------
def testbot(update, context):
    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("testbot", testbot))
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("testgov", testgov))
    dp.add_handler(CommandHandler("metarea", metarea))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            send_new_sealagom(updater)
            send_rss(updater)
        except Exception as e:
            print("Auto check error:", e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()