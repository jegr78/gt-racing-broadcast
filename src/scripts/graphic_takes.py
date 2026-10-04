#!/usr/bin/env python3
"""Crew graphic takes: which broadcast stills a commentator or Race Control may put
on air, and what a take does in OBS. Pure: no relay imports, no OBS. The relay
applies the returned intents and routes flags through FlagGraphicStore.

Spec: docs/superpowers/specs/2026-10-04-crew-pages-mobile-graphic-takes-design.md
"""

import threading

import flag_graphic   # siblings in src/scripts (sys.path injected by the relay/tests)
import obs_ws

COMMENTATOR = "commentator"
RACE_CONTROL = "race_control"

# "request" joins once the Director Panel can show requests (#747).
MODES = ("off", "direct")

# OBS source names, which are also the Sheet asset labels. Standby Cover is the
# panel's RED FLAG and the grid rows belong to the director's grid sequence.
DIRECTOR_ONLY = ("Standby Cover",) + tuple(f"Grid Row {n}" for n in range(1, 9))
EDITORIAL_PROGRAM = tuple(s for s in obs_ws.GRAPHIC_SOURCES if s not in DIRECTOR_ONLY)
EDITORIAL_INTERVIEW = ("Post Race Interviews",)


def normalize_mode(raw):
    """The GRAPHICS_TAKE value, or "off" for a missing or unknown one."""
    mode = (raw or "").strip().lower()
    return mode if mode in MODES else "off"


def definitions(solo):
    """Every takeable graphic of the endurance or the solo collection, as
    {"source", "group", "scenes", "roles", "flag"} dicts."""
    program = obs_ws.graphic_scene(solo)
    out = []
    for source in EDITORIAL_PROGRAM:
        out.append({"source": source, "group": "editorial", "scenes": [program],
                    "roles": [COMMENTATOR, RACE_CONTROL], "flag": None})
    for source in EDITORIAL_INTERVIEW:
        out.append({"source": source, "group": "editorial", "scenes": ["Interview"],
                    "roles": [COMMENTATOR, RACE_CONTROL], "flag": None})
    for key, source in flag_graphic.FLAG_GRAPHIC_SOURCES.items():
        out.append({"source": source, "group": "flag",
                    "scenes": list(flag_graphic.flag_graphic_scenes(solo)),
                    "roles": [RACE_CONTROL], "flag": key})
    return out


def find(defs, source):
    return next((d for d in defs if d["source"] == source), None)


def may_take(entry, roles, mode):
    return normalize_mode(mode) != "off" and bool(set(entry["roles"]) & set(roles))


def scene_ok(entry, program_scene):
    """True while one of the graphic's scenes is the program scene."""
    return program_scene in entry["scenes"]


def take_intents(entry, on, crew_current, defs, visible):
    """(intents, new_crew_current) for an editorial take or hide. Intents are
    (scene, source, enabled); *visible* maps a source to what OBS reports. Crew
    takes keep one editorial graphic: a new take hides the previous crew take
    while it is still on air, never a graphic the director set."""
    intents = []
    if on:
        prev = find(defs, crew_current) if crew_current != entry["source"] else None
        if prev is not None and visible.get(prev["source"]):
            intents += [(sc, prev["source"], False) for sc in prev["scenes"]]
        if not visible.get(entry["source"]):
            intents += [(sc, entry["source"], True) for sc in entry["scenes"]]
        return intents, entry["source"]
    if visible.get(entry["source"]):
        intents += [(sc, entry["source"], False) for sc in entry["scenes"]]
    return intents, (None if crew_current == entry["source"] else crew_current)


class CrewTakes:
    """The editorial graphic the crew put on air and who set each one. In memory
    only: OBS stays the truth for what is visible."""

    def __init__(self):
        self.lock = threading.Lock()
        self.current = None
        self.by = {}

    def apply(self, entry, on, name, defs, visible, apply_fn):
        """Apply a take or hide through apply_fn(scene, source, enabled) -> (ok,
        note). Returns (intents, failures); the state changes only when every
        intent succeeded."""
        with self.lock:
            prev = self.current
            intents, new = take_intents(entry, on, prev, defs, visible)
            failed = []
            for scene, source, enabled in intents:
                ok, note = apply_fn(scene, source, enabled)
                if not ok:
                    failed.append(f"{source} in {scene}: {note}")
            if not failed:
                self.current = new
                if on:
                    self.by.pop(prev, None)
                    self.by[entry["source"]] = name
                else:
                    self.by.pop(entry["source"], None)
            return intents, failed

    def snapshot(self):
        with self.lock:
            return self.current, dict(self.by)


def chat_line(name, source, on):
    return f"{name} put {source} on air" if on else f"{name} took {source} off air"
