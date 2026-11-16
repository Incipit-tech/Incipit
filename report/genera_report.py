#!/usr/bin/env python3
"""Genera la pagina di esempio del report di analisi degli atti di un comune.

Legge albo.sqlite (repo privato incipit-dati) e un file di testi per il comune
(opportunità, prossimo passo, etichette curate: anche questi nel repo privato),
e scrive una pagina HTML autonoma: font e logo incorporati, nessun file esterno,
stampabile in A4.

La pagina contiene solo conteggi e oggetti-tipo degli atti, nessun dato personale.

Uso:
  python genera_report.py --dati ../../incipit-dati --comune magenta
      # legge <dati>/report/testi-magenta.json, scrive <dati>/report/magenta.html
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "raccolta-albo"))
from analisi import ALTRO, classifica  # noqa: E402

DESIGN = REPO / "design"
MESI = "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split()
TIPI_NOMI = {"DETERMINAZIONE": "Determinazioni", "DELIBERA DI GIUNTA": "Delibere di giunta",
             "ORDINANZE": "Ordinanze", "DELIBERA DI CONSIGLIO": "Delibere di consiglio",
             "PERMESSI DI COSTRUIRE": "Permessi di costruire", "DECRETI": "Decreti"}
MIN_RIPETIZIONI = 20  # un oggetto-tipo entra nella sezione "atti fotocopia" da qui in su


def num(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


def data_it(iso: str) -> str:
    a, m, g = iso[:10].split("-")
    return f"{int(g)} {MESI[int(m) - 1]} {a}"


# ---------- dati ----------

def carica(con: sqlite3.Connection, slug: str) -> list[dict]:
    """Un atto per registro: albo e storico danno id diversi allo stesso atto."""
    righe = con.execute(
        """SELECT anno_registro, numero_registro, MAX(sottocategoria), MAX(oggetto), MIN(inizio_pubblicazione)
           FROM (SELECT anno_registro, numero_registro, sottocategoria, oggetto, inizio_pubblicazione
                   FROM storico WHERE comune=:c
                 UNION ALL
                 SELECT anno_registro, numero_registro, sottocategoria, oggetto, inizio_pubblicazione
                   FROM atti WHERE comune=:c)
           WHERE anno_registro IS NOT NULL AND numero_registro IS NOT NULL
           GROUP BY anno_registro, numero_registro""", {"c": slug}).fetchall()
    return [{"anno": a, "tipo": (t or "").strip(), "oggetto": (o or "").strip(), "data": d or ""}
            for a, _, t, o, d in righe]


def chiave_modello(o: str) -> str:
    """Oggetto ridotto all'ossatura: senza numeri, date, importi, codici. Atti con la stessa
    ossatura sono lo stesso atto riscritto."""
    o = o.upper()
    o = re.sub(r"[\"'’“”`]", " ", o)
    o = re.sub(r"\b(ANNO|ANNI|PERIODO|MESE)\b.*?\d{4}", " ", o)
    o = re.sub(r"\d+([.,/]\d+)*", " ", o)
    o = re.sub(r"€|\bEURO\b|\bCIG\s*\w+|\bCUP\s*\w+", " ", o)
    o = re.sub(r"\b(" + "|".join(m.upper() for m in MESI) + r")\b", " ", o)
    o = re.sub(r"[^A-ZÀ-Ü ]", " ", o)
    return " ".join(o.split()[:9])


def etichetta_da_oggetto(o: str, maxlen: int = 80) -> str:
    o = re.sub(r"\s+", " ", o).strip(" .–-")
    o = o[:1].upper() + o[1:].lower()
    if len(o) > maxlen:
        o = o[:maxlen].rsplit(" ", 1)[0] + "…"
    return o


def calcola(con: sqlite3.Connection, slug: str, testi: dict) -> dict:
    atti = carica(con, slug)
    oggi = date.today()
    per_anno = Counter(a["anno"] for a in atti)
    anni = sorted(per_anno)
    pieni = [a for a in anni if a < oggi.year]
    media = sum(per_anno[a] for a in pieni) / len(pieni)

    tipi = Counter(a["tipo"] or "(senza tipo)" for a in atti)

    pratiche, pratiche_anno = Counter(), defaultdict(Counter)
    for a in atti:
        m, p = classifica(a["oggetto"])
        if ALTRO not in (m, p):
            pratiche[(m, p)] += 1
            pratiche_anno[(m, p)][a["anno"]] += 1

    modelli, esempi, modelli_anno = Counter(), defaultdict(list), defaultdict(Counter)
    for a in atti:
        k = chiave_modello(a["oggetto"])
        if len(k.split()) >= 4:  # ossature troppo corte non dicono niente
            modelli[k] += 1
            esempi[k].append(a["oggetto"])
            modelli_anno[k][a["anno"]] += 1
    ripetuti = [(k, c) for k, c in modelli.most_common() if c >= MIN_RIPETIZIONI]
    etichette = testi.get("etichette", {})
    escludi = set(testi.get("escludi_modelli", []))
    fotocopia = [{"etichetta": etichette.get(k) or etichetta_da_oggetto(min(esempi[k], key=len)),
                  "totale": c, "per_anno": modelli_anno[k], "chiave": k}
                 for k, c in ripetuti if k not in escludi]
    atti_in_modelli = sum(c for k, c in modelli.items() if c >= 10)

    uff = con.execute("SELECT proponente, oggetto FROM atti WHERE comune=? AND proponente IS NOT NULL",
                      (slug,)).fetchall()
    per_pratica = defaultdict(set)
    for u, o in uff:
        m, p = classifica(o)
        if ALTRO not in (m, p):
            per_pratica[(m, p)].add(u)
    condivise = sorted(((k, sorted(v)) for k, v in per_pratica.items() if len(v) > 1), key=lambda x: -len(x[1]))

    return {
        "comune": testi.get("nome", slug.title()), "totale": len(atti), "anni": anni, "per_anno": per_anno,
        "pieni": pieni, "media": media, "dal": min(a["data"] for a in atti if a["data"]),
        "al": max(a["data"] for a in atti if a["data"]), "tipi": tipi,
        "pratiche": pratiche, "pratiche_anno": pratiche_anno,
        "fotocopia": fotocopia, "n_modelli": len(ripetuti), "quota_modelli": atti_in_modelli / len(atti),
        "uffici_atti": len(uff), "uffici_n": len({u for u, _ in uff}), "condivise": condivise,
        "testi": testi,
    }


# ---------- grafica ----------

def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def css() -> str:
    tokens = (DESIGN / "tokens.css").read_text(encoding="utf-8")
    tokens = re.sub(r'url\("fonts/([^"]+)"\)',
                    lambda m: f'url("data:font/woff2;base64,{b64(DESIGN / "fonts" / m.group(1))}")', tokens)
    return tokens + """
