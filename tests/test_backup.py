#!/usr/bin/env python3
"""Stdlib unit checks for profile look backups (overlay+graphics+media zips).
Run: python3 tests/test_backup.py"""
import importlib.util, os, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "backup_admin", os.path.join(ROOT, "src", "scripts", "backup_admin.py"))
ba = importlib.util.module_from_spec(spec); spec.loader.exec_module(ba)


def _sources(d):
    """Make a fake profile look: overlay/ + graphics/ + media/ with one file each."""
    overlay = os.path.join(d, "profiles", "x", "overlay")
    graphics = os.path.join(d, "runtime", "x", "graphics")
    media = os.path.join(d, "runtime", "x", "media")
    for sub in (overlay, graphics, media):
        os.makedirs(sub, exist_ok=True)
    with open(os.path.join(overlay, "hud.css"), "w") as f:
        f.write("body{}")
    with open(os.path.join(graphics, "Overlay.png"), "wb") as f:
        f.write(b"PNG")
    with open(os.path.join(media, "Intro.mp4"), "wb") as f:
        f.write(b"MP4")
    return {"overlay": overlay, "graphics": graphics, "media": media,
            "backups": os.path.join(d, "runtime", "x", "backups")}


def t_label_sanitize():
    assert ba.sanitize_label("Winter Theme!") == "winter-theme"
    assert ba.sanitize_label("  v2 2026 ") == "v2-2026"
    try:
        ba.sanitize_label("***"); raise AssertionError()
    except ValueError:
        pass


def t_create_writes_zip_with_manifest():
    d = tempfile.mkdtemp(); src = _sources(d)
    path = ba.create_backup("Winter Theme", src, profile="x")
    assert path.endswith(os.path.join("backups", "winter-theme.zip"))
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert "manifest.json" in names
        assert "overlay/hud.css" in names
        assert "graphics/Overlay.png" in names
        assert "media/Intro.mp4" in names
        import json
        man = json.loads(z.read("manifest.json"))
        assert man["label"] == "Winter Theme" and man["profile"] == "x"


def t_create_duplicate_needs_force():
    d = tempfile.mkdtemp(); src = _sources(d)
    ba.create_backup("dup", src, profile="x")
    try:
        ba.create_backup("dup", src, profile="x"); raise AssertionError()
    except FileExistsError:
        pass
    ba.create_backup("dup", src, profile="x", force=True)


def t_list_reads_manifests():
    d = tempfile.mkdtemp(); src = _sources(d)
    ba.create_backup("Alpha", src, profile="x")
    ba.create_backup("Beta", src, profile="x")
    items = ba.list_backups(src["backups"])
    labels = sorted(i["label"] for i in items)
    assert labels == ["Alpha", "Beta"], labels
    assert all("created" in i and "bytes" in i and i["slug"] for i in items)


def t_restore_full_replace():
    d = tempfile.mkdtemp(); src = _sources(d)
    ba.create_backup("snap", src, profile="x")
    # mutate live: extra graphic that must be DROPPED, changed css
    with open(os.path.join(src["graphics"], "Extra.png"), "wb") as f:
        f.write(b"X")
    with open(os.path.join(src["overlay"], "hud.css"), "w") as f:
        f.write("CHANGED")
    ba.restore_backup(os.path.join(src["backups"], "snap.zip"), src)
    with open(os.path.join(src["overlay"], "hud.css")) as f:
        assert f.read() == "body{}"
    assert not os.path.exists(os.path.join(src["graphics"], "Extra.png"))
    assert os.path.exists(os.path.join(src["graphics"], "Overlay.png"))


def t_restore_rejects_traversal():
    d = tempfile.mkdtemp(); src = _sources(d)
    bad = os.path.join(src["backups"], "bad.zip"); os.makedirs(src["backups"], exist_ok=True)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", '{"label":"bad","slug":"bad"}')
        zf.writestr("overlay/../../escape.txt", "x")
    try:
        ba.restore_backup(bad, src)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def t_restore_rejects_decompression_bomb_bytes():
    """A backup whose members decompress past the cap is rejected BEFORE
    extraction. (#99)"""
    d = tempfile.mkdtemp(); src = _sources(d)
    bad = os.path.join(src["backups"], "bomb.zip"); os.makedirs(src["backups"], exist_ok=True)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", '{"label":"b","slug":"b"}')
        zf.writestr("overlay/hud.css", "x" * 1000)
    orig = ba.MAX_BUNDLE_BYTES
    ba.MAX_BUNDLE_BYTES = 100
    try:
        ba.restore_backup(bad, src)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    finally:
        ba.MAX_BUNDLE_BYTES = orig


def t_restore_rejects_too_many_members():
    d = tempfile.mkdtemp(); src = _sources(d)
    bad = os.path.join(src["backups"], "many.zip"); os.makedirs(src["backups"], exist_ok=True)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", '{"label":"b","slug":"b"}')
        for i in range(20):
            zf.writestr(f"overlay/f{i}.css", "x")
    orig = ba.MAX_BUNDLE_MEMBERS
    ba.MAX_BUNDLE_MEMBERS = 5
    try:
        ba.restore_backup(bad, src)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    finally:
        ba.MAX_BUNDLE_MEMBERS = orig


def t_restore_missing_manifest_rejected():
    d = tempfile.mkdtemp(); src = _sources(d)
    bad = os.path.join(src["backups"], "nomani.zip"); os.makedirs(src["backups"], exist_ok=True)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("overlay/hud.css", "x")
    try:
        ba.restore_backup(bad, src)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def t_restore_unsafe_archive_leaves_live_untouched():
    d = tempfile.mkdtemp(); src = _sources(d)
    # a known-good live state
    with open(os.path.join(src["overlay"], "hud.css"), "w") as f:
        f.write("LIVE")
    bad = os.path.join(src["backups"], "bad2.zip"); os.makedirs(src["backups"], exist_ok=True)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", '{"label":"bad","slug":"bad"}')
        zf.writestr("/etc/evil.txt", "x")        # absolute path -> rejected
    try:
        ba.restore_backup(bad, src)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    with open(os.path.join(src["overlay"], "hud.css")) as f:
        assert f.read() == "LIVE"


def t_list_sorted_newest_first():
    d = tempfile.mkdtemp(); src = _sources(d)
    p1 = ba.create_backup("Old", src, profile="x")
    p2 = ba.create_backup("New", src, profile="x")
    # list_backups sorts on the manifest's `created`, so pin two distinct stamps.
    import json as _j, zipfile as _z
    def stamp(path, created):
        with _z.ZipFile(path) as z:
            data = {n: z.read(n) for n in z.namelist()}
        man = _j.loads(data["manifest.json"]); man["created"] = created
        data["manifest.json"] = _j.dumps(man).encode()
        with _z.ZipFile(path, "w") as z:
            for n, b in data.items():
                z.writestr(n, b)
    stamp(p1, "2026-01-01T00:00:00Z")
    stamp(p2, "2026-06-01T00:00:00Z")
    items = ba.list_backups(src["backups"])
    assert [i["label"] for i in items] == ["New", "Old"], items


def t_delete_backup():
    d = tempfile.mkdtemp(); src = _sources(d)
    ba.create_backup("gone", src, profile="x")
    p = os.path.join(src["backups"], "gone.zip")
    assert os.path.exists(p)
    assert ba.delete_backup(src["backups"], "gone") is True
    assert not os.path.exists(p)
    assert ba.delete_backup(src["backups"], "gone") is False   # already gone


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
