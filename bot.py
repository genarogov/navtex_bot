import os
import re
import json
import time
import imaplib
import email
from email.header import decode_header

from telegram.ext import Updater, CommandHandler

from docx import Document
from pdf2image import convert_from_path

TOKEN=os.getenv("BOT_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")

EMAIL_USER=os.getenv("EMAIL_USER")
EMAIL_PASS=os.getenv("EMAIL_PASS")

SENDER="benzviy.mot.gov.il@send.vpcontact.com"

CHECK_INTERVAL=1800
CACHE_FILE="cache.json"

# ---------------- CACHE ----------------

def load_cache():

    if not os.path.exists(CACHE_FILE):

        return {"gmail":[]}

    with open(CACHE_FILE) as f:

        return json.load(f)

def save_cache(c):

    with open(CACHE_FILE,"w") as f:

        json.dump(c,f)

cache=load_cache()

# ---------------- COORDINATES ----------------

def coord_links(text):

    pattern=re.compile(
    r'(\d{1,3})[^\d]+(\d{1,2}\.?\d*)\s*([NS])[^\d]+(\d{1,3})[^\d]+(\d{1,2}\.?\d*)\s*([EW])',
    re.I)

    def repl(m):

        lat=float(m.group(1))+float(m.group(2))/60
        lon=float(m.group(4))+float(m.group(5))/60

        if m.group(3).upper()=="S":
            lat=-lat

        if m.group(6).upper()=="W":
            lon=-lon

        url=f"https://maps.google.com/?q={lat},{lon}"

        return f'<a href="{url}">{m.group(0)}</a>'

    return pattern.sub(repl,text)

# ---------------- GMAIL ----------------

def check_gmail():

    mail=imaplib.IMAP4_SSL("imap.gmail.com")

    mail.login(EMAIL_USER,EMAIL_PASS)

    mail.select("inbox")

    status,data=mail.search(None,'ALL')

    ids=data[0].split()

    ids=ids[-10:]

    for i in ids[::-1]:

        status,msg_data=mail.fetch(i,'(RFC822)')

        msg=email.message_from_bytes(msg_data[0][1])

        subject,enc=decode_header(msg["Subject"])[0]

        if isinstance(subject,bytes):

            subject=subject.decode(enc or "utf8")

        if SENDER not in msg["From"]:
            continue

        if "notice to mariner" not in subject.lower():
            continue

        mid=msg["Message-ID"]

        if mid in cache["gmail"]:
            continue

        for part in msg.walk():

            if part.get_content_type()=="application/vnd.openxmlformats-officedocument.wordprocessingml.document":

                filename=part.get_filename()

                with open(filename,"wb") as f:

                    f.write(part.get_payload(decode=True))

                doc=Document(filename)

                text="\n".join([p.text for p in doc.paragraphs])

                text=coord_links(text)

                pdf=filename.replace(".docx",".pdf")

                os.system(f'libreoffice --headless --convert-to pdf "{filename}"')

                if os.path.exists(pdf):

                    img=filename.replace(".docx",".png")

                    images=convert_from_path(pdf)

                    images[0].save(img)

                    return subject,text,img,mid

    return None,None,None,None

# ---------------- COMMAND ----------------

def checkgovil(update,context):

    subject,text,img,mid=check_gmail()

    if not subject:

        update.message.reply_text("No new messages")

        return

    context.bot.send_message(
    CHAT_ID,
    f"{subject}\n\n{text}",
    parse_mode="HTML",
    disable_web_page_preview=True)

    context.bot.send_photo(CHAT_ID,photo=open(img,"rb"))

    cache["gmail"].append(mid)

    save_cache(cache)

# ---------------- AUTO CHECK ----------------

def auto(updater):

    while True:

        try:

            subject,text,img,mid=check_gmail()

            if subject:

                updater.bot.send_message(
                CHAT_ID,
                f"{subject}\n\n{text}",
                parse_mode="HTML",
                disable_web_page_preview=True)

                updater.bot.send_photo(CHAT_ID,photo=open(img,"rb"))

                cache["gmail"].append(mid)

                save_cache(cache)

        except Exception as e:

            print(e)

        time.sleep(CHECK_INTERVAL)

# ---------------- MAIN ----------------

def main():

    updater=Updater(TOKEN)

    dp=updater.dispatcher

    dp.add_handler(CommandHandler("checkgovil",checkgovil))

    updater.start_polling()

    import threading

    threading.Thread(target=auto,args=(updater,),daemon=True).start()

    updater.idle()

if __name__=="__main__":

    main()