:root { --barra: var(--blu-incipit); --barra-tenue: #B9C8FF; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --page:#0E1116; --surface:#181C23; --ink:#F3F4F6; --ink-muted:#9AA1AE; --link:#8DA6FF;
  --line:#2A2F38; --blu-tenue:#1C2A5C; --barra:#6F8DFF; --barra-tenue:#33417A; color-scheme: dark; } }
:root[data-theme="dark"] { --barra:#6F8DFF; --barra-tenue:#33417A; color-scheme: dark; }
:root[data-theme="dark"] .logo .chiaro { display:none; } :root[data-theme="dark"] .logo .scuro { display:block; }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink); font-family:var(--font-sans);
       font-size:15px; line-height:1.5; -webkit-font-smoothing:antialiased; }
.foglio { max-width: 860px; margin: 32px auto; background: var(--surface); border-radius: var(--radius-lg);
          padding: 48px 56px; box-shadow: 0 1px 2px rgba(14,17,22,.06); }
header.testa { display:flex; justify-content:space-between; align-items:flex-start; gap:24px;
               border-bottom:1px solid var(--line); padding-bottom:28px; margin-bottom:8px; }
header.testa .logo svg { height: 30px; width: auto; display:block; }
.logo .scuro { display:none; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .logo .chiaro { display:none; } :root:not([data-theme="light"]) .logo .scuro { display:block; } }
.eyebrow { color: var(--link); }
h1 { font-family:var(--font-display); font-weight:500; font-size:44px; line-height:1.1; letter-spacing:-.01em; margin:8px 0 10px; }
h2 { font-family:var(--font-display); font-weight:500; font-size:26px; line-height:1.2; margin:0 0 6px; }
.sotto { color: var(--ink-muted); margin:0; }
.marchio { text-align:right; color:var(--ink-muted); font-size:13px; }
section { padding: 32px 0 8px; border-bottom: 1px solid var(--line); }
section:last-of-type { border-bottom: 0; }
.numero { font-family:var(--font-sans); font-size:13px; font-weight:600; color:var(--ink-muted);
          letter-spacing:.12em; text-transform:uppercase; margin-bottom:4px; }
