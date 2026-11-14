#!/bin/zsh
# Riepilogo veloce di cosa è stato raccolto (compatto, sta in un terminale stretto).
cd "$(dirname "$0")"
sqlite3 -header -column dati/albo.sqlite <<'SQL'
SELECT comune, COUNT(*) AS atti,
       strftime('%d/%m %H:%M', MIN(primo_avvistamento)) AS dal,
       strftime('%d/%m %H:%M', MAX(ultimo_avvistamento)) AS ultimo
  FROM atti GROUP BY comune;
.print
SELECT comune, COUNT(*) AS allegati, SUM(file IS NOT NULL) AS presi,
       ROUND(SUM(byte)/1e6) AS MB,
       COUNT(CASE WHEN errore LIKE 'saltato:%' THEN 1 END) AS saltati,
       SUM(errore IS NOT NULL AND errore NOT LIKE 'saltato:%') AS errori
  FROM allegati GROUP BY comune;
.print
SELECT strftime('%d/%m %H:%M', inizio) AS corsa, substr(comune,1,8) AS comune,
       atti_in_lista AS lista, atti_nuovi AS nuovi, allegati_scaricati AS pdf, esito
  FROM esecuzioni ORDER BY inizio DESC LIMIT 6;
SQL
