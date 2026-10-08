#!/usr/bin/env python3
"""GT7 track recognition: name the layout from one lap of car positions.

A lap's (x, z) points every 20 m are compared with the racing lines in
signatures.json (plus learned rows): length within 3 %, inside the bounding box,
close to the line, and the driving direction separates a layout from its reverse.
"""
import json
import logging
import math

import gt7_data

LOG = logging.getLogger("racecast.relay.telemetry")

LENGTH_TOL = 0.03
BOX_MARGIN_M = 50.0
MAX_SCORE_M = 15.0
AMBIGUOUS_M = 3.0
GRID_M = 50.0
MIN_POINTS = 10
POINT_STEP_M = 20.0
LEARNED_FORMAT = "racecast-gt7-learned"


class _Line:
    """A closed racing line with cumulative distance and a grid for nearest-point lookups."""

    def __init__(self, path):
        self.pts = [(float(p[0]), float(p[1])) for p in path]
        self.cum = [0.0]
        for (x0, z0), (x1, z1) in zip(self.pts, self.pts[1:], strict=False):
            self.cum.append(self.cum[-1] + math.hypot(x1 - x0, z1 - z0))
        (xa, za), (xb, zb) = self.pts[-1], self.pts[0]
        self.length = self.cum[-1] + math.hypot(xb - xa, zb - za)
        self.grid = {}
        for i, (x, z) in enumerate(self.pts):
            self.grid.setdefault((int(x // GRID_M), int(z // GRID_M)), []).append(i)

    def nearest(self, x, z):
        """(index, distance) of the closest line point."""
        cx, cz = int(x // GRID_M), int(z // GRID_M)
        for r in (1, 2, 4, 8):
            best = None
            for gx in range(cx - r, cx + r + 1):
                for gz in range(cz - r, cz + r + 1):
                    for i in self.grid.get((gx, gz), ()):
                        d = math.hypot(self.pts[i][0] - x, self.pts[i][1] - z)
                        if best is None or d < best[1]:
                            best = (i, d)
            if best is not None and best[1] <= r * GRID_M:   # nothing closer outside the ring
                return best
        return min(((i, math.hypot(px - x, pz - z)) for i, (px, pz) in enumerate(self.pts)),
                   key=lambda t: t[1])

    def station(self, x, z):
        """Distance along the line from its first point to the projection of (x, z)."""
        i, _ = self.nearest(x, z)
        n = len(self.pts)
        best = None
        for a, b in (((i - 1) % n, i), (i, (i + 1) % n)):
            (ax, az), (bx, bz) = self.pts[a], self.pts[b]
            dx, dz = bx - ax, bz - az
            seg2 = dx * dx + dz * dz
            u = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / seg2))
            d = math.hypot(x - (ax + u * dx), z - (az + u * dz))
            s = self.cum[a] + u * math.sqrt(seg2)
            if best is None or d < best[0]:
                best = (d, s % self.length)
        return best[1]


def _direction(indices, n):
    """Positive when the indices mostly rise along the line (forward), negative when they fall."""
    total = 0
    for a, b in zip(indices, indices[1:], strict=False):
        step = (b - a) % n
        total += step - n if step > n / 2 else step
    return total


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


def _row(raw, provenance=None):
    path = raw.get("path")
    if not isinstance(path, list) or len(path) < MIN_POINTS:
        return None
    try:
        return {"id": str(raw["official_id"]), "length_m": float(raw["length_m"]),
                "box": (float(raw["min_x"]), float(raw["max_x"]),
                        float(raw["min_z"]), float(raw["max_z"])),
                "line": _Line(path),
                "reverse": (raw.get("reverse") or {}).get("official_id"),
                "official_name": raw.get("official_name") or "",
                "provenance": provenance or raw.get("provenance") or ""}
    except (KeyError, TypeError, ValueError):
        return None


class TrackDB:
    """Track catalogue plus recognisable signatures; never raises on bad data files."""

    def __init__(self, index_path, signatures_path, learned_path=None):
        self._learned_path = learned_path
        self._catalog = {}
        doc = _read_json(index_path) or {}
        for c in doc.get("configurations") or []:
            if isinstance(c, dict) and c.get("official_id"):
                self._catalog[str(c["official_id"])] = {
                    "id": str(c["official_id"]), "track": c.get("track") or "",
                    "layout": c.get("layout") or "", "reverse": bool(c.get("reverse")),
                    "country": c.get("country") or "", "length_m": c.get("length_m"),
                    "official_name": c.get("official_name") or ""}
        self._shipped = {}
        doc = _read_json(signatures_path) or {}
        for raw in doc.get("signatures") or []:
            row = _row(raw) if isinstance(raw, dict) else None
            if row:
                self._shipped[row["id"]] = row
        self._reload_learned()

    @classmethod
    def load(cls, runtime_base, bundled=None):
        return cls(gt7_data.resolve("index.json", runtime_base, bundled),
                   gt7_data.resolve("signatures.json", runtime_base, bundled),
                   gt7_data.learned_path(runtime_base) if runtime_base else None)

    def _read_learned(self):
        doc = _read_json(self._learned_path) if self._learned_path else None
        if not isinstance(doc, dict) or doc.get("format") != LEARNED_FORMAT:
            return {"format": LEARNED_FORMAT, "version": 1, "signatures": [],
                    "assignments": {}}
        doc.setdefault("signatures", [])
        doc.setdefault("assignments", {})
        return doc

    def _reload_learned(self):
        doc = self._read_learned()
        learned = {}
        for raw in doc["signatures"]:
            row = _row(raw, "learned") if isinstance(raw, dict) else None
            if row:
                learned[row["id"]] = row
        self._assign = dict(doc["assignments"]) if isinstance(doc["assignments"], dict) else {}
        self._rows = dict(self._shipped)
        self._rows.update(learned)                 # a learned row wins over the shipped one
        self._twins = {r["reverse"]: r["id"] for r in self._rows.values() if r["reverse"]}

    def name(self, official_id):
        info = self._catalog.get(official_id)
        if info is not None:
            return dict(info)
        row = self._rows.get(official_id)
        if row is None:
            return None
        return {"id": official_id, "track": row["official_name"], "layout": "",
                "reverse": False, "country": "", "length_m": row["length_m"],
                "official_name": row["official_name"]}

    def layouts(self):
        return sorted((dict(c) for c in self._catalog.values()),
                      key=lambda c: (c["track"], c["layout"], c["reverse"]))

    def _line_for(self, official_id):
        """(line, reversed) for a layout id, or (None, False)."""
        row = self._rows.get(official_id)
        if row is not None:
            return row["line"], False
        fwd = self._twins.get(official_id)
        if fwd is not None:
            return self._rows[fwd]["line"], True
        return None, False

    def distance_m(self, official_id, x, z):
        line, _rev = self._line_for(official_id)
        return line.nearest(x, z)[1] if line is not None else None

    def match(self, points, length_m):
        if not points or len(points) < MIN_POINTS or not length_m:
            return None
        scored = []
        for row in self._rows.values():
            ref = row["length_m"]
            if abs(ref - length_m) > LENGTH_TOL * ref:
                continue
            x0, x1, z0, z1 = row["box"]
            m = BOX_MARGIN_M
            if any(not (x0 - m <= x <= x1 + m and z0 - m <= z <= z1 + m) for x, z in points):
                continue
            near = [row["line"].nearest(x, z) for x, z in points]
            score = sum(d for _i, d in near) / len(near)
            if score > MAX_SCORE_M:
                continue
            if _direction([i for i, _d in near], len(row["line"].pts)) >= 0:
                scored.append((score, row["id"]))
            elif row["reverse"]:
                scored.append((score, row["reverse"]))
        if not scored:
            return None
        scored.sort()
        best_score, best_id = scored[0]
        close = [oid for s, oid in scored if s - best_score <= AMBIGUOUS_M]
        if len(close) > 1:
            return {"candidates": close}
        info = self.name(best_id) or {"track": best_id, "layout": "", "reverse": False}
        return {"id": best_id, "track": info["track"], "layout": info["layout"],
                "reverse": info["reverse"], "score_m": round(best_score, 2)}
