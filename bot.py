import time
import re
import requests
from bs4 import BeautifulSoup

SEALAGOM_URL = "https://www.sealagom.com/navarea/3/messages/"
CACHE_FILE = "cache.json"
NAVTEX_INTERVAL = 900  # каждые 15 минут

import json
import os

# ---------------- CACHE ----------------
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {"navtex": []}
    with open(CACHE_FILE) as f:
        return json.load(f)

def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)

cache = load_cache()

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
        print("Error fetching NAVTEX:", e)
        return []

def check_navtex():
    messages = fetch_sealagom_navtex()
    new_msgs = [m for m in messages if m not in cache["navtex"]]
    if not new_msgs:
        return
    for m in new_msgs:
        print("📡 NAVTEX Message:\n", m, "\n")  # <-- пуш в терминал
        cache["navtex"].append(m)
    save_cache(cache)

# ---------------- MAIN ----------------
def main():
    last_navtex_check = 0
    print("NAVTEX terminal bot started")
    while True:
        try:
            if time.time() - last_navtex_check >= NAVTEX_INTERVAL:
                check_navtex()
                last_navtex_check = time.time()
        except Exception as e:
            print("Error:", e)
        time.sleep(10)  # проверка каждые 10 секунд

if __name__=="__main__":
    main()