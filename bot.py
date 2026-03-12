def get_metarea():

    try:
        r = requests.get(METAREA_URL, timeout=20)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        clean = "\n".join(lines)

        issued_match = re.search(
            r"\d{1,2}\s+[A-Z]+\s+\d{4}\s*/\s*\d{4}\s*UTC",
            clean,
            re.I
        )

        issued = issued_match.group(0) if issued_match else "N/A"

        # берём ПОСЛЕДНИЕ вхождения зон
        taurus_pos = clean.rfind("TAURUS")
        delta_pos = clean.rfind("DELTA")
        crusade_pos = clean.rfind("CRUSADE")
        kast_pos = clean.rfind("KASTELLORIZO SEA")

        taurus = clean[taurus_pos:delta_pos]
        delta = clean[delta_pos:crusade_pos]
        crusade = clean[crusade_pos:kast_pos]

        def format_zone(name, text):
            text = text.replace(name, "", 1).strip()
            text = re.sub(r"\.\s*", ".\n", text)
            return f"📍 {name}\n{text.strip()}"

        blocks = [
            format_zone("TAURUS", taurus),
            format_zone("DELTA", delta),
            format_zone("CRUSADE", crusade),
        ]

        msg = f"🕒 Issued: {issued}\n\n" + "\n\n".join(blocks)

        return msg[:4000]

    except Exception as e:
        print("METAREA error:", e)
        return f"METAREA error: {e}"