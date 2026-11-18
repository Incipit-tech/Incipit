# raccolta-albo

Raccoglie ogni giorno gli atti pubblicati sull'albo pretorio di alcuni comuni:
metadati e allegati (PDF). Serve perché sull'albo il testo completo resta
visibile solo durante la pubblicazione (circa 15 giorni). Dopo, in archivio,
resta solo l'elenco.

## Comuni monitorati (`comuni.json`)
| Comune | Abitanti (circa) | Piattaforma |
|---|---|---|
| Abbiategrasso | 32.700 | JCityGov (`*.trasparenza-valutazione-merito.it`) |
| Magenta | 24.000 | JCityGov |
| Cassano d'Adda | 19.000 | JCityGov |

Per aggiungere un comune che usa la stessa piattaforma basta una riga in
`comuni.json`: lo script trova da solo l'URL della lista leggendo il menu del portale.
Comuni su altre piattaforme (Halley, Maggioli/Sicraweb, ecc.) avranno bisogno di un
nuovo "lettore".

## Script
| File | Cosa fa |
|---|---|
| `raccolta.py` | atti **in pubblicazione**: metadati completi (anche l'ufficio proponente) e allegati |
| `storico.py` | **storico atti**: solo l'elenco (registro, tipo, oggetto, date), ma per anni (dal 2019-2021) |
| `analisi.py` | riepilogo per comune: volumi per anno, tipi, materie × procedure, uffici. Classificazione a regole, prima bozza |
| `classifica_ai.py` | classificazione materia × procedura con Claude Haiku (API Batch). Di default fa solo la stima dei costi; `--invia` è a pagamento. Dipendenza in `requirements-ai.txt` |
| `verifica.py` | misura la classificazione su un campione verificato a mano (nel repo privato, `verifica/`): quante materie e procedure sono giuste. Serve per ogni miglioria delle regole e, a gennaio, per confrontare i modelli AI sugli stessi atti |
| `salute.py` | controllo di salute a fine raccolta: fallisce se un comune non dà atti nuovi da più di 3 giorni lavorativi o se l'ultima esecuzione è andata male. Un'esecuzione fallita manda l'email di GitHub |

## Dove gira
La raccolta gira ogni giorno su **GitHub Actions**, nel repository privato
`Incipit-tech/incipit-dati`, che contiene anche i dati. Non serve nessun computer acceso.
Gli script per macOS (`installa-schedulazione.sh`) restano solo per l'uso in locale.

## Uso in locale
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python raccolta.py                    # tutti i comuni
.venv/bin/python raccolta.py --comune magenta   # un solo comune
.venv/bin/python raccolta.py --senza-allegati   # solo metadati
.venv/bin/python raccolta.py --dati ../incipit-dati --allegati /tmp/arch  # come in Actions
./stato.sh                                      # riepilogo
./installa-schedulazione.sh                     # raccolta automatica 08:00 e 19:00
./installa-schedulazione.sh --rimuovi
```

## Dove finiscono i dati
In Actions, `dati/` è il repo privato `incipit-dati`: database e **documenti principali**
nel repo, gli **altri allegati** compressi in una Release per ogni esecuzione
(nel DB il loro percorso inizia con `archivio:`).

- `dati/albo.sqlite`, con le tabelle:
  - `atti`: una riga per atto, con categoria, ufficio proponente, firmatario, oggetto, date e tutti i metadati grezzi in JSON;
  - `allegati`: titolo, tipo e percorso del file, più l'hash SHA-256;
  - `esecuzioni`: lo storico delle corse, per controllare che la raccolta sia affidabile.
- `dati/<comune>/<anno>/<n.registro>_<id>/`: i PDF scaricati.
- `log/raccolta.log`: il log dettagliato.

## Scelte
- **Incrementale**: un atto già visto non viene riscaricato. Si aggiorna solo
  `ultimo_avvistamento`. Gli allegati mancanti si recuperano alla corsa
  successiva, finché l'atto resta in pubblicazione.
- **Si preferisce il PDF "sbustato"** (`downloadSigned=false`) al `.p7m` firmato,
  perché è leggibile subito dall'OCR e dall'AI.
- **Cortesia**: una richiesta al secondo e 3 tentativi con attesa crescente.
- **Dati personali**: gli atti possono contenerne, quindi i dati stanno solo nel
  repository **privato** `incipit-dati` e mai in questo repository pubblico.
