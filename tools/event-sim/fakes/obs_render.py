"""Program screenshots of the simulated OBS as PNG, stdlib only.

The picture paints the scene's layers bottom first at their canvas rects: a test pattern
per feed (colour bars plus the input name or its label) and a solid panel per opaque
item, under a top bar with the scene name. The same layers always give the same bytes."""
import struct
import zlib

FONT = {   # 5x7 glyphs, one 5-bit row each, top row first
    "A": (14, 17, 17, 31, 17, 17, 17), "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14), "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31), "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15), "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14), "J": (7, 2, 2, 2, 2, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17), "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17), "N": (17, 17, 25, 21, 19, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14), "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13), "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30), "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14), "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10), "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4), "Z": (31, 1, 2, 4, 8, 16, 31),
    "0": (14, 17, 19, 21, 25, 17, 14), "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31), "3": (31, 2, 4, 2, 1, 17, 14),
    "4": (2, 6, 10, 18, 31, 2, 2), "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14), "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14), "9": (14, 17, 17, 15, 1, 2, 12),
    " ": (0, 0, 0, 0, 0, 0, 0), "-": (0, 0, 0, 31, 0, 0, 0), ".": (0, 0, 0, 0, 0, 12, 12),
    ":": (0, 12, 12, 0, 12, 12, 0), "/": (1, 1, 2, 4, 8, 16, 16), "_": (0, 0, 0, 0, 0, 0, 31),
    "#": (10, 10, 31, 10, 31, 10, 10), "(": (2, 4, 8, 8, 8, 4, 2), ")": (8, 4, 2, 2, 2, 4, 8),
    "?": (14, 17, 1, 2, 4, 0, 4),
}
BARS = ((192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192),
        (192, 0, 0), (0, 0, 192))
CANVAS_W = 1920.0
TEXT = (255, 255, 255)
LABEL_BG = (0, 0, 0)


def _tint(name):
    """A stable dark colour for a scene background or an opaque item."""
    h = zlib.crc32(name.encode("utf-8"))
    return (32 + h % 64, 32 + (h >> 8) % 64, 48 + (h >> 16) % 64)


class Canvas:
    def __init__(self, width, height, colour=(0, 0, 0)):
        self.w, self.h = width, height
        self.px = bytearray(bytes(colour) * (width * height))

    def rect(self, x, y, w, h, colour):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(colour) * (x1 - x0)
        for yy in range(y0, y1):
            start = (yy * self.w + x0) * 3
            self.px[start:start + len(row)] = row

    def text(self, x, y, text, scale, colour=TEXT):
        for ch in text.upper():
            glyph = FONT.get(ch, FONT["?"])
            for r, bits in enumerate(glyph):
                for c in range(5):
                    if bits & (16 >> c):
                        self.rect(x + c * scale, y + r * scale, scale, scale, colour)
            x += 6 * scale

    def png(self):
        stride = self.w * 3
        raw = b"".join(b"\x00" + bytes(self.px[y * stride:(y + 1) * stride]) for y in range(self.h))

        def chunk(tag, body):
            return (struct.pack(">I", len(body)) + tag + body
                    + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def text_width(text, scale):
    return max(0, len(text) * 6 - 1) * scale


def _fit(text, width, scale):
    while scale > 1 and text_width(text, scale) > width:
        scale -= 1
    keep = max(1, (width // scale + 1) // 6)
    return (text if len(text) <= keep else text[:keep]), scale


def _label(canvas, x0, y0, w, h, text):
    label, scale = _fit(text, w - 8, max(1, h // 50))
    lw, lh = text_width(label, scale), 7 * scale
    lx, ly = x0 + (w - lw) // 2, y0 + (h - lh) // 2
    canvas.rect(lx - 2 * scale, ly - 2 * scale, lw + 4 * scale, lh + 4 * scale, LABEL_BG)
    canvas.text(lx, ly, label, scale)


def render_program(scene, layers, width=640, labels=None):
    """PNG bytes of `scene`. `layers` are {kind: feed|opaque, name, rect: [x, y, w, h]}
    on the 1920x1080 canvas, bottom first; `labels` maps a feed to the text it shows."""
    width = max(64, min(int(width or 640), 1920))
    height = width * 9 // 16
    k = width / CANVAS_W
    canvas = Canvas(width, height, _tint(scene))
    for layer in layers:
        x, y, w, h = (round(v * k) for v in layer["rect"])
        x0, y0 = max(0, x), max(0, y)
        w, h = min(width, x + w) - x0, min(height, y + h) - y0
        if w <= 0 or h <= 0:
            continue
        if layer["kind"] == "feed":
            for b, colour in enumerate(BARS):
                bx = x0 + w * b // len(BARS)
                canvas.rect(bx, y0, x0 + w * (b + 1) // len(BARS) - bx, h, colour)
            _label(canvas, x0, y0, w, h, (labels or {}).get(layer["name"]) or layer["name"])
        else:
            canvas.rect(x0, y0, w, h, _tint(layer["name"]))
            _label(canvas, x0, y0, w, h, layer["name"])
    bar = max(10, height // 8)
    canvas.rect(0, 0, width, bar, LABEL_BG)
    title, scale = _fit(scene or "?", width - 8, max(1, (bar - 4) // 7))
    canvas.text(4, (bar - 7 * scale) // 2, title, scale)
    return canvas.png()
