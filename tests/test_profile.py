#!/usr/bin/env python3
"""Stdlib unit checks for the profile management commands
(src/scripts/profile_admin.py). Run: python3 tests/test_profile.py"""
import importlib.util, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "src", "scripts")
sys.path.insert(0, SCRIPTS)   # so profile_admin's `import config` resolves
spec = importlib.util.spec_from_file_location(
    "profile_admin", os.path.join(SCRIPTS, "profile_admin.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return
    raise AssertionError("expected exception")


def t_parse_list_takes_no_args():
    assert m.parse_profile_args(["list"]) == {
        "verb": "list", "name": None, "source": "example",
        "no_assets": False, "out": None, "file": None, "force": False,
        "kind": "endurance", "template": None}
    _raises(lambda: m.parse_profile_args(["list", "extra"]))


def t_parse_show_optional_name():
    assert m.parse_profile_args(["show"])["name"] is None
    assert m.parse_profile_args(["show", "demo"])["name"] == "demo"
    _raises(lambda: m.parse_profile_args(["show", "a", "b"]))


def t_parse_use_requires_one_name():
    assert m.parse_profile_args(["use", "erf"]) == {
        "verb": "use", "name": "erf", "source": "example",
        "no_assets": False, "out": None, "file": None, "force": False,
        "kind": "endurance", "template": None}
    # --force lets `profile use` switch past a running relay/streams (#273)
    assert m.parse_profile_args(["use", "erf", "--force"])["force"] is True
    assert m.parse_profile_args(["use", "erf"])["force"] is False
    _raises(lambda: m.parse_profile_args(["use"]))
    _raises(lambda: m.parse_profile_args(["use", "a", "b"]))
    _raises(lambda: m.parse_profile_args(["use", "--force"]))   # --force needs a name


def t_parse_new_with_from():
    assert m.parse_profile_args(["new", "erf"]) == {
        "verb": "new", "name": "erf", "source": "example",
        "no_assets": False, "out": None, "file": None, "force": False,
        "kind": "endurance", "template": None}
    assert m.parse_profile_args(["new", "erf", "--from", "demo"])["source"] == "demo"
    assert m.parse_profile_args(["new", "erf", "--from=demo"])["source"] == "demo"
    _raises(lambda: m.parse_profile_args(["new"]))
    _raises(lambda: m.parse_profile_args(["new", "erf", "--bogus"]))
    _raises(lambda: m.parse_profile_args(["new", "erf", "--from"]))    # missing value
    _raises(lambda: m.parse_profile_args(["new", "erf", "--from="]))   # empty value


def t_parse_new_kind_and_template():
    # default kind is endurance, no template
    o = m.parse_profile_args(["new", "erf"])
    assert o["kind"] == "endurance" and o["template"] is None
    # solo with an explicit template
    o = m.parse_profile_args(["new", "solo1", "--kind", "solo", "--template", "pov"])
    assert o["kind"] == "solo" and o["template"] == "pov"
    assert m.parse_profile_args(
        ["new", "solo1", "--kind=solo", "--template=commentary"])["template"] == "commentary"
    # solo without --template defaults to the first starter template
    assert m.parse_profile_args(
        ["new", "solo1", "--kind", "solo"])["template"] == m.cfg.SOLO_TEMPLATES[0]


def t_parse_new_kind_template_validation():
    _raises(lambda: m.parse_profile_args(["new", "x", "--kind", "bogus"]))
    _raises(lambda: m.parse_profile_args(["new", "x", "--kind", "solo", "--template", "bogus"]))
    # --template only valid with --kind solo
    _raises(lambda: m.parse_profile_args(["new", "x", "--template", "pov"]))
    # --from cannot combine with a generated solo profile
    _raises(lambda: m.parse_profile_args(["new", "x", "--kind", "solo", "--from", "demo"]))
    # missing/empty values
    _raises(lambda: m.parse_profile_args(["new", "x", "--kind"]))
    _raises(lambda: m.parse_profile_args(["new", "x", "--kind", "solo", "--template"]))


def t_parse_unknown_verb_raises():
    _raises(lambda: m.parse_profile_args([]))
    _raises(lambda: m.parse_profile_args(["frobnicate"]))


def t_split_profile_flag_extracts_anywhere():
    assert m.split_profile_flag(["relay", "start"]) == (["relay", "start"], None)
    assert m.split_profile_flag(["--profile", "erf", "relay", "start"]) == (
        ["relay", "start"], "erf")
    assert m.split_profile_flag(["relay", "--profile=erf", "start"]) == (
        ["relay", "start"], "erf")


def t_split_profile_flag_missing_value_raises():
    _raises(lambda: m.split_profile_flag(["--profile"]))
    _raises(lambda: m.split_profile_flag(["--profile=", "relay"]))   # empty value


def t_valid_profile_name():
    assert m.valid_profile_name("erf")
    assert m.valid_profile_name("gt-2026_a")
    assert not m.valid_profile_name("ERF")
    assert not m.valid_profile_name("-bad")
    assert not m.valid_profile_name("has space")
    assert not m.valid_profile_name("")


def t_slugify_makes_directory_safe_slug():
    assert m.slugify("Demo League") == "demo-league"
    assert m.slugify("erf") == "erf"
    assert m.slugify("gt-2026_a") == "gt-2026_a"        # already-valid slugs unchanged
    assert m.slugify("  Hello   World!  ") == "hello-world"
    assert m.slugify("../../etc") == "etc"               # path traversal collapses to a slug
    assert m.slugify("!!!") == ""                        # nothing usable
    assert m.slugify("") == ""


def _mkroot_with_example(td):
    """A fake project root with profiles/example/profile.env."""
    root = os.path.join(td, "proj")
    ex = os.path.join(root, "profiles", "example")
    os.makedirs(ex)
    with open(os.path.join(ex, "profile.env"), "w", encoding="utf-8") as fh:
        fh.write("NAME=Example League\nSHEET_ID=\n")
    open(os.path.join(root, ".env.example"), "w").close()   # project marker
    return root


def t_create_profile_copies_example():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        target = m.create_profile(root, "erf")
        assert target == os.path.join(root, "profiles", "erf")
        assert os.path.isfile(os.path.join(target, "profile.env"))
        assert m.cfg.list_profiles(root) == ["erf"], "config now lists it (example stays excluded)"


def t_create_profile_from_other_profile():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        m.create_profile(root, "demo")
        with open(os.path.join(root, "profiles", "demo", "profile.env"),
                  "w", encoding="utf-8") as fh:
            fh.write("NAME=Demo\nSHEET_ID=abc\n")
        m.create_profile(root, "erf", source="demo")
        assert m.cfg.parse_profile(root, "erf")["SHEET_ID"] == "abc"


def t_create_solo_profile_has_sheet_and_carries_template():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        target = m.create_profile(root, "Demo Solo", kind="solo", template="pov")
        assert target == os.path.join(root, "profiles", "demo-solo")
        assert m.cfg.list_profiles(root) == ["demo-solo"]
        prof = m.cfg.parse_profile(root, "demo-solo")
        assert prof["NAME"] == "Demo Solo"
        assert prof["KIND"] == "solo"
        assert prof["TEMPLATE"] == "pov"
        # sheet-always: a solo profile carries SHEET_ID (blank, to be filled)
        assert "SHEET_ID" in prof and prof["SHEET_ID"] == ""
        assert "SHEET_PUSH_URL" in prof
        # and it resolves as a solo profile
        rcfg = m.cfg.resolve_config(root, override="demo-solo",
                                    runtime_root=os.path.join(td, "runtime"))
        assert rcfg.kind == "solo" and rcfg.template == "pov"


def t_create_solo_profile_defaults_template():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        m.create_profile(root, "s1", kind="solo")
        assert m.cfg.parse_profile(root, "s1")["TEMPLATE"] == m.cfg.SOLO_TEMPLATES[0]


def t_create_profile_accepts_spaces_via_slug_and_sets_display_name():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        target = m.create_profile(root, "Demo League")
        assert target == os.path.join(root, "profiles", "demo-league")   # slugged dir
        assert m.cfg.list_profiles(root) == ["demo-league"]
        assert m.cfg.parse_profile(root, "demo-league")["NAME"] == "Demo League", \
            "the typed name is preserved as the league display NAME, not the slug"


def t_create_profile_rejects_unsluggable_existing_and_missing_source():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        _raises(lambda: m.create_profile(root, "!!!"))       # nothing usable -> empty slug
        _raises(lambda: m.create_profile(root, "example"))   # reserved
        m.create_profile(root, "erf")
        _raises(lambda: m.create_profile(root, "erf"))        # already exists
        _raises(lambda: m.create_profile(root, "Erf"))        # same slug already exists
        _raises(lambda: m.create_profile(root, "x", source="ghost"))  # no source


def t_set_active_profile_writes_pointer():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        m.create_profile(root, "erf")
        runtime = os.path.join(td, "runtime")
        assert m.set_active_profile(root, runtime, "erf") == "erf"
        assert m.cfg.read_active_pointer(runtime) == "erf"


def t_set_active_profile_unknown_raises():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot_with_example(td)
        runtime = os.path.join(td, "runtime")
        _raises(lambda: m.set_active_profile(root, runtime, "ghost"))


def t_format_profile_list_marks_active():
    out = m.format_profile_list(["erf", "demo"], "demo")
    assert "  erf" in out
    assert "* demo" in out


def t_format_profile_list_empty():
    assert "no profiles" in m.format_profile_list([], None)


def t_mask_hides_secret_body():
    assert m.mask_secret("") == "(unset)"
    assert m.mask_secret("https://script.google.com/exec?key=SECRETVALUE") \
        .startswith("http")
    # the secret body is not shown in full
    assert "SECRETVALUE" not in m.mask_secret(
        "https://x/exec?key=SECRETVALUE")
    assert m.mask_secret("short") == "****"


def t_format_profile_show_masks_push_url():
    cfg_obj = m.cfg.ResolvedConfig(
        profile="demo", name="Demo League", sheet_id="SHEETID123",
        sheet_push_url="https://x/exec?key=TOPSECRET",
        profile_dir="/p/profiles/demo", runtime_dir="/p/runtime/demo")
    out = m.format_profile_show(cfg_obj, active="demo")
    assert "Demo League" in out
    assert "SHEETID123" in out          # sheet id shown (link-shared, not a secret)
    assert "TOPSECRET" not in out       # push-url secret masked
    assert "(active)" in out


def t_parse_export_defaults():
    o = m.parse_profile_args(["export", "iro-gtec"])
    assert o["verb"] == "export" and o["name"] == "iro-gtec"
    assert o["no_assets"] is False and o["out"] is None


def t_parse_export_flags():
    o = m.parse_profile_args(["export", "iro-gtec", "--no-assets", "--out", "/tmp/x.zip"])
    assert o["no_assets"] is True and o["out"] == "/tmp/x.zip"
    o2 = m.parse_profile_args(["export", "iro-gtec", "--out=/tmp/y.zip"])
    assert o2["out"] == "/tmp/y.zip"


def t_parse_import():
    o = m.parse_profile_args(["import", "/tmp/bundle.zip"])
    assert o["verb"] == "import" and o["file"] == "/tmp/bundle.zip" and o["force"] is False
    o2 = m.parse_profile_args(["import", "/tmp/bundle.zip", "--force"])
    assert o2["force"] is True


def t_parse_export_needs_name():
    _raises(lambda: m.parse_profile_args(["export"]))


def t_parse_import_needs_file():
    _raises(lambda: m.parse_profile_args(["import"]))


def t_parse_export_rejects_bad_flags():
    _raises(lambda: m.parse_profile_args(["export", "iro", "--bogus"]))
    _raises(lambda: m.parse_profile_args(["export", "iro", "--out"]))      # no value
    _raises(lambda: m.parse_profile_args(["export", "iro", "--out="]))     # empty value


def t_parse_import_rejects_bad_flag():
    _raises(lambda: m.parse_profile_args(["import", "x.zip", "--bogus"]))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
