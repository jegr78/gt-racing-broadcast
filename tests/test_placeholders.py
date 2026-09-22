#!/usr/bin/env python3
"""Stdlib unit checks for the neutral asset-placeholder helper + bundled assets.
Run: python3 tests/test_placeholders.py"""
import os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLACEHOLDERS = os.path.join(ROOT, "src", "assets", "placeholders")
PNG = os.path.join(PLACEHOLDERS, "transparent-1080p.png")
MP4 = os.path.join(PLACEHOLDERS, "neutral-5s-1080p.mp4")


def t_bundled_graphic_is_1080p_rgba_png():
    assert os.path.isfile(PNG), "bundled transparent PNG missing"
    with open(PNG, "rb") as fh:
        data = fh.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    # IHDR is the first chunk: 8-byte sig, 4-byte len, 4-byte 'IHDR', then payload.
    w, h, depth, color = struct.unpack(">IIBB", data[16:26])
    assert (w, h) == (1920, 1080), f"wrong size {(w, h)}"
    assert depth == 8 and color == 6, f"expected 8-bit RGBA, got depth={depth} color={color}"


def t_bundled_media_clip_is_a_small_mp4():
    assert os.path.isfile(MP4), "bundled neutral clip missing"
    with open(MP4, "rb") as fh:
        head = fh.read(12)
    assert head[4:8] == b"ftyp", "not an MP4 (no ftyp box)"
    assert os.path.getsize(MP4) < 2 * 1024 * 1024, "clip unexpectedly large (>2 MB)"


import sys, tempfile

sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import placeholders as ph  # noqa: E402


def t_expected_graphics_extracts_sorted_unique():
    text = ('{"a":"__RACECAST_GRAPHICS__/Weather Sunny.png",'
            '"b":"__RACECAST_GRAPHICS__/Overlay.png",'
            '"c":"__RACECAST_GRAPHICS__/Overlay.png",'
            '"d":"__RACECAST_MEDIA__/intro.mp4"}')
    assert ph.expected_graphics_from_template(text) == ["Overlay.png", "Weather Sunny.png"]


def t_expected_graphics_empty_when_no_refs():
    assert ph.expected_graphics_from_template('{"x":"y"}') == []


def t_expected_graphics_rejects_path_traversal_refs():
    """A ref name must be a bare basename. A '/'-bearing ref is not captured, so no
    consumer can join it into a path that escapes the graphics dir. (#324)"""
    text = ('{"a":"__RACECAST_GRAPHICS__/../../etc/evil.png",'
            '"b":"__RACECAST_GRAPHICS__/sub/dir/x.png",'
            '"c":"__RACECAST_GRAPHICS__/Overlay.png"}')
    assert ph.expected_graphics_from_template(text) == ["Overlay.png"]
    media = '{"m":"__RACECAST_MEDIA__/../evil.mp4","n":"__RACECAST_MEDIA__/intro.mp4"}'
    assert ph.expected_media_from_template(media) == ["intro.mp4"]


def t_find_obs_template_prefers_template_then_json():
    with tempfile.TemporaryDirectory() as tmp:
        assert ph.find_obs_template(tmp) is None
        open(os.path.join(tmp, "GT_Racing_Endurance.json"), "w").close()
        assert ph.find_obs_template(tmp).endswith("GT_Racing_Endurance.json")
        open(os.path.join(tmp, "GT_Racing_Endurance.template.json"), "w").close()
        assert ph.find_obs_template(tmp).endswith("GT_Racing_Endurance.template.json")


def t_placeholder_paths_resolve_to_bundled_files():
    assert ph.graphic_placeholder_path() == PNG
    assert ph.media_placeholder_path() == MP4


def t_fill_missing_writes_only_absent_byte_identical():
    with tempfile.TemporaryDirectory() as tmp:
        with open(PNG, "rb") as fh:
            src_bytes = fh.read()
        # An already-present file must not be touched or re-listed.
        with open(os.path.join(tmp, "Overlay.png"), "wb") as fh:
            fh.write(b"REAL")
        written = ph.fill_missing(["Overlay.png", "Weather Sunny.png"], tmp, PNG)
        assert written == ["Weather Sunny.png"]
        with open(os.path.join(tmp, "Overlay.png"), "rb") as fh:
            assert fh.read() == b"REAL"          # untouched
        with open(os.path.join(tmp, "Weather Sunny.png"), "rb") as fh:
            assert fh.read() == src_bytes        # byte-identical to the bundle
        assert not any(n.endswith(".part") for n in os.listdir(tmp))  # atomic, no temp left