.messaggio { background: var(--blu-tenue); border-radius: var(--radius-md); padding: 14px 18px; margin: 18px 0 24px; }
.messaggio b { color: var(--ink); }
.kpi { display:grid; grid-template-columns: repeat(3, 1fr); gap:16px; margin: 24px 0 8px; }
.kpi > div { border:1px solid var(--line); border-radius: var(--radius-md); padding:16px 18px; }
.kpi .v { font-family: var(--font-display); font-size: 34px; font-weight:500; line-height:1.1; }
.kpi .l { color: var(--ink-muted); font-size: 13px; margin-top:4px; }
.due { display:grid; grid-template-columns: 1.1fr 1fr; gap: 32px; align-items:start; }
figure { margin: 8px 0 0; }
figcaption { font-size: 13px; color: var(--ink-muted); margin-bottom: 8px; font-weight:500; }
svg text { font-family: var(--font-sans); fill: var(--ink-muted); font-size: 12px; }
svg text.val { fill: var(--ink); font-weight: 500; }
.barra { fill: var(--barra); } .barra.parziale { fill: var(--barra-tenue); }
.barra:hover { opacity:.85; }
table { width:100%; border-collapse: collapse; font-size: 14px; margin: 8px 0 16px; }
th { text-align:left; font-weight:600; font-size:12px; color: var(--ink-muted); text-transform: uppercase;
     letter-spacing:.06em; padding: 8px 8px; border-bottom: 1px solid var(--line); }
td { padding: 9px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td .materia { font-weight: 500; } td .proc { color: var(--ink-muted); font-size: 13px; }
.spark { display:block; }
.nota { font-size: 13px; color: var(--ink-muted); }
ol.opp { padding-left: 0; list-style: none; counter-reset: o; margin: 16px 0; }
ol.opp li { counter-increment: o; display:grid; grid-template-columns: 36px 1fr; gap: 12px; margin-bottom: 16px; }
ol.opp li::before { content: counter(o); width:28px; height:28px; border-radius: 19%; background: var(--blu-incipit);
                    color: var(--on-blu); font-weight:600; display:flex; align-items:center; justify-content:center; font-size:14px; }
ol.opp b { display:block; }
.bozza { display:inline-block; font-size:12px; font-weight:600; color: var(--warning); background: var(--warning-tenue);
         border-radius: 999px; padding: 2px 10px; margin-left: 8px; vertical-align: middle; font-family: var(--font-sans); letter-spacing: 0; }
.passo { background: var(--blu-incipit); color: var(--on-blu); border-radius: var(--radius-md); padding: 20px 24px; margin: 20px 0 8px; }
.passo h3 { margin:0 0 6px; font-size:18px; }
footer { margin-top: 24px; font-size: 12px; color: var(--ink-muted); }
@media (max-width: 720px) { .foglio { margin:0; border-radius:0; padding-block: 28px;
    padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
  header.testa { flex-direction: column; } .marchio { text-align:left; }
  .kpi, .due { grid-template-columns: 1fr; } h1 { font-size: 34px; } }
@media print { body { background: white; } .foglio { box-shadow:none; margin:0; max-width:none; padding: 0; }
  section { break-inside: avoid; } @page { size: A4; margin: 16mm; } }
"""


def grafico_anni(d: dict) -> str:
    anni, w, h, base, top = d["anni"], 400, 190, 160, 26
    mx = max(d["per_anno"].values())
    passo = w / len(anni)
    bw = min(44, passo * 0.56)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Atti pubblicati per anno">']
    for i, a in enumerate(anni):
        v = d["per_anno"][a]
        bh = (base - top) * v / mx
        x = i * passo + (passo - bw) / 2
        y = base - bh
        parz = a not in d["pieni"]
        # barra con angoli arrotondati solo in alto, ancorata alla base
        r = 4
        path = (f"M{x:.1f},{base} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
                f"H{x + bw - r:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{base} Z")
        tit = f"{a}: {num(v)} atti" + (" (anno in corso)" if parz else "")
        out.append(f'<path class="barra{" parziale" if parz else ""}" d="{path}"><title>{tit}</title></path>')
        out.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle">{num(v)}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 18}" text-anchor="middle">{a}{"*" if parz else ""}</text>')
    out.append(f'<line x1="0" x2="{w}" y1="{base}" y2="{base}" stroke="var(--line)" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def grafico_tipi(d: dict) -> str:
    tot = d["totale"]
    top = d["tipi"].most_common(5)
    altri = tot - sum(c for _, c in top)
    voci = [(TIPI_NOMI.get(t, t.capitalize()), c) for t, c in top] + ([("Altri tipi", altri)] if altri else [])
    w, rh, lab = 360, 30, 150
    mx = max(c for _, c in voci)
    out = [f'<svg viewBox="0 0 {w} {rh * len(voci)}" width="100%" role="img" aria-label="Atti per tipo">']
    for i, (nome, c) in enumerate(voci):
        y = i * rh + 6
        bw = (w - lab - 60) * c / mx
        out.append(f'<text x="0" y="{y + 13}">{html.escape(nome)}</text>')
        out.append(f'<rect class="barra" x="{lab}" y="{y}" width="{max(bw, 2):.1f}" height="16" rx="4">'
                   f'<title>{html.escape(nome)}: {num(c)} atti</title></rect>')
        out.append(f'<text class="val" x="{lab + bw + 8:.1f}" y="{y + 13}">{100 * c / tot:.0f}%</text>')
    out.append("</svg>")
    return "".join(out)


def spark(per_anno: Counter, anni: list[int], pieni: list[int]) -> str:
    w, h, gap = 96, 26, 2
    bw = (w - gap * (len(anni) - 1)) / len(anni)
    mx = max([per_anno[a] for a in anni] + [1])
    out = [f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">']
    for i, a in enumerate(anni):
        v = per_anno[a]
        bh = max(2, (h - 2) * v / mx) if v else 0
        cls = "barra" + ("" if a in pieni else " parziale")
        out.append(f'<rect class="{cls}" x="{i * (bw + gap):.1f}" y="{h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2">'
                   f'<title>{a}: {v}</title></rect>')
    out.append("</svg>")
    return "".join(out)


def pagina(d: dict) -> str:
    t, e = d["testi"], html.escape
    def svg(nome: str) -> str:
        return re.sub(r"<\?xml[^>]*>", "", (DESIGN / "logo" / nome).read_text(encoding="utf-8"))
    logo = (f'<div class="chiaro">{svg("incipit-logo-orizzontale.svg")}</div>'
            f'<div class="scuro">{svg("incipit-logo-fondo-scuro.svg")}</div>')
    quota8 = sum(c for _, c in d["pratiche"].most_common(8)) / d["totale"]
    anni, pieni = d["anni"], d["pieni"]
    primo_pieno, ultimo_pieno = pieni[0], pieni[-1]
    crescita = 100 * (d["per_anno"][ultimo_pieno] / d["per_anno"][primo_pieno] - 1)
    det = d["tipi"].get("DETERMINAZIONE", 0) / d["totale"]

    righe_pratiche = []
    for (m, p), c in d["pratiche"].most_common(8):
        pa = d["pratiche_anno"][(m, p)]
        med = sum(pa[a] for a in pieni) / len(pieni)
        righe_pratiche.append(
            f'<tr><td><div class="materia">{e(m)}</div><div class="proc">{e(p)}</div></td>'
            f'<td>{spark(pa, anni, pieni)}</td><td class="n">{num(c)}</td><td class="n">circa {num(med)}</td></tr>')

    righe_foto = []
    for f in d["fotocopia"][:8]:
        med = sum(f["per_anno"][a] for a in pieni) / len(pieni)
        righe_foto.append(
            f'<tr><td>{e(f["etichetta"])}</td><td>{spark(f["per_anno"], anni, pieni)}</td>'
            f'<td class="n">{num(f["totale"])}</td><td class="n">circa {num(med)}</td></tr>')

    condivise = "".join(
        f'<tr><td><div class="materia">{e(m)}</div><div class="proc">{e(p)}</div></td>'
        f'<td>{e(", ".join(u))}</td></tr>' for (m, p), u in d["condivise"][:4])

    opp = "".join(f'<li><div><b>{e(o["titolo"])}</b>{e(o["testo"])}</div></li>' for o in t.get("opportunita", []))
    bozza = '<span class="bozza">Bozza da validare con l\'esperta</span>' if t.get("opportunita_bozza", True) else ""
    asterisco = (f'* {anni[-1]}: atti fino al {data_it(d["al"])}.' if anni[-1] not in pieni else "")

    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Report atti {e(d["comune"].replace("Comune di ", ""))}</title>
<style>{css()}</style></head>
<body><main class="foglio">
<header class="testa">
  <div>
    <div class="eyebrow">Analisi degli atti · pagina di esempio</div>
    <h1>{e(d["comune"])}</h1>
    <p class="sotto">{num(d["totale"])} atti pubblicati all'albo pretorio dal {data_it(d["dal"])} al {data_it(d["al"])}</p>
  </div>
  <div class="marchio"><div class="logo">{logo}</div><div style="margin-top:8px">Report Incipit</div></div>
</header>

<div class="kpi">
  <div><div class="v">{num(d["media"])}</div><div class="l">atti l'anno in media ({primo_pieno}–{ultimo_pieno})</div></div>
  <div><div class="v">{100 * d["quota_modelli"]:.0f}%</div><div class="l">degli atti ripete un oggetto già scritto almeno 10 volte</div></div>
  <div><div class="v">{d["n_modelli"]}</div><div class="l">atti tipo riscritti da capo {MIN_RIPETIZIONI} volte o più</div></div>
</div>

<section>
  <div class="numero">1 · Quanto produce il comune</div>
  <h2>Circa {num(d["media"])} atti l'anno, in crescita</h2>
  <p class="sotto">Dal {primo_pieno} al {ultimo_pieno} gli atti sono aumentati del {crescita:.0f}%. Tre su quattro sono determinazioni dirigenziali.</p>
  <div class="due">
    <figure><figcaption>Atti pubblicati per anno</figcaption>{grafico_anni(d)}<div class="nota">{e(asterisco)}</div></figure>
    <figure><figcaption>Per tipo di atto, {anni[0]}–{anni[-1]}</figcaption>{grafico_tipi(d)}</figure>
  </div>
</section>

<section>
  <div class="numero">2 · Le pratiche che si ripetono</div>
  <h2>Otto pratiche fanno da sole un atto su {round(1 / quota8)}</h2>
  <p class="sotto">Ogni atto è classificato per <b>materia</b> (di cosa si occupa) e <b>procedura</b> (che passaggio amministrativo è).
  Queste sono le combinazioni più frequenti.</p>
  <table><thead><tr><th>Pratica</th><th>{anni[0]}–{anni[-1]}</th><th class="n">Totale</th><th class="n">All'anno</th></tr></thead>
  <tbody>{"".join(righe_pratiche)}</tbody></table>
  <div class="messaggio"><b>Cosa significa.</b> Sono pratiche che il comune conosce bene e rifà ogni anno:
  sono le prime da trasformare in un percorso guidato, con le regole e le scadenze già pronte.</div>
</section>

<section>
  <div class="numero">3 · Gli atti riscritti da capo</div>
  <h2>Lo stesso atto, decine di volte</h2>
  <p class="sotto">Atti con lo stesso oggetto, a parte numeri, date e importi. Ognuno è stato preparato, controllato e firmato da zero.</p>
  <table><thead><tr><th>Atto tipo</th><th>{anni[0]}–{anni[-1]}</th><th class="n">Volte</th><th class="n">All'anno</th></tr></thead>
  <tbody>{"".join(righe_foto)}</tbody></table>
  <div class="messaggio"><b>Cosa significa.</b> Per questi atti un modello unico, con i controlli già fatti,
  riduce il tempo di preparazione e il rischio di errore.</div>
</section>

<section>
  <div class="numero">4 · Uffici che fanno cose simili</div>
  <h2>La stessa pratica, in uffici diversi</h2>
  <p class="sotto">Primo segnale, su {num(d["uffici_atti"])} atti recenti di {d["uffici_n"]} uffici (l'ufficio è indicato solo per gli atti in pubblicazione;
  il campione cresce ogni giorno).</p>
  <table><thead><tr><th>Pratica</th><th>Uffici che la svolgono</th></tr></thead><tbody>{condivise}</tbody></table>
  <div class="messaggio"><b>Cosa significa.</b> Quando più uffici fanno la stessa pratica, chi la fa per la prima volta
  può partire dal lavoro già fatto da un collega, invece che da una pagina bianca.</div>
</section>

<section>
  <div class="numero">5 · Opportunità</div>
  <h2>Tre interventi concreti per {e(d["comune"].replace("Comune di ", ""))}{bozza}</h2>
  <ol class="opp">{opp}</ol>
</section>

<section>
  <div class="numero">6 · Il prossimo passo</div>
  <div class="passo"><h3>{e(t.get("passo_titolo", ""))}</h3><div>{e(t.get("passo_testo", ""))}</div></div>
</section>

<footer>
  <b>Fonte e metodo.</b> Albo pretorio e storico atti del comune, dati pubblici raccolti in automatico.
  Classificazione automatica sull'oggetto degli atti (prima versione, a regole): i numeri sono indicativi.
  Nessun dato personale è usato o mostrato. Generato il {data_it(date.today().isoformat())} · Incipit
</footer>
</main></body></html>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dati", type=Path, required=True)
    p.add_argument("--comune", required=True)
    p.add_argument("--testi", type=Path, help="default: <dati>/report/testi-<comune>.json")
    p.add_argument("--uscita", type=Path, help="default: <dati>/report/<comune>.html")
    p.add_argument("--artifact", action="store_true", help="senza involucro <html>, per pubblicarla come pagina su claude.ai")
    p.add_argument("--elenca-modelli", action="store_true", help="stampa le chiavi degli atti ripetuti (per curare le etichette)")
    args = p.parse_args()
    testi_path = args.testi or args.dati / "report" / f"testi-{args.comune}.json"
    testi = json.loads(testi_path.read_text(encoding="utf-8")) if testi_path.exists() else {}
    d = calcola(sqlite3.connect(args.dati / "albo.sqlite"), args.comune, testi)
    if args.elenca_modelli:
        for f in d["fotocopia"][:15]:
            print(f'{f["totale"]:4d}  {f["chiave"]!r}  →  {f["etichetta"]}')
        return 0
    uscita = args.uscita or args.dati / "report" / f"{args.comune}.html"
    uscita.parent.mkdir(exist_ok=True)
    testo = pagina(d)
    if args.artifact:  # pagina per claude.ai: l'involucro <html>/<head>/<body> lo aggiunge la piattaforma
        testo = re.sub(r"<!doctype html>\s*<html[^>]*><head>.*?(?=<title>)", "", testo, flags=re.S | re.I)
        testo = re.sub(r"</head>\s*<body>|</body></html>", "", testo)
    uscita.write_text(testo, encoding="utf-8")
    print(f"scritto {uscita} ({uscita.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
