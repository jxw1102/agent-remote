#!/usr/bin/env bash
# Build Agent Remote (unified multi-harness client) as an unsigned .bar.
#
#   ./build-bar-docker.sh           → ../dist/AgentRemote.bar
#   ./build-bar-docker.sh unified   → same
#
# Single-provider ClaudeRemote / GrokRemote variants are retired — one app
# talks to agentremoted and picks the harness (Claude / Grok / Codex) at
# session start.
#
# Uses the same local NDK image the Term49 project builds with
# (delaya73/bbndk — already pulled). Works on Apple Silicon via
# linux/amd64 emulation; the NDK host tools are 32-bit x86, which Rosetta
# cannot run — if you get "exec format error", turn OFF "Use Rosetta for
# x86/amd64 emulation" in Docker Desktop → Settings → General.
set -euo pipefail

VARIANT="${1:-unified}"
if [[ "$VARIANT" == "all" || "$VARIANT" == "unified" ]]; then
  VARIANT=unified
elif [[ "$VARIANT" == "grok" || "$VARIANT" == "claude" ]]; then
  echo "note: single-provider $VARIANT bars are retired; building AgentRemote (unified)" >&2
  VARIANT=unified
else
  echo "usage: $0 [unified|all]" >&2
  exit 2
fi
TARGET=AgentRemote

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="${OUTDIR:-$(cd "$APP_DIR/.." && pwd)/dist}"
mkdir -p "$OUTDIR"

IMAGES="${BB10_NDK_IMAGE:-delaya73/bbndk:latest accupara/bbndk:latest}"

# The whole build runs inside the container; only the sources and the
# output dir are mounted. VARIANT/TARGET are baked in via envsubst-free
# interpolation below (single-quoted heredoc keeps $NDK vars literal).
INNER='
set -euo pipefail
log() { echo "[docker-build] $*"; }

# 1. Locate the NDK inside the image (same lookup Term49 ships:
#    delaya73/bbndk keeps it at /home/admin/bin/bbndk)
ENV_SH=$(ls /home/*/bin/bbndk/bbndk-env*.sh /opt/bbndk/bbndk-env*.sh \
            /root/bbndk/bbndk-env*.sh 2>/dev/null | head -1 || true)
if [ -z "$ENV_SH" ]; then
    ENV_SH=$(find /opt /root /bbndk /home -maxdepth 5 -name "bbndk-env*.sh" 2>/dev/null | head -1 || true)
fi
if [ -n "$ENV_SH" ]; then
    log "sourcing $ENV_SH"
    set +u; source "$ENV_SH"; set -u
else
    QNX_HOST=$(find / -maxdepth 6 -type d -path "*host_10*/linux/x86" 2>/dev/null | head -1 || true)
    QNX_TARGET=$(find / -maxdepth 6 -type d -name qnx6 -path "*target_10*" 2>/dev/null | head -1 || true)
    [ -n "$QNX_HOST" ] && [ -n "$QNX_TARGET" ] || { log "FATAL: no NDK in this image"; exit 2; }
    export QNX_HOST QNX_TARGET
    export QNX_CONFIGURATION="$HOME/.rim"
    export PATH="$QNX_HOST/usr/bin:$PATH"
fi
log "QNX_HOST=$QNX_HOST"
log "QNX_TARGET=$QNX_TARGET"
export CPUVARDIR=armle-v7

# 2. Sanity: can this kernel run the 32-bit host tools?
if ! "$QNX_HOST/usr/bin/qmake" -v >/dev/null 2>&1; then
    log "FATAL: cannot execute 32-bit NDK tools."
    log "On Apple Silicon: Docker Desktop -> Settings -> General ->"
    log "disable \"Use Rosetta for x86/amd64 emulation\", then retry."
    "$QNX_HOST/usr/bin/qmake" -v || true
    exit 3
fi

# 3. Build out-of-tree (the mount stays clean)
BUILD=/tmp/build
rm -rf "$BUILD"; mkdir -p "$BUILD"
cp -a /src/. "$BUILD/"
cd "$BUILD"
rm -rf arm Makefile Makefile.* release debug 2>/dev/null || true
mkdir -p arm/o.le-v7 arm/o.le-v7-g release debug

SPEC=blackberry-armv7le-qcc
[ -d "$QNX_TARGET/usr/share/qt4/mkspecs/$SPEC" ] || SPEC=blackberry-armle-v7-qcc
log "mkspec: $SPEC"
qmake -spec "$SPEC" CONFIG+=device CONFIG+=release CONFIG-=debug "VARIANT=__VARIANT__" app.pro
if [ -f Makefile.Release ]; then make -f Makefile.Release -j2; else make -j2; fi

BIN=""
for c in arm/o.le-v7/__TARGET__ arm/o.le-v7-g/__TARGET__ __TARGET__; do
    [ -f "$c" ] && { BIN="$c"; break; }
done
[ -n "$BIN" ] || { log "FATAL: no binary"; exit 4; }
log "binary: $BIN"
cp -a "$BIN" /out/__TARGET__.symbols.elf

# 4. Package (unsigned, dev-mode — the flow proven on-device)
STAGE=/tmp/stage
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -a "$BIN" "$STAGE/__TARGET__"
cp -a variant/__VARIANT__/bar-descriptor.xml "$STAGE/"
cp -a assets "$STAGE/assets"
cp -a variant/__VARIANT__/icon.png "$STAGE/icon.png"
cd "$STAGE"
blackberry-nativepackager -package /out/__TARGET__.bar \
    -devMode \
    bar-descriptor.xml \
    -e __TARGET__ app \
    -e icon.png icon.png \
    -C . assets
log "OK: __TARGET__.bar"
'
INNER="${INNER//__VARIANT__/$VARIANT}"
INNER="${INNER//__TARGET__/$TARGET}"

for IMG in $IMAGES; do
    echo "=== trying image: $IMG ==="
    if docker run --rm --platform linux/amd64 \
        -v "$APP_DIR":/src:ro \
        -v "$OUTDIR":/out \
        "$IMG" bash -c "$INNER"; then
        echo "OK: $OUTDIR/$TARGET.bar (built in $IMG)"
        ls -la "$OUTDIR/$TARGET.bar"
        # ~/Public is the sideload pickup folder (and the Claude daemon's
        # drop dir, so the phone can fetch its own next build). Copying here
        # keeps it from ever holding a stale bar. PUBLIC_DIR= to override,
        # PUBLIC_DIR="" to skip.
        PUBLIC_DIR="${PUBLIC_DIR-$HOME/Public}"
        if [[ -n "$PUBLIC_DIR" && -d "$PUBLIC_DIR" ]]; then
            cp "$OUTDIR/$TARGET.bar" "$PUBLIC_DIR/"
            echo "OK: copied to $PUBLIC_DIR/$TARGET.bar"
        fi
        exit 0
    fi
    echo "--- $IMG failed, trying next ---"
done

echo "ERROR: no image produced a build" >&2
exit 1
