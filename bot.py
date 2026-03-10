import os
import time
import json
import re
import requests
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from bs4 import BeautifulSoup

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

CHECK_INTERVAL = 900

CACHE_FILE = "cache.json"

METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"
SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"

GOV_API = "https://www.gov.il/en/api/DynamicCollector"

ZONES = ["TAURUS","DELTA","CRUSADE"]

# ---------------- CACHE ----------------

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"gov":[], "navtex":[], "metarea":""}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(c):
    with open(CACHE_FILE,"w") as f:
        json.dump(c,f)

cache = load_cache()

# ---------------- GOV ----------------

def get_notice_text(url):

    try:
        r = requests.get(url,timeout=20)
        soup = BeautifulSoup(r.text,"html.parser")

        content = soup.find("div",{"id":"content"})

        if not content:
            content = soup

        text = content.get_text("\n")

        text = re.sub(r"\n{2,}","\n\n",text)

        return text[:3500]

    except:
        return ""

def get_gov_notices():

    try:

        r = requests.get(GOV_API,timeout=20)
        data = r.json()

        notices=[]

        for item in data["Results"][:5]:

            d = item["Data"]

            number = d.get("number","")
            subject = d.get("sunject","")

            valid = d.get("valid","")
            until = d.get("date","")

            link = "https://www.gov.il" + d["link_to_notice"]["URL"]

            notices.append({
                "number":number,
                "subject":subject,
                "valid":valid,
                "until":until,
                "link":link
            })

        return notices

    except Exception as e:

        print("gov error",e)

        return []

def check_gov():

    notices = get_gov_notices()

    for n in notices:

        if n["number"] in cache["gov"]:
            continue

        text = get_notice_text(n["link"])

        msg = f"""⚓ <a href="{n['link']}">{n['number']}</a>

Subject:
{n['subject']}

Valid:
{n['valid']} - {n['until']}

{text}
"""

        bot.send_message(CHAT_ID,msg,parse_mode="HTML")

        cache["gov"].append(n["number"])

        save_cache(cache)

def lastgov(update:Update,context:CallbackContext):

    notices = get_gov_notices()

    if not notices:
        update.message.reply_text("No gov notices")
        return

    for n in notices:

        text = get_notice_text(n["link"])

        msg = f"""⚓ <a href="{n['link']}">{n['number']}</a>

Subject:
{n['subject']}

Valid:
{n['valid']} - {n['until']}

{text}
"""

        update.message.reply_text(msg,parse_mode="HTML")

# ---------------- METAREA ----------------

def get_metarea():

    try:

        r = requests.get(METAREA_URL,timeout=20)

        soup = BeautifulSoup(r.text,"html.parser")

        text = soup.get_text()

        issued = re.search(r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC",text)

        issued = issued.group(0) if issued else "N/A"

        start = text.find("TAURUS")

        end = text.find("KASTELLORIZO SEA")

        if start==-1 or end==-1:
            return "Forecast not found"

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

    except:

        return "Error loading METAREA"

def check_metarea():

    text = get_metarea()

    if text == cache["metarea"]:
        return

    cache["metarea"]=text

    save_cache(cache)

    bot.send_message(CHAT_ID,"🌊 METAREA III FORECAST\n\n"+text)

def metarea(update:Update,context:CallbackContext):

    update.message.reply_text(get_metarea())

# ---------------- NAVTEX ----------------

def fetch_navtex():

    try:

        r = requests.get(SEALAGOM_URL,timeout=20)

        soup = BeautifulSoup(r.text,"html.parser")

        text = soup.get_text("\n")

        raw = re.split(r"\n(?=\d{4}/\d{2})",text)

        messages=[]

        for m in raw:

            date_match = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC",m)

            if not date_match:
                continue

            start=date_match.start()

            end_match = re.search(r"\bDetails\b",m)

            if not end_match:
                continue

            end=end_match.start()

            clean=m[start:end].strip()

            if len(clean)>30:
                messages.append(clean)

        return messages[:5]

    except Exception as e:

        print(e)

        return []

def send_navtex():

    messages = fetch_navtex()

    new = [m for m in messages if m not in cache["navtex"]]

    if not new:
        return

    for m in new:

        bot.send_message(CHAT_ID,m)

        cache["navtex"].append(m)

    save_cache(cache)

def last(update:Update,context:CallbackContext):

    msgs = fetch_navtex()

    if not msgs:
        update.message.reply_text("No NAVTEX messages")
        return

    for m in msgs:
        update.message.reply_text(m)

# ---------------- TEST ----------------

def test(update:Update,context:CallbackContext):

    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------

def main():

    updater = Updater(TOKEN)

    dp = updater.dispatcher

    dp.add_handler(CommandHandler("test",test))
    dp.add_handler(CommandHandler("metarea",metarea))
    dp.add_handler(CommandHandler("lastgov",lastgov))
    dp.add_handler(CommandHandler("last",last))

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

if __name__ == "__main__":
    main()