#!/usr/bin/env python3
"""Stdlib checks for build.py's secret-pattern verify. Run: python3 tests/test_build.py

Importing build.py only runs its module-level defs (the __main__ guard does not
fire), so this never triggers an actual build."""
import importlib.util, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The POV feed and every toggled still-graphic in the Stint scene carry a baked-in
# 300 ms Fade show/hide transition, so on-air elements ease in and out instead of
# cutting hard on each obs-websocket visibility toggle. (#291)
OBS_COLLECTION = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
FADE_ITEMS = [
    "Feed POV", "Standby Cover", "Standings", "Schedule", "Race Results",
    "Quali Results", "Race Weather 1", "Race Weather 2", "Quali Weather",
]
FADE_DURATION_MS = 300
spec = importlib.util.spec_from_file_location(
    "build", os.path.join(ROOT, "tools", "build.py"))
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)


def _build_src():
    with open(os.path.join(ROOT, "tools", "build.py"), encoding="utf-8") as fh:
        return fh.read()


def _served_html_relpaths():
    """All src/ HTML pages the relay/Control Center serve (posix relpaths under
    src/), excluding the docs/ subtree (cheat_sheets.html ships inside docs/slides/)."""
    src = os.path.join(ROOT, "src")
    out = []
    for dirpath, _dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        if rel.split(os.sep)[0] == "docs":
            continue
        for f in files:
            if f.endswith(".html"):
                p = os.path.normpath(os.path.join(rel, f)) if rel != "." else f
                out.append(p.replace(os.sep, "/"))
    return sorted(out)


def t_has_appscript_secret_flags_exec_endpoint():
    # the SHEET_PUSH_URL secret class most likely to leak into the OBS json
    assert b.has_appscript_secret("https://script.google.com/macros/s/ABC123def/exec")
    assert b.has_appscript_secret(
        '{"url": "https://script.googleusercontent.com/macros/echo?key=secret"}')
    assert b.has_appscript_secret("anything .../exec trailing")


def t_has_appscript_secret_flags_key_query():
    assert b.has_appscript_secret("https://api.example.com/data?key=AIzaSyXXXX")
    assert b.has_appscript_secret("https://x/y?a=1&key=zzz")


def t_has_appscript_secret_clean_text_passes():
    assert not b.has_appscript_secret("http://127.0.0.1:8088/hud")
    assert not b.has_appscript_secret("__RACECAST_GRAPHICS__/Overlay.png")
    assert not b.has_appscript_secret("http://127.0.0.1:8088/timer/data")
    assert not b.has_appscript_secret("")


def _wholedir_copies(build_src):
    """Top-level src items copied verbatim, e.g. cp("ui", "ui") -> {"ui", ...}."""
    return set(re.findall(r'cp\("([^"/]+)",\s*"[^"]*"\)', build_src))


def t_every_served_html_page_is_shipped():
    # Each relay/Control-Center-served .html must be copied into the dist package, or
    # the distributed package 404s that page. The standalone binary has its own guard
    # in test_build_binary's t_every_served_html_dir_is_bundled.
    build_src = _build_src()
    wholedirs = _wholedir_copies(build_src)
    missing = []
    for rel in _served_html_relpaths():
        top = rel.split("/")[0]
        shipped = (f'cp("{rel}"' in build_src) or (top in wholedirs)
        if not shipped:
            missing.append(rel)
    assert not missing, (
        "served HTML pages not copied into the dist package by tools/build.py "
        f"(they would 404 in the distributed package): {missing}")


def t_console_pages_are_shipped():
    # Explicit pins for the two pages a release test found missing. (#244)
    build_src = _build_src()
    assert 'cp("console/console.html"' in build_src, "console.html not shipped"
    assert 'cp("console/buttons.html"' in build_src, "buttons.html not shipped"


def _stint_items():
    with open(OBS_COLLECTION, encoding="utf-8") as fh:
        coll = json.load(fh)
    for src in coll["sources"]:
        if src.get("id") == "scene" and src.get("name") == "Stint":
            return {it["name"]: it for it in src["settings"]["items"]}
    raise AssertionError("Stint scene not found in OBS collection")


