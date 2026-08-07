"""114x114 BB10 icon for Agent Remote, no PIL: the Android launcher motif -
an orange chevron '>' and a cyan underscore on a dark CIRCLE."""
import struct
import zlib
import math

W = H = 114

def dist_to_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

# Android vector viewport is 108; scale to 114.
S = 114 / 108.0
CHEVRON = [(34, 40, 48, 54), (48, 54, 34, 68)]          # orange, width 7
UNDERSCORE = [(56, 68, 76, 68)]                          # cyan, width 7
STROKE = 7 * S / 2.0                                     # half-width

BG = (18, 18, 26)          # #12121A
ORANGE = (217, 119, 87)    # #D97757
CYAN = (0, 212, 255)       # #00D4FF
RADIUS = W / 2.0 - 1.0     # circular tile, 1px breathing room for AA

rows = []
for y in range(H):
    row = bytearray()
    row.append(0)  # filter: none
    for x in range(W):
        # circular alpha for the tile itself
        d = math.hypot(x + 0.5 - W / 2.0, y + 0.5 - H / 2.0)
        tile_a = max(0.0, min(1.0, RADIUS - d + 0.5))

        r, g, b = BG
        for (x1, y1, x2, y2) in CHEVRON:
            d = dist_to_segment(x, y, x1 * S, y1 * S, x2 * S, y2 * S)
            a = max(0.0, min(1.0, STROKE - d + 0.5))
            if a > 0:
                r = int(r + (ORANGE[0] - r) * a)
                g = int(g + (ORANGE[1] - g) * a)
                b = int(b + (ORANGE[2] - b) * a)
        for (x1, y1, x2, y2) in UNDERSCORE:
            d = dist_to_segment(x, y, x1 * S, y1 * S, x2 * S, y2 * S)
            a = max(0.0, min(1.0, STROKE - d + 0.5))
            if a > 0:
                r = int(r + (CYAN[0] - r) * a)
                g = int(g + (CYAN[1] - g) * a)
                b = int(b + (CYAN[2] - b) * a)
        row += bytes((r, g, b, int(255 * tile_a)))
    rows.append(bytes(row))

def chunk(tag, payload):
    data = tag + payload
    return struct.pack(">I", len(payload)) + data + struct.pack(
        ">I", zlib.crc32(data) & 0xFFFFFFFF)

png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
png += chunk(b"IEND", b"")

out = "blackberry/variant/unified/icon.png"
with open(out, "wb") as f:
    f.write(png)
print("wrote", out, len(png), "bytes")
