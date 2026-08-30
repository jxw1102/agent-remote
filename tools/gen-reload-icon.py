#!/usr/bin/env python3
"""Draw the title-bar reload glyph, blackberry/assets/images/ic_reload.png.

A clockwise reload arrow: the ring breaks on the right, with a rounded tail cap
just past 3 o'clock and an arrowhead at 1 o'clock whose tip leads the sweep down
and to the right. Drawn at 8x and downscaled with Lanczos, because PIL has no
antialiasing and a 1:1 draw looks ragged on the device.

Run from anywhere:
    python3 tools/gen-reload-icon.py
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "blackberry", "assets", "images", "ic_reload.png")

# 96, not 48: the icon buttons scale with the screen (Passport ~73 px), so
# a 48 px source had to be upscaled there. Geometry lives in the 100-unit
# design space, so this is the same drawing with more pixels - the Classic
# still downscales it to 44.
SIZE = 96
SS = 8
WHITE = (255, 255, 255, 255)

R = 33.0    # ring mid-radius in a 100-unit design space
# A hair heavier than the 6.8 the larger action icons use: at 48px the
# reference weight thins out to ~3px and loses its punch in the title bar.
W = 8.0


def draw_reload(d, s, color=WHITE):
    def polar(rad, deg):
        a = math.radians(deg)
        return ((50 + rad * math.cos(a)) * s, (50 + rad * math.sin(a)) * s)

    # PIL grows the arc width inward, so the box is the ring's outer edge.
    rb = R + W / 2
    d.arc([(50 - rb) * s, (50 - rb) * s, (50 + rb) * s, (50 + rb) * s],
          start=2, end=308, fill=color, width=max(2, int(round(W * s))))
    tx, ty = polar(R, 2)
    h = W / 2 * s
    d.ellipse([tx - h, ty - h, tx + h, ty + h], fill=color)
    d.polygon([polar(R * 1.41, 306), polar(R * 1.08, 337), polar(R * 0.66, 294)],
              fill=color)


def main():
    big = SIZE * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw_reload(ImageDraw.Draw(img), big / 100.0)
    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
