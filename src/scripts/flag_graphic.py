#!/usr/bin/env python3
"""Flag-status graphics, parallel to the flag-text chip: a pure value->OBS-source
mapping, mutual-exclusion intents and a small persisted store. No relay imports;
the relay wires obs_ws in as the store's apply_fn.

Canonical keys are the slugified flag conditions; the OBS source name equals the
Sheet Assets label equals the PNG basename (e.g. key 'safety-car' -> 'Flag Safety
Car' -> 'Flag Safety Car.png'). Flags are mutually exclusive: at most one graphic
is visible, in BOTH the Stint and Splitscreen scenes, or none."""

import json
import os
import threading

# Scenes that carry the flag-graphic scene items (both get all five, kept in
# sync so a scene switch preserves the shown flag). Mirrors the OBS collection.
FLAG_GRAPHIC_SCENES = ("Stint", "Splitscreen")

# Canonical key -> OBS source name (== Sheet Assets label == PNG basename).
FLAG_GRAPHIC_SOURCES = {
    "green": "Flag Green",
    "yellow": "Flag Yellow",
    "red": "Flag Red",
    "safety-car": "Flag Safety Car",
    "virtual-safety-car": "Flag Virtual Safety Car",
}

# Input aliases accepted by normalize_flag_value (parity with the HUD flag chip).
FLAG_GRAPHIC_ALIASES = {"sc": "safety-car", "vsc": "virtual-safety-car"}


def normalize_flag_value(raw):
    """Canonical key for *raw*, or '' for empty/clear, or None for an unknown
    non-empty value. Lowercases, trims and slugifies spaces to dashes, then
    applies the alias map, so 'Safety Car', 'safety-car' and 'sc' all map to
    'safety-car'."""
    if raw is None:
        return ""
    slug = "-".join(str(raw).strip().lower().split())
    if not slug:
        return ""
    slug = FLAG_GRAPHIC_ALIASES.get(slug, slug)
    return slug if slug in FLAG_GRAPHIC_SOURCES else None


def flag_graphic_intents(active):
    """[(scene, source, enabled), …] for every flag source in every flag scene;
    enabled is True only for *active*'s source. active '' / None / unknown -> all
    hidden. Deterministic order (scenes outer, sources inner)."""
    shown = FLAG_GRAPHIC_SOURCES.get(active)
    out = []
    for scene in FLAG_GRAPHIC_SCENES:
        for source in FLAG_GRAPHIC_SOURCES.values():
            out.append((scene, source, source == shown))
    return out


def _noop_apply(scene, source, enabled):
    return False, "obs unavailable"


class FlagGraphicStore:
    """Active flag-graphic state: in memory, in a restart-safe JSON file, and
    applied to OBS through an injected apply_fn (the relay passes
    obs_ws.set_scene_item_enabled). There is NO sheet sync, because this is OBS
    source visibility rather than a HUD value. Selecting a flag shows its source
    and hides the other four in both scenes; clear hides all. Best-effort
    throughout: an OBS failure degrades to a note and the state is still stored."""

    def __init__(self, path, apply_fn=None):
        self.path = path
        self.apply_fn = apply_fn or _noop_apply
        self.lock = threading.Lock()
        self.active = ""                         # canonical key or ""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # fresh layout; _save_file degrades per-write if the dir is missing
        self._load_file()

    def _load_file(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return  # no/corrupt file -> keep default ""
        if isinstance(saved, dict) and saved.get("active") in FLAG_GRAPHIC_SOURCES:
            self.active = saved["active"]

    def _save_file(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"active": self.active}, fh)
        except OSError:
            pass  # best-effort, same contract as the timer/event caches

    def get(self):
        with self.lock:
            return self.active

    def data(self):
        return {"active": self.get()}

    def set(self, raw):
        key = normalize_flag_value(raw)
        if key is None:
            return {"error": f"unknown flag graphic: {raw!r} "
                             f"(one of {', '.join(FLAG_GRAPHIC_SOURCES)})"}
        with self.lock:
            self.active = key
            self._save_file()
            self._apply_locked()
            return {"ok": True, "active": self.active}

    def clear(self):
        return self.set("")

    def reassert(self):
        """Re-push the persisted active flag to OBS. Best-effort."""
        with self.lock:
            self._apply_locked()

    def _apply_locked(self):
        for scene, source, enabled in flag_graphic_intents(self.active):
            self.apply_fn(scene, source, enabled)   # (ok, note) ignored, best-effort
