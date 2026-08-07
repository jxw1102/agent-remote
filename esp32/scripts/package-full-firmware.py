#!/usr/bin/env python3
"""Build a single full-flash image for the LILYGO T-LoRa Pager.

PlatformIO leaves bootloader.bin / partitions.bin / firmware.bin as separate
images. This merges them (plus Arduino boot_app0) into one binary you can
flash at offset 0x0:

    esptool.py --chip esp32s3 write_flash 0x0 agentremote-full-tlora-pager.bin

Run after:

    pio run -e tlora_pager_sx1262
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

# ESP32 Arduino / PlatformIO default layout (16 MB, default_16MB.csv).
BOOTLOADER_OFF = 0x0
PARTITIONS_OFF = 0x8000
BOOT_APP0_OFF = 0xE000
APP_OFF = 0x10000

# One entry per supported device; --device selects (multi-device ready).
DEVICES = {
    "tlora-pager": "tlora_pager_sx1262",
    "tdeck": "tdeck",  # EXPERIMENTAL — not yet verified on hardware
}
DEFAULT_DEVICE = "tlora-pager"


def _find_boot_app0() -> Path:
    home = Path(os.environ.get("PLATFORMIO_CORE_DIR") or
                (Path.home() / ".platformio"))
    patterns = [
        home / "packages" / "framework-arduinoespressif32" /
        "tools" / "partitions" / "boot_app0.bin",
        home / "packages" / "framework-arduinoespressif32*" /
        "tools" / "partitions" / "boot_app0.bin",
    ]
    for p in patterns:
        if p.is_file():
            return p
        for match in glob.glob(str(p)):
            mp = Path(match)
            if mp.is_file():
                return mp
    raise FileNotFoundError(
        "boot_app0.bin not found under ~/.platformio/packages "
        "(run `pio run` once so the Arduino framework is installed)")


def merge_images(parts: list[tuple[int, Path]]) -> bytes:
    """Pad with 0xFF (erased flash) and write each image at its offset."""
    end = 0
    blobs: list[tuple[int, bytes]] = []
    for off, path in parts:
        data = path.read_bytes()
        blobs.append((off, data))
        end = max(end, off + len(data))
    out = bytearray(b"\xff" * end)
    for off, data in blobs:
        out[off: off + len(data)] = data
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--device",
        choices=sorted(DEVICES),
        default=DEFAULT_DEVICE,
        help="Target device (default: %s)" % DEFAULT_DEVICE,
    )
    ap.add_argument(
        "--build-dir",
        type=Path,
        default=None,
        help="PlatformIO build dir (default: .pio/build/<env>)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <pager>/dist)",
    )
    args = ap.parse_args()

    env = DEVICES[args.device]
    out_name = "agentremote-full-%s.bin" % args.device
    pager_root = Path(__file__).resolve().parents[1]
    build_dir = args.build_dir or (pager_root / ".pio" / "build" / env)
    out_dir = args.out_dir or (pager_root / "dist")

    bootloader = build_dir / "bootloader.bin"
    partitions = build_dir / "partitions.bin"
    firmware = build_dir / "firmware.bin"
    for p in (bootloader, partitions, firmware):
        if not p.is_file():
            print("missing %s — run: pio run -e %s" % (p, env), file=sys.stderr)
            return 1

    boot_app0 = _find_boot_app0()
    parts = [
        (BOOTLOADER_OFF, bootloader),
        (PARTITIONS_OFF, partitions),
        (BOOT_APP0_OFF, boot_app0),
        (APP_OFF, firmware),
    ]
    print("Merging full flash image:")
    for off, path in parts:
        print("  0x%05x  %s  (%d bytes)" % (off, path, path.stat().st_size))

    image = merge_images(parts)
    out_dir.mkdir(parents=True, exist_ok=True)
    full_path = out_dir / out_name
    full_path.write_bytes(image)

    print("Wrote %s (%d bytes)" % (full_path, len(image)))
    print("Flash full image: esptool.py --chip esp32s3 write_flash 0x0 %s"
          % full_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
