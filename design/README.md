# Design

Asset del design system di Incipit (identità **Capolettera**) da usare nel codice.
Il design system completo, con regole d'uso, anteprima, componenti React e PNG,
è su Google Drive nella cartella *Desing System* dentro *startup*.

| File | Cosa contiene |
|---|---|
| `tokens.css` | Variabili CSS (colori con tema chiaro e scuro, font, spazi, angoli) e `@font-face` |
| `tokens.json` | Gli stessi token in JSON, sorgente da cui è generato `tokens.css` |
| `fonts/` | Fraunces e Inter in woff2, pesi 400, 500, 600 (licenza OFL) |
| `logo/` | Logo in SVG: orizzontale (principale), verticale, fondo scuro, mono nero, negativo, simbolo |
| `icone/` | Selezione di icone Lucide in SVG (licenza ISC) |

## Uso rapido

```html
<link rel="stylesheet" href="design/tokens.css">
<body style="background: var(--page); color: var(--ink); font-family: var(--font-sans)">
```

`tokens.css` carica i font con percorsi relativi `fonts/...`, quindi va servito
insieme alla cartella `fonts/`. Il tema scuro si attiva con `data-theme="dark"`.

## Regole essenziali

- **Colori**: nell'interfaccia usa i token semantici (`page`, `surface`, `ink`, `ink-muted`, `link`, `line`), non i valori esadecimali. Sopra `blu-incipit` il testo è sempre `on-blu`.
- **Tipografia**: Fraunces solo per logotipo e titoli grandi, Inter per tutto il resto.
- **Logo**: usa sempre gli SVG, senza ricolorarli né deformarli. Spazio di rispetto pari a metà del lato del simbolo; minimo 16 px per il simbolo, 96 px di larghezza per il logo completo.
- **Icone**: solo dal set Lucide, tratto 1.75 su griglia 24 px.
