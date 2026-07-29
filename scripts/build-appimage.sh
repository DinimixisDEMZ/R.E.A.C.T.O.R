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
STRESS_NG_BIN=""
if [ ! -f "$BUILD_DIR/stress-ng" ]; then
    echo "  Descargando stress-ng desde Alpine (musl)..."
    STRESS_NG_APK=$(wget -q -O- "https://dl-cdn.alpinelinux.org/alpine/edge/community/x86_64/" 2>/dev/null | \
        grep -oP 'stress-ng-\d+\.\d+\.\w+-r\d+\.apk' | sort -V | tail -1 || true)
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
    STRESS_NG_BIN=$(which stress-ng 2>/dev/null || true)
    if [ -n "$STRESS_NG_BIN" ]; then
        cp -L "$STRESS_NG_BIN" "$APP_DIR/usr/bin/stress-ng"
        echo "  ✓ stress-ng (sistema)"
    else
        echo "  ⚠ stress-ng no disponible"
    fi
fi

# cyclictest: se compila desde source en el benchmark de compilación
# (no hay paquete Alpine). Si está en el sistema, se incluye.
CYCLICTEST_BIN=$(which cyclictest 2>/dev/null || true)
if [ -n "$CYCLICTEST_BIN" ]; then
    cp -L "$CYCLICTEST_BIN" "$APP_DIR/usr/bin/cyclictest"
    echo "  ✓ cyclictest (sistema)"
else
    echo "  - cyclictest se compilará desde rt-tests source si está presente"
fi

# rt-tests source (para benchmark de compilación)
RT_TESTS_SRC="${RT_TESTS_DIR:-/tmp/rt-tests}"
if [ ! -d "$RT_TESTS_SRC" ] || [ ! -f "$RT_TESTS_SRC/Makefile" ]; then
    echo "  Descargando rt-tests source desde kernel.org..."
    git clone --depth 1 "https://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git" "$RT_TESTS_SRC" 2>/dev/null || {
        echo "  ⚠ No se pudo clonar rt-tests. La compilación paralela no estará disponible."
        RT_TESTS_SRC=""
    }
fi
if [ -n "$RT_TESTS_SRC" ] && [ -f "$RT_TESTS_SRC/Makefile" ]; then
    echo "  Copiando rt-tests source..."
    mkdir -p "$APP_DIR/usr/share/reactor/rt-tests"
    cp -r "$RT_TESTS_SRC/"* "$APP_DIR/usr/share/reactor/rt-tests/"
    echo "  ✓ rt-tests source"
fi

# scxctl: solo desde el sistema (específico del kernel)
SCXCTL_BIN=$(which scxctl 2>/dev/null || true)
if [ -n "$SCXCTL_BIN" ]; then
    cp -L "$SCXCTL_BIN" "$APP_DIR/usr/bin/scxctl"
    echo "  ✓ scxctl"
else
    echo "  ⚠ scxctl no encontrado"
fi

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
