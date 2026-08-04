#!/usr/bin/env bash
# Build one unsigned .bar from the shared codebase:
#   ./build-bar.sh grok     ->  dist/GrokRemote.bar    (Grok Remote)
#   ./build-bar.sh claude   ->  dist/ClaudeRemote.bar  (Claude Remote)
#   ./build-bar.sh all      ->  both
# Uses a host-installed BB10 NDK (/opt/bbndk) — same flow that produced the
# on-device-proven GrokRemote/ClaudeSessions bars.
set -euo pipefail

VARIANT="${1:-all}"
if [[ "$VARIANT" == "all" ]]; then
  "$0" grok
  "$0" claude
  exit 0
fi
if [[ "$VARIANT" != "grok" && "$VARIANT" != "claude" ]]; then
  echo "usage: $0 [grok|claude|all]" >&2
  exit 2
fi
if [[ "$VARIANT" == "grok" ]]; then
  TARGET=GrokRemote
else
  TARGET=ClaudeRemote
fi

BBNDK="${BBNDK:-/opt/bbndk}"
ENV_SH="$BBNDK/bbndk-env_10_3_1_995.sh"

if [[ ! -f "$ENV_SH" ]]; then
  echo "ERROR: BB10 NDK not found at $BBNDK" >&2
  echo "Expected: $ENV_SH" >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="${OUTDIR:-$(cd "$APP_DIR/.." && pwd)/dist}"
mkdir -p "$OUTDIR"

# 32-bit host tools need i386 libz (installed under /lib/i386-linux-gnu)
export LD_LIBRARY_PATH="/lib/i386-linux-gnu:/usr/local/lib32-bbndk${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# shellcheck disable=SC1090
set +u
# shellcheck source=/dev/null
source "$ENV_SH"
set -u
export CPUVARDIR=armle-v7

cd "$APP_DIR"

rm -rf arm Makefile Makefile.* release debug 2>/dev/null || true
mkdir -p arm/o.le-v7 arm/o.le-v7-g release debug

qmake -spec blackberry-armv7le-qcc \
  CONFIG+=device \
  CONFIG+=release \
  CONFIG-=debug \
  "VARIANT=$VARIANT" \
  app.pro

if [[ -f Makefile.Release ]]; then
  make -f Makefile.Release -j"$(nproc 2>/dev/null || echo 2)"
elif [[ -f Makefile ]]; then
  make -j"$(nproc 2>/dev/null || echo 2)"
else
  echo "ERROR: no Makefile generated" >&2
  exit 1
fi

BIN=""
for c in "arm/o.le-v7/$TARGET" "arm/o.le-v7-g/$TARGET" "$TARGET"; do
  if [[ -f "$c" ]]; then BIN="$c"; break; fi
done
if [[ -z "$BIN" ]]; then
  echo "ERROR: binary not found after make" >&2
  exit 1
fi
echo "Binary: $BIN"
file "$BIN"

# Keep the unstripped binary of every sideloaded release: crash dumps are
# raw addresses that only symbolicate against this exact build.
cp -a "$BIN" "$OUTDIR/$TARGET.symbols.elf"

STAGE=$(mktemp -d "/tmp/${TARGET}-stage.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
cp -a "$BIN" "$STAGE/$TARGET"
cp -a "variant/$VARIANT/bar-descriptor.xml" "$STAGE/"
cp -a assets "$STAGE/assets"
cp -a "variant/$VARIANT/icon.png" "$STAGE/icon.png"

cd "$STAGE"
# unsigned, development mode for sideload without signing keys
blackberry-nativepackager -package "$OUTDIR/$TARGET.bar" \
  -devMode \
  bar-descriptor.xml \
  -e "$TARGET" app \
  -e icon.png icon.png \
  -C . assets

echo "OK: $OUTDIR/$TARGET.bar"
ls -la "$OUTDIR/$TARGET.bar"
file "$OUTDIR/$TARGET.bar"

# Same convention as build-bar-docker.sh: land every build in the sideload
# pickup folder. PUBLIC_DIR= to override, PUBLIC_DIR="" to skip.
PUBLIC_DIR="${PUBLIC_DIR-$HOME/Public}"
if [[ -n "$PUBLIC_DIR" && -d "$PUBLIC_DIR" ]]; then
  cp "$OUTDIR/$TARGET.bar" "$PUBLIC_DIR/"
  echo "OK: copied to $PUBLIC_DIR/$TARGET.bar"
fi
