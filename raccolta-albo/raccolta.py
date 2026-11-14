#!/usr/bin/env python3
"""Raccolta giornaliera degli atti dall'albo pretorio (piattaforma JCityGov).

Per ogni comune in comuni.json:
  1. trova l'URL della lista "Albo pretorio" leggendo il menu del portale;
  2. scorre tutte le pagine della lista degli atti pubblicati oggi;
  3. per ogni atto mai visto scarica il dettaglio (metadati) e gli allegati.

Gli atti restano leggibili per intero solo durante la pubblicazione (~15 giorni),
quindi lo script va eseguito ogni giorno: accumula un archivio da oggi in avanti.

Uso:
  .venv/bin/python raccolta.py                 # tutti i comuni
  .venv/bin/python raccolta.py --comune magenta
  .venv/bin/python raccolta.py --senza-allegati  # solo metadati (più veloce)
  # in GitHub Actions: database e documenti principali in un repo,
  # gli altri allegati (pesanti) in una cartella da archiviare a parte
  python raccolta.py --dati ../incipit-dati --allegati ../da-archiviare
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATI = ROOT / "dati"            # database + documenti principali (sovrascrivibile con --dati)
DB_PATH = DATI / "albo.sqlite"
ALLEGATI: Path | None = None    # se impostato (--allegati), qui vanno gli allegati non principali
PREFISSO_ARCHIVIO = "archivio:"  # nel DB, file salvati in ALLEGATI e poi spostati in un archivio
LOG_PATH = ROOT / "log" / "raccolta.log"

PORTLET = "jcitygovalbopubblicazioni_WAR_jcitygovalbiportlet"
PAUSA_SECONDI = 1.0  # cortesia verso il server del comune
MAX_PAGINE = 100     # protezione da loop infiniti
TIMEOUT = 60         # secondi di silenzio massimo tra due pezzi della risposta
MAX_MB_ALLEGATO = 25        # file più grandi (tavole, planimetrie) si saltano
MAX_SECONDI_ALLEGATO = 180  # durata massima di un singolo download
SALTATO = "saltato:"        # prefisso dell'errore per i file saltati di proposito

log = logging.getLogger("raccolta")


# --------------------------------------------------------------------------- DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS atti (
    comune            TEXT NOT NULL,
    id                TEXT NOT NULL,        -- data-id della piattaforma
    anno_registro     INTEGER,
    numero_registro   INTEGER,
    categoria         TEXT,
    sottocategoria    TEXT,
    anno_atto         INTEGER,
    numero_atto       TEXT,
    data_atto         TEXT,
    proponente        TEXT,                 -- ufficio che ha prodotto l'atto
    firmatario        TEXT,
    oggetto           TEXT,
    inizio_pubblicazione TEXT,
    fine_pubblicazione   TEXT,
    numero_allegati   INTEGER,
    url_dettaglio     TEXT,
    metadati_json     TEXT,                 -- tutti i campi del dettaglio, grezzi
    primo_avvistamento TEXT NOT NULL,
    ultimo_avvistamento TEXT NOT NULL,
    PRIMARY KEY (comune, id)
);
CREATE TABLE IF NOT EXISTS allegati (
    comune       TEXT NOT NULL,
    atto_id      TEXT NOT NULL,
    chiave       TEXT NOT NULL,             -- data-chiave-allegato
    titolo       TEXT,
    descrizione  TEXT,
    mimetype     TEXT,
    file         TEXT,                      -- percorso relativo a dati/
    byte         INTEGER,
    sha256       TEXT,
    scaricato_il TEXT,
    errore       TEXT,
    PRIMARY KEY (comune, atto_id, chiave)
);
CREATE TABLE IF NOT EXISTS esecuzioni (
    inizio      TEXT, fine TEXT, comune TEXT,
    atti_in_lista INTEGER, atti_nuovi INTEGER, allegati_scaricati INTEGER,
    esito TEXT, messaggio TEXT
);
"""


