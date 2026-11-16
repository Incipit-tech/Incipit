#!/usr/bin/env python3
"""Classificazione con AI (Claude Haiku) degli atti di un comune: materia × procedura.

Affianca le regole di `analisi.py`, che riconoscono circa due atti su tre e
sbagliano sulle parole ambigue. Le categorie sono le stesse delle regole, così
i due risultati si possono confrontare.

Tre passi, separati perché l'API Batch risponde entro qualche ora (a metà prezzo):
  python classifica_ai.py --dati ../incipit-dati --comune magenta            # 1. stima: nessuna chiamata, nessun costo
  python classifica_ai.py --dati ../incipit-dati --comune magenta --invia    # 2. invia il batch (A PAGAMENTO)
  python classifica_ai.py --dati ../incipit-dati --comune magenta --scarica  # 3. salva i risultati nel DB

Si manda all'AI solo l'oggetto dell'atto (testo pubblico dell'albo), mai PDF
o allegati. Credenziali: variabile ANTHROPIC_API_KEY (o profilo `ant auth login`).
Risultati nella tabella `classificazione_ai` di albo.sqlite.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from analisi import ALTRO, MATERIE, PROCEDURE

MODELLO = "claude-haiku-5-5"
PREZZO_INPUT, PREZZO_OUTPUT = 0.10, 0.50  # $ per milione di token, prezzo pieno
SCONTO_BATCH = 0.5
PER_RICHIESTA = 25  # oggetti classificati in una sola richiesta

NOMI_MATERIE = [n for n, _ in MATERIE] + [ALTRO]
NOMI_PROCEDURE = [n for n, _ in PROCEDURE] + [ALTRO]

SISTEMA = f"""Sei un esperto di amministrazione dei comuni italiani.
Ricevi un elenco numerato di oggetti di atti pubblicati all'albo pretorio di un comune
(determinazioni, delibere, ordinanze, decreti). Per ciascuno indica:
- materia: di cosa si occupa l'atto;
- procedura: che tipo di passaggio amministrativo è.

Materie ammesse: {"; ".join(NOMI_MATERIE)}.
Procedure ammesse: {"; ".join(NOMI_PROCEDURE)}.

Criteri:
- Scegli in base alla sostanza, non alle parole: "contributo costo di costruzione" è edilizia,
  non un contributo a terzi; un contributo a una persona in difficoltà è servizi sociali.
- "Patrocinio / contributo a terzi" vale solo quando il comune dà denaro o patrocinio ad altri.
- Se l'atto ha più passaggi (es. "approvazione e impegno di spesa"), scegli quello che produce
  l'effetto principale.
- Usa "{ALTRO}" solo se nessuna categoria è adatta.
Rispondi per ogni numero dell'elenco, nello stesso ordine."""

SCHEMA = {
    "type": "object",
    "properties": {
        "atti": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "materia": {"type": "string", "enum": NOMI_MATERIE},
                    "procedura": {"type": "string", "enum": NOMI_PROCEDURE},
                },
                "required": ["n", "materia", "procedura"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["atti"],
    "additionalProperties": False,
}

TABELLA = """CREATE TABLE IF NOT EXISTS classificazione_ai (
    comune TEXT NOT NULL, anno_registro INTEGER NOT NULL, numero_registro INTEGER NOT NULL,
    materia TEXT, procedura TEXT, modello TEXT, batch_id TEXT, data TEXT,
    PRIMARY KEY (comune, anno_registro, numero_registro))"""


def atti_da_classificare(con: sqlite3.Connection, slug: str) -> list[tuple[int, int, str]]:
    """Un atto per registro (albo e storico danno id diversi allo stesso atto), non ancora classificato."""
    con.execute(TABELLA)
    return con.execute(
        """SELECT anno_registro, numero_registro, MAX(oggetto) FROM (
               SELECT anno_registro, numero_registro, oggetto FROM storico WHERE comune=:c
               UNION ALL SELECT anno_registro, numero_registro, oggetto FROM atti WHERE comune=:c)
           WHERE anno_registro IS NOT NULL AND numero_registro IS NOT NULL AND COALESCE(oggetto,'') != ''
             AND (anno_registro, numero_registro) NOT IN
                 (SELECT anno_registro, numero_registro FROM classificazione_ai WHERE comune=:c)
           GROUP BY anno_registro, numero_registro ORDER BY anno_registro, numero_registro""",
        {"c": slug}).fetchall()


def richieste(slug: str, atti: list) -> list[dict]:
    out = []
    for i in range(0, len(atti), PER_RICHIESTA):
        gruppo = atti[i:i + PER_RICHIESTA]
        elenco = "\n".join(f"{k}. {ogg.strip()[:400]}" for k, (_, _, ogg) in enumerate(gruppo, 1))
        out.append({
            # custom_id: comune + primo e ultimo registro del gruppo, per ricollegare le risposte
            "custom_id": f"{slug}-{i // PER_RICHIESTA:05d}",
            "registri": [(a, n) for a, n, _ in gruppo],
            "params": {
                "model": MODELLO,
                "max_tokens": 4096,
                "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                "system": [{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": elenco}],
            },
        })
    return out


