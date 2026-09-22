#!/usr/bin/env python3
"""Stdlib unit checks for the profile-aware config resolver.
Run: python3 tests/test_config.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "racecast_config", os.path.join(ROOT, "src", "scripts", "config.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _mkroot(td):
    """A fake project root: a dir holding a .env.example marker."""
    root = os.path.join(td, "proj")
    os.makedirs(root)
    open(os.path.join(root, ".env.example"), "w").close()
    return root


def t_find_project_root_finds_marker_above_start():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        start = os.path.join(root, "src", "scripts")
        os.makedirs(start)
        assert m.find_project_root(start) == root


def t_find_project_root_returns_none_when_no_marker_within_bound():
    with tempfile.TemporaryDirectory() as td:
        deep = os.path.join(td, "a", "b", "c", "d", "e")
        os.makedirs(deep)
        # The walk is bounded, so it never reaches an unrelated parent.
        assert m.find_project_root(deep, max_levels=4) is None


def t_parse_env_text_ignores_blanks_comments_and_strips_quotes():
    text = '\n'.join([
        "# a comment",
        "",
        "SHEET_ID=abc123",
        '  NAME = "Demo League" ',
        "URL='https://x/y?key=z'",   # '=' inside the value is kept
        "noequals",
    ])
    got = m.parse_env_text(text)
    assert got == {
        "SHEET_ID": "abc123",
        "NAME": "Demo League",
        "URL": "https://x/y?key=z",
    }


def t_load_machine_env_reads_dotenv_at_root():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as fh:
            fh.write("RACECAST_PROFILE=demo\nRACECAST_UI_PORT=8089\n")
        got = m.load_machine_env(root)
        assert got == {"RACECAST_PROFILE": "demo", "RACECAST_UI_PORT": "8089"}


def t_load_machine_env_returns_empty_when_no_dotenv():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)   # marker present, but no .env file
        assert m.load_machine_env(root) == {}


def _mkprofile(root, name, body):
    pdir = os.path.join(root, "profiles", name)
    os.makedirs(pdir)
    with open(os.path.join(pdir, "profile.env"), "w", encoding="utf-8") as fh:
        fh.write(body)
    return pdir


def t_list_profiles_sorted_excludes_example_and_dirs_without_profile_env():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "SHEET_ID=a\n")
        _mkprofile(root, "erf", "SHEET_ID=b\n")
        _mkprofile(root, "example", "SHEET_ID=\n")           # template, excluded
        os.makedirs(os.path.join(root, "profiles", "empty"))  # no profile.env
        assert m.list_profiles(root) == ["demo", "erf"]


def t_list_profiles_empty_when_no_profiles_dir():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        assert m.list_profiles(root) == []


def t_parse_profile_reads_named_profile():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo League\nSHEET_ID=abc\n")
        assert m.parse_profile(root, "demo") == {
            "NAME": "Demo League", "SHEET_ID": "abc"}


def t_read_active_pointer_reads_and_strips():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "active-profile"), "w", encoding="utf-8") as fh:
            fh.write("erf\n")
        assert m.read_active_pointer(td) == "erf"
        os.remove(os.path.join(td, "active-profile"))
        assert m.read_active_pointer(td) is None


def t_resolve_active_profile_precedence_override_beats_env_and_pointer():
    avail = ["erf", "demo"]
    assert m.resolve_active_profile(
        avail, override="demo", env_value="erf", pointer="erf") == "demo"


def t_resolve_active_profile_env_beats_pointer():
    assert m.resolve_active_profile(
        ["erf", "demo"], env_value="erf", pointer="demo") == "erf"


def t_resolve_active_profile_pointer_used_when_no_override_or_env():
    assert m.resolve_active_profile(["erf", "demo"], pointer="demo") == "demo"


def t_resolve_active_profile_single_profile_is_implicit():
    assert m.resolve_active_profile(["demo"]) == "demo"


def t_resolve_active_profile_unknown_name_raises():
    try:
        m.resolve_active_profile(["demo"], override="ghost")
        raise AssertionError("expected ProfileError")
    except m.ProfileError as e:
        assert "ghost" in str(e) and "demo" in str(e)


def t_resolve_active_profile_ambiguous_raises():
    try:
        m.resolve_active_profile(["erf", "demo"])
        raise AssertionError("expected ProfileError")
    except m.ProfileError as e:
        assert "--profile" in str(e)


def t_resolve_active_profile_none_raises():
    try:
        m.resolve_active_profile([])
        raise AssertionError("expected ProfileError")
    except m.ProfileError as e:
        assert "no profiles" in str(e)


def t_profile_runtime_dir_is_runtime_slash_name():
    assert m.profile_runtime_dir("/p", "erf") == os.path.join("/p", "runtime", "erf")


def t_resolve_config_end_to_end_single_profile():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo League\nSHEET_ID=abc\nSHEET_PUSH_URL=https://x?key=y\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.profile == "demo"
        assert cfg.name == "Demo League"
        assert cfg.sheet_id == "abc"
        assert cfg.sheet_push_url == "https://x?key=y"
        assert cfg.intro_url == "" and cfg.outro_url == ""
        assert cfg.discord_webhook_url == ""    # no DISCORD_WEBHOOK_URL set
        assert cfg.logo_path == ""              # no LOGO set
        assert cfg.profile_dir == os.path.join(root, "profiles", "demo")
        assert cfg.runtime_dir == os.path.join(root, "runtime", "demo")


def t_resolve_config_name_defaults_to_profile_when_unset():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "erf", "SHEET_ID=zzz\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.name == "erf"


def t_resolve_config_override_selects_among_many():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "SHEET_ID=a\n")
        _mkprofile(root, "erf", "SHEET_ID=b\n")
        cfg = m.resolve_config(root, override="erf", environ={})
        assert cfg.profile == "erf" and cfg.sheet_id == "b"


def t_resolve_config_env_var_selects_among_many():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "SHEET_ID=a\n")
        _mkprofile(root, "erf", "SHEET_ID=b\n")
        cfg = m.resolve_config(root, environ={"RACECAST_PROFILE": "demo"})
        assert cfg.profile == "demo"


def t_resolve_config_logo_path_resolved_when_file_present():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        pdir = _mkprofile(root, "demo", "SHEET_ID=a\nLOGO=logo.png\n")
        open(os.path.join(pdir, "logo.png"), "w").close()
        cfg = m.resolve_config(root, environ={})
        assert cfg.logo_path == os.path.join(pdir, "logo.png")


def t_resolve_config_logo_path_blank_when_file_missing():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "SHEET_ID=a\nLOGO=missing.png\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.logo_path == ""


def t_normalize_kind_folds_to_known():
    assert m.normalize_kind("solo") == "solo"
    assert m.normalize_kind("endurance") == "endurance"
    assert m.normalize_kind(" SOLO ") == "solo"          # trimmed + lowercased
    assert m.normalize_kind("") == m.DEFAULT_KIND         # blank -> default
    assert m.normalize_kind(None) == m.DEFAULT_KIND
    assert m.normalize_kind("bogus") == m.DEFAULT_KIND    # unknown -> default


def t_resolve_config_kind_defaults_to_endurance():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "SHEET_ID=a\n")          # no KIND -> backwards-compat
        cfg = m.resolve_config(root, environ={})
        assert cfg.kind == "endurance" and cfg.template == ""


def t_resolve_config_kind_solo_with_template():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "s1", "NAME=Solo One\nKIND=solo\nTEMPLATE=pov\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.kind == "solo" and cfg.template == "pov"


def t_resolve_config_discord_webhook_from_field():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo\nSHEET_ID=abc\n"
                   "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/tok\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.discord_webhook_url == "https://discord.com/api/webhooks/1/tok"


def t_resolve_config_console_secret_from_new_key():
    """New CONSOLE_SECRET key resolves to cfg.console_secret."""
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\nCONSOLE_SECRET=s3cr3t\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.console_secret == "s3cr3t"



def t_resolve_config_console_secret_blank_when_absent():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.console_secret == ""


def t_resolve_config_event_title_from_field():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\n"
                   "EVENT_TITLE=GTEC - 2026 - Round 4 - Nürburgring 24h\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.event_title == "GTEC - 2026 - Round 4 - Nürburgring 24h"


def t_resolve_config_event_title_blank_when_absent():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.event_title == ""


def t_resolve_config_obs_collection_from_field():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo League\nSHEET_ID=abc\nOBS_COLLECTION=Demo Broadcast\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.obs_collection == "Demo Broadcast"


def t_resolve_config_obs_collection_falls_back_to_name():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "erf", "NAME=ERF Endurance\nSHEET_ID=abc\n")
        cfg = m.resolve_config(root, environ={})
        # The default is the product prefix plus the league NAME, em-dash joined.
        assert cfg.obs_collection == "GT Racing Endurance — ERF Endurance"


def t_resolve_config_obs_collection_falls_back_to_profile_dir_when_no_name():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "erf", "SHEET_ID=abc\n")   # no NAME, no OBS_COLLECTION
        cfg = m.resolve_config(root, environ={})
        # Falls back to cfg.name, the profile dir name, still prefixed.
        assert cfg.obs_collection == "GT Racing Endurance — erf"


def t_resolve_config_obs_collection_default_is_prefixed():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo League\nSHEET_ID=x\n")  # no OBS_COLLECTION
        cfg = m.resolve_config(root, environ={})
        assert cfg.obs_collection == "GT Racing Endurance — Demo League"


def t_resolve_config_discord_oauth_keys():
    """DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET are read from profile.env."""
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo\nSHEET_ID=abc\n"
                   "DISCORD_CLIENT_ID=cid\nDISCORD_CLIENT_SECRET=csecret\n")
        rc = m.resolve_config(root, override="demo", environ={})
        assert rc.discord_client_id == "cid"
        assert rc.discord_client_secret == "csecret"


def t_resolve_config_discord_oauth_keys_blank_when_absent():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\n")
        rc = m.resolve_config(root, environ={})
        assert rc.discord_client_id == ""
        assert rc.discord_client_secret == ""


def t_resolve_config_discord_voice_url_from_field():
    """DISCORD_VOICE_URL is read from profile.env (the env fallback that
    resolve_voice_target uses when the Sheet's `Discord Voice` cell is empty)."""
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo\nSHEET_ID=abc\n"
                   "DISCORD_VOICE_URL=https://discord.com/channels/1/2\n")
        rc = m.resolve_config(root, override="demo", environ={})
        assert rc.discord_voice_url == "https://discord.com/channels/1/2"