def apri_db() -> sqlite3.Connection:
    DATI.mkdir(parents=True, exist_ok=True)
    # timeout + WAL: più thread (uno per comune) scrivono sullo stesso file
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


# ----------------------------------------------------------------------- HTTP

def nuova_sessione() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (compatible; raccolta-albo/0.1)"
    return s


def get(s: requests.Session, url: str, **kw) -> requests.Response:
    """GET con 3 tentativi e pausa di cortesia."""
    for tentativo in range(3):
        try:
            r = s.get(url, timeout=TIMEOUT, **kw)
            r.raise_for_status()
            time.sleep(PAUSA_SECONDI)
            return r
        except requests.RequestException as e:
            if tentativo == 2:
                raise
            log.warning("errore su %s (%s), riprovo", url, e)
            time.sleep(5 * (tentativo + 1))
    raise AssertionError("irraggiungibile")


class FileSaltato(Exception):
    """Allegato non scaricato di proposito (troppo grande o troppo lento)."""


def scarica(s: requests.Session, url: str) -> tuple[requests.Response, bytes]:
    """Download a pezzi con limite di dimensione e di durata totale.

    Il timeout di requests conta solo il silenzio tra due pezzi: un server che
    manda pochi byte alla volta può tenere aperta la connessione per ore.
    """
    limite = MAX_MB_ALLEGATO * 1024 * 1024
    for tentativo in range(2):
        try:
            with s.get(url, timeout=TIMEOUT, stream=True) as r:
                r.raise_for_status()
                dichiarati = int(r.headers.get("Content-Length") or 0)
                if dichiarati > limite:
                    raise FileSaltato(f"{SALTATO} {dichiarati / 1e6:.0f} MB")
                inizio, pezzi, totale = time.monotonic(), [], 0
                for pezzo in r.iter_content(256 * 1024):
                    pezzi.append(pezzo)
                    totale += len(pezzo)
                    if totale > limite:
                        raise FileSaltato(f"{SALTATO} oltre {MAX_MB_ALLEGATO} MB")
                    if time.monotonic() - inizio > MAX_SECONDI_ALLEGATO:
                        raise FileSaltato(f"{SALTATO} oltre {MAX_SECONDI_ALLEGATO} s")
                time.sleep(PAUSA_SECONDI)
                return r, b"".join(pezzi)
        except FileSaltato:
            raise
        except requests.RequestException as e:
            if tentativo == 1:
                raise
            log.warning("download fallito (%s), riprovo", e)
            time.sleep(5)
    raise AssertionError("irraggiungibile")


# --------------------------------------------------------------------- PARSER

