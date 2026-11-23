#!/usr/bin/env python3
"""Guida TV leggera per Canali TV (versione 3: aggancio per id e per nome + file per canale).

Scarica la guida XMLTV di un paese, tiene i canali che corrispondono a quelli delle
playlist usate dall'app (per id oppure per nome) e solo i programmi delle prossime ore.

Scrive:
  epg/<paese>.json        {"v":2,"t":<epoch>,"p":{idGuida:[[inizio,fine,"Titolo"],...]},
                           "m":{chiaveNome:idGuida}}   (tutto in un file, come prima)
  epg/<paese>.idx.json    {"v":3,"t":<epoch>,"i":{idGuida:file},"m":{chiaveNome:file}}
                           indice piccolo: dice a quale file andare per un canale
  epg/<paese>/<file>.json {"p":[[inizio,fine,"Titolo"],...]}   un file per canale
  epg/diagnosi-<paese>.txt  quali canali sono coperti e quali no

Gli orari sono secondi Unix (UTC). Nessuna dipendenza esterna: basta Python 3.
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

FREE = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_%s.m3u8"
IPTV = "https://iptv-org.github.io/iptv/countries/%s.m3u"
EPGS = "https://epgshare01.online/epgshare01/epg_ripper_%s.xml.gz"

# Le playlist sono le stesse dell'app. Se un indirizzo non esiste viene saltato (nessun errore).
PAESI = {
    "it": {"epg": EPGS % "IT1", "playlist": [FREE % "italy", IPTV % "it"]},
    "uk": {"epg": EPGS % "UK1", "playlist": [FREE % "uk", FREE % "united_kingdom", IPTV % "uk", IPTV % "gb"]},
    "us": {"epg": EPGS % "US1", "playlist": [FREE % "usa", FREE % "us", FREE % "united_states", IPTV % "us"]},
    "de": {"epg": EPGS % "DE1", "playlist": [FREE % "germany", FREE % "de", IPTV % "de"]},
}

PASSATO = 2 * 3600
FUTURO = 36 * 3600
UA = {"User-Agent": "Mozilla/5.0 (CanaliTV-EPG)"}


def scarica(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def chiave(nome):
    """Stessa normalizzazione dell'app (keyOf in index.html)."""
    n = re.sub(r"[\u24B6-\u24E9]", "", nome or "")
    n = re.sub(r"[\(\[].*?[\)\]]", "", n)
    n = re.sub(r"\s+", " ", n).strip().lower()
    return re.sub(r"hd|\s|[^a-z0-9]", "", n)


def leggi_playlist(url):
    """Restituisce (id, {chiave: nome}) dei canali della playlist."""
    testo = scarica(url).decode("utf-8", "replace")
    ids, nomi = set(), {}
    for riga in testo.splitlines():
        if not riga.startswith("#EXTINF"):
            continue
        m = re.search(r'tvg-id="([^"]+)"', riga)
        if m:
            ids.add(re.sub(r"@.*$", "", m.group(1)))
        for nome in (riga.rsplit(",", 1)[-1].strip(), (re.search(r'tvg-name="([^"]*)"', riga) or [None, ""])[1]):
            k = chiave(nome)
            if k:
                nomi.setdefault(k, nome)
    return ids, nomi


