import os
import re
import time
import json
import imaplib
import email
import tempfile
import threading
import html
from io import BytesIO
from datetime import datetime, date, timezone, timedelta
from email.header import decode_header
from zoneinfo import ZoneInfo

import requests
from telegram import ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from docx import Document

# ----------------------- НАСТРОЙКИ (ENV) -----------------------
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMS_API_TOKEN = os.getenv("IMS_API_TOKEN", "").strip()

# Таймзона Израиля
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Кэширование метаданных станций (ID, каналы) - 12 часов
IMS_STATION_CACHE_TTL = 12 * 60 * 60
IMS_STATIONS_METADATA_CACHE = {"fetched_at": 0, "stations": []}
IMS_STATION_DETAIL_CACHE = {}

# Константы для поиска писем
SENDER_KEYWORD = "mot.gov.il"
SUBJECT_KEYWORD = "notice to mariner"

# Константы для кнопок
HAIFA_BUOY_BUTTON = "Haifa buoy"
ASHDOD_BUOY_BUTTON = "Ashdod buoy"
FORECAST_BUTTON = "Forecast Taurus Delta Crusade"
GOV_BUTTON = "gov.il"
NAVAREA_BUTTON = "Navarea III"

WEATHER_KEYBOARD = [
    [GOV_BUTTON, NAVAREA_BUTTON],
    [FORECAST_BUTTON],
    ["Haifa Technion", HAIFA_BUOY_BUTTON],
    ["En Carmel", "Hadera Port"],
    ["Tel Aviv Coast", "Ashqelon Port"],
    ["Ashdod Port", ASHDOD_BUOY_BUTTON],
]

# ----------------------- ВСЕ ТВОИ РЕГУЛЯРКИ И КООРДИНАТЫ -----------------------

def dms_to_decimal(deg, minutes, seconds, direction):
    try:
        value = float(deg) + float(minutes) / 60 + float(seconds) / 3600
        if direction.upper() in ["S", "W"]: value = -value
        return value
    except: return None

def dm_to_decimal(deg, minutes, direction):
    try:
        value = float(deg) + float(minutes) / 60
        if direction.upper() in ["S", "W"]: value = -value
        return value
    except: return None

def decimal_signed(value, direction):
    try:
        value = float(value)
        if direction.upper() in ["S", "W"]: return -abs(value)
        return abs(value)
    except: return None

def replace_coordinates(text, pattern, parser):
    matches = list(pattern.finditer(text))
    replacements = []
    for m in matches:
        try:
            coords = parser(m)
            if coords and coords[0] is not None and coords[1] is not None:
                lat, lon = coords
                url = f"https://www.google.com/maps?q={lat},{lon}"
                original = m.group(0)
                html_link = f'<a href="{url}">{original}</a>'
                replacements.append((m.start(), m.end(), html_link))
        except: pass
    for start, end, html_link in reversed(replacements):
        text = text[:start] + html_link + text[end:]
    return text