def t_stint_graphics_carry_fade_transitions():
    # Each in-scope item eases in/out: a 300 ms fade_transition on show AND hide.
    items = _stint_items()
    for name in FADE_ITEMS:
        assert name in items, f"{name!r} missing from Stint scene"
        item = items[name]
        for key in ("show_transition", "hide_transition"):
            tr = item.get(key, {})
            assert tr.get("id") == "fade_transition", \
                f"{name} {key} is not a fade ({tr!r})"
            assert tr.get("duration") == FADE_DURATION_MS, \
                f"{name} {key} duration != {FADE_DURATION_MS} ({tr!r})"


def _all_scene_items():
    """(scene_name, item) for every scene item in the collection."""
    with open(OBS_COLLECTION, encoding="utf-8") as fh:
        coll = json.load(fh)
    out = []
    for src in coll["sources"]:
        if src.get("id") == "scene":
            for it in src.get("settings", {}).get("items", []):
                out.append((src["name"], it))
    return coll, out


def _all_groups(coll):
    """Group sources, whether stored in the OBS 31+ top-level `groups` array or
    inline in `sources` with id=='group'."""
    return list(coll.get("groups", [])) + [
        s for s in coll["sources"] if s.get("id") == "group"]


def t_no_orphaned_group_item_backup():
    # OBS sets group_item_backup=true only on a scene item that belongs to a GROUP,
    # as the backup of that membership. When the group is absent, OBS treats the item
    # as an orphaned backup and DROPS it on import, so every flagged item must be a
    # member of some group.
    coll, items = _all_scene_items()
    members = {m["name"] for g in _all_groups(coll)
               for m in g.get("settings", {}).get("items", [])}
    orphaned = [f"{scene}/{it['name']}" for scene, it in items
                if it.get("group_item_backup") and it["name"] not in members]
    assert not orphaned, (
        "scene items flagged group_item_backup=true without a parent group. "
        f"OBS drops these on import: {orphaned}")


# The per-scene HUD groups are separate so the Splitscreen group can carry the
# CURRENT/NEXT labels and the Stint one cannot.
HUD_GROUPS = {
    "Stint HUD": {"Overlay", "HUD Overlay"},
    "Split HUD": {"Overlay", "HUD Overlay", "Splitscreen Labels"},
}


def t_hud_groups_present_with_members():
    coll, _ = _all_scene_items()
    groups = {g["name"]: {m["name"] for m in g.get("settings", {}).get("items", [])}
              for g in _all_groups(coll)}
    for name, want in HUD_GROUPS.items():
        assert name in groups, f"group {name!r} missing from the collection"
        assert groups[name] == want, \
            f"group {name!r} members {groups[name]} != expected {want}"


def t_director_panel_targets_existing_hud_groups():
    # The Director Panel toggles the HUD group by name per scene; those names
    # must match the groups in the collection or the toggle silently no-ops.
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"),
              encoding="utf-8") as fh:
        panel = fh.read()
    assert 'scene:"Stint",       source:"Stint HUD"' in panel \
        or 'source:"Stint HUD"' in panel, "panel does not target 'Stint HUD'"
    assert 'source:"Split HUD"' in panel, "panel does not target 'Split HUD'"
    assert 'source:"HUD"' not in panel, "panel still targets the removed 'HUD' group"


def t_all_scene_and_group_items_locked():
    # Every source is edit-locked in the shipped collection so a producer cannot
    # nudge a placed source by accident.
    coll, items = _all_scene_items()
    unlocked = [f"{scene}/{it['name']}" for scene, it in items if not it.get("locked")]
    for g in _all_groups(coll):
        for m in g.get("settings", {}).get("items", []):
            if not m.get("locked"):
                unlocked.append(f"group {g['name']}/{m['name']}")
    assert not unlocked, f"scene/group items not edit-locked: {unlocked}"


def t_solo_templates_secret_free_and_tokenized():
    for fn in ("GT_Racing_Solo_Commentary.json", "GT_Racing_Solo_POV.json"):
        with open(os.path.join(ROOT, "src", "obs", fn), encoding="utf-8") as fh:
            raw = fh.read()
        assert not b.has_appscript_secret(raw), fn
        assert "__RACECAST_CAPTURE__" in raw and "__RACECAST_WEBCAM__" in raw, fn


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