def epoch(s):
    m = re.match(r"\s*(\d{14})\s*([+-]\d{4})?", s)
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    off = m.group(2)
    if off:
        segno = 1 if off[0] == "+" else -1
        tz = timezone(segno * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    else:
        tz = timezone.utc
    return int(dt.replace(tzinfo=tz).timestamp())


def nome_file(cid):
    """Nome di file sicuro per l'id di un canale della guida."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", cid)


def scrivi_split(codice, scelti, mappa, ora):
    """Un piccolo file per canale + un indice: la TV scarica solo quello che serve."""
    cartella = os.path.join("epg", codice)
    os.makedirs(cartella, exist_ok=True)
    for vecchio in os.listdir(cartella):  # niente file di canali che non ci sono piu'
        if vecchio.endswith(".json"):
            os.remove(os.path.join(cartella, vecchio))
    indice = {"v": 3, "t": ora, "i": {}, "m": {}}
    for cid, lista in scelti.items():
        f = nome_file(cid)
        indice["i"][cid] = f
        with open(os.path.join(cartella, f + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"p": lista}, fh, ensure_ascii=False, separators=(",", ":"))
    for k, cid in mappa.items():
        indice["m"][k] = nome_file(cid)
    path = os.path.join("epg", codice + ".idx.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(indice, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"[{codice}] scritti {len(scelti)} file per canale + indice ({os.path.getsize(path) / 1024:.0f} KB)")


def costruisci(codice, cfg, adesso=None):
    pl_ids, pl_nomi = set(), {}
    for u in cfg["playlist"]:
        try:
            i, n = leggi_playlist(u)
            pl_ids |= i
            for k, v in n.items():
                pl_nomi.setdefault(k, v)
        except Exception as e:
            print(f"[{codice}] playlist non letta: {u} ({e})")
    print(f"[{codice}] playlist: {len(pl_ids)} id, {len(pl_nomi)} nomi")

    dati = scarica(cfg["epg"])
    print(f"[{codice}] guida scaricata: {len(dati) / 1e6:.1f} MB compressi")

    ora = int(adesso or time.time())
    da, a = ora - PASSATO, ora + FUTURO
    alias = {}   # id guida -> insieme di chiavi dai display-name
    nomi_g = {}  # id guida -> primo display-name
    prog = {}
    for _, el in ET.iterparse(gzip.GzipFile(fileobj=io.BytesIO(dati)), events=("end",)):
        if el.tag == "channel":
            cid = el.get("id")
            for d in el.findall("display-name"):
                if d.text:
                    nomi_g.setdefault(cid, d.text.strip())
                    k = chiave(d.text)
                    if k:
                        alias.setdefault(cid, set()).add(k)
        elif el.tag == "programme":
            cid = el.get("channel")
            try:
                s, e = epoch(el.get("start")), epoch(el.get("stop"))
            except Exception:
                s = e = 0
            if cid and e > da and s < a:
                t = el.find("title")
                titolo = (t.text or "").strip() if t is not None else ""
                if titolo:
                    prog.setdefault(cid, []).append([s, e, titolo])
        if el.tag in ("channel", "programme"):  # svuoto solo dopo aver letto i figli (title, display-name)
            el.clear()

    # scelgo i canali della guida che corrispondono a un canale delle playlist
    scelti, mappa = {}, {}
    for cid, lista in prog.items():
        chiavi = alias.get(cid, set())
        k_id = chiave(cid.split(".")[0])
        ok_id = cid in pl_ids
        k_ok = {k for k in chiavi | {k_id} if k in pl_nomi}
        if ok_id or k_ok:
            lista.sort()
            scelti[cid] = lista
            for k in k_ok:
                mappa.setdefault(k, cid)

    # diagnosi leggibile
    righe = [f"Guida: {len(prog)} canali con programmi nella finestra; scelti: {len(scelti)}", ""]
    righe.append("== Canali della GUIDA (con programmi) e se agganciati ==")
    for cid in sorted(prog):
        righe.append(f"{'SI ' if cid in scelti else 'NO '} {cid} | {nomi_g.get(cid, '')} | {len(prog[cid])} programmi")
    righe += ["", "== Canali delle PLAYLIST senza guida (per nome) =="]
    righe += sorted(v for k, v in pl_nomi.items() if k not in mappa)
    os.makedirs("epg", exist_ok=True)
    with open(os.path.join("epg", f"diagnosi-{codice}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(righe) + "\n")
    print(f"[{codice}] canali della guida con programmi: {len(prog)}; agganciati alle playlist: {len(scelti)}")

    if not scelti:
        raise RuntimeError(f"[{codice}] nessun canale agganciato: tengo il file precedente")

    path = os.path.join("epg", f"{codice}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"v": 2, "t": ora, "p": scelti, "m": mappa}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[{codice}] scritto {path}: {os.path.getsize(path) / 1024:.0f} KB")

    scrivi_split(codice, scelti, mappa, ora)


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
