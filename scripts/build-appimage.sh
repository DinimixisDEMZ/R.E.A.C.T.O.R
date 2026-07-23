#!/bin/bash
# Build script for R.E.A.C.T.O.R AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT/build/appimage"
APP_DIR="$BUILD_DIR/R.E.A.C.T.O.R.AppDir"
OUTPUT="$ROOT/R.E.A.C.T.O.R-x86_64.AppImage"

echo "=== Construyendo AppImage de R.E.A.C.T.O.R ==="

# ── Preparar directorios ──
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/reactor"
mkdir -p "$APP_DIR/usr/share/applications"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/scalable/apps"

# ── Copiar entry point ──
cp "$BUILD_DIR/AppRun" "$APP_DIR/AppRun"
chmod +x "$APP_DIR/AppRun"

# ── Copiar aplicación ──
echo "Copiando aplicación..."
rsync -a --exclude='__pycache__' \
         --exclude='*.pyc' \
         --exclude='.git' \
         --exclude='build/' \
         --exclude='node_modules/' \
         --exclude='design/' \
         "$ROOT/"*.py \
         "$ROOT/core/" \
         "$ROOT/ui/" \
         "$ROOT/utils/" \
         "$ROOT/widgets/" \
         "$APP_DIR/usr/share/reactor/"

# ── Copiar binarios del sistema ──
echo "Copiando binarios..."
for bin in scxctl stress-ng hyperfine; do
    path=$(which "$bin" 2>/dev/null || true)
    if [ -n "$path" ]; then
        cp -L "$path" "$APP_DIR/usr/bin/$bin"
        echo "  ✓ $bin ($path)"
    else
        echo "  ⚠ $bin no encontrado"
    fi
done

# ── Copiar .desktop e icono ──
cp "$BUILD_DIR/R.E.A.C.T.O.R.desktop" "$APP_DIR/usr/share/applications/"
cp "$BUILD_DIR/reactor.svg" "$APP_DIR/usr/share/icons/hicolor/scalable/apps/reactor.svg"
cp "$BUILD_DIR/reactor.svg" "$APP_DIR/reactor.svg"

# ── Descargar linuxdeploy si no existe ──
LINUXDEPLOY="$BUILD_DIR/linuxdeploy-x86_64.AppImage"
if [ ! -f "$LINUXDEPLOY" ]; then
    echo "Descargando linuxdeploy..."
    wget -q -O "$LINUXDEPLOY" \
        "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
    chmod +x "$LINUXDEPLOY"
fi

LINUXDEPLOY_GTK="$BUILD_DIR/linuxdeploy-plugin-gtk-x86_64.AppImage"
if [ ! -f "$LINUXDEPLOY_GTK" ]; then
    echo "Descargando linuxdeploy-plugin-gtk..."
    wget -q -O "$LINUXDEPLOY_GTK" \
        "https://github.com/linuxdeploy/linuxdeploy-plugin-gtk/releases/download/continuous/linuxdeploy-plugin-gtk-x86_64.AppImage"
    chmod +x "$LINUXDEPLOY_GTK"
fi

# ── Ejecutar linuxdeploy ──
echo "Ejecutando linuxdeploy..."
export LDAI_OUTPUT="$OUTPUT"
export DEPLOY_GTK_VERSION=4

./linuxdeploy-x86_64.AppImage \
    --appdir "$APP_DIR" \
    --plugin gtk \
    --output appimage \
    2>&1 | grep -v "^$"

echo ""
echo "✓ AppImage generada: $OUTPUT"
ls -lh "$OUTPUT"
