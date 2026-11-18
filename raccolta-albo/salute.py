#!/usr/bin/env python3
"""Controllo di salute della raccolta: fallisce (exit 1) se qualcosa non va.

Gira alla fine di ogni esecuzione su GitHub Actions. Un'esecuzione fallita fa
partire l'email di GitHub, quindi nessuno deve più guardare il tab Actions ogni
giorno: se non arriva l'email, la raccolta va.

Per ogni comune controlla:
  - che l'ultima esecuzione sia andata a buon fine;
  - che arrivino atti nuovi: nessun atto nuovo da più di GIORNI_SENZA_ATTI giorni
    lavorativi vuol dire, quasi sempre, che il portale è cambiato e lo scraper non
    legge più la lista (un comune pubblica ogni giorno lavorativo);
  - che lo storico sia stato letto almeno una volta.

Scrive il riepilogo anche in <dati>/log/salute.md.

Uso:
  python salute.py --dati ../incipit-dati
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GIORNI_SENZA_ATTI = 3  # giorni lavorativi


def giorni_lavorativi_tra(da: date, a: date) -> int:
    """Giorni da lunedì a venerdì strettamente dopo `da` e fino ad `a` compreso."""
    n, g = 0, da
    while g < a:
        g += timedelta(days=1)
        if g.weekday() < 5:
            n += 1
    return n


def controlla(con: sqlite3.Connection, slug: str, oggi: date) -> list[str]:
    problemi = []
    ultima = con.execute(
        "SELECT esito, messaggio, fine FROM esecuzioni WHERE comune=? ORDER BY fine DESC LIMIT 1", (slug,)
    ).fetchone()
    if not ultima:
        problemi.append("nessuna esecuzione registrata")
    elif ultima[0] != "ok":
        problemi.append(f"ultima esecuzione ({ultima[2]}) con esito '{ultima[0]}': {ultima[1] or ''}".strip())

    ultimo_nuovo = con.execute("SELECT MAX(primo_avvistamento) FROM atti WHERE comune=?", (slug,)).fetchone()[0]
    if not ultimo_nuovo:
        problemi.append("nessun atto raccolto")
    else:
        giorni = giorni_lavorativi_tra(date.fromisoformat(ultimo_nuovo[:10]), oggi)
        if giorni > GIORNI_SENZA_ATTI:
            problemi.append(f"nessun atto nuovo da {giorni} giorni lavorativi (ultimo il {ultimo_nuovo[:10]})")

    try:
        storico = con.execute("SELECT COUNT(*) FROM storico WHERE comune=?", (slug,)).fetchone()[0]
    except sqlite3.OperationalError:
        storico = 0
    if storico == 0:
        problemi.append("storico atti vuoto")
    return problemi


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dati", type=Path, required=True)
    p.add_argument("--oggi", type=date.fromisoformat, default=date.today(), help="per le prove")
    args = p.parse_args()
    comuni = json.loads((ROOT / "comuni.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(args.dati / "albo.sqlite")

    righe, falliti = [], 0
    for c in comuni:
        problemi = controlla(con, c["slug"], args.oggi)
        falliti += bool(problemi)
        stato = "ok" if not problemi else "PROBLEMA: " + "; ".join(problemi)
        righe.append(f"| {c['nome']} | {stato} |")
        print(f"{c['slug']}: {stato}")

    log = args.dati / "log"
    log.mkdir(exist_ok=True)
    (log / "salute.md").write_text(
        f"# Salute della raccolta\n\nControllo del {datetime.now().isoformat(timespec='minutes')}.\n\n"
        "| Comune | Stato |\n|---|---|\n" + "\n".join(righe) + "\n", encoding="utf-8")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