def stima(rq: list[dict]) -> tuple[int, int, float]:
    """Stima grossolana (circa 3,5 caratteri per token in italiano), senza chiamare l'API."""
    tin = sum((len(SISTEMA) + len(r["params"]["messages"][0]["content"])) / 3.5 for r in rq)
    tout = sum(len(r["registri"]) * 30 for r in rq)  # ~30 token per riga di JSON
    costo = (tin * PREZZO_INPUT + tout * PREZZO_OUTPUT) / 1e6 * SCONTO_BATCH
    return int(tin), int(tout), costo


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dati", type=Path, required=True)
    p.add_argument("--comune", required=True)
    p.add_argument("--limite", type=int, help="classifica solo i primi N atti (per una prova)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--invia", action="store_true", help="invia il batch all'API (a pagamento)")
    g.add_argument("--scarica", action="store_true", help="scarica i risultati del batch inviato")
    args = p.parse_args()

    con = sqlite3.connect(args.dati / "albo.sqlite")
    stato = args.dati / "analisi" / f"batch-{args.comune}.json"

    if args.scarica:
        return scarica(con, args.comune, stato)

    atti = atti_da_classificare(con, args.comune)[: args.limite]
    rq = richieste(args.comune, atti)
    tin, tout, costo = stima(rq)
    print(f"{args.comune}: {len(atti)} atti da classificare in {len(rq)} richieste, modello {MODELLO}")
    print(f"stima: ~{tin:,} token in ingresso, ~{tout:,} in uscita, circa {costo:.2f} $ con lo sconto batch")
    if not args.invia:
        print("nessuna chiamata fatta. Per inviare: aggiungi --invia")
        return 0
    if not rq:
        return 0

    import anthropic  # solo qui: la stima non richiede il pacchetto
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[{"custom_id": r["custom_id"], "params": r["params"]} for r in rq])
    stato.parent.mkdir(exist_ok=True)
    stato.write_text(json.dumps({
        "batch_id": batch.id, "inviato": datetime.now().isoformat(timespec="seconds"),
        "gruppi": {r["custom_id"]: r["registri"] for r in rq}}), encoding="utf-8")
    print(f"batch inviato: {batch.id}. Tra qualche ora: --scarica")
    return 0


def scarica(con: sqlite3.Connection, slug: str, stato: Path) -> int:
    import anthropic
    info = json.loads(stato.read_text(encoding="utf-8"))
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(info["batch_id"])
    if batch.processing_status != "ended":
        print(f"batch {batch.id} ancora in corso: {batch.request_counts.processing} richieste da fare")
        return 1
    adesso, salvati, problemi = datetime.now().isoformat(timespec="seconds"), 0, 0
    with con:
        con.execute(TABELLA)
        for res in client.messages.batches.results(batch.id):
            registri = info["gruppi"].get(res.custom_id)
            if res.result.type != "succeeded" or not registri:
                problemi += 1
                continue
            msg = res.result.message
            testo = next((b.text for b in msg.content if b.type == "text"), "")
            if msg.stop_reason != "end_turn" or not testo:
                problemi += 1
                continue
            for riga in json.loads(testo)["atti"]:
                k = riga["n"] - 1
                if 0 <= k < len(registri):
                    anno, num = registri[k]
                    con.execute("INSERT OR REPLACE INTO classificazione_ai VALUES (?,?,?,?,?,?,?,?)",
                                (slug, anno, num, riga["materia"], riga["procedura"], MODELLO, batch.id, adesso))
                    salvati += 1
    con.execute("PRAGMA journal_mode=DELETE")
    print(f"salvate {salvati} classificazioni; richieste non riuscite: {problemi} (rilanciando --invia si rifanno solo quelle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
