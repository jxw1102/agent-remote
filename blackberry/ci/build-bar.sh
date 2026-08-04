#!/bin/bash
# Cross-compile the Cascades app and package an unsigned dev-mode .bar.
# Usage: build-bar.sh <ndk-dir> [grok|claude]   (expects <ndk-dir>/env.sh
# from fetch-ndk.sh; variant defaults to claude)
#
# Packaging mirrors the flow proven on-device by the grok-bb10 reference app:
# real ELF executable packaged as `app` with -devMode (the device accepts
# dev-mode sideloads; Entry-Point-Type Qnx/Elf, no .so entry point).
set -euo pipefail

NDK="${1:-$HOME/bbndk}"
VARIANT="${2:-claude}"
if [ "$VARIANT" = "grok" ]; then
    TARGET=GrokRemote
else
    VARIANT=claude
    TARGET=ClaudeRemote
fi
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

source "$NDK/env.sh"

log() { echo "[build-bar] $*"; }

log "QNX_HOST=$QNX_HOST"
log "QNX_TARGET=$QNX_TARGET"
log "variant: $VARIANT -> $TARGET"

QMAKE="$QNX_HOST/usr/bin/qmake"
if [ ! -x "$QMAKE" ]; then
    log "qmake not found under QNX_HOST; searching..."
    QMAKE=$(find "$NDK" -name qmake -type f | head -1)
fi
log "qmake: $QMAKE ($(file -b "$QMAKE" 2>/dev/null || true))"

# The Cascades Qt4 device spec, as used by Momentics (and the working
# grok-bb10 build: blackberry-armv7le-qcc).
SPEC=""
for candidate in blackberry-armv7le-qcc blackberry-armle-v7-qcc \
                 unsupported/blackberry-armv7le-qcc; do
    for root in "$QNX_TARGET/usr/share/qt4/mkspecs" "$QNX_HOST/usr/share/qt4/mkspecs"; do
        if [ -d "$root/$candidate" ]; then
            SPEC="$candidate"
            break 2
        fi
    done
done
log "using mkspec: ${SPEC:-<default>}"

export CPUVARDIR=armle-v7

cd "$APP_DIR"
rm -rf arm Makefile Makefile.* release debug 2>/dev/null || true
mkdir -p arm/o.le-v7 arm/o.le-v7-g release debug

if [ -n "$SPEC" ]; then
    "$QMAKE" app.pro -spec "$SPEC" \
        CONFIG+=device CONFIG+=release CONFIG-=debug "VARIANT=$VARIANT"
else
    "$QMAKE" app.pro CONFIG+=device CONFIG+=release CONFIG-=debug "VARIANT=$VARIANT"
fi

if [ -f Makefile.Release ]; then
    make -f Makefile.Release -j"$(nproc)"
else
    make -j"$(nproc)"
fi

log "build output:"
find arm -maxdepth 2 -type f -name "$TARGET*" | sed 's/^/   /'

BINARY=""
for c in "arm/o.le-v7/$TARGET" "arm/o.le-v7-g/$TARGET" "$TARGET"; do
    [ -f "$c" ] && { BINARY="$c"; break; }
done
[ -n "$BINARY" ] || { log "FATAL: no binary produced"; exit 1; }
log "binary: $BINARY ($(file -b "$BINARY" 2>/dev/null || true))"

PACKAGER="$QNX_HOST/usr/bin/blackberry-nativepackager"
[ -x "$PACKAGER" ] || PACKAGER=$(find "$NDK" -name 'blackberry-nativepackager' -type f | head -1)
log "packager: $PACKAGER"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$BINARY" "$STAGE/$TARGET"
cp -a "variant/$VARIANT/bar-descriptor.xml" "$STAGE/"
cp -a assets "$STAGE/assets"
cp -a "variant/$VARIANT/icon.png" "$STAGE/icon.png"

(
    cd "$STAGE"
    "$PACKAGER" -package "$APP_DIR/$TARGET.bar" \
        -devMode \
        bar-descriptor.xml \
        -e "$TARGET" app \
        -e icon.png icon.png \
        -C . assets
)

log "packaged:"
ls -la "$APP_DIR/$TARGET.bar"
unzip -p "$APP_DIR/$TARGET.bar" META-INF/MANIFEST.MF \
    | grep -i "development-mode\|entry-point\|requires-system" | sed 's/^/   /' || true