def t_resolve_config_discord_voice_url_blank_when_absent():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo", "NAME=Demo\nSHEET_ID=abc\n")
        rc = m.resolve_config(root, environ={})
        assert rc.discord_voice_url == ""


def t_resolve_config_obs_collection_explicit_wins_over_prefix():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "demo",
                   "NAME=Demo League\nSHEET_ID=x\nOBS_COLLECTION=Custom Name\n")
        cfg = m.resolve_config(root, environ={})
        assert cfg.obs_collection == "Custom Name"


def t_solo_kind_defaults_solo_collection_name():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "myleague",
                   "NAME=My League\nSHEET_ID=abc\nKIND=solo\nTEMPLATE=commentary\n")
        rc = m.resolve_config(root, environ={})
        assert rc.kind == "solo"
        assert rc.obs_collection == "GT Racing Solo — My League"


def t_endurance_collection_name_unchanged():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "endur", "NAME=Endur\nSHEET_ID=abc\n")
        rc = m.resolve_config(root, environ={})
        assert rc.kind == "endurance"
        assert rc.obs_collection == "GT Racing Endurance — Endur"


def t_sheet_edit_url_builds_edit_link():
    assert m.sheet_edit_url("ABC123") == \
        "https://docs.google.com/spreadsheets/d/ABC123/edit"