def t_fill_missing_is_idempotent_and_creates_dir():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "gfx")        # does not exist yet
        first = ph.fill_missing(["A.png"], target, PNG)
        assert first == ["A.png"] and os.path.isfile(os.path.join(target, "A.png"))
        assert ph.fill_missing(["A.png"], target, PNG) == []   # second run: nothing


def t_fill_missing_tolerates_absent_source():
    with tempfile.TemporaryDirectory() as tmp:
        assert ph.fill_missing(["A.png"], tmp, None) == []
        assert ph.fill_missing(["A.png"], tmp, os.path.join(tmp, "nope.png")) == []


def t_reset_placeholders_overwrites_existing_byte_identical():
    # Unlike fill_missing, reset_placeholders replaces a stale real asset with the
    # placeholder, so a removed Sheet link does not leave the old file. (#387)
    with tempfile.TemporaryDirectory() as tmp:
        with open(PNG, "rb") as fh:
            src_bytes = fh.read()
        with open(os.path.join(tmp, "Standings.png"), "wb") as fh:
            fh.write(b"STALE-REAL-GRAPHIC")
        written = ph.reset_placeholders(["Standings.png", "Weather Sunny.png"], tmp, PNG)
        assert written == ["Standings.png", "Weather Sunny.png"]   # both written
        with open(os.path.join(tmp, "Standings.png"), "rb") as fh:
            assert fh.read() == src_bytes    # stale file replaced by the placeholder
        with open(os.path.join(tmp, "Weather Sunny.png"), "rb") as fh:
            assert fh.read() == src_bytes    # absent one written too
        assert not any(n.endswith(".part") for n in os.listdir(tmp))  # atomic


def t_reset_placeholders_tolerates_absent_source():
    with tempfile.TemporaryDirectory() as tmp:
        assert ph.reset_placeholders(["A.png"], tmp, None) == []
        assert ph.reset_placeholders(["A.png"], tmp, os.path.join(tmp, "nope.png")) == []


def t_setup_assets_fills_placeholders_for_missing():
    # Template-driven scan: each __RACECAST_MEDIA__/<name> reference gets the
    # placeholder its extension picks, the neutral clip for .mp4 and the ambient
    # loop for .mp3.
    import json, subprocess
    with tempfile.TemporaryDirectory() as tmp:
        tpl = os.path.join(tmp, "tpl.json")
        with open(tpl, "w", encoding="utf-8") as fh:
            json.dump({"name": "T", "sources": [
                {"settings": {"file": "__RACECAST_GRAPHICS__/Weather Sunny.png"}},
                {"settings": {"local_file": "__RACECAST_MEDIA__/intro.mp4"}},
                {"settings": {"local_file": "__RACECAST_MEDIA__/outro.mp4"}},
                {"settings": {"local_file": "__RACECAST_MEDIA__/trailer.mp4"}},
                {"settings": {"local_file": "__RACECAST_MEDIA__/intermission.mp3"}},
            ]}, fh)
        gfx, med = os.path.join(tmp, "gfx"), os.path.join(tmp, "med")
        out = os.path.join(tmp, "import.json")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "src", "setup-assets.py"),
             "--template", tpl, "--assets", os.path.join(ROOT, "src", "assets"),
             "--graphics", gfx, "--media", med, "--out", out, "--sheet-id", "x"],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert os.path.isfile(os.path.join(gfx, "Weather Sunny.png")), r.stdout
        assert os.path.isfile(os.path.join(med, "intro.mp4")), r.stdout
        assert os.path.isfile(os.path.join(med, "outro.mp4")), r.stdout
        assert os.path.isfile(os.path.join(med, "trailer.mp4")), r.stdout
        assert os.path.isfile(os.path.join(med, "intermission.mp3")), r.stdout
        assert "placeholder" in r.stdout.lower(), r.stdout


