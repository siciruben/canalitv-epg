#!/usr/bin/env python3
"""Guida TV leggera per Canali TV.

Scarica la guida XMLTV di un paese, tiene solo i canali presenti nelle playlist
usate dall'app e solo i programmi delle prossime ore, e scrive un piccolo JSON:

  epg/<paese>.json  ->  {"v":1,"t":<epoch>,"c":{"Rai1.it":[[inizio,fine,"Titolo"],...]}}

Gli orari sono secondi Unix (UTC), quindi la TV non deve fare conversioni di fuso.
Nessuna dipendenza esterna: basta Python 3.
"""
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# Per aggiungere un paese basta una nuova voce (poi lo si aggiunge anche nell'app).
PAESI = {
    "it": {
        "epg": "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz",
        "playlist": [
            "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_italy.m3u8",
            "https://iptv-org.github.io/iptv/countries/it.m3u",
        ],
    },
    # "uk": {"epg": "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    #        "playlist": ["https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_uk.m3u8",
    #                     "https://iptv-org.github.io/iptv/countries/uk.m3u"]},
    # "de": {"epg": "https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz",
    #        "playlist": ["https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_germany.m3u8",
    #                     "https://iptv-org.github.io/iptv/countries/de.m3u"]},
}

PASSATO = 2 * 3600   # tengo anche i programmi finiti da poco
FUTURO = 36 * 3600   # e quelli delle prossime 36 ore
UA = {"User-Agent": "Mozilla/5.0 (CanaliTV-EPG)"}


def scarica(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def id_playlist(url):
    testo = scarica(url).decode("utf-8", "replace")
    # iptv-org aggiunge un suffisso tipo "@SD": lo tolgo per far combaciare gli id
    return {re.sub(r"@.*$", "", m) for m in re.findall(r'tvg-id="([^"]+)"', testo) if m}


def epoch(s):
    """'20261123203000 +0100' -> secondi Unix UTC."""
    m = re.match(r"\s*(\d{14})\s*([+-]\d{4})?", s)
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    off = m.group(2)
    if off:
        segno = 1 if off[0] == "+" else -1
        tz = timezone(segno * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    else:
        tz = timezone.utc
    return int(dt.replace(tzinfo=tz).timestamp())


def costruisci(codice, cfg, adesso=None):
    ids = set()
    for u in cfg["playlist"]:
        try:
            ids |= id_playlist(u)
        except Exception as e:  # una playlist irraggiungibile non deve fermare tutto
            print(f"[{codice}] playlist non letta: {u} ({e})")
    print(f"[{codice}] canali con id nelle playlist: {len(ids)}")

    xml = gzip.decompress(scarica(cfg["epg"]))
    print(f"[{codice}] guida scaricata: {len(xml) / 1e6:.1f} MB non compressi")

    ora = int(adesso or time.time())
    da, a = ora - PASSATO, ora + FUTURO
    out, visti = {}, set()
    for _, el in ET.iterparse(io.BytesIO(xml), events=("end",)):
        if el.tag == "channel":
            visti.add(el.get("id"))
        elif el.tag == "programme":
            cid = el.get("channel")
            if cid and (not ids or cid in ids):
                try:
                    s, e = epoch(el.get("start")), epoch(el.get("stop"))
                except Exception:
                    s = e = 0
                if e > da and s < a:
                    t = el.find("title")
                    titolo = (t.text or "").strip() if t is not None else ""
                    if titolo:
                        out.setdefault(cid, []).append([s, e, titolo])
            el.clear()

    for cid in out:
        out[cid].sort()

    if ids:
        senza = sorted(i for i in ids if i not in out)
        print(f"[{codice}] canali con programmi: {len(out)} su {len(ids)}")
        if senza:
            print(f"[{codice}] senza guida ({len(senza)}): " + ", ".join(senza[:80]) + (" ..." if len(senza) > 80 else ""))
    if not out:
        raise RuntimeError(f"[{codice}] nessun programma trovato: tengo il file precedente")

    os.makedirs("epg", exist_ok=True)
    path = os.path.join("epg", f"{codice}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "t": ora, "c": out}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[{codice}] scritto {path}: {os.path.getsize(path) / 1024:.0f} KB")


def main():
    errori = 0
    for codice, cfg in PAESI.items():
        try:
            costruisci(codice, cfg)
        except Exception as e:
            errori += 1
            print(f"ERRORE {codice}: {e}")
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
