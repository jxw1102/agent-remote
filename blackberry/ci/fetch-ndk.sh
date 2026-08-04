#!/bin/bash
# Assemble a Linux BB10 NDK (QNX_HOST + QNX_TARGET) in $1 from public sources.
#
# BlackBerry's servers are gone and only the *win32* SDK zips were mirrored
# to archive.org / the Wayback Machine, so the Linux host toolchain (qcc,
# ntoarm binutils, qmake, blackberry-nativepackager) is recovered from
# community Docker images that were built while the servers were alive.
# The target sysroot (target_*/qnx6) is host-independent; if the image
# doesn't carry one, the mirrored win32 "libraries" zip fills it in.
set -uo pipefail

DEST="${1:-$HOME/bbndk}"
ZIPS="${NDK_ZIP_CACHE:-$HOME/bbndk-zips}"
mkdir -p "$DEST" "$ZIPS"

log() { echo "[fetch-ndk] $*"; }

fetch() { # url out
    local url="$1" out="$2"
    if [ -s "$out" ]; then
        log "cached: $(basename "$out") ($(du -h "$out" | cut -f1))"
        return 0
    fi
    log "downloading: $url"
    curl -fL --retry 3 --retry-delay 5 -o "$out.part" "$url" && mv "$out.part" "$out"
}

# ---------------------------------------------------------------------------
# 1. Linux host toolchain from community Docker images
# ---------------------------------------------------------------------------

IMAGES="${NDK_DOCKER_IMAGES:-uvatbc/bbndk yamsergey/bb10-ndk delaya73/bbndk accupara/bbndk}"

image_tag() { # repo -> best tag (prefers latest)
    curl -fsL --max-time 30 "https://hub.docker.com/v2/repositories/$1/tags?page_size=25" \
        | python3 -c '
import json, sys
try:
    names = [t["name"] for t in json.load(sys.stdin)["results"]]
except Exception:
    names = []
if "latest" in names:
    print("latest")
elif names:
    print(names[0])
'
}

extract_from_image() { # image:tag -> 0 if linux host toolchain extracted
    local ref="$1" cid hits
    log "pulling $ref ..."
    if ! timeout 900 docker pull "$ref" >/dev/null 2>&1; then
        log "   pull failed"
        return 1
    fi
    cid=$(docker create "$ref" /bin/true 2>/dev/null) || cid=$(docker create "$ref" 2>/dev/null) || {
        log "   create failed"; return 1; }
    log "   scanning filesystem for NDK dirs..."
    hits=$(docker export "$cid" | tar -t 2>/dev/null \
        | grep -E 'host_10[^/]*/linux/x86/usr/bin/qcc$|target_10[^/]*/qnx6/usr/include/stdio.h$' || true)
    if [ -n "$hits" ]; then
        echo "$hits" | sed 's/^/      /'
    fi
    if ! echo "$hits" | grep -q 'linux/x86/usr/bin/qcc'; then
        log "   no linux qcc in this image"
        docker rm "$cid" >/dev/null 2>&1
        return 1
    fi
    log "   extracting host_10*/target_10* trees (this takes a few minutes)..."
    docker export "$cid" | tar -x -C "$DEST" --wildcards '*host_10*' '*target_10*' 2>/dev/null || true
    docker rm "$cid" >/dev/null 2>&1
    docker rmi "$ref" >/dev/null 2>&1 || true
    return 0
}

find_qnx_host() { find "$DEST" -maxdepth 8 -type d -path '*host_10*/linux/x86' 2>/dev/null | head -1; }
find_qnx_target() { find "$DEST" -maxdepth 8 -type d -name qnx6 2>/dev/null \
    | grep -E 'target_10' | head -1; }

QNX_HOST=$(find_qnx_host)
if [ -z "$QNX_HOST" ]; then
    for img in $IMAGES; do
        tag=$(image_tag "$img")
        if [ -z "$tag" ]; then
            log "$img: no tags on docker hub"
            continue
        fi
        log "$img: candidate tag '$tag'"
        if extract_from_image "$img:$tag"; then
            break
        fi
    done
    QNX_HOST=$(find_qnx_host)
fi

if [ -z "$QNX_HOST" ]; then
    log "FATAL: no Docker image yielded a Linux host toolchain"
    exit 1
fi
log "QNX_HOST candidate: $QNX_HOST"

# ---------------------------------------------------------------------------
# 2. Target sysroot — from the image if present, else the mirrored zips
# ---------------------------------------------------------------------------

QNX_TARGET=$(find_qnx_target)
if [ -z "$QNX_TARGET" ]; then
    log "no target sysroot in image — using mirrored win32 zips (host-independent)"
    IA="https://archive.org/download/bbdevtools"
    fetch "$IA/bbndk.win32.libraries.10.3.1.995.zip" "$ZIPS/bbndk.win32.libraries.10.3.1.995.zip"
    fetch "$IA/bbndk.win32.qconfigmk.10.3.1.995.zip" "$ZIPS/bbndk.win32.qconfigmk.10.3.1.995.zip"
    unzip -oq "$ZIPS/bbndk.win32.libraries.10.3.1.995.zip" -d "$DEST"
    unzip -oq "$ZIPS/bbndk.win32.qconfigmk.10.3.1.995.zip" -d "$DEST"
    QNX_TARGET=$(find_qnx_target)
fi

if [ -z "$QNX_TARGET" ]; then
    log "FATAL: no target sysroot found"
    exit 1
fi
log "QNX_TARGET candidate: $QNX_TARGET"

# ---------------------------------------------------------------------------
# 3. Sanity + env file
# ---------------------------------------------------------------------------

log "host tools sample:"
ls "$QNX_HOST/usr/bin" 2>/dev/null | grep -E '^(qcc|qmake|moc|blackberry-nativepackager|ntoarmv7-g\+\+)' | sed 's/^/   /'
log "qcc file type: $(file -b "$QNX_HOST/usr/bin/qcc" 2>/dev/null)"

{
    echo "export QNX_HOST=$QNX_HOST"
    echo "export QNX_TARGET=$QNX_TARGET"
    echo "export QNX_CONFIGURATION=\$HOME/.rim"
    echo "export PATH=$QNX_HOST/usr/bin:\$PATH"
} > "$DEST/env.sh"
log "wrote $DEST/env.sh"
