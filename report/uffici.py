"""Stima dell'ufficio proponente per gli atti dello storico.

Il portale indica l'ufficio solo per gli atti ancora in pubblicazione (qualche
centinaio per comune, e crescono ogni giorno). Per i migliaia di atti dello
storico l'ufficio si stima dall'oggetto: si contano le parole che ogni ufficio
usa negli atti dove l'ufficio è noto (Naive Bayes multinomiale, come un filtro
antispam) e si assegna l'ufficio più probabile.

La stima si usa solo quando è sicura (probabilità sopra una soglia), e la sua
accuratezza si misura sugli atti con l'ufficio noto, togliendone uno alla volta
(leave-one-out): il numero finisce nel report, accanto alla stima.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

PAROLE_VUOTE = set("""
alla alle allo agli dalla dalle dallo dagli della delle dello degli nella nelle nello negli sulla sulle
sullo sugli anno anni periodo comune comunale comunali relativo relativa relativi relative presso
sensi ditta favore spesa impegno approvazione determinazione oggetto mediante tramite parte servizio
servizi codice importo euro dell nell sull all dall quale quali come contro inoltre ovvero oppure
""".split())


def parole(testo: str) -> list[str]:
    return [w for w in re.findall(r"[a-zà-ù]{4,}", (testo or "").lower()) if w not in PAROLE_VUOTE]


@dataclass
class Modello:
    conteggi: dict[str, Counter]
    totali: dict[str, int]
    priori: dict[str, float]
    vocabolario: set[str]

    def probabilita(self, testo: str) -> list[tuple[str, float]]:
        """Uffici in ordine di probabilità, normalizzata a somma 1."""
        ws = parole(testo)
        v = len(self.vocabolario) + 1
        punteggi = {}
        for u in self.conteggi:
            s = math.log(self.priori[u])
            den = self.totali[u] + v
            for w in ws:
                s += math.log((self.conteggi[u][w] + 1) / den)
            punteggi[u] = s
        mx = max(punteggi.values())
        esp = {u: math.exp(s - mx) for u, s in punteggi.items()}
        z = sum(esp.values())
        return sorted(((u, e / z) for u, e in esp.items()), key=lambda x: -x[1])


def addestra(esempi: list[tuple[str, str]], minimo_per_ufficio: int = 3) -> Modello | None:
    """esempi: (ufficio, oggetto). Gli uffici con meno di `minimo_per_ufficio` atti non si stimano."""
    per_ufficio = Counter(u for u, _ in esempi)
    esempi = [(u, o) for u, o in esempi if per_ufficio[u] >= minimo_per_ufficio]
    if len({u for u, _ in esempi}) < 2:
        return None
    conteggi: dict[str, Counter] = defaultdict(Counter)
    for u, o in esempi:
        conteggi[u].update(parole(o))
    n = len(esempi)
    priori = {u: c / n for u, c in Counter(u for u, _ in esempi).items()}
    return Modello(dict(conteggi), {u: sum(c.values()) for u, c in conteggi.items()}, priori,
                   {w for c in conteggi.values() for w in c})


@dataclass
class Valutazione:
    esempi: int
    coperti: int  # atti con probabilità sopra la soglia
    giusti: int  # di quelli coperti, quanti con l'ufficio giusto

    @property
    def accuratezza(self) -> float:
        return self.giusti / self.coperti if self.coperti else 0.0

    @property
    def copertura(self) -> float:
        return self.coperti / self.esempi if self.esempi else 0.0


def valuta(esempi: list[tuple[str, str]], soglia: float) -> Valutazione:
    """Leave-one-out: ogni atto è stimato da un modello addestrato su tutti gli altri."""
    coperti = giusti = 0
    quanti = Counter(u for u, _ in esempi)
    usabili = [(u, o) for u, o in esempi if quanti[u] >= 4]
    for i, (u, o) in enumerate(usabili):
        m = addestra(usabili[:i] + usabili[i + 1:])
        if not m:
            continue
        stima, p = m.probabilita(o)[0]
        if p >= soglia:
            coperti += 1
            giusti += stima == u
    return Valutazione(len(usabili), coperti, giusti)
