#!/usr/bin/env python3
"""Pure helpers for the bundled overlay-font set: assemble a fonts.zip at build
time and extract it into runtime/fonts/ at app start. No network: fetching the
fonts is the maintainer tool's job (tools/fetch-fonts.py); this module only zips
bytes it is handed and unzips them safely. Stdlib only, unit-tested in
tests/test_fonts.py.

The zip carries a manifest.json {version, fonts:[names], stamp} where stamp is a
sha256 of the sorted font filenames. Extraction is stamp-gated (a marker file
records the last applied stamp, so an unchanged set is a cheap no-op every start),
per-file only-if-absent (never overwrites an operator's own font), and zip-slip
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


def extract_bundled(zip_path, dest):
    """Seed dest (runtime/fonts/) from zip_path's bundled font set.

    Stamp-gated: if the marker already records the zip's stamp, returns
    {"skipped": True, "extracted": []} without touching the filesystem. Otherwise
    extracts each font entry that passes font_name_ok + realpath containment and is
    not already present (never overwrites), then writes the marker. Returns
    {"skipped": False, "extracted": [names]}."""
    manifest = read_manifest(zip_path)
    if not manifest:
        return {"skipped": True, "extracted": []}
    stamp = manifest.get("stamp")
    if stamp and read_marker(dest) == stamp:
        return {"skipped": True, "extracted": []}
    os.makedirs(dest, exist_ok=True)
    base = os.path.realpath(dest)
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in manifest.get("fonts", []):
            if not font_name_ok(name):
                continue                          # zip-slip / junk entry -> skip
            target = os.path.realpath(os.path.join(base, name))
            if not target.startswith(base + os.sep):
                continue                          # escaped dest -> skip
            if os.path.exists(target):
                continue                          # never overwrite operator's own
            try:
                data = zf.read(name)
            except KeyError:
                continue                          # listed but missing in the zip
            with open(target + ".tmp", "wb") as fh:
                fh.write(data)
            os.replace(target + ".tmp", target)
            extracted.append(name)
    with open(os.path.join(dest, MARKER_NAME), "w", encoding="utf-8") as fh:
        json.dump({"stamp": stamp}, fh)
    return {"skipped": False, "extracted": extracted}
