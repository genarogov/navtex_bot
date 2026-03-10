import os
import time
import json
import re
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)

CHECK_INTERVAL = 300

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"

CACHE_FILE = "cache.json"
ZONES = ["TAURUS", "DELTA", "CRUSADE"]

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": "", "navtex": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- METAREA ----------------
def get_metarea():
    try:
        r = requests.get(METAREA_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text()

        issued = re.search(r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC", text)
        issued = issued.group(0) if issued else "N/A"

        start = text.find("TAURUS")
        end = text.find("KASTELLORIZO SEA")

        if start == -1 or end == -1:
            return "Forecast not found"

        forecast = text[start:end]

        blocks=[]

        for i, zone in enumerate(ZONES):
            s = forecast.find(zone)
            if s == -1:
                continue
            nxt = [forecast.find(z, s+1) for z in ZONES[i+1:]]
            nxt = [n for n in nxt if n!=-1]
            e = min(nxt) if nxt else len(forecast)
            txt = forecast[s:e].strip()
            if txt.startswith(zone):
                txt = txt[len(zone):].lstrip()
            blocks.append(f"📍 {zone}\n{txt}")

        msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)
        return msg[:4000]
    except:
        return "Error loading METAREA"

def check_metarea():
    text = get_metarea()
    if text == cache["metarea"]:
        return
    cache["metarea"] = text
    save_cache(cache)
    bot.send_message(CHAT_ID, "🌊 METAREA III FORECAST\n\n" + text)

def metarea(update: Update, context: CallbackContext):
    update.message.reply_text(get_metarea())

# ---------------- NAVTEX ----------------
def fetch_sealagom_navtex():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r"\n(?=\d{4}/\d{2})", text)
        messages = []
        for m in raw_msgs:
            date_match = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC", m)
            if not date_match:
                continue
            start = date_match.start()
            end_match = re.search(r"\bDetails\b", m)
            if not end_match:
                continue
            end = end_match.start()
            clean = m[start:end].strip()
            if len(clean) > 30:
                messages.append(clean)
        return messages[:5]
    except Exception as e:
        print(e)
        return []

# ---------------- UNIVERSAL COORDINATE PARSER ----------------
def convert_to_decimal(deg, minutes, direction):
    value = float(deg) + float(minutes)/60
    if direction in ["S","W"]:
        value = -value
    return value

def add_coordinate_links(text):
    coord_pattern = re.compile(
        r'(\d{1,3})[°\-\s]+(\d{1,2}\.\d+)\s*([NSEW])',
        re.IGNORECASE
    )
    coords = list(coord_pattern.finditer(text))
    replacements = []
    i = 0
    while i < len(coords) - 1:
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
        text = text[:start] + html + text[end:]
    return text

def send_navtex():
    messages = fetch_sealagom_navtex()
    new_msgs = [m for m in messages if m not in cache["navtex"]]
    if not new_msgs:
        return
    for m in new_msgs:
        msg = add_coordinate_links(m[:3500])
        bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
        cache["navtex"].append(m)
    save_cache(cache)

def last(update: Update, context: CallbackContext):
    msgs = fetch_sealagom_navtex()
    if not msgs:
        update.message.reply_text("No NAVTEX messages")
        return
    for m in msgs:
        msg = add_coordinate_links(m[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

# ---------------- TEST ----------------
def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

# ---------------- GOV via Selenium ----------------
def fetch_gov_notices():
    """Получаем последние 5 notices с gov.il через Selenium"""
    GOV_INDEX_URL = "https://www.gov.il/en/Departments/DynamicCollectors/notice-to-mariners?skip=0"
    options = Options()
    options.headless = True
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    notices = []
    try:
        driver.get(GOV_INDEX_URL)
        time.sleep(5)  # ждём рендер JS
        notice_links = driver.find_elements(By.XPATH, "//a[contains(@href,'/en/pages/mariners-')]")
        seen = set()
        for a in notice_links:
            link = a.get_attribute("href")
            text = a.text.strip()
            import re
            m = re.search(r"(\d{1,4}/\d{4})", text)
            if not m:
                continue
            notice_number = m.group(1)
            if notice_number in seen:
                continue
            seen.add(notice_number)
            notices.append((notice_number, link))
            if len(notices) >= 5:
                break
        # Берём текст для каждого notice
        results = []
        for number, link in notices:
            driver.get(link)
            time.sleep(3)
            content_div = driver.find_elements(By.XPATH, "//div[contains(@class,'govil-page-content')]")
            content_text = ""
            if content_div:
                content_text = content_div[0].text.strip()
            results.append({
                "notice_number": number,
                "link": link,
                "text": content_text
            })
        return results
    finally:
        driver.quit()

def check_gov():
    notices = fetch_gov_notices()
    for n in notices:
        if n["notice_number"] in cache["gov"]:
            continue
        msg = f'<a href="{n["link"]}">⚓ Notice {n["notice_number"]}</a>\n\n{n["text"]}'
        bot.send_message(CHAT_ID, msg, parse_mode="HTML")
        cache["gov"].append(n["notice_number"])
    save_cache(cache)

def lastgov(update: Update, context: CallbackContext):
    notices = fetch_gov_notices()
    if not notices:
        update.message.reply_text("No GOV notices found")
        return
    for n in notices:
        msg = f'<a href="{n["link"]}">⚓ Notice {n["notice_number"]}</a>\n\n{n["text"]}'
        update.message.reply_text(msg, parse_mode="HTML")

# ---------------- MAIN ----------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("metarea", metarea))
    dp.add_handler(CommandHandler("lastgov", lastgov))
    dp.add_handler(CommandHandler("last", last))

    updater.start_polling()
    print("BOT STARTED")

    while True:
        try:
            check_gov()
            check_metarea()
            send_navtex()
        except Exception as e:
            print(e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()