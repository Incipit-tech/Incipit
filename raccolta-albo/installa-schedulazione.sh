#!/bin/zsh
# Installa (o reinstalla) la raccolta automatica giornaliera su macOS.
# Per disinstallare:  ./installa-schedulazione.sh --rimuovi
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents/tech.incipit.raccolta-albo.plist"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
if [[ "$1" == "--rimuovi" ]]; then rm -f "$DEST"; echo "Schedulazione rimossa."; exit 0; fi
mkdir -p "$ROOT/log" "$HOME/Library/LaunchAgents"
sed "s|__ROOT__|$ROOT|g" "$ROOT/tech.incipit.raccolta-albo.plist" > "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installata: gira ogni giorno alle 08:00 e alle 19:00. Log in $ROOT/log/"
