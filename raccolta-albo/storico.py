#!/usr/bin/env python3
"""Raccolta dello "Storico atti" (piattaforma JCityGov): l'elenco degli atti
non più in pubblicazione, che i comuni tengono online per anni.

Dello storico il portale mostra solo l'elenco (registro, tipo, oggetto, date):
niente dettaglio, niente allegati, niente ufficio proponente. È però la base
per il report di analisi: dice quali pratiche il comune ripete da anni.

La lista è ordinata dal più recente. Alla prima esecuzione si scorre tutto
(migliaia di righe, ~10 minuti per comune); poi ci si ferma alla prima pagina
già interamente nota.

Uso:
  python storico.py --dati ../incipit-dati            # tutti i comuni
  python storico.py --dati ../incipit-dati --comune magenta --completo
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import raccolta as r  # stessa sessione HTTP, stesso parser della lista

log = logging.getLogger("storico")

SCHEMA = """
CREATE TABLE IF NOT EXISTS storico (
    comune          TEXT NOT NULL,
    id              TEXT NOT NULL,
    anno_registro   INTEGER,
    numero_registro INTEGER,
    categoria       TEXT,
    sottocategoria  TEXT,
    oggetto         TEXT,
    inizio_pubblicazione TEXT,
    fine_pubblicazione   TEXT,
    primo_avvistamento   TEXT NOT NULL,
    PRIMARY KEY (comune, id)
);
CREATE INDEX IF NOT EXISTS storico_anno ON storico(comune, anno_registro);
"""


def trova_url_storico(s, host: str) -> tuple[str, str]:
    """URL della prima pagina e percorso base (papca-sa o papca-ap, varia per comune)."""
    html = r.get(s, host + "/web/trasparenza/albo-pretorio").text
    m = re.search(
        r'data-resource="Storico atti"[^>]*?data-mainurl="([^"]+)"[^>]*?data-idmenuitem="(\d+)"', html)
    if not m:
        raise RuntimeError("voce di menu 'Storico atti' non trovata")
    return f"{host}{m.group(1)}/{m.group(2)}", m.group(1).split("/-/")[0]


def raccogli_storico(db: Path, comune: dict, completo: bool) -> dict:
    slug, host = comune["slug"], comune["host"]
    con = sqlite3.connect(db, timeout=60)
    con.executescript(SCHEMA)
    noti = {row[0] for row in con.execute("SELECT id FROM storico WHERE comune=?", (slug,))}
    s = r.nuova_sessione()
    adesso = datetime.now().isoformat(timespec="seconds")

    url, base = trova_url_storico(s, host)
    url_next = (f"{host}{base}?p_p_id={r.PORTLET}&p_p_lifecycle=0&p_p_state=pop_up"
                f"&p_p_mode=view&_{r.PORTLET}_paginationAction=NEXT&_{r.PORTLET}_action=mostraLista")
    log.info("[%s] storico: %s", slug, url)

    html, pagine, nuovi, visti = r.get(s, url).text, 0, 0, set()
    while pagine < 2000:  # protezione: ~40.000 atti
        pagine += 1
        righe, ha_successiva = r.leggi_lista(html)
        righe = [x for x in righe if x["id"] not in visti]
        if not righe:
            break
        visti.update(x["id"] for x in righe)
        da_inserire = [x for x in righe if x["id"] not in noti]
        with con:
            for x in da_inserire:
                anno, _, num = x["registro"].partition("/")
                con.execute(
                    "INSERT OR IGNORE INTO storico VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (slug, x["id"], int(anno) if anno.isdigit() else None,
                     int(num) if num.isdigit() else None, x["categoria"], x["sottocategoria"],
                     x["oggetto"], x["inizio"], x["fine"], adesso))
        nuovi += len(da_inserire)
        if pagine % 50 == 0:
            log.info("[%s] pagina %d, nuovi finora %d", slug, pagine, nuovi)
        if not completo and not da_inserire:
            break  # pagina tutta già nota: il resto è più vecchio ed è già nel DB
        if not ha_successiva:
            break
        html = r.get(s, url_next).text
    totale = con.execute("SELECT COUNT(*) FROM storico WHERE comune=?", (slug,)).fetchone()[0]
    con.close()
    return {"pagine": pagine, "nuovi": nuovi, "totale": totale}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dati", type=Path, required=True, help="cartella con albo.sqlite")
    p.add_argument("--comune", help="slug del comune (default: tutti)")
    p.add_argument("--completo", action="store_true", help="scorri tutte le pagine anche se già note")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    comuni = json.loads((r.ROOT / "comuni.json").read_text(encoding="utf-8"))
    if args.comune:
        comuni = [c for c in comuni if c["slug"] == args.comune]
    db = args.dati.resolve() / "albo.sqlite"
    sqlite3.connect(db).execute("PRAGMA journal_mode=WAL").close()

    def uno(c):
        try:
            e = raccogli_storico(db, c, args.completo)
            log.info("[%s] OK storico: %d pagine, %d nuovi, %d in totale",
                     c["slug"], e["pagine"], e["nuovi"], e["totale"])
            return True
        except Exception:
            log.exception("[%s] storico fallito", c["slug"])
            return False

    with ThreadPoolExecutor(max_workers=len(comuni) or 1) as pool:
        ok = list(pool.map(uno, comuni))
    # il DB va nel repo: niente file -wal/-shm lasciati in giro
    sqlite3.connect(db).execute("PRAGMA journal_mode=DELETE").close()
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
