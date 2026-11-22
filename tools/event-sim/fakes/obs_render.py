"""Program screenshots of the simulated OBS as PNG, stdlib only.

The picture paints the scene's layers bottom first at their canvas rects: a test pattern
per feed (its grabbed frame, or else colour bars plus the input name or its label) and a
solid panel per opaque
item, under a top bar with the scene name. The same layers always give the same bytes."""
import operator
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

    def blit(self, x, y, w, h, rgb):
        """Copy a w*h RGB image to (x, y), clipped to the canvas."""
        x0, x1 = max(0, x), min(self.w, x + w)
        if x1 <= x0:
            return
        for row in range(max(0, -y), min(h, self.h - y)):
            src = (row * w + (x0 - x)) * 3
            dst = ((y + row) * self.w + x0) * 3
            self.px[dst:dst + (x1 - x0) * 3] = rgb[src:src + (x1 - x0) * 3]

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


def scale_rgb(rgb, sw, sh, dw, dh):
    """Nearest-neighbour resize of a sw*sh RGB image to dw*dh."""
    if (sw, sh) == (dw, dh):
        return rgb
    cols = [min(sw - 1, x * sw // dw) * 3 + c for x in range(dw) for c in range(3)]
    pick = operator.itemgetter(*cols)
    rows = []
    for y in range(dh):
        start = min(sh - 1, y * sh // dh) * sw * 3
        rows.append(bytes(pick(rgb[start:start + sw * 3])))
    return b"".join(rows)


CANVAS_H = 1080.0
FORMATS = ("png", "jpg", "jpeg")


def size(width=None, height=None):
    """The picture size OBS renders for a requested width and/or height: the canvas
    scaled to fit inside them, the full canvas without either."""
    if not width and not height:
        return int(CANVAS_W), int(CANVAS_H)
    k = min(width / CANVAS_W if width else float("inf"), height / CANVAS_H if height else float("inf"))
    return max(1, round(CANVAS_W * k)), max(1, round(CANVAS_H * k))


def pixel_rect(rect, width):
    """A canvas rect [x, y, w, h] in pixels of a picture `width` wide, unclipped."""
    k = width / CANVAS_W
    return tuple(round(v * k) for v in rect)


def render_program(scene, layers, picture=(640, 360), labels=None, fmt="png", quality=75):
    """Image bytes of `scene` at `picture` (width, height) as PNG or JPEG, see render_canvas."""
    canvas = render_canvas(scene, layers, picture, labels)
    return canvas.png() if fmt == "png" else jpeg(canvas.w, canvas.h, canvas.px, quality)


def render_canvas(scene, layers, picture=(640, 360), labels=None):
    """The Canvas of `scene` at `picture` (width, height). `layers` are {kind:
    feed|opaque, name, rect: [x, y, w, h], frame: (rgb, w, h) or None} on the 1920x1080
    canvas, bottom first, a frame scaled to its rect; `labels` maps a feed to the text
    its pattern shows."""
    width, height = picture
    canvas = Canvas(width, height, _tint(scene))
    for layer in layers:
        x, y, w, h = pixel_rect(layer["rect"], width)
        if layer.get("frame") and w > 0 and h > 0:
            canvas.blit(x, y, w, h, scale_rgb(*layer["frame"], w, h))
            continue
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
    return canvas


# Baseline JPEG, 4:2:0, stdlib only. Fixed-length Huffman codes keep the tables trivial:
# every DC category is a 4-bit code and every AC symbol an 8-bit one, valid but larger.
_ZIGZAG = [r * 8 + c for s in range(15)
           for r, c in (((i, s - i) for i in range(s, -1, -1)) if s % 2 == 0 else ((i, s - i) for i in range(s + 1)))
           if 0 <= r < 8 and 0 <= c < 8]
_LUMA_Q = (16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
           14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
           49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99)
_CHROMA_Q = (17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99, 24, 26, 56, 99, 99, 99, 99, 99,
             47, 66, 99, 99, 99, 99, 99, 99) + (99,) * 32
_AAN = (1.0, 1.387039845, 1.306562965, 1.175875602, 1.0, 0.785694958, 0.541196100, 0.275899379)
_DC_SYMBOLS = list(range(12))
_AC_SYMBOLS = [0x00, 0xF0] + [(run << 4) | size for run in range(16) for size in range(1, 11)]


def _qtable(base, quality):
    q = max(1, min(100, int(quality)))
    scale = 5000 // q if q < 50 else 200 - 2 * q
    return [max(1, min(255, (b * scale + 50) // 100)) for b in base]


def _fixed_codes(symbols, bits):
    return {sym: (n, bits) for n, sym in enumerate(symbols)}


_DC_CODES = _fixed_codes(_DC_SYMBOLS, 4)
_AC_CODES = _fixed_codes(_AC_SYMBOLS, 8)


def _fdct(block):
    """AAN float forward DCT of 64 samples, rows then columns, in place."""
    for step in (1, 8):
        for k in range(8):
            o = k * 8 if step == 1 else k
            d = [block[o + i * step] for i in range(8)]
            t0, t7 = d[0] + d[7], d[0] - d[7]
            t1, t6 = d[1] + d[6], d[1] - d[6]
            t2, t5 = d[2] + d[5], d[2] - d[5]
            t3, t4 = d[3] + d[4], d[3] - d[4]
            t10, t13, t11, t12 = t0 + t3, t0 - t3, t1 + t2, t1 - t2
            z1 = (t12 + t13) * 0.707106781
            out = [0.0] * 8
            out[0], out[4] = t10 + t11, t10 - t11
            out[2], out[6] = t13 + z1, t13 - z1
            t10, t11, t12 = t4 + t5, t5 + t6, t6 + t7
            z5 = (t10 - t12) * 0.382683433
            z2, z4, z3 = 0.541196100 * t10 + z5, 1.306562965 * t12 + z5, t11 * 0.707106781
            z11, z13 = t7 + z3, t7 - z3
            out[5], out[3], out[1], out[7] = z13 + z2, z13 - z2, z11 + z4, z11 - z4
            for i in range(8):
                block[o + i * step] = out[i]
    return block


class _Bits:
    """MSB-first bit writer with JPEG byte stuffing, drained in bulk."""

    def __init__(self):
        self.out, self.acc, self.n = bytearray(), 0, 0

    def put(self, value, length):
        self.acc = (self.acc << length) | (value & ((1 << length) - 1))
        self.n += length
        if self.n >= 4096:
            self._drain()

    def _drain(self):
        whole, rest = self.n // 8, self.n % 8
        chunk = (self.acc >> rest).to_bytes(whole, "big")
        self.out += chunk.replace(b"\xff", b"\xff\x00")
        self.acc &= (1 << rest) - 1
        self.n = rest

    def flush(self):
        if self.n % 8:
            pad = 8 - self.n % 8
            self.acc, self.n = (self.acc << pad) | ((1 << pad) - 1), self.n + pad
        self._drain()


def _category(v):
    return 0 if v == 0 else abs(v).bit_length()


def _amplitude(v, size):
    return v if v >= 0 else v + (1 << size) - 1


def _put_dc(bits, diff):
    size = _category(diff)
    code, length = _DC_CODES[size]
    bits.put((code << size) | (_amplitude(diff, size) if size else 0), length + size)


_EOB = _AC_CODES[0x00]
_ZRL = _AC_CODES[0xF0]


def _encode_block(bits, samples, divisors, prev_dc):
    coef = _fdct(samples)
    q = [int(round(coef[k] / d)) for k, d in zip(_ZIGZAG, divisors, strict=True)]
    _put_dc(bits, q[0] - prev_dc)
    run = 0
    for v in q[1:]:
        if v == 0:
            run += 1
            continue
        while run > 15:
            bits.put(*_ZRL)
            run -= 16
        size = _category(v)
        code, length = _AC_CODES[(run << 4) | size]
        bits.put((code << size) | _amplitude(v, size), length + size)
        run = 0
    if run:
        bits.put(*_EOB)
    return q[0]


def _segment(marker, body):
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def _dht(table_class, table_id, symbols, bits):
    counts = [0] * 16
    counts[bits - 1] = len(symbols)
    return _segment(0xC4, bytes([(table_class << 4) | table_id]) + bytes(counts) + bytes(symbols))


def _mcu_rows(px, width, height, mx, my):
    """The 16 rows of 16 RGB pixels of one MCU, edges repeated past the picture."""
    rows = []
    x0, x1 = mx * 16, min(width, mx * 16 + 16)
    for y in range(my * 16, my * 16 + 16):
        start = (min(y, height - 1) * width + x0) * 3
        row = px[start:start + (x1 - x0) * 3]
        if x1 - x0 < 16:
            row += row[-3:] * (16 - (x1 - x0))
        rows.append(row)
    return rows


def jpeg(width, height, px, quality=75):
    """Baseline JPEG bytes of a width*height RGB buffer. An MCU of one colour, most of a
    synthetic picture, is written from its DC values without a DCT."""
    lq, cq = _qtable(_LUMA_Q, quality), _qtable(_CHROMA_Q, quality)
    ldiv = [lq[k] * _AAN[k // 8] * _AAN[k % 8] * 8 for k in _ZIGZAG]
    cdiv = [cq[k] * _AAN[k // 8] * _AAN[k % 8] * 8 for k in _ZIGZAG]
    px = bytes(px)
    bits, dc = _Bits(), [0, 0, 0]
    for my in range((height + 15) // 16):
        for mx in range((width + 15) // 16):
            rows = _mcu_rows(px, width, height, mx, my)
            pixel = rows[0][:3]
            if rows.count(pixel * 16) == 16:
                R, G, B = pixel
                values = (0.299 * R + 0.587 * G + 0.114 * B - 128,
                          -0.168736 * R - 0.331264 * G + 0.5 * B,
                          0.5 * R - 0.418688 * G - 0.081312 * B)
                for n, (value, div) in enumerate(zip(values, (ldiv, cdiv, cdiv), strict=True)):
                    q = int(round(64 * value / div[0]))
                    for _ in range(4 if n == 0 else 1):
                        _put_dc(bits, q - dc[n])
                        bits.put(*_EOB)
                        dc[n] = q
                continue
            flat = b"".join(rows)
            r, g, b = flat[0::3], flat[1::3], flat[2::3]
            ys = [0.299 * R + 0.587 * G + 0.114 * B - 128 for R, G, B in zip(r, g, b, strict=True)]
            cbs = [-0.168736 * R - 0.331264 * G + 0.5 * B for R, G, B in zip(r, g, b, strict=True)]
            crs = [0.5 * R - 0.418688 * G - 0.081312 * B for R, G, B in zip(r, g, b, strict=True)]
            for by, bx in ((0, 0), (0, 8), (8, 0), (8, 8)):
                block = []
                for y in range(by, by + 8):
                    block.extend(ys[y * 16 + bx:y * 16 + bx + 8])
                dc[0] = _encode_block(bits, block, ldiv, dc[0])
            for n, plane in ((1, cbs), (2, crs)):
                block = [(plane[32 * y + 2 * x] + plane[32 * y + 2 * x + 1]
                          + plane[32 * y + 16 + 2 * x] + plane[32 * y + 17 + 2 * x]) / 4
                         for y in range(8) for x in range(8)]
                dc[n] = _encode_block(bits, block, cdiv, dc[n])
    bits.flush()
    head = (b"\xff\xd8"
            + _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + _segment(0xDB, b"\x00" + bytes(lq[k] for k in _ZIGZAG) + b"\x01" + bytes(cq[k] for k in _ZIGZAG))
            + _segment(0xC0, struct.pack(">BHHB", 8, height, width, 3)
                       + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")
            + _dht(0, 0, _DC_SYMBOLS, 4) + _dht(1, 0, _AC_SYMBOLS, 8)
            + _dht(0, 1, _DC_SYMBOLS, 4) + _dht(1, 1, _AC_SYMBOLS, 8)
            + _segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"))
    return head + bytes(bits.out) + b"\xff\xd9"
