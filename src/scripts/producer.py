#!/usr/bin/env python3
"""Pure parser for the league Sheet's read-only `Producer` tab
(`Part | Producer | MagicDNS`), the per-event producer handover schedule shown on
the Control Center Home view. No I/O: the Control Center provider fetches the gviz
CSV and tags each row with `self` after parsing.

A header row is REQUIRED; unlike Schedule and Crew there is no positional fallback.
An unrecognized header yields an empty list, and the Home card hides itself, rather
than a silent column mis-read."""
import csv
import io

PRODUCER_PART_HEADERS = ("part",)
PRODUCER_PRODUCER_HEADERS = ("producer",)
PRODUCER_MAGICDNS_HEADERS = ("magicdns", "magic-dns", "magicdns name", "magic dns")
PRODUCER_STREAMKEY_HEADERS = ("stream key", "streamkey", "key ref", "stream key ref")


def _find(header, names):
    """Index of the first cell in the already-normalized `header` that matches any
    of `names`, or None."""
    for i, cell in enumerate(header):
        if cell in names:
            return i
    return None


def _cell(row, i):
    return row[i].strip() if (0 <= i < len(row) and row[i]) else ""


def _fqdn_eq(value, self_name):
    """Exact FQDN equality, case-insensitive and ignoring a trailing dot, the same
    normalization as tailscale.magicdns_is_self. It is duplicated here so
    producer.py stays dependency-free. False when either side is blank."""
    a = (value or "").strip().rstrip(".").lower()
    b = (self_name or "").strip().rstrip(".").lower()
    return bool(a) and bool(b) and a == b


def resolve_producer_name(rows, self_magicdns):
    """This machine's producer display name by reverse-resolving its own MagicDNS
    name against the `Producer` tab: the first row whose `magicdns` FQDN equals
    `self_magicdns` and that carries a non-empty `producer`. Returns "" when own
    identity is unknown, no row matches, or the matched row has no producer; the
    caller then falls back to the hostname."""
    for r in rows or []:
        if r.get("producer") and _fqdn_eq(r.get("magicdns"), self_magicdns):
            return r["producer"]
    return ""


def parse_producer_rows(text):
    """Parse the `Producer` tab CSV into [{"part","producer","magicdns","stream_key"}, ...].

    A header is REQUIRED: returns [] unless part, producer and magicdns are all
    located in row 1 by case-insensitive match. The stream key column is optional and
    defaults to an empty string. Order and duplicate rows are preserved, since one
    producer may do consecutive parts. Cells are trimmed. A row whose Producer AND
    MagicDNS are both blank is a spacer and is dropped, while a present Producer with
    an empty MagicDNS is kept and the UI renders it with a disabled action."""
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return []
    header = [(c or "").strip().lower() for c in rows[0]]
    pi = _find(header, PRODUCER_PART_HEADERS)
    ri = _find(header, PRODUCER_PRODUCER_HEADERS)
    mi = _find(header, PRODUCER_MAGICDNS_HEADERS)
    if pi is None or ri is None or mi is None:
        return []
    ki = _find(header, PRODUCER_STREAMKEY_HEADERS)   # optional -> None if absent
    out = []
    for row in rows[1:]:
        part, prod, magic = _cell(row, pi), _cell(row, ri), _cell(row, mi)
        if not prod and not magic:
            continue
        skey = _cell(row, ki) if ki is not None else ""
        out.append({"part": part, "producer": prod, "magicdns": magic,
                    "stream_key": skey})
    return out


def part_kind(label):
    """Classify a Producer part by its label: 'qualifying' when the trimmed,
    uppercased label starts with 'Q', else 'race'. The qualifying broadcast is
    modelled as a 'Q' row in the same Producer tab, so the Parts control can show
    the race parts in race mode and the single Q part in qualifying mode."""
    return "qualifying" if str(label or "").strip().upper().startswith("Q") else "race"


def active_producer_rows(rows, mode):
    """The subset of parsed Producer rows whose part_kind matches the relay mode:
    'qualifying' -> the Q rows, anything else -> the numeric race rows. Order is
    preserved and empty rows return []."""
    want = "qualifying" if mode == "qualifying" else "race"
    return [r for r in (rows or []) if part_kind(r.get("part")) == want]
