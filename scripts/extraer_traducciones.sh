#!/bin/bash
# Extract translatable strings and compile .mo files
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

POT="$DIR/po/reactor.pot"
echo "=== Extrayendo cadenas traducibles ==="

echo "" > "$POT"

find . -name "*.py" -not -path "./.*" -not -path "*/__pycache__/*" | while read f; do
    xgettext -j -o "$POT" -L Python --keyword=_ --keyword=traducir --from-code=UTF-8 "$f" 2>/dev/null || true
done

echo "=== Combinando con traducciones existentes ==="
for po in "$DIR/po/"*/LC_MESSAGES/reactor.po; do
    if [ -f "$po" ]; then
        lang=$(basename "$(dirname "$(dirname "$po")")")
        msgmerge --update "$po" "$POT" 2>/dev/null || true
        mkdir -p "$DIR/po/$lang/LC_MESSAGES"
        msgfmt "$po" -o "$DIR/po/$lang/LC_MESSAGES/reactor.mo" 2>/dev/null || true
        echo "  Compilado: $lang"
    fi
done

echo "=== Listo ==="
