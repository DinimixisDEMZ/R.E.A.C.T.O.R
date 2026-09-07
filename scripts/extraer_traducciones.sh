#!/bin/bash
# Extrae las cadenas traducibles del código y actualiza los catálogos.
#
# Se hace en UNA sola pasada de xgettext. La versión anterior recorría los
# archivos uno a uno con `xgettext -j`, releyendo y reescribiendo el .pot en
# cada iteración: además de ser lento, silenciaba todos los errores con
# `2>/dev/null || true`, así que una extracción fallida pasaba desapercibida.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

POT="$DIR/po/reactor.pot"
VERSION="$(grep -oP '(?<=^VERSION = ")[^"]+' core/constantes.py || echo "dev")"

echo "=== Extrayendo cadenas traducibles ==="

mapfile -t ARCHIVOS < <(
    find . -name '*.py' \
        -not -path './.*' \
        -not -path '*/__pycache__/*' \
        -not -path './tests/*' \
        -not -path './build/*' | sort
)

xgettext \
    --language=Python \
    --from-code=UTF-8 \
    --keyword=_ \
    --keyword=traducir \
    --package-name=reactor \
    --package-version="$VERSION" \
    --msgid-bugs-address="https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/issues" \
    --copyright-holder="El proyecto R.E.A.C.T.O.R" \
    --add-comments=TRADUCTORES \
    --sort-by-file \
    -o "$POT" \
    "${ARCHIVOS[@]}"

echo "  ${#ARCHIVOS[@]} archivos · $(grep -c '^msgid ' "$POT") cadenas"

echo "=== Actualizando catálogos ==="
for po in "$DIR/po/"*/LC_MESSAGES/reactor.po; do
    [ -f "$po" ] || continue
    lang="$(basename "$(dirname "$(dirname "$po")")")"

    # --backup=none evita dejar archivos .po~ en el árbol.
    msgmerge --quiet --update --backup=none --previous "$po" "$POT"
    msgfmt --check-format -o "${po%.po}.mo" "$po"

    estado="$(msgfmt --statistics -o /dev/null "$po" 2>&1)"
    printf '  %-6s %s\n' "$lang" "$estado"
done

echo "=== Listo ==="
