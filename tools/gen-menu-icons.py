#!/usr/bin/env python3
"""Re-render the six small UI glyphs at 81 px.

Why: these were 48 px, and every item in the application menu (top-bezel
swipe-down) uses one of them. BB10 draws an ActionItem's icon from the asset
itself — there is no size property to bind — so a 48 px file simply looks
small, next to the long-press menus whose icons are 81 px and look right.
81 px matches those, on the Classic as well as the Passport.

The glyphs are 1-bit line art, so a plain upscale would only blur them.
Instead each is supersampled, its alpha re-thresholded to restore hard
edges, then downsampled — which keeps the original drawing but with real
edges at the larger size. ic_settings is the exception: its faint inner
ring is sub-pixel in the source and thresholding shatters it into dashes,
so that one takes a straight Lanczos resize.

Inputs are the original 48 px files kept in tools/icon-src-48/ (the glyphs
have no vector source in this repo). Re-running is safe: it always reads
those, never the generated output.

    python3 tools/gen-menu-icons.py
"""
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "icon-src-48")
OUT = os.path.join(ROOT, "blackberry", "assets", "images")

SIZE = 81
SS = 8          # supersample factor before re-thresholding
ALPHA_CUT = 128  # >= this stays opaque

# ic_settings' ring is a sub-pixel stroke: thresholding breaks it up.
SMOOTH = {"ic_settings"}

NAMES = ["ic_settings", "ic_inbox", "ic_model", "ic_queue", "ic_tui", "ic_usage"]


def render(name):
    src = Image.open(os.path.join(SRC, name + ".png")).convert("RGBA")
    if name in SMOOTH:
        return src.resize((SIZE, SIZE), Image.LANCZOS)
    big = src.resize((SIZE * SS, SIZE * SS), Image.LANCZOS)
    r, g, b, a = big.split()
    # The art is pure white; only the alpha carries the shape.
    white = r.point(lambda v: 255)
    hard = Image.merge("RGBA", (white, white, white,
                                a.point(lambda v: 255 if v >= ALPHA_CUT else 0)))
    return hard.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    for name in NAMES:
        out = os.path.join(OUT, name + ".png")
        render(name).save(out)
        print("wrote %s (%dx%d)" % (out, SIZE, SIZE))


if __name__ == "__main__":
    main()
