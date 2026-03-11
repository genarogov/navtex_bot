import os
import re
import requests
from bs4 import BeautifulSoup
from telegram.ext import Updater, CommandHandler

TOKEN = os.getenv("BOT_TOKEN")

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
METAREA_URL = "https://wwmiws.wmo.int/index.php/metareas/bulletinset/3/html"

ZONES = ["TAURUS","DELTA","CRUSADE"]

# ---------------- COORDINATES ----------------

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

# ---------------- NAVTEX ----------------

def fetch_navtex():
    try:
        r = requests.get(SEALAGOM_URL, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")
        raw_msgs = re.split(r"\n(?=\d{4}/\d{2})", text)
        messages = []
        for m in raw_msgs:
            date_match = re.search(
                r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2}\s+UTC",
                m
            )
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
        print("NAVTEX error:", e)
        return []

def last(update, context):
    msgs = fetch_navtex()
    if not msgs:
        update.message.reply_text("No NAVTEX messages")
        return
    for m in msgs:
        msg = add_coordinate_links(m[:3500])
        update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

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

# ---------------- GOV ----------------
# только блок /lastgov переписан
GOV_NTM_URLS = [
    "https://www.gov.il/en/pages/mariners-011-2026",
    "https://www.gov.il/en/pages/mariners-010-2026",
    "https://www.gov.il/en/pages/mariners-009-2026",
    "https://www.gov.il/en/pages/mariners-008-2026",
    "https://www.gov.il/en/pages/mariners-007-2026",
]

def get_gov_notices():
    notices = []
    for url in GOV_NTM_URLS:
        try:
            r = requests.get(url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.find("h1")
            if not h1:
                continue
            title_text = h1.get_text().strip()
            if "NOTICE TO MARINER" in title_text.upper():
                notices.append({
                    "number": title_text,
                    "link": url
                })
        except Exception as e:
            print("GOV page error:", e)
            continue
    return notices[:5]

def lastgov(update, context):
    notices = get_gov_notices()
    if not notices:
        update.message.reply_text("No GOV notices found")
        return
    for n in notices:
        msg = f"Новое сообщение: ⚓ <a href='{n['link']}'>{n['number']}</a>"
        update.message.reply_text(msg, parse_mode="HTML")

# ---------------- TEST ----------------

def test(update, context):
    update.message.reply_text("✅ Bot running")

# ---------------- MAIN ----------------

def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("test", test))
    dp.add_handler(CommandHandler("lastgov", lastgov))
    dp.add_handler(CommandHandler("metarea", metarea))
    dp.add_handler(CommandHandler("last", last))
    updater.start_polling()
    print("BOT STARTED")
    updater.idle()

if __name__ == "__main__":
    main()