import importlib.util


def _load_script(rel):
    """Import a hyphen-named src script (e.g. relay/get-graphics.py) by path."""
    name = os.path.basename(rel).replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "src", rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def t_get_graphics_seeds_from_real_template():
    gg = _load_script("relay/get-graphics.py")
    here = os.path.join(ROOT, "src", "relay")
    with tempfile.TemporaryDirectory() as tmp:
        seeded = gg.seed_missing_graphics(tmp, here)
        assert seeded, "expected placeholders for collection-referenced graphics"
        with open(PNG, "rb") as a, open(os.path.join(tmp, seeded[0]), "rb") as b:
            assert a.read() == b.read()          # byte-identical to the bundle


def t_get_graphics_obs_template_dir_is_sibling():
    gg = _load_script("relay/get-graphics.py")
    assert gg.obs_template_dir(os.path.join(ROOT, "src", "relay")) == \
        os.path.join(ROOT, "src", "obs")


def t_get_media_seeds_placeholder_clip():
    gm = _load_script("relay/get-media.py")
    with tempfile.TemporaryDirectory() as tmp:
        seeded = gm.seed_missing_media(tmp, {"intro", "outro", "trailer"})
        assert sorted(seeded) == ["intro.mp4", "outro.mp4", "trailer.mp4"]
        with open(MP4, "rb") as a, open(os.path.join(tmp, "intro.mp4"), "rb") as b:
            assert a.read() == b.read()
        # An already-present clip is not overwritten or re-listed.
        assert gm.seed_missing_media(tmp, {"intro"}) == []


def t_build_binary_freezes_placeholders():
    with open(os.path.join(ROOT, "tools", "build-binary.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert '"placeholders"' in src, \
        "build-binary.py must --hidden-import placeholders (importlib-loaded scripts " \
        "import it; PyInstaller's static scan cannot see that)"


def t_build_py_seeds_and_relabels_placeholders():
    with open(os.path.join(ROOT, "tools", "build.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "import placeholders" in src
    assert "placeholders.fill_missing(" in src
    assert "[placeholder]" in src


def t_fill_missing_skips_path_traversal_names():
    with tempfile.TemporaryDirectory() as tmp:
        written = ph.fill_missing(["../evil.png", "sub/x.png", "ok.png"], tmp, PNG)
        assert written == ["ok.png"]
        assert os.listdir(tmp) == ["ok.png"]   # nothing escaped, no subdir created


def t_build_is_placeholder_detects_byte_identity():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "buildmod", os.path.join(ROOT, "tools", "build.py"))
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)
    with tempfile.TemporaryDirectory() as tmp:
        ph.fill_missing(["a.png"], tmp, PNG)          # tmp/a.png is byte-identical to the bundle
        real = os.path.join(tmp, "real.png")
        with open(real, "wb") as fh:
            fh.write(b"REALPNGDATA")
        assert bm._is_placeholder(os.path.join(tmp, "a.png"), PNG) is True
        assert bm._is_placeholder(real, PNG) is False
        assert bm._is_placeholder(os.path.join(tmp, "absent.png"), PNG) is False


def t_music_placeholder_file_committed():
    p = ph.music_placeholder_path()
    assert p and os.path.isfile(p), "neutral-ambient-loop.mp3 not committed under src/assets/placeholders/"
    assert os.path.getsize(p) > 1000


def t_media_placeholder_for_selects_by_extension():
    music = ph.music_placeholder_path()
    video = ph.media_placeholder_path()
    assert ph.media_placeholder_for("intermission.mp3") == music
    assert ph.media_placeholder_for("intro.mp4") == video
    assert ph.media_placeholder_for("outro.mp4") == video


def t_expected_media_from_template_finds_all():
    raw = ('"file":"__RACECAST_MEDIA__/intro.mp4" ... '
           '"file":"__RACECAST_MEDIA__/outro.mp4" ... '
           '"file":"__RACECAST_MEDIA__/intermission.mp3"')
    assert ph.expected_media_from_template(raw) == ["intermission.mp3", "intro.mp4", "outro.mp4"]


if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("t_") and callable(fn):
            fn(); print("ok", n)
    print("ALL PASS")