def testo(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def data_iso(gg_mm_aaaa: str) -> str | None:
    try:
        return datetime.strptime(gg_mm_aaaa.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def trova_url_albo(s: requests.Session, host: str) -> str:
    """Il menu del portale contiene data-resource="Albo pretorio" data-mainurl="/.../igrid/X"."""
    html = get(s, host + "/web/trasparenza/albo-pretorio").text
    m = re.search(
        r'data-resource="Albo pretorio"[^>]*?data-mainurl="([^"]+)"[^>]*?data-idmenuitem="(\d+)"',
        html,
    )
    if not m:
        raise RuntimeError("voce di menu 'Albo pretorio' non trovata")
    return f"{host}{m.group(1)}/{m.group(2)}"


def leggi_lista(html: str) -> tuple[list[dict], bool]:
    """Restituisce le righe della pagina e se esiste una pagina successiva."""
    soup = BeautifulSoup(html, "html.parser")
    righe = []
    for tr in soup.select("tr.master-detail-list-line"):
        reg = testo(tr.select_one("td.annonumeroregistrazione"))
        periodo = testo(tr.select_one("td.periodo-pubblicazione")).split()
        righe.append({
            "id": tr["data-id"],
            "registro": reg,
            "categoria": testo(tr.select_one(".categoria_categoria")),
            "sottocategoria": testo(tr.select_one(".categoria_sottocategoria")),
            "oggetto": testo(tr.select_one("td.oggetto")),
            "inizio": data_iso(periodo[0]) if periodo else None,
            "fine": data_iso(periodo[-1]) if len(periodo) > 1 else None,
        })
    ha_successiva = "paginationAction=NEXT" in html
    return righe, ha_successiva


def leggi_dettaglio(html: str) -> tuple[dict, list[dict]]:
    """Metadati (etichetta -> valore) e allegati con i link di download."""
    soup = BeautifulSoup(html, "html.parser")
    meta: dict[str, str] = {}
    for tr in soup.select("tr"):
        lab = tr.select_one("span.label")
        tds = tr.find_all("td")
        if lab and len(tds) >= 2:
            meta[testo(lab)] = testo(tds[1])

    allegati = []
    for tr in soup.select("tr[data-chiave-allegato]"):
        tds = tr.find_all("td")
        link_firmato = link_pdf = None
        # i link sono offuscati in base64 dentro onclick="window.open(atob('...'))"
        for b64 in re.findall(r"atob\('([^']+)'\)", str(tr)):
            url = base64.b64decode(b64).decode("utf-8", "replace")
            if "downloadSigned=false" in url:
                link_pdf = url
            elif "downloadSigned=true" in url:
                link_firmato = url
        allegati.append({
            "chiave": tr["data-chiave-allegato"],
            "mimetype": tr.get("data-mimetype"),
            "titolo": testo(tds[0]) if tds else "",
            "descrizione": testo(tds[1]) if len(tds) > 1 else "",
            # preferiamo la versione "sbustata" (PDF leggibile) a quella firmata .p7m
            "url": link_pdf or link_firmato,
        })
    return meta, allegati


def nome_file(r: requests.Response, fallback: str) -> str:
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    nome = unquote(m.group(1)) if m else fallback
    nome = re.sub(r"[^\w.\- ]", "_", nome).strip() or fallback
    return nome[:150]


# ------------------------------------------------------------------- RACCOLTA

def raccogli_comune(con: sqlite3.Connection, comune: dict, scarica_allegati: bool) -> dict:
    slug, host = comune["slug"], comune["host"]
    s = nuova_sessione()
    adesso = datetime.now().isoformat(timespec="seconds")

    url_lista = trova_url_albo(s, host)
    log.info("[%s] lista: %s", slug, url_lista)
    url_next = (f"{host}/web/trasparenza/papca-ap?p_p_id={PORTLET}&p_p_lifecycle=0"
                f"&p_p_state=pop_up&p_p_mode=view&_{PORTLET}_paginationAction=NEXT"
                f"&_{PORTLET}_action=mostraLista")

    # 1. scorro la lista (la paginazione è legata ai cookie della sessione)
    tutte: dict[str, dict] = {}
    html = get(s, url_lista).text
    for _ in range(MAX_PAGINE):
        righe, ha_successiva = leggi_lista(html)
        nuove_in_pagina = [r for r in righe if r["id"] not in tutte]
        for r in righe:
            tutte[r["id"]] = r
        if not ha_successiva or not nuove_in_pagina:
            break
        html = get(s, url_next).text
    log.info("[%s] atti in pubblicazione: %d", slug, len(tutte))

    # 2. aggiorno ultimo_avvistamento per quelli già noti, dettaglio per i nuovi
    noti = {row[0] for row in con.execute("SELECT id FROM atti WHERE comune=?", (slug,))}
    con.executemany("UPDATE atti SET ultimo_avvistamento=? WHERE comune=? AND id=?",
                    [(adesso, slug, i) for i in tutte if i in noti])
    con.commit()  # non tenere aperta la transazione durante i download

    nuovi = [r for i, r in tutte.items() if i not in noti]
    # atti già noti ma con allegati mai scaricati (es. corsa precedente --senza-allegati
    # o download fallito): finché sono in pubblicazione possiamo ancora recuperarli
    incompleti = []
    if scarica_allegati:
        da_completare = {row[0] for row in con.execute(
            """SELECT a.id FROM atti a WHERE a.comune=? AND (
                   EXISTS (SELECT 1 FROM allegati x WHERE x.comune=a.comune
                           AND x.atto_id=a.id AND x.file IS NULL
                           AND COALESCE(x.errore, '') NOT LIKE 'saltato:%')
                   OR (a.numero_allegati > 0 AND NOT EXISTS (
                           SELECT 1 FROM allegati x WHERE x.comune=a.comune AND x.atto_id=a.id)))""",
            (slug,))}
        incompleti = [r for i, r in tutte.items() if i in da_completare]
    log.info("[%s] nuovi: %d, da completare: %d", slug, len(nuovi), len(incompleti))

    scaricati = 0
    for r in nuovi + incompleti:
        url_det = f"{host}/web/trasparenza/papca-ap/-/papca/display/{r['id']}?p_p_state=pop_up"
        try:
            meta, allegati = leggi_dettaglio(get(s, url_det).text)
        except Exception as e:  # un atto rotto non deve fermare gli altri
            log.error("[%s] dettaglio %s fallito: %s", slug, r["id"], e)
            continue

        anno_reg, _, num_reg = r["registro"].partition("/")
        primo = con.execute("SELECT primo_avvistamento FROM atti WHERE comune=? AND id=?",
                            (slug, r["id"])).fetchone()
        # Le scritture si raccolgono qui e si eseguono tutte insieme alla fine:
        # così il lock di scrittura su SQLite dura millisecondi, non i minuti
        # dei download (che bloccavano i thread degli altri comuni).
        riga_atto = (slug, r["id"],
             int(anno_reg) if anno_reg.isdigit() else None,
             int(num_reg) if num_reg.isdigit() else None,
             r["categoria"], r["sottocategoria"],
             int(meta["Anno atto"]) if meta.get("Anno atto", "").isdigit() else None,
             meta.get("Numero atto"),
             data_iso(meta.get("Data atto", "") or meta.get("Data documento", "")),
             meta.get("Proponente"), meta.get("Dirigente/Firmatario"),
             meta.get("Oggetto") or r["oggetto"],
             r["inizio"], r["fine"],
             int(meta["Numero allegati"]) if meta.get("Numero allegati", "").isdigit() else len(allegati),
             url_det, json.dumps(meta, ensure_ascii=False),
             primo[0] if primo else adesso, adesso)
        righe_allegati = []

        sotto = Path(slug) / (anno_reg or "senza-anno") / f"{num_reg or 'x'}_{r['id']}"
        gia_scaricati = {row[0] for row in con.execute(
            "SELECT chiave FROM allegati WHERE comune=? AND atto_id=? AND file IS NOT NULL",
            (slug, r["id"]))}
        gia_saltati = {row[0] for row in con.execute(
            "SELECT chiave FROM allegati WHERE comune=? AND atto_id=? AND errore LIKE 'saltato:%'",
            (slug, r["id"]))}
        for a in allegati:
            if a["chiave"] in gia_scaricati or a["chiave"] in gia_saltati:
                continue
            file_rel = byte = sha = errore = None
            # il documento principale (la determina/delibera) è piccolo e serve all'analisi:
            # resta accanto al database. Gli altri allegati, se richiesto, vanno a parte.
            principale = a["descrizione"].lower().startswith("documento principale")
            base = DATI if principale or ALLEGATI is None else ALLEGATI
            if scarica_allegati and a["url"]:
                try:
                    resp, contenuto = scarica(s, a["url"])
                    cartella = base / sotto
                    cartella.mkdir(parents=True, exist_ok=True)
                    dest = cartella / nome_file(resp, f"allegato_{a['chiave']}")
                    dest.write_bytes(contenuto)
                    file_rel = str(dest.relative_to(base))
                    if base is not DATI:
                        file_rel = PREFISSO_ARCHIVIO + file_rel
                    byte, sha = len(contenuto), hashlib.sha256(contenuto).hexdigest()
                    scaricati += 1
                except FileSaltato as e:
                    errore = str(e)
                    log.info("[%s] allegato %s dell'atto %s %s", slug, a["chiave"], r["id"], e)
                except Exception as e:
                    errore = str(e)[:300]
                    log.error("[%s] allegato %s dell'atto %s: %s", slug, a["chiave"], r["id"], e)
            righe_allegati.append(
                (slug, r["id"], a["chiave"], a["titolo"], a["descrizione"], a["mimetype"],
                 file_rel, byte, sha, adesso if file_rel else None, errore))

        with con:  # una transazione breve per atto: se si interrompe, non si perde nulla
            con.execute("INSERT OR REPLACE INTO atti VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", riga_atto)
            con.executemany("INSERT OR REPLACE INTO allegati VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            righe_allegati)

    con.commit()
    return {"in_lista": len(tutte), "nuovi": len(nuovi), "allegati": scaricati}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--comune", help="slug del comune (default: tutti)")
    p.add_argument("--senza-allegati", action="store_true", help="salva solo i metadati")
    p.add_argument("--dati", type=Path, help="cartella per database e documenti principali")
    p.add_argument("--allegati", type=Path,
                   help="cartella separata per gli allegati non principali (da archiviare)")
    p.add_argument("--log", type=Path, help="file di log")
    args = p.parse_args()

    global DATI, DB_PATH, ALLEGATI, LOG_PATH
    if args.dati:
        DATI = args.dati.resolve()
        DB_PATH = DATI / "albo.sqlite"
    if args.allegati:
        ALLEGATI = args.allegati.resolve()
    if args.log:
        LOG_PATH = args.log.resolve()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )

    comuni = json.loads((ROOT / "comuni.json").read_text(encoding="utf-8"))
    if args.comune:
        comuni = [c for c in comuni if c["slug"] == args.comune]
        if not comuni:
            log.error("comune %s non presente in comuni.json", args.comune)
            return 2

    apri_db().close()  # crea lo schema prima di partire con i thread

    def un_comune(c: dict) -> bool:
        """Fase 1: metadati di tutti gli atti (veloce, così nulla scade).
        Fase 2: allegati. Ogni fase è registrata in `esecuzioni`."""
        con = apri_db()
        ok = True
        fasi = [False] if args.senza_allegati else [False, True]
        for con_allegati in fasi:
            inizio = datetime.now().isoformat(timespec="seconds")
            try:
                esito = raccogli_comune(con, c, con_allegati)
                log.info("[%s] OK (%s): %d in lista, %d nuovi, %d allegati scaricati",
                         c["slug"], "allegati" if con_allegati else "metadati",
                         esito["in_lista"], esito["nuovi"], esito["allegati"])
                riga = (esito["in_lista"], esito["nuovi"], esito["allegati"], "ok", None)
            except Exception as e:
                ok = False
                log.exception("[%s] raccolta fallita", c["slug"])
                riga = (None, None, None, "errore", str(e)[:500])
            con.execute("INSERT INTO esecuzioni VALUES (?,?,?,?,?,?,?,?)",
                        (inizio, datetime.now().isoformat(timespec="seconds"), c["slug"], *riga))
            con.commit()
        con.close()
        return ok

    # comuni diversi = server diversi: in parallelo non si carica nessuno di più
    with ThreadPoolExecutor(max_workers=len(comuni)) as pool:
        risultati = list(pool.map(un_comune, comuni))
    # riporta tutto nel file principale: i -wal/-shm non vanno nel repo dati
    sqlite3.connect(DB_PATH).execute("PRAGMA journal_mode=DELETE").close()
    return 0 if all(risultati) else 1


if __name__ == "__main__":
    sys.exit(main())
