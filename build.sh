#!/bin/bash
# build.sh — Compila SCXCTL con Nuitka (Solus Linux)
# PyGObject/cairo se excluyen porque dependen de libs del sistema (GLib/GTK4)
set -euo pipefail

APP_NAME="scxctl"
MAIN="main.py"
DIST_DIR="dist"
BUILD_DIR="build_nuitka"

echo "🔧 Limpiando artefactos anteriores..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

echo "🔧 Compilando con Nuitka..."
python3 -m nuitka \
    --output-dir="$BUILD_DIR" \
    --output-filename="$APP_NAME" \
    --nofollow-import-to=gi \
    --nofollow-import-to=gi.overrides \
    --nofollow-import-to=gi.repository \
    --nofollow-import-to=cairo \
    --nofollow-import-to=gi.cairo \
    --nofollow-import-to=gi._gi \
    --nofollow-import-to=gi._option \
    --include-package=core \
    --include-package=ui \
    --include-package=widgets \
    --include-package=utils \
    --assume-yes-for-downloads \
    --remove-output \
    --follow-imports \
    "$MAIN"

echo ""
echo "📦 Buscando binario compilado..."
BIN=$(find "$BUILD_DIR" -name "$APP_NAME*" -type f 2>/dev/null | grep -v '\.o$' | grep -v '\.c$' | head -1)

if [ -n "$BIN" ]; then
    cp "$BIN" "$DIST_DIR/$APP_NAME"
    chmod +x "$DIST_DIR/$APP_NAME"
    echo "✅ Binario: $DIST_DIR/$APP_NAME ($(du -h "$DIST_DIR/$APP_NAME" | cut -f1))"
else
    echo "❌ No se encontró el binario compilado"
    ls -la "$BUILD_DIR/" 2>/dev/null
    exit 1
fi

echo ""
echo "📋 Para ejecutar: ./dist/$APP_NAME"
echo "⚠️  Requisitos: python3-gobject, gtk4, stress-ng, hyperfine, scxctl"