def add_coordinate_links(text):
    if not text: return ""
    safe = html.escape(text)
    
    # 1. 32 50 42 N 034 54 24 E (DMS)
    p1 = re.compile(r'(?P<lat_deg>\d{1,2})\s+(?P<lat_min>\d{2})\s+(?P<lat_sec>\d{2}(?:\.\d+)?)\s+(?P<lat_dir>[NS])\s+(?P<lon_deg>\d{3})\s+(?P<lon_min>\d{2})\s+(?P<lon_sec>\d{2}(?:\.\d+)?)\s+(?P<lon_dir>[EW])', re.I)
    safe = replace_coordinates(safe, p1, lambda m: (dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), m.group("lat_dir")), dms_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_sec"), m.group("lon_dir"))))

    # 2. 32° 50' 42" N 034° 54' 24" E (DMS with symbols)
    p2 = re.compile(r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*(?P<lat_min>\d{1,2})\s*[\'′]?\s*(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*[NS][\s,;/:\-]*(?P<lon_deg>\d{1,3})\s*[°º]?\s*(?P<lon_min>\d{1,2})\s*[\'′]?\s*(?P<lon_sec>\d{1,2}(?:\.\d+)?)\s*[EW]', re.I)
    safe = replace_coordinates(safe, p2, lambda m: (dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), "N" if "N" in m.group(0).upper() else "S"), dms_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_sec"), "E" if "E" in m.group(0).upper() else "W")))

    # 3. 32° 50.7' N 034° 54.4' E (DM)
    p3 = re.compile(r'(?P<lat_deg>\d{1,2})\s*[°º]?\s*(?P<lat_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*(?P<lat_dir>[NS])[\s,;/:\-]*(?P<lon_deg>\d{1,3})\s*[°º]?\s*(?P<lon_min>\d{1,2}(?:\.\d+)?)\s*[\'′]?\s*(?P<lon_dir>[EW])', re.I)
    safe = replace_coordinates(safe, p3, lambda m: (dm_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_dir")), dm_to_decimal(m.group("lon_deg"), m.group("lon_min"), m.group("lon_dir"))))

    # 4. Latitude: 32.845 Longitude: 34.906 (Decimal)
    p4 = re.compile(r'LAT(?:ITUDE)?[:\s]+(?P<lat_val>\-?\d+\.\d+).*?LON(?:GITUDE)?[:\s]+(?P<lon_val>\-?\d+\.\d+)', re.I | re.S)
    safe = replace_coordinates(safe, p4, lambda m: (float(m.group("lat_val")), float(m.group("lon_val"))))

    return safe

# ----------------------- ИСПРАВЛЕННЫЕ ФУНКЦИИ ВРЕМЕНИ И IMS -----------------------

def parse_datetime_any(value):
    if not value: return None
    if isinstance(value, (int, float)):
        try:
            if value > 10**12: return datetime.fromtimestamp(value/1000.0, tz=timezone.utc).replace(tzinfo=None)
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except: return None
    
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo: return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except: pass
    
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try: return datetime.strptime(text, fmt)
        except: continue
    return None

def ims_measurement_to_utc(dt_raw):
    """Считает время от IMS израильским и конвертирует в UTC"""
    if not dt_raw: return None
    if isinstance(dt_raw, str) and ("Z" in dt_raw or "+00:00" in dt_raw):
        return parse_datetime_any(dt_raw)
    
    dt = parse_datetime_any(dt_raw)
    if not dt: return None
    try:
        # Присваиваем зону Израиля и переводим в UTC
        dt_local = dt.replace(tzinfo=ISRAEL_TZ)
        return dt_local.astimezone(timezone.utc).replace(tzinfo=None)
    except: return dt

def format_full_datetime_with_isr(dt_utc):
    if not dt_utc: return "N/A"
    dt_isr = dt_utc.replace(tzinfo=timezone.utc).astimezone(ISRAEL_TZ)
    return f"{dt_utc.strftime('%d %b %Y').upper()} / {dt_utc.strftime('%H:%M')} UTC / {dt_isr.strftime('%H:%M')} ISR"

def ims_headers():
    return {"Authorization": f"ApiToken {IMS_API_TOKEN}", "Accept": "application/json"}

def fetch_ims_stations_list():
    now = time.time()
    if IMS_STATIONS_METADATA_CACHE["stations"] and (now - IMS_STATIONS_METADATA_CACHE["fetched_at"] < IMS_STATION_CACHE_TTL):
        return IMS_STATIONS_METADATA_CACHE["stations"]
    try:
        r = requests.get("https://api.ims.gov.il/v1/Envista/stations", headers=ims_headers(), timeout=20)
        data = r.json()
        IMS_STATIONS_METADATA_CACHE["stations"] = data
        IMS_STATIONS_METADATA_CACHE["fetched_at"] = now
        return data
    except: return []

def get_ims_weather_report(station_name):
    stations = fetch_ims_stations_list()
    target = station_name.upper().strip()
    st_id = next((s.get("stationId") for s in stations if target in str(s.get("name","") or s.get("stationName","")).upper()), None)
    
    if not st_id: return f"Station {station_name} not found."

    # 1. МЕТАДАННЫЕ (датчики) - из кэша
    if st_id not in IMS_STATION_DETAIL_CACHE:
        try:
            r_meta = requests.get(f"https://api.ims.gov.il/v1/Envista/stations/{st_id}", headers=ims_headers(), timeout=15)
            IMS_STATION_DETAIL_CACHE[st_id] = r_meta.json()
        except: return "Error fetching station metadata."

    meta = IMS_STATION_DETAIL_CACHE[st_id]
    
    # 2. ДАННЫЕ - ВСЕГДА СВЕЖИЕ
    try:
        r_data = requests.get(f"https://api.ims.gov.il/v1/Envista/stations/{st_id}/data/latest", headers=ims_headers(), timeout=10)
        latest_json = r_data.json()
        data_point = latest_json.get("data", [])[0]
    except: return "No recent data available from IMS (API error)."

    utc_time = ims_measurement_to_utc(data_point.get("datetime"))
    channels_map = {str(ch["channelId"]): ch["description"] for ch in meta.get("monitors", [])}
    
    measurements = []
    for m in data_point.get("channels", []):
        name = channels_map.get(str(m.get("channelId")), f"Ch {m.get('channelId')}")
        val = m.get("value")
        if val is not None: measurements.append(f"<b>{name}:</b> {val}")

    return (f"📍 <b>{station_name.upper()}</b>\n"
            f"Updated: {format_full_datetime_with_isr(utc_time)}\n\n" + "\n".join(measurements))

# ----------------------- GMAIL И DOCX -----------------------

def get_gmail_service():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    return mail

def check_gmail_and_notify(bot):
    try:
        mail = get_gmail_service()
        mail.select("inbox")
        search_query = f'(FROM "{SENDER_KEYWORD}" SUBJECT "{SUBJECT_KEYWORD}")'
        status, messages = mail.search(None, search_query)
        
        if status != "OK" or not messages[0]:
            mail.logout()
            return
        
        for num in messages[0].split():
            _, data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart': continue
                filename = part.get_filename()
                if filename and filename.lower().endswith(".docx"):
                    content = part.get_payload(decode=True)
                    doc = Document(BytesIO(content))
                    full_text = "\n".join([p.text for p in doc.paragraphs])
                    # Обработка текста и добавление ссылок на карты
                    linked_text = add_coordinate_links(full_text)
                    bot.send_message(CHAT_ID, linked_text, parse_mode='HTML', disable_web_page_preview=True)
                    
            # Помечаем как прочитанное или перемещаем (твоя логика)
            mail.store(num, '+FLAGS', '\\Seen')
            
        mail.logout()
    except Exception as e:
        print(f"Gmail loop error: {e}")

# ----------------------- БУИ CAMERI -----------------------

def fetch_cameri_buoy_latest(location_id):
    now_ms = int(time.time() * 1000)
    payload = {
        "queries": [{
            "refId": "A",
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "d4fb9d12-3057-41b7-9ce2-36c7320d8a58"},
            "rawSql": f"SELECT middle_time AS \"time\", avg_hs, avg_tp, avg_temp FROM backend_buoysmsr_2h_avg WHERE location_id = '{location_id}' AND middle_time >= (now() - interval '3 days') ORDER BY middle_time DESC LIMIT 1;"
        }],
        "from": str(now_ms - 259200000), "to": str(now_ms)
    }
    try:
        r = requests.post("https://adva.cameri-eng.com/api/ds/query", json=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        data = r.json()
        frames = data.get("results", {}).get("A", {}).get("frames", [])
        if not frames: return None
        vals = frames[0]["data"]["values"]
        return {"time": vals[0][0], "hs": vals[1][0], "tp": vals[2][0], "temp": vals[3][0]}
    except: return None

def build_cameri_message(name, loc_id):
    data = fetch_cameri_buoy_latest(loc_id)
    if not data: return f"Buoy {name}: No data available."
    utc_time = parse_datetime_any(data["time"])
    return (f"🌊 <b>{name.upper()}</b>\n"
            f"Updated: {format_full_datetime_with_isr(utc_time)}\n\n"
            f"<b>Hs (SWH):</b> {data['hs']} m\n"
            f"<b>Tp (Peak Period):</b> {data['tp']} s\n"
            f"<b>Water Temp:</b> {data['temp']} °C")

# ----------------------- NAVAREA III -----------------------

def get_navarea_info():
    try:
        # Упрощенный пример вызова, используй свою логику парсинга sealagom
        r = requests.get("https://www.sealagom.com/navarea/3/", timeout=20)
        if r.status_code == 200:
            return "Navarea III: Information fetched. Please check coordinates in the latest notices."
        return "Navarea III service temporarily unavailable."
    except:
        return "Error connecting to Navarea service."

# ----------------------- TELEGRAM HANDLERS -----------------------

def start(update, context):
    update.message.reply_text(
        "Welcome! Choose a station or service:",
        reply_markup=ReplyKeyboardMarkup(WEATHER_KEYBOARD, resize_keyboard=True)
    )

def handle_message(update, context):
    text = update.message.text
    
    # Станции IMS
    ims_stations = ["Haifa Technion", "En Carmel", "Hadera Port", "Tel Aviv Coast", "Ashqelon Port", "Ashdod Port"]
    if text in ims_stations:
        update.message.reply_text("⏳ Fetching live update from IMS...")
        report = get_ims_weather_report(text)
        update.message.reply_text(report, parse_mode='HTML')
        return

    # Буи
    if text == HAIFA_BUOY_BUTTON:
        update.message.reply_text(build_cameri_message("Haifa Buoy", 1), parse_mode='HTML')
    elif text == ASHDOD_BUOY_BUTTON:
        update.message.reply_text(build_cameri_message("Ashdod Buoy", 2), parse_mode='HTML')

    # Прочее
    elif text == NAVAREA_BUTTON:
        update.message.reply_text(get_navarea_info())
    elif text == GOV_BUTTON:
        update.message.reply_text("Official Navigational Notices: https://www.gov.il/he/departments/publications/reports/notices-to-mariners")
    elif text == FORECAST_BUTTON:
        update.message.reply_text("Forecast Taurus/Delta/Crusade: [Link to service]")

def main():
    if not TOKEN:
        print("Error: BOT_TOKEN not found in environment variables.")
        return

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Фоновая проверка почты
    def gmail_thread():
        while True:
            check_gmail_and_notify(updater.bot)
            time.sleep(300) # каждые 5 минут
            
    threading.Thread(target=gmail_thread, daemon=True).start()

    print("Bot is running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()