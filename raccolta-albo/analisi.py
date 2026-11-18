#!/usr/bin/env python3
"""Prima analisi degli atti di un comune, base del report di Incipit.

Legge albo.sqlite (tabelle `storico` e `atti`) e scrive un riepilogo in Markdown:
  - quanti atti produce il comune ogni anno e di che tipo;
  - quali pratiche si ripetono: materia × procedura (classificazione a regole sull'oggetto);
  - quali uffici le producono (solo per gli atti in pubblicazione, gli unici con
    l'ufficio proponente).

Il riepilogo contiene solo conteggi, nessun dato personale, ma va comunque nel
repo privato insieme ai dati. La classificazione a regole è un primo passo:
sarà sostituita o affiancata da una classificazione con AI.

Uso:
  python analisi.py --dati ../incipit-dati --comune magenta
  python analisi.py --dati ../incipit-dati            # tutti, in <dati>/analisi/
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Ogni atto ha due dimensioni, riconosciute dall'oggetto con regole semplici:
#   MATERIA   = di cosa si occupa (cultura, scuola, lavori...)
#   PROCEDURA = che tipo di passaggio amministrativo è (affidamento, impegno...)
# La coppia materia × procedura è la "pratica" che il report cerca di trovare ripetuta.
# Vince la prima regola che corrisponde: le più specifiche stanno in alto.
# Le regole sono misurate su un campione verificato a mano (verifica.py): ogni modifica
# va rimisurata lì. Attenzione alle parole dentro altre parole: "sport" in "trasporto",
# "mozione" in "promozionale", "accertamento" in "riaccertamento".
MATERIE: list[tuple[str, str]] = [
    ("Manifestazioni di sorte / premi", r"concors\w* a premi|lotteri|estrazion\w* a sorte|tombol|manifestazion\w* (di|a) sorte"),
    ("Farmacie comunali", r"farmaci[ae]"),
    ("Affari legali e contenzioso", r"incarico legale|costituzion\w* in giudizio|\bavvocat|contenzios|giudice di pace|\btribunale|\bricorso\b|decreto ingiuntiv"),
    ("Servizi demografici ed elettorali", r"elettoral|\belezion|referendum|censimento|stato civile|\banagrafe (?!unica|per la stazione)|cittadinanza|leva militare"),
    # "assunzione impegno di spesa" è contabilità, "manutenzione straordinaria" sono lavori: non personale
    ("Personale", r"\bassunzion\w*\b(?!\s+(?:dell?'?\s*|di\s+)?(?:impegn|spes|accertament|obbligazion|onere|oneri))|concorso pubblic|selezion\w* pubblic|\bdipendent|personale\b|progression|mobilit\w* (volontaria|estern)|\bferie\b|(lavoro|ore|prestazion\w*) straordinari|retribuz|salario accessorio|indennit\w* contrattual|permessi sindacal|dimission|pensionament|aspettativa|comitato unico di garanzia|dotazione organica|micro-?organizzazion|somministrazione di lavoro|lavoro temporaneo|dispositivi di protezione individual|contributiv\w* inps|buoni pasto|\bart\.? ?(90|110)\b|area degli istruttori|formazione (del personale|degli operatori)"),
    ("Bilancio e contabilità", r"bilancio|residui|rendiconto|\bpeg\b|piano della performance|\bdup\b|documento unico di programmazione|variazion\w* di cassa|esercizio finanziario|programma (biennale|triennale) degli acquisti|entrate vincolate|economato|contabilit|assistenza fiscale|tesoreria"),
    ("Tributi ed entrate", r"\btari\b|tassa sui rifiuti|\bimu\b|\btribut|ruolo coattiv|riscossion|canone unico|entrat\w* tributari"),
    ("Edilizia e urbanistica", r"permesso di costruire|sanatoria|\bscia\b|piano (attuativo|di governo)|\bpgt\b|urbanistic|\bedilizi|oneri di urbanizzazione|costo di costruzione|\bp\.?e\.?e\.?p\b|\bp\.?i\.?p\b"),
    ("Polizia locale e sicurezza", r"polizia locale|sanzion|verbal\w* (di accertamento|codice)|videosorveglian|sicurezza urbana|protezione civile|infrazion|contingibile e urgente.{0,60}(tranquillit|sicurezza|quiete)"),
    ("Strade, viabilità e traffico", r"viabilit|traffico|divieto (temporaneo )?di (sosta|transito|circolazione)|divieto di sosta|circolazion|senso unico|\bstrad|marciapied|asfalt|segnaletic|parchegg|\bstall[oi]\b|ciclopedonal|passerella|bituminos|trasporto pubblico locale(?! finalizzato)"),
    ("Ambiente e rifiuti", r"rifiut|ambient|ecologic|igiene urbana|raccolta differenziata|inquinament|zanzar|insetti|impianti termici|impianto termico|rischio idraulico|\bcer\b|comunit\w* energetic"),
    ("Edifici e manutenzioni", r"manutenzion|immobil|edifici|riqualificazion|impiant|illuminazion|cimiter|verde (pubblic|generale)|giardin|\blavori\b|risanament|adeguamento strutturale|bonifica|imbiancatur|perdita idrica|caldai|utenz|calore|gas naturale|energia elettrica|automezz|fotovoltaic|partenariato pubblico privato|vigili del fuoco"),
    ("Cultura, biblioteca, eventi", r"bibliotec|cultur|museo|teatro|spettacol|mostra|rassegna|festival|manifestazion\w* (cultural|natal|estiv)|event[io]\b|anniversario|battaglia di magenta|giorno del ricordo|giorno della memoria|celebrazion|cerimoni|\bband[ae]\b|fanfara|opuscolo|\bfiera\b"),
    ("Sport e tempo libero", r"\bsport(?!ell)|palestr|piscin|stadio|campo da|\ba\.?s\.?d\b"),
    ("Scuola e servizi educativi", r"scuol|scolastic|refezion|mensa|nido|centri estivi|pre ?e post ?scuola|istruzion|\bdva\b|\bics\b|alunn"),
    ("Servizi sociali e casa", r"assistenz\w* (domiciliar|social|economic|educativ)|teleassistenz|disabil|anzian|minor[ei]\b|\brsa\b|ricovero|servizi sociali|servizi alla persona|ambito (territoriale|del magentino)|\bsap\b|abitativ|alloggi|famigli|sostegno al reddito|caregiver|fragilit|assistenti familiari|l\.? ?112/16|misura complementare|maternit|care leavers|violenza di genere|stranieri|integrazione lavorativa|comunit\w* terapeutic|povert|accoglienza"),
    ("Commercio e attività produttive", r"commerci|\bduc\b|distretto urbano|attivit\w* produttiv|\bsuap\b|mercat|esercizi\w* pubblic|artigian"),
    ("Informatica e digitale", r"informatic|software|digital|hardware|licenz\w* d'uso|licenze|autocad|telefonia|connettivit|pagopa|\bpnrr\b.{0,40}(digital|cloud)|cloud"),
    ("Organi istituzionali", r"consiglio comunale|consiliar|regolament|statuto|\bmozion|interrogazion|surroga|giunta comunale.{0,20}(nomina|composizion)|\brasa\b|stazione appaltante"),
]
PROCEDURE: list[tuple[str, str]] = [
    # specifiche prima: "assunzione ... mediante graduatoria" è un'assunzione, non una graduatoria
    ("Nomina / incarico", r"\bassunzion\w* a tempo|conferimento (dell')?incarico|incarico (legale|professionale|a tempo)|\bnomina\b"),
    ("Liquidazione / pagamento", r"liquidazion|rimbors|pagament(?!o (retta|delle rette|di rette))|contributo associativo|intervento sostitutivo"),
    ("Patrocinio / contributo a terzi", r"patrocini|\bcontribut[oi]\b|sussidi|buoni? (spesa|scuola)|bonus|erogazion|assegno di maternit|misure a sostegno"),
    ("Gara / procedura negoziata", r"procedura (aperta|negoziata)|\bgara\b|aggiudicazion|partenariato pubblico privato|contratto per l.affidamento della gestione"),
    ("Affidamento diretto", r"affidament\w* dirett|art\.? ?50,? comma 1|ordine diretto|trattativa diretta|determin\w* a contra(rre|ttare)|decisione a contrarre|\baffidamento (del|dell|di|della|alla|ai)\b|\bfornitura\b|adesione (alla )?campagna|campagna promozionale|adesione della farmacia|proroga (del )?servizio|\bacquisto\b|manutenzione automezz|somministrazione di lavoro"),
    ("Graduatoria / bando / avviso", r"graduatori|\bbando\b|avviso pubblic|avviso di mobilit|mobilit\w* estern|manifestazion\w* di interesse|progressione tra le aree|procedura valutativa|selezione pubblic"),
    ("Convenzione / accordo", r"convenzion(?!e consip)|protocollo d'intesa|accordo (di|tra)|co-?progettazion"),
    ("Concessione / autorizzazione", r"concession|autorizzazion|occupazion\w* (di )?suolo|comodato|locazion|permesso|rateizzazion|aspettativa|subappalt|iscrizione\b.{0,40}registro"),
    ("Ordinanza", r"ordinanz|divieto|disciplina temporanea|\bstall[oi]\b|esercizio degli impianti termici|prevenzione ed il controllo"),
    ("Nomina / incarico", r"nomin|incaric|designazion|elezione dei componenti|componenti del comitato"),
    ("Impegno / accertamento di spesa", r"impegn|\baccertament|prelevament"),
    ("Approvazione atti e progetti", r"approvazion|presa d.atto|adozion|\bpiano\b|programma|bilancio|rendiconto|verifica|atto di indirizzo|schema|riaccertamento|variazion|costituzione (del fondo|in giudizio)|iscrizione|aggiornamento|esame progetto|provvediment|ricognizione|determinazione e ripartizione|organizzazione di una cerimonia"),
]
_MAT = [(n, re.compile(rx, re.IGNORECASE)) for n, rx in MATERIE]
_PRO = [(n, re.compile(rx, re.IGNORECASE)) for n, rx in PROCEDURE]
ALTRO = "Altro"


def classifica(oggetto: str) -> tuple[str, str]:
    o = oggetto or ""
    mat = next((n for n, rx in _MAT if rx.search(o)), ALTRO)
    pro = next((n for n, rx in _PRO if rx.search(o)), ALTRO)
    return mat, pro


def tabella(intestazioni: list[str], righe: list[list]) -> str:
    out = ["| " + " | ".join(intestazioni) + " |", "|" + "---|" * len(intestazioni)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in righe]
    return "\n".join(out)


def analizza(con: sqlite3.Connection, slug: str, nome: str) -> str:
    righe = con.execute(
        """SELECT anno_registro, COALESCE(sottocategoria,''), COALESCE(oggetto,'') FROM storico WHERE comune=?
           UNION ALL
           SELECT anno_registro, COALESCE(sottocategoria,''), COALESCE(oggetto,'') FROM atti a WHERE comune=?
             -- lo stesso atto ha id diversi in albo e storico: si confronta il registro
             AND NOT EXISTS (SELECT 1 FROM storico s WHERE s.comune=a.comune
                             AND s.anno_registro=a.anno_registro AND s.numero_registro=a.numero_registro)""",
        (slug, slug)).fetchall()
    if not righe:
        return f"# {nome}\n\nNessun dato.\n"

    per_anno = Counter(a for a, _, _ in righe if a)
    anni = sorted(per_anno)
    anno_corrente = date.today().year
    anni_pieni = [a for a in anni if a < anno_corrente and a != anni[0]]  # il primo anno può essere parziale
    media = round(sum(per_anno[a] for a in anni_pieni) / len(anni_pieni)) if anni_pieni else None

    mat, pro, coppie = Counter(), Counter(), Counter()
    coppie_anno = defaultdict(Counter)
    tipi = Counter()
    for a, tipo, ogg in righe:
        m, p = classifica(ogg)
        mat[m] += 1
        pro[p] += 1
        if m != ALTRO and p != ALTRO:
            coppie[(m, p)] += 1
            coppie_anno[(m, p)][a] += 1
        tipi[tipo.strip() or "(senza tipo)"] += 1
    n = len(righe)
    pct = lambda c: f"{100 * c / n:.0f}%"

    out = [f"# {nome}: prima analisi degli atti",
           f"_Generata il {date.today().isoformat()} da `analisi.py`. Fonte: albo pretorio e storico atti "
           f"({anni[0]}–{anni[-1]}), {n:,} atti._".replace(",", "."), ""]

    out += ["## Volumi", tabella(["Anno", "Atti pubblicati"], [[a, per_anno[a]] for a in anni]), ""]
    if media:
        out += [f"**Media sugli anni completi: circa {media:,} atti l'anno.**".replace(",", "."), ""]

    out += ["## Tipi di atto (top 10)",
            tabella(["Tipo", "Atti", "%"], [[t, c, pct(c)] for t, c in tipi.most_common(10)]), ""]
    out += ["## Materie", f"Riconosciute dall'oggetto: {pct(n - mat[ALTRO])} degli atti.", "",
            tabella(["Materia", "Atti", "%"], [[m, c, pct(c)] for m, c in mat.most_common()]), ""]
    out += ["## Procedure", f"Riconosciute dall'oggetto: {pct(n - pro[ALTRO])} degli atti.", "",
            tabella(["Procedura", "Atti", "%"], [[p, c, pct(c)] for p, c in pro.most_common()]), ""]
    ultimi = anni[-3:]
    out += ["## Pratiche che si ripetono (materia × procedura, top 20)",
            tabella(["Materia", "Procedura", "Totale"] + [str(a) for a in ultimi],
                    [[m, p, c] + [coppie_anno[(m, p)][a] for a in ultimi]
                     for (m, p), c in coppie.most_common(20)]), ""]

    uffici = con.execute(
        """SELECT COALESCE(proponente,'(non indicato)'), oggetto FROM atti WHERE comune=?""", (slug,)).fetchall()
    if uffici:
        uf = defaultdict(Counter)
        for u, ogg in uffici:
            uf[u][classifica(ogg)] += 1
        # la stessa pratica (materia × procedura) fatta da più uffici = possibile analogia
        per_fam = defaultdict(set)
        for u, c in uf.items():
            for (m, p) in c:
                if ALTRO not in (m, p):
                    per_fam[f"{m} — {p}"].add(u)
        out += ["## Uffici (solo atti in pubblicazione, gli unici con l'ufficio proponente)",
                tabella(["Ufficio", "Atti", "Pratica più frequente"],
                        [[u, sum(c.values()), " — ".join(c.most_common(1)[0][0])]
                         for u, c in sorted(uf.items(), key=lambda x: -sum(x[1].values()))[:15]]), "",
                "### Stesse pratiche in uffici diversi (possibili analogie)",
                tabella(["Pratica", "N. uffici", "Uffici"],
                        [[f, len(us), ", ".join(sorted(us))[:150]]
                         for f, us in sorted(per_fam.items(), key=lambda x: -len(x[1])) if len(us) > 1]), ""]
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dati", type=Path, required=True)
    p.add_argument("--comune")
    args = p.parse_args()
    comuni = json.loads((ROOT / "comuni.json").read_text(encoding="utf-8"))
    if args.comune:
        comuni = [c for c in comuni if c["slug"] == args.comune]
    con = sqlite3.connect(args.dati / "albo.sqlite")
    con.execute("CREATE TABLE IF NOT EXISTS storico (comune TEXT, id TEXT, anno_registro INTEGER, "
                "numero_registro INTEGER, categoria TEXT, sottocategoria TEXT, oggetto TEXT, "
                "inizio_pubblicazione TEXT, fine_pubblicazione TEXT, primo_avvistamento TEXT)")
    cartella = args.dati / "analisi"
    cartella.mkdir(exist_ok=True)
    for c in comuni:
        testo = analizza(con, c["slug"], c["nome"])
        (cartella / f"{c['slug']}.md").write_text(testo + "\n", encoding="utf-8")
        print(f"scritto analisi/{c['slug']}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