def t_sheet_edit_url_strips_whitespace():
    assert m.sheet_edit_url("  ABC123  ") == \
        "https://docs.google.com/spreadsheets/d/ABC123/edit"


def t_sheet_edit_url_empty_when_unset():
    assert m.sheet_edit_url("") == ""
    assert m.sheet_edit_url(None) == ""
    assert m.sheet_edit_url("   ") == ""


# The shipped profiles under profiles/ in the repo root. 'demo' is the committed,
# directly usable public-Sheet demo league (#206); 'example' is the copy-from
# template and must stay out of the league list.

def t_shipped_demo_profile_is_listed_and_example_is_not():
    listed = m.list_profiles(ROOT)
    assert "demo" in listed, listed
    assert "example" not in listed, listed


def t_shipped_demo_profile_env_is_complete_and_secret_free():
    env = m.parse_profile(ROOT, "demo")
    # Directly usable: a name, a real public read-only Sheet id, and a collection.
    assert env.get("NAME"), env
    assert env.get("SHEET_ID"), env
    assert env.get("OBS_COLLECTION"), env
    # No write credential or secret is ever committed.
    assert env.get("SHEET_PUSH_URL", "") == "", env
    assert env.get("CONSOLE_SECRET", "") == "", env
    assert env.get("DISCORD_WEBHOOK_URL", "") == "", env


def t_shipped_demo_profile_resolves_as_active():
    cfg = m.resolve_config(ROOT, environ={"RACECAST_PROFILE": "demo"})
    assert cfg.profile == "demo"
    assert cfg.sheet_id == m.parse_profile(ROOT, "demo")["SHEET_ID"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
