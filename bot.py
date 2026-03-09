import os
import time
import json
import re
import hashlib
import requests
import feedparser
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)

CHECK_INTERVAL = 300  # 5 минут

RSS_URL = "https://www.gov.il/he/Departments/Rss/NoticeToMariners"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
WMO_URL = "https://wwmiws.wmo.int/index.php/metareas/affiche/3"
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"

CACHE_FILE = "cache.json"
ZONES = ["TAURUS", "DELTA", "CRUSADE"]

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov": [], "metarea": "", "navtex_sent": [], "navtex_msgs": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

# ---------------- UTILS ----------------
def hash_msg(text):
    return hashlib.sha1(text.encode()).hexdigest()

def normalize(text):
    text = text.upper()
    text = re.sub(r"\s+", " ", text)
    return text[:200]

# ---------------- GOV ----------------
def format_gov(entry):
    title = entry.get("title","")
    link = entry.get("link","")
    date = entry.get("published","")
    return f"⚓ GOV.il Notice\n\n{title}\n{date}\n{link}"

def check_gov():
    feed = feedparser.parse(RSS_URL)
    for entry in feed.entries[:5]:
        nid = entry.get("link")
        if nid in cache["gov"]:
            continue
        bot.send_message(CHAT_ID, format_gov(entry))
        cache["gov"].append(nid)
        save_cache(cache)

def lastgov(update: Update, context: CallbackContext):
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        update.message.reply_text("No GOV notices found")
        return
    for entry in feed.entries[:5]:
        update.message.reply_text(format_gov(entry))

# ---------------- METAREA ----------------
def get_metarea():
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
def fetch_wmo():
    r = requests.get(WMO_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.find_all("a")
    txt=None
    for l in links:
        href=l.get("href","")
        if ".txt" in href:
            txt=href
    if not txt:
        return []
    if not txt.startswith("http"):
        txt = "https://wwmiws.wmo.int"+txt
    text = requests.get(txt).text
    msgs = text.split("NAVAREA")
    res=[]
    for m in msgs:
        if len(m.strip())<40:
            continue
        res.append("NAVAREA "+m.strip())
    return res

def fetch_sealagom():
    r = requests.get(SEALAGOM_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    ps = soup.find_all("p")
    res=[]
    for p in ps:
        t=p.get_text().strip()
        if "NAVAREA" in t:
            res.append(t)
    return res

def collect_navtex():
    msgs = []
    msgs += fetch_wmo()
    msgs += fetch_sealagom()
    uniq = {}
    for m in msgs:
        key = normalize(m)
        if key not in uniq:
            uniq[key] = m
    return list(uniq.values())

def extract_coords(text):
    coords = re.findall(r"\d{2}-\d{2}[NS]\s*\d{3}-\d{2}[EW]", text)
    out=[]
    for c in coords:
        lat, lon = c.split()
        latd, latm = int(lat[:2]), int(lat[3:5])
        if "S" in lat: latd*=-1
        lond, lonm = int(lon[:3]), int(lon[4:6])
        if "W" in lon: lond*=-1
        latf = latd + latm/60
        lonf = lond + lonm/60
        out.append((latf, lonf))
    return out

def check_navtex():
    msgs = collect_navtex()
    for m in msgs:
        h = hash_msg(m)
        if h in cache["navtex_sent"]:
            continue
        bot.send_message(CHAT_ID, "⚠️ NAVAREA WARNING\n\n" + m[:3500])
        coords = extract_coords(m)
        for lat, lon in coords:
            bot.send_location(CHAT_ID, lat, lon)
        cache["navtex_sent"].append(h)
        cache["navtex_msgs"].append(m)
        save_cache(cache)

def last(update: Update, context: CallbackContext):
    msgs = cache["navtex_msgs"][-5:]
    if not msgs:
        update.message.reply_text("No NAVTEX messages yet")
        return
    for m in msgs:
        update.message.reply_text(m[:3500])
        coords = extract_coords(m)
        for lat, lon in coords:
            update.message.reply_location(lat, lon)

# ---------------- COMMANDS ----------------
def test(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot running")

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
            check_navtex()
        except Exception as e:
            print(e)
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()