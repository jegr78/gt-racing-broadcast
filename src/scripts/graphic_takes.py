#!/usr/bin/env python3
"""Crew graphic takes: which broadcast stills a commentator or Race Control may put
on air, and what a take does in OBS. Pure: no relay imports, no OBS. The relay
applies the returned intents and routes flags through FlagGraphicStore.

Spec: docs/superpowers/specs/2026-10-04-crew-pages-mobile-graphic-takes-design.md
"""

import itertools
import threading

import flag_graphic   # siblings in src/scripts (sys.path injected by the relay/tests)
import obs_ws

COMMENTATOR = "commentator"
RACE_CONTROL = "race_control"

ROLES = (COMMENTATOR, RACE_CONTROL)
MODES = ("off", "request", "direct")
_RANK = {mode: n for n, mode in enumerate(MODES)}
REQUEST_TTL_S = 60.0

# OBS source names, which are also the Sheet asset labels. Standby Cover is the
# panel's RED FLAG and the grid rows belong to the director's grid sequence.
DIRECTOR_ONLY = ("Standby Cover",) + tuple(f"Grid Row {n}" for n in range(1, 9))
EDITORIAL_PROGRAM = tuple(s for s in obs_ws.GRAPHIC_SOURCES if s not in DIRECTOR_ONLY)
EDITORIAL_INTERVIEW = ("Post Race Interviews",)


# The Director Panel's graphic buses, in its button order and with its short labels.
PANEL_STANDARD = ("Standings", "Schedule", "Race Results", "Quali Results",
                  "Race Weather 1", "Race Weather 2", "Quali Weather")
PANEL_PRE_RACE = ("Weekend Info", "Race Info", "Next Event")
PANEL_LABELS = {
    "Stint HUD": "HUD", "Split HUD": "HUD", "Standings": "STANDINGS",
    "Schedule": "SCHEDULE", "Race Results": "RACE RESULTS",
    "Quali Results": "QUALI RESULTS", "Race Weather 1": "RACE WX 1",
    "Race Weather 2": "RACE WX 2", "Quali Weather": "QUALI WX",
    "Post Race Interviews": "POST-RACE", "Weekend Info": "WEEKEND",
    "Race Info": "RACE INFO", "Next Event": "NEXT EVENT", "Starting Grid": "STARTING GRID",
}


def panel_catalog(solo):
    """The Director Panel's graphic buses for the endurance or the solo collection:
    {"graphics", "graphicsPreRace", "graphicsGrid"}, each a list of
    {"label", "scene", "source"} (the grid's lead key also has "top")."""
    program = obs_ws.graphic_scene(solo)

    def key(source, scene):
        return {"label": PANEL_LABELS.get(source, source.upper()), "scene": scene,
                "source": source}
    hud = [key("Stint HUD", program)] + ([] if solo else [key("Split HUD", "Splitscreen")])
    grid = [dict(key("Starting Grid", program), top=True)]
    grid += [dict(key(f"Grid Row {n}", program), label=f"GRID R{n}") for n in range(1, 9)]
    return {
        "graphics": hud + [key(s, program) for s in PANEL_STANDARD]
                    + [key("Post Race Interviews", "Interview")],
        "graphicsPreRace": [key(s, program) for s in PANEL_PRE_RACE],
        "graphicsGrid": grid,
    }


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


def entry_mode(entry, roles, modes):
    """The caller's mode for one graphic: the most permissive over the caller's
    roles that may take it. *modes* maps a role to its mode. Race Control flags
    go on air directly in request mode too."""
    best = "off"
    for role in set(entry["roles"]) & set(roles):
        mode = normalize_mode(modes.get(role))
        if _RANK[mode] > _RANK[best]:
            best = mode
    return "direct" if entry["flag"] and best == "request" else best


def may_take(entry, roles, modes):
    return entry_mode(entry, roles, modes) == "direct"


def may_request(entry, roles, modes):
    return entry["flag"] is None and entry_mode(entry, roles, modes) == "request"


def may_hide(entry, roles, modes, crew_current):
    """Take rights hide any graphic; in request mode the crew still hides the one
    graphic the crew put on air."""
    return may_take(entry, roles, modes) or (
        may_request(entry, roles, modes) and crew_current == entry["source"])


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

    def forget(self, source):
        """The director toggled *source*: it is no longer a crew graphic."""
        with self.lock:
            if self.current == source:
                self.current = None
            self.by.pop(source, None)


def chat_line(name, source, on):
    return f"{name} put {source} on air" if on else f"{name} took {source} off air"


def request_line(name, source):
    return f"{name} asked for {source}"


def decline_line(source):
    return f"Director declined {source}"


class TakeModes:
    """The take mode per crew role: the league value plus the director's live
    override. In memory, so a relay start returns to the league value."""

    def __init__(self, league):
        self.league = normalize_mode(league)
        self.lock = threading.Lock()
        self.override = {}

    def set(self, role, mode):
        if role not in ROLES or mode not in MODES:
            return False
        with self.lock:
            self.override[role] = mode
        return True

    def snapshot(self):
        with self.lock:
            return {role: self.override.get(role, self.league) for role in ROLES}


class Requests:
    """Crew requests for the director. Requests for one graphic merge into one
    entry that keeps its first deadline. An answered or expired request stays
    readable for REQUEST_TTL_S so the requester sees the outcome."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self._ids = itertools.count(1)

    @staticmethod
    def _copy(item):
        return dict(item, keys=list(item["keys"]), by=list(item["by"]))

    def _prune(self, now):
        for item in self.items:
            if item["state"] == "pending" and now - item["at"] > REQUEST_TTL_S:
                item.update(state="expired", done_at=item["at"] + REQUEST_TTL_S)
        self.items = [i for i in self.items
                      if i["state"] == "pending" or now - i["done_at"] <= REQUEST_TTL_S]

    def add(self, source, key, name, now):
        """(request, merged) for a crew member asking for *source*."""
        with self.lock:
            self._prune(now)
            for item in self.items:
                if item["state"] == "pending" and item["source"] == source:
                    if key not in item["keys"]:
                        item["keys"].append(key)
                        item["by"].append(name)
                    return self._copy(item), True
            item = {"id": next(self._ids), "source": source, "keys": [key],
                    "by": [name], "at": now, "state": "pending", "done_at": None}
            self.items.append(item)
            return self._copy(item), False

    def pending(self, now):
        with self.lock:
            self._prune(now)
            return [self._copy(i) for i in self.items if i["state"] == "pending"]

    def get(self, rid, now):
        with self.lock:
            self._prune(now)
            item = next((i for i in self.items
                         if i["id"] == rid and i["state"] == "pending"), None)
            return self._copy(item) if item else None

    def resolve(self, rid, state, now):
        """Mark a pending request taken or declined; None when it is not pending.
        A take may also close a request that expired while OBS applied it."""
        with self.lock:
            self._prune(now)
            for item in self.items:
                if item["id"] == rid and (item["state"] == "pending" or
                                          (state == "taken" and item["state"] == "expired")):
                    item.update(state=state, done_at=now)
                    return self._copy(item)
            return None

    def states_for(self, key, now):
        """{source: state} of the requests *key* took part in, newest wins."""
        with self.lock:
            self._prune(now)
            return {i["source"]: i["state"] for i in self.items if key in i["keys"]}

    def drop_pending(self):
        with self.lock:
            self.items = [i for i in self.items if i["state"] != "pending"]
