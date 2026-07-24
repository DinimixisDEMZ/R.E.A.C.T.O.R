#!/bin/bash
# Build script for R.E.A.C.T.O.R AppImage (system GTK approach)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPIMAGE_SRC="$ROOT/appimage"
BUILD_DIR="$ROOT/build/appimage"
APP_DIR="$BUILD_DIR/R.E.A.C.T.O.R.AppDir"
OUTPUT="$ROOT/R.E.A.C.T.O.R-x86_64.AppImage"

echo "=== Construyendo AppImage de R.E.A.C.T.O.R ==="

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/reactor"
mkdir -p "$APP_DIR/usr/share/applications"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/scalable/apps"

cp "$APPIMAGE_SRC/AppRun" "$APP_DIR/AppRun"
chmod +x "$APP_DIR/AppRun"

echo "Copiando aplicación..."
cp "$ROOT/"*.py "$APP_DIR/usr/share/reactor/"
cp -r "$ROOT/core" "$ROOT/ui" "$ROOT/utils" "$ROOT/widgets" "$ROOT/data" "$APP_DIR/usr/share/reactor/"
find "$APP_DIR/usr/share/reactor/" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

echo "Copiando binarios..."
for bin in scxctl stress-ng hyperfine; do
    path=$(which "$bin" 2>/dev/null || true)
    [ -n "$path" ] && cp -L "$path" "$APP_DIR/usr/bin/$bin" && echo "  ✓ $bin"
done

cp "$APPIMAGE_SRC/R.E.A.C.T.O.R.desktop" "$APP_DIR/"
cp "$APPIMAGE_SRC/reactor.svg" "$APP_DIR/"
cp "$APPIMAGE_SRC/R.E.A.C.T.O.R.desktop" "$APP_DIR/usr/share/applications/"
cp "$APPIMAGE_SRC/reactor.svg" "$APP_DIR/usr/share/icons/hicolor/scalable/apps/reactor.svg"

# ── Descargar appimagetool si no existe ──
APPIMAGETOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Descargando appimagetool..."
    wget -q -O "$APPIMAGETOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

echo "Generando AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APP_DIR" "$OUTPUT" 2>&1 | grep -v "^$"

echo ""
echo "✓ AppImage generada: $OUTPUT"
ls -lh "$OUTPUT"
