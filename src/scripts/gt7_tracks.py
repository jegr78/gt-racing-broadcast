#!/usr/bin/env python3
"""GT7 track recognition: name the layout from one lap of car positions.

A lap's (x, z) points every 20 m are compared with the racing lines in
signatures.json (plus learned rows): length within 3 %, inside the bounding box,
close to the line, and the driving direction separates a layout from its reverse.
"""
import json
import math
import os

import gt7_data

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
        self.pts = []
        for p in path:
            try:
                self.pts.append((float(p[0]), float(p[1])))
            except (IndexError, TypeError, ValueError):
                continue                    # a short or non-numeric point is skipped
        self.cum = [0.0]
        for (x0, z0), (x1, z1) in zip(self.pts, self.pts[1:], strict=False):
            self.cum.append(self.cum[-1] + math.hypot(x1 - x0, z1 - z0))
        (xa, za), (xb, zb) = self.pts[-1], self.pts[0]
        self.length = self.cum[-1] + math.hypot(xb - xa, zb - za)
        self.grid = {}
        for i, (x, z) in enumerate(self.pts):
            self.grid.setdefault((int(x // GRID_M), int(z // GRID_M)), []).append(i)

    def _nearest_index(self, x, z):
        """Index of the closest vertex, via the grid (not a scan of the whole path)."""
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
                return best[0]
        return min(range(len(self.pts)),
                   key=lambda i: math.hypot(self.pts[i][0] - x, self.pts[i][1] - z))

    def locate(self, x, z):
        """(vertex index, distance to the line, station) of (x, z)'s projection onto
        the two segments next to the nearest vertex."""
        i = self._nearest_index(x, z)
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
        d, s = best
        return i, d, s

    def station(self, x, z):
        """Distance along the line from its first point to the projection of (x, z)."""
        return self.locate(x, z)[2]


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
    rev = raw.get("reverse")
    reverse_id = rev.get("official_id") if isinstance(rev, dict) else None
    try:
        return {"id": str(raw["official_id"]), "length_m": float(raw["length_m"]),
                "box": (float(raw["min_x"]), float(raw["max_x"]),
                        float(raw["min_z"]), float(raw["max_z"])),
                "line": _Line(path),
                "reverse": reverse_id,
                "official_name": raw.get("official_name") or "",
                "provenance": provenance or raw.get("provenance") or ""}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None


class TrackDB:
    """Track catalogue plus recognisable signatures; never raises on bad data files."""

    def __init__(self, index_path, signatures_path, learned_path=None):
        self._learned_path = learned_path
        self._catalog = {}
        doc = _read_json(index_path)
        cfgs = doc.get("configurations") if isinstance(doc, dict) else None
        for c in cfgs if isinstance(cfgs, list) else []:
            if isinstance(c, dict) and c.get("official_id"):
                self._catalog[str(c["official_id"])] = {
                    "id": str(c["official_id"]), "track": c.get("track") or "",
                    "layout": c.get("layout") or "", "reverse": bool(c.get("reverse")),
                    "country": c.get("country") or "", "length_m": c.get("length_m"),
                    "official_name": c.get("official_name") or ""}
        self._shipped = {}
        doc = _read_json(signatures_path)
        sigs = doc.get("signatures") if isinstance(doc, dict) else None
        for raw in sigs if isinstance(sigs, list) else []:
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
            return {"signatures": [], "assignments": {}}
        sigs = doc.get("signatures")
        assigns = doc.get("assignments")
        return {"signatures": sigs if isinstance(sigs, list) else [],
                "assignments": assigns if isinstance(assigns, dict) else {}}

    def _reload_learned(self):
        doc = self._read_learned()
        learned = {}
        for raw in doc["signatures"]:
            row = _row(raw, "learned") if isinstance(raw, dict) else None
            if row:
                learned[row["id"]] = row
        self._assign = dict(doc["assignments"])
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

    def line_length(self, official_id):
        line, _rev = self._line_for(official_id)
        return line.length if line is not None else None

    def project(self, points, official_id):
        line, rev = self._line_for(official_id)
        if line is None:
            return None
        out = []
        for x, z in points:
            try:
                finite = math.isfinite(x) and math.isfinite(z)
            except TypeError:
                finite = False
            if not finite:
                out.append(None)
                continue
            s = line.station(x, z)
            out.append((line.length - s) % line.length if rev else s)
        return out

    def assignment(self, key):
        return self._assign.get(key)

    def learn(self, official_id, points, length_m, key=None):
        """Store a learned signature for `official_id` (and the recording assignment `key`).

        A user-triggered write: a write failure (OSError) propagates to the caller
        instead of being swallowed, unlike the read paths elsewhere in this class."""
        if not self._learned_path:
            raise ValueError("no learned-tracks file configured")
        finite = []
        for x, z in points:
            try:
                ok = math.isfinite(x) and math.isfinite(z)
            except TypeError:
                ok = False
            if ok:
                finite.append((x, z))
        if len(finite) < MIN_POINTS:
            raise ValueError("a lap needs at least %d finite position points" % MIN_POINTS)
        xs, zs = [p[0] for p in finite], [p[1] for p in finite]
        info = self.name(official_id) or {}
        prev = self._rows.get(official_id)
        row = {"official_id": official_id,
               "official_name": info.get("official_name") or official_id,
               "length_m": round(float(length_m), 1),
               "min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs),
               "provenance": "learned",
               "reverse": {"official_id": prev["reverse"]} if prev and prev["reverse"] else None,
               "ambiguous_with": [], "flags": [],
               "path": [[round(x, 1), round(z, 1)] for x, z in finite]}
        doc = self._read_learned()
        doc["format"], doc["version"] = LEARNED_FORMAT, 1
        doc["signatures"] = [r for r in doc["signatures"]
                             if not (isinstance(r, dict) and r.get("official_id") == official_id)]
        doc["signatures"].append(row)
        if key:
            doc["assignments"][key] = official_id
        tmp = self._learned_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._learned_path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            os.replace(tmp, self._learned_path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass  # the failed write may never have created it
            raise
        self._reload_learned()

    def distance_m(self, official_id, x, z):
        try:
            finite = math.isfinite(x) and math.isfinite(z)
        except TypeError:
            return None
        if not finite:
            return None
        line, _rev = self._line_for(official_id)
        return line.locate(x, z)[1] if line is not None else None

    def match(self, points, length_m):
        if not points or len(points) < MIN_POINTS or not length_m:
            return None
        scored = {}              # official_id -> lowest score_m seen, forward or reverse
        via_reverse = set()
        for row in self._rows.values():
            ref = row["length_m"]
            if abs(ref - length_m) > LENGTH_TOL * ref:
                continue
            x0, x1, z0, z1 = row["box"]
            m = BOX_MARGIN_M
            if any(not (x0 - m <= x <= x1 + m and z0 - m <= z <= z1 + m) for x, z in points):
                continue
            located = [row["line"].locate(x, z) for x, z in points]
            score = sum(d for _i, d, _s in located) / len(located)
            if score > MAX_SCORE_M:
                continue
            forward = _direction([i for i, _d, _s in located], len(row["line"].pts)) >= 0
            if forward:
                oid = row["id"]
            elif row["reverse"]:
                oid = row["reverse"]
                via_reverse.add(oid)
            else:
                continue
            if oid not in scored or score < scored[oid]:
                scored[oid] = score
        if not scored:
            return None
        best_id, best_score = min(scored.items(), key=lambda kv: kv[1])
        close = [oid for oid, s in scored.items() if s - best_score <= AMBIGUOUS_M]
        if len(close) > 1:
            return {"candidates": close}
        info = self.name(best_id)
        reverse = info["reverse"] if info is not None else best_id in via_reverse
        track = info["track"] if info is not None else best_id
        layout = info["layout"] if info is not None else ""
        return {"id": best_id, "track": track, "layout": layout,
                "reverse": reverse, "score_m": round(best_score, 2)}
