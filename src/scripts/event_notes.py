#!/usr/bin/env python3
"""Pure parser for the Sheet `Event Notes` tab (#owner-notes).

Header-located like broadcast_chat.parse_channel_tab: a `Note` column is
required; `Heading` and `Priority` are optional. Each data row with a non-empty
Note becomes {"heading", "note", "priority"} with priority normalised to
"important" (the only highlighted value) or "info" (empty/unknown). No header /
no Note column / empty CSV -> [] (the modal then has nothing to show). No I/O and
no network; the relay's EventNotesSource does the fetch."""

import csv
import io

NOTE_HEADERS = ("note", "notes", "text")
HEADING_HEADERS = ("heading", "title", "section")
PRIORITY_HEADERS = ("priority", "level")


def _col(header, names):
    return next((header.index(h) for h in names if h in header), None)


def parse_event_notes(text):
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return []
    header = [(h or "").strip().lower() for h in rows[0]]
    note_i = _col(header, NOTE_HEADERS)
    if note_i is None:
        return []
    head_i = _col(header, HEADING_HEADERS)
    pri_i = _col(header, PRIORITY_HEADERS)
    out = []
    for r in rows[1:]:
        note = r[note_i].strip() if len(r) > note_i else ""
        if not note:
            continue
        heading = r[head_i].strip() if head_i is not None and len(r) > head_i else ""
        pri_raw = r[pri_i].strip().lower() if pri_i is not None and len(r) > pri_i else ""
        priority = "important" if pri_raw == "important" else "info"
        out.append({"heading": heading, "note": note, "priority": priority})
    return out
