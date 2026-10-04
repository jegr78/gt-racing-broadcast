#!/usr/bin/env python3
"""Pure helpers for the bundled overlay-font set: assemble a fonts.zip at build
time and extract it into runtime/fonts/ at app start. No network: fetching the
fonts is the maintainer tool's job (tools/fetch-fonts.py); this module only zips
bytes it is handed and unzips them safely. Stdlib only, unit-tested in
tests/test_fonts.py.

The zip carries a manifest.json {version, fonts:[names], stamp} where stamp is a
sha256 of the sorted font filenames. Extraction is stamp-gated (a marker file
records the last applied stamp, so an unchanged set is a cheap no-op every start),
per-file only-if-absent unless the caller forces an overwrite, and zip-slip
safe (every entry is whitelist- and containment-checked, never a blind extractall).
"""
import hashlib, json, os, zipfile

import overlay_build as ob

MANIFEST_NAME = "manifest.json"
MARKER_NAME = ".bundled.json"


def font_name_ok(name):
    """True for a safe bundled font filename (whitelisted stem + known extension).
    Mirrors racecast._font_name_ok using the shared overlay_build constants."""
    return (isinstance(name, str) and bool(ob.FONT_NAME_RE.match(name))
            and "." in name and name.rsplit(".", 1)[1].lower() in ob.FONT_EXTS)


def compute_stamp(filenames):
    """A deterministic stamp for a font set = sha256 of the sorted filenames."""
    joined = "\n".join(sorted(filenames)).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def build_zip(zip_path, fonts, version="dev"):
    """Write fonts.zip at zip_path from {filename: bytes}. Adds manifest.json with
    {version, fonts:[sorted names], stamp}. Returns the stamp. Rejects unsafe names
    so a bad manifest can never be produced."""
    names = sorted(fonts)
    for n in names:
        if not font_name_ok(n):
            raise ValueError(f"unsafe font filename: {n!r}")
    stamp = compute_stamp(names)
    manifest = {"version": version, "fonts": names, "stamp": stamp}
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    tmp = zip_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for n in names:
            zf.writestr(n, fonts[n])
    os.replace(tmp, zip_path)
    return stamp


def read_manifest(zip_path):
    """The {version, fonts, stamp} dict from a fonts.zip (None on any problem)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except Exception:    # not a zip / no manifest -> caller treats as "nothing to do"
        return None


def read_marker(dest):
    """The last-applied stamp recorded in dest/.bundled.json, or None."""
    try:
        with open(os.path.join(dest, MARKER_NAME), encoding="utf-8") as fh:
            return json.load(fh).get("stamp")
    except Exception:    # absent / unreadable -> force a (re)extract
        return None


def _bundled_entries(zip_path, dest):
    """(zipfile, [(name, target path)]) for every safe manifest entry, or None when
    zip_path carries no manifest. Entries failing font_name_ok or realpath
    containment in dest are dropped."""
    manifest = read_manifest(zip_path)
    if not manifest:
        return None
    base = os.path.realpath(dest)
    entries = []
    for name in manifest.get("fonts", []):
        if not font_name_ok(name):
            continue                          # zip-slip / junk entry -> skip
        target = os.path.realpath(os.path.join(base, name))
        if target.startswith(base + os.sep):
            entries.append((name, target))
    return manifest, entries


def _write_entry(zf, name, target):
    """Write one zip entry atomically to target. False when the zip lacks it."""
    try:
        data = zf.read(name)
    except KeyError:
        return False                          # listed but missing in the zip
    with open(target + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(target + ".tmp", target)
    return True


def extract_bundled(zip_path, dest, overwrite=False, gated=True):
    """Seed dest (runtime/fonts/) from zip_path's bundled font set.

    Gated by default: if the marker already records the zip's stamp, returns
    {"skipped": True, "extracted": []} without touching the filesystem. Otherwise
    extracts each safe entry that is not already present, then writes the marker.
    overwrite=True also replaces same-named files and implies gated=False. Returns
    {"skipped": False, "extracted": [names]}."""
    found = _bundled_entries(zip_path, dest)
    if not found:
        return {"skipped": True, "extracted": []}
    manifest, entries = found
    stamp = manifest.get("stamp")
    if gated and not overwrite and stamp and read_marker(dest) == stamp:
        return {"skipped": True, "extracted": []}
    os.makedirs(dest, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for name, target in entries:
            if os.path.exists(target) and not overwrite:
                continue                      # operator's own font
            if _write_entry(zf, name, target):
                extracted.append(name)
    with open(os.path.join(dest, MARKER_NAME), "w", encoding="utf-8") as fh:
        json.dump({"stamp": stamp}, fh)
    return {"skipped": False, "extracted": extracted}


def replace_bundled_copies(zip_path, dest):
    """Overwrite the files in dest (a profile's overlay/fonts/) that share a name
    with a bundled font. Never adds a font dest lacks. Returns the replaced names."""
    found = _bundled_entries(zip_path, dest) if os.path.isdir(dest) else None
    if not found:
        return []
    replaced = []
    with zipfile.ZipFile(zip_path) as zf:
        for name, target in found[1]:
            if os.path.isfile(target) and _write_entry(zf, name, target):
                replaced.append(name)
    return replaced
