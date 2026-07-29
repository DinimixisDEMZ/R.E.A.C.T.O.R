#!/bin/bash
# Build script for R.E.A.C.T.O.R AppImage (system GTK approach)
# Bundles app + static binaries for stress-ng, hyperfine, cyclictest
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPIMAGE_SRC="$ROOT/appimage"
BUILD_DIR="$ROOT/build/appimage"
APP_DIR="$BUILD_DIR/R.E.A.C.T.O.R.AppDir"
OUTPUT="$ROOT/R.E.A.C.T.O.R-x86_64.AppImage"
VER_HYPERFINE="1.19.0"

# Versión desde git tag (si no hay tag, usa constantes.py)
VERSION="${VERSION:-$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null || echo '')}"

echo "=== Construyendo AppImage de R.E.A.C.T.O.R ==="

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/reactor"
mkdir -p "$APP_DIR/usr/share/applications"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/scalable/apps"

# ── AppRun ──
cp "$APPIMAGE_SRC/AppRun" "$APP_DIR/AppRun"
chmod +x "$APP_DIR/AppRun"

# ── App Python ──
echo "Copiando aplicación..."
cp "$ROOT/"*.py "$APP_DIR/usr/share/reactor/"
cp -r "$ROOT/core" "$ROOT/ui" "$ROOT/utils" "$ROOT/widgets" "$ROOT/data" "$APP_DIR/usr/share/reactor/"
find "$APP_DIR/usr/share/reactor/" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Inyectar versión desde git tag si está disponible
if [ -n "$VERSION" ]; then
    echo "  Inyectando versión v$VERSION desde git tag..."
    sed -i "s/^VERSION = \".*\"/VERSION = \"${VERSION#v}\"/" "$APP_DIR/usr/share/reactor/core/constantes.py"
fi

# ── Binarios static ──
echo "Copiando binarios..."

# hyperfine: static musl desde GitHub
if [ ! -f "$BUILD_DIR/hyperfine" ]; then
    echo "  Descargando hyperfine v$VER_HYPERFINE (static musl)..."
    wget -q -O /tmp/hyperfine.tar.gz \
        "https://github.com/sharkdp/hyperfine/releases/download/v$VER_HYPERFINE/hyperfine-v${VER_HYPERFINE}-x86_64-unknown-linux-musl.tar.gz"
    tar xzf /tmp/hyperfine.tar.gz -C /tmp/
    cp "/tmp/hyperfine-v${VER_HYPERFINE}-x86_64-unknown-linux-musl/hyperfine" "$BUILD_DIR/hyperfine"
    chmod +x "$BUILD_DIR/hyperfine"
    rm -rf "/tmp/hyperfine-v${VER_HYPERFINE}-x86_64-unknown-linux-musl" /tmp/hyperfine.tar.gz
fi
cp "$BUILD_DIR/hyperfine" "$APP_DIR/usr/bin/hyperfine"
echo "  ✓ hyperfine"

# stress-ng: Alpine musl static (repo community)
if [ ! -f "$BUILD_DIR/stress-ng" ]; then
    echo "  Descargando stress-ng desde Alpine (musl)..."
    STRESS_NG_APK=$(wget -q -O- "https://dl-cdn.alpinelinux.org/alpine/edge/community/x86_64/" 2>/dev/null | \
        grep -oE 'stress-ng-[0-9]+\.[0-9]+\.[a-zA-Z0-9]+-r[0-9]+\.apk' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1 || true)
    if [ -n "$STRESS_NG_APK" ]; then
        wget -q -O /tmp/stress-ng.apk "https://dl-cdn.alpinelinux.org/alpine/edge/community/x86_64/$STRESS_NG_APK"
        tar xzf /tmp/stress-ng.apk -C /tmp/ 2>/dev/null || true
        if [ -f /tmp/usr/bin/stress-ng ]; then
            cp /tmp/usr/bin/stress-ng "$BUILD_DIR/stress-ng"
            chmod +x "$BUILD_DIR/stress-ng"
        fi
        rm -rf /tmp/stress-ng.apk /tmp/usr/
    fi
fi
if [ -f "$BUILD_DIR/stress-ng" ]; then
    cp "$BUILD_DIR/stress-ng" "$APP_DIR/usr/bin/stress-ng"
    echo "  ✓ stress-ng (static)"
else
    echo "  ⚠ stress-ng no disponible (se omite — solo afecta benchmark memory/threads)"
fi

# cyclictest + rt-tests source
# Se compila cyclictest estático y se bundlea el source para benchmark compile
echo "  Preparando cyclictest y rt-tests..."
BUILD_RT_DIR="$BUILD_DIR/rt-tests"
if [ ! -d "$BUILD_RT_DIR" ] || [ ! -f "$BUILD_RT_DIR/Makefile" ]; then
    rm -rf "$BUILD_RT_DIR"
    echo "  Clonando rt-tests desde kernel.org..."
    if ! git clone --depth 1 "https://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git" "$BUILD_RT_DIR"; then
        echo "  ⚠ No se pudo clonar rt-tests. Compilación paralela no disponible."
        BUILD_RT_DIR=""
    fi
fi
if [ -n "$BUILD_RT_DIR" ] && [ -f "$BUILD_RT_DIR/Makefile" ]; then
    echo "  Compilando cyclictest estático..."
    if make -C "$BUILD_RT_DIR" cyclictest -j"$(nproc)"; then
        cp "$BUILD_RT_DIR/cyclictest" "$BUILD_DIR/cyclictest"
        chmod +x "$BUILD_DIR/cyclictest"
        cp "$BUILD_DIR/cyclictest" "$APP_DIR/usr/bin/cyclictest"
        echo "  ✓ cyclictest (estático)"
    else
        echo "  ⚠ No se pudo compilar cyclictest."
    fi
    # Source para benchmark de compilación paralela (se copia a /tmp al ejecutar)
    echo "  Copiando rt-tests source al AppImage..."
    mkdir -p "$APP_DIR/usr/share/reactor/rt-tests"
    cp -r "$BUILD_RT_DIR/"* "$APP_DIR/usr/share/reactor/rt-tests/"
    echo "  ✓ rt-tests source"
fi

# scxctl NO se bundlea — viene siempre del sistema (específico del kernel).
# El AppImage verifica scxctl en el sistema al arrancar.

# ── Desktop y metadatos ──
cp "$APPIMAGE_SRC/R.E.A.C.T.O.R.desktop" "$APP_DIR/"
cp "$APPIMAGE_SRC/reactor.svg" "$APP_DIR/"
cp "$APPIMAGE_SRC/R.E.A.C.T.O.R.desktop" "$APP_DIR/usr/share/applications/"
cp "$APPIMAGE_SRC/reactor.svg" "$APP_DIR/usr/share/icons/hicolor/scalable/apps/reactor.svg"

# ── appimagetool ──
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
echo ""
echo "Binarios incluidos:"
for b in "$APP_DIR/usr/bin/"*; do
    echo "  $(basename "$b") ($(du -h "$b" | cut -f1))"
done
