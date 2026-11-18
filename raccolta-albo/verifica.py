#!/usr/bin/env python3
"""Misura la classificazione (materia × procedura) su un campione verificato a mano.

Il campione è un CSV con le colonne `oggetto`, `materia`, `procedura` (le risposte
giuste) e sta nel repo privato dei dati, in `verifica/`. Serve a sapere quanto
sbagliano le regole di `analisi.py`, a misurare ogni miglioria, e a confrontare
sugli stessi atti i modelli AI candidati (decisione del 14/11: si sceglie a gennaio).

Uso:
  python verifica.py --campione ../incipit-dati/verifica/magenta-200.csv
  python verifica.py --campione ... --errori 15     # mostra anche i primi errori
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from analisi import classifica


def misura(righe: list[dict]) -> dict:
    giuste_m = giuste_p = giuste_entrambe = 0
    errori = []
    confusioni = Counter()
    for r in righe:
        m, p = classifica(r["oggetto"])
        ok_m, ok_p = m == r["materia"], p == r["procedura"]
        giuste_m += ok_m
        giuste_p += ok_p
        giuste_entrambe += ok_m and ok_p
        if not ok_m:
            confusioni[(r["materia"], m)] += 1
        if not (ok_m and ok_p):
            errori.append((r["oggetto"][:90], (r["materia"], r["procedura"]), (m, p)))
    n = len(righe)
    return {"n": n, "materia": giuste_m / n, "procedura": giuste_p / n, "entrambe": giuste_entrambe / n,
            "errori": errori, "confusioni": confusioni}


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--campione", type=Path, required=True)
    a.add_argument("--errori", type=int, default=0)
    args = a.parse_args()
    with args.campione.open(encoding="utf-8") as f:
        righe = list(csv.DictReader(f))
    r = misura(righe)
    print(f"Campione: {r['n']} atti ({args.campione.name})")
    print(f"Materia giusta:   {r['materia']:.0%}")
    print(f"Procedura giusta: {r['procedura']:.0%}")
    print(f"Entrambe giuste:  {r['entrambe']:.0%}")
    print("Materie scambiate più spesso (giusta → regole):")
    for (g, s), c in r["confusioni"].most_common(6):
        print(f"  {c:3d}  {g} → {s}")
    for o, g, s in r["errori"][: args.errori]:
        print(f"- {o}\n    giusta: {g[0]} / {g[1]}\n    regole: {s[0]} / {s[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
