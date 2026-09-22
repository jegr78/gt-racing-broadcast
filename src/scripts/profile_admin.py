#!/usr/bin/env python3
"""Profile (league) management commands for the operator CLI: list / show /
use / new. Pure logic over config.py + the filesystem; racecast.py thin-wraps these.

config.py owns the READ side (resolve the active profile, load its values).
This module owns the WRITE side (create a profile directory, set the active
pointer) plus CLI arg-parsing, the global --profile splitter, and output
formatting. Stdlib only."""

import os
import re
import shutil

import config as cfg   # sibling in src/scripts (sys.path injected by racecast.py/tests)

PROFILE_VERBS = ("list", "show", "use", "new", "export", "import")
_USAGE = ("usage: racecast profile {list | show [<name>] | use <name> [--force] | "
          "new <name> [--from <source>] [--kind endurance|solo] [--template commentary|pov] | "
          "export <name> [--no-assets] [--out PATH] | import <file> [--force]}")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def valid_profile_name(name):
    """A profile name is a lowercase slug: starts alphanumeric, then letters,
    digits, '-' or '_'. (It becomes a directory name + an env-var value.)"""
    return bool(_NAME_RE.match(name or ""))


def slugify(name):
    """Turn a free-form league name into a directory-safe slug: lowercase, runs of
    anything outside [a-z0-9_-] collapse to a single '-', and leading/trailing
    '-'/'_' are trimmed. 'Demo League' -> 'demo-league'; an already-valid slug is
    unchanged. Returns '' when nothing usable remains. Doubles as path-traversal
    defense ('../etc' -> 'etc')."""
    s = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower())
    return s.strip("-_")


def _display_name(name):
    """The league display NAME we store from a typed profile name: trimmed, with
    internal whitespace runs collapsed to single spaces. 'Demo  League ' -> 'Demo League'."""
    return " ".join((name or "").split())


def _set_env_name(env_path, display):
    """Rewrite the first `NAME=` line in a profile.env to `display`, preserving the
    rest of the file (comments, other keys). Appends a NAME line if none exists."""
    with open(env_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("#") and re.match(r"\s*NAME\s*=", ln):
            lines[i] = f"NAME={display}"
            break
    else:
        lines.append(f"NAME={display}")
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def parse_profile_args(rest):
    """argv after `profile` -> {verb, name, source, no_assets, out, file, force}.
    Raises ValueError (with usage text) on an unknown/missing verb, wrong arity,
    or an unknown flag."""
    if not rest or rest[0] not in PROFILE_VERBS:
        raise ValueError(_USAGE)
    verb, args = rest[0], rest[1:]
    out = {"verb": verb, "name": None, "source": "example",
           "no_assets": False, "out": None, "file": None, "force": False,
           "kind": cfg.DEFAULT_KIND, "template": None}
    if verb == "list":
        if args:
            raise ValueError(_USAGE)
    elif verb == "show":
        if len(args) > 1:
            raise ValueError(_USAGE)
        if args:
            out["name"] = args[0]
    elif verb == "use":
        names = []
        for t in args:
            if t == "--force":
                out["force"] = True
            elif t.startswith("-"):
                raise ValueError(_USAGE)
            else:
                names.append(t)
        if len(names) != 1:
            raise ValueError(_USAGE)
        out["name"] = names[0]
    elif verb == "new":
        if not args:
            raise ValueError(_USAGE)
        out["name"] = args[0]
        from_given = False
        toks = list(args[1:])
        while toks:
            t = toks.pop(0)
            if t == "--from":
                if not toks:
                    raise ValueError("--from requires a profile name")
                out["source"] = toks.pop(0)
                from_given = True
            elif t.startswith("--from="):
                src = t.split("=", 1)[1]
                if not src:
                    raise ValueError("--from requires a profile name")
                out["source"] = src
                from_given = True
            elif t == "--kind":
                if not toks:
                    raise ValueError("--kind requires a value")
                out["kind"] = toks.pop(0)
            elif t.startswith("--kind="):
                out["kind"] = t.split("=", 1)[1]
            elif t == "--template":
                if not toks:
                    raise ValueError("--template requires a value")
                out["template"] = toks.pop(0)
            elif t.startswith("--template="):
                out["template"] = t.split("=", 1)[1]
            else:
                raise ValueError(_USAGE)
        if out["kind"] not in cfg.KNOWN_KINDS:
            raise ValueError(
                f"--kind must be one of: {', '.join(cfg.KNOWN_KINDS)}")
        if out["template"] is not None and out["template"] not in cfg.SOLO_TEMPLATES:
            raise ValueError(
                f"--template must be one of: {', '.join(cfg.SOLO_TEMPLATES)}")
        if out["kind"] == "solo":
            if from_given:
                raise ValueError("--from cannot be combined with --kind solo "
                                 "(a solo profile is scaffolded, not copied)")
            # a solo profile always carries a starter template; default to the
            # first one when the operator did not pick one explicitly.
            if out["template"] is None:
                out["template"] = cfg.SOLO_TEMPLATES[0]
        elif out["template"] is not None:
            raise ValueError("--template is only valid with --kind solo")
    elif verb == "export":
        if not args:
            raise ValueError(_USAGE)
        out["name"] = args[0]
        toks = list(args[1:])
        while toks:
            t = toks.pop(0)
            if t == "--no-assets":
                out["no_assets"] = True
            elif t == "--out":
                if not toks:
                    raise ValueError("--out requires a path")
                out["out"] = toks.pop(0)
            elif t.startswith("--out="):
                val = t.split("=", 1)[1]
                if not val:
                    raise ValueError("--out requires a path")
                out["out"] = val
            else:
                raise ValueError(_USAGE)
    elif verb == "import":
        if not args:
            raise ValueError(_USAGE)
        out["file"] = args[0]
        toks = list(args[1:])
        while toks:
            t = toks.pop(0)
            if t == "--force":
                out["force"] = True
            else:
                raise ValueError(_USAGE)
    return out


def split_profile_flag(argv):
    """Pull a global `--profile <name>` / `--profile=<name>` out of anywhere in
    argv. Returns (cleaned_argv, name_or_None). Raises ValueError if --profile
    is given without a value."""
    out, name, i, toks = [], None, 0, list(argv)
    while i < len(toks):
        t = toks[i]
        if t == "--profile":
            if i + 1 >= len(toks):
                raise ValueError("--profile requires a profile name")
            name = toks[i + 1]
            i += 2
            continue
        if t.startswith("--profile="):
            name = t.split("=", 1)[1]
            if not name:
                raise ValueError("--profile requires a profile name")
            i += 1
            continue
        out.append(t)
        i += 1
    return out, name


def _solo_profile_env_text(display, template):
    """The generated profile.env for a solo profile (#301): a single-event
    commentary/POV broadcast whose main program is a local capture card + webcam
    (no A/B stint feeds), carrying the chosen starter TEMPLATE. A solo profile
    still uses a Google Sheet, the same tabs as endurance MINUS the
    Schedule/Qualifying ones, so it carries SHEET_ID (#302). The matching OBS
    scene-collection source is materialized by `racecast setup` (#303)."""
    return (
        "# Solo profile (kind=solo): a single-event commentary/POV broadcast.\n"
        "# The main program is a local capture card + webcam in OBS (no A/B stint\n"
        "# feeds). It still uses a Google Sheet (below). Created by `racecast profile new`.\n"
        "\n"
        "# Display name shown in the CLI / Control Center / docs.\n"
        f"NAME={display}\n"
        "\n"
        "# Profile kind: solo (vs. the classic feed-/sheet-driven `endurance`).\n"
        "KIND=solo\n"
        "\n"
        "# Starter template chosen at creation: commentary | pov. The matching OBS\n"
        "# scene-collection source is materialized by `racecast setup` (#303).\n"
        f"TEMPLATE={template}\n"
        "\n"
        "# Google Sheet that drives the HUD, timer, crew/console roles, broadcast\n"
        "# chat and assets (the long ID from the sheet URL). A solo Sheet uses the\n"
        "# same tabs as endurance MINUS the Schedule/Qualifying tabs.\n"
        "SHEET_ID=\n"
        "\n"
        "# OPTIONAL: sheet-write webhook (Apps Script /exec URL incl. its ?key=...)\n"
        "# enabling the Director Panel's Setup/POV write-back + the race timer.\n"
        "SHEET_PUSH_URL=\n"
        "\n"
        "# OPTIONAL: the OBS scene-collection name this profile uses. Blank = the\n"
        "# product default per kind.\n"
        "OBS_COLLECTION=\n"
        "\n"
        "# OPTIONAL: a logo image (path relative to this profile dir), shown next\n"
        "# to the profile name in the Control Center sidebar.\n"
        "LOGO=\n"
        "\n"
        "# OPTIONAL: a free-text event title shown in the Director Panel and every\n"
        "# Discord message (e.g. \"GT Racing - Round 4 - Le Mans\").\n"
        "EVENT_TITLE=\n"
        "\n"
        "# OPTIONAL: per-console secret. Auto-provisioned on first relay start;\n"
        "# travels with `profile export`. Leave blank. Treat it like a password.\n"
        "CONSOLE_SECRET=\n"
    )


def create_profile(root, name, source="example", kind=cfg.DEFAULT_KIND,
                   template=None):
    """Create profiles/<slug>/ and return the new dir path. The typed `name` may
    contain spaces/capitals (e.g. "Demo League"): it is slugged for the directory
    ("demo-league") and kept verbatim as the league display NAME.

    kind == "endurance" (default): copy profiles/<source>/ verbatim.
    kind == "solo" (#301): generate a fresh, sheet-less profile.env carrying the
    chosen starter `template` (default: the first SOLO_TEMPLATES entry); no
    `source` is copied.

    Raises ValueError when the name has no sluggable characters, the slug is
    reserved/already exists, or (endurance) the source is missing."""
    slug = slugify(name)
    if not valid_profile_name(slug):
        raise ValueError(f"invalid profile name {name!r} (needs at least one "
                         "letter or digit)")
    if slug == "example":
        raise ValueError("'example' is the reserved template name")
    pdir = cfg.profiles_dir(root)
    target = os.path.join(pdir, slug)
    if os.path.exists(target):
        raise ValueError(f"profile {slug!r} already exists ({target})")
    if kind == "solo":
        os.makedirs(target)
        with open(os.path.join(target, cfg.PROFILE_ENV_NAME),
                  "w", encoding="utf-8") as fh:
            fh.write(_solo_profile_env_text(
                _display_name(name), template or cfg.SOLO_TEMPLATES[0]))
        return target
    src = os.path.join(pdir, source)
    if not os.path.isfile(os.path.join(src, cfg.PROFILE_ENV_NAME)):
        raise ValueError(
            f"source profile {source!r} not found "
            f"({os.path.join(src, cfg.PROFILE_ENV_NAME)})")
    shutil.copytree(src, target)
    _set_env_name(os.path.join(target, cfg.PROFILE_ENV_NAME), _display_name(name))
    return target


def set_active_profile(root, runtime_root, name):
    """Write runtime/active-profile = name. Raises ValueError if `name` is not a
    known profile. Creates runtime_root if needed. Returns the name."""
    available = cfg.list_profiles(root)
    if name not in available:
        raise ValueError(f"unknown profile {name!r} "
                         f"(available: {', '.join(available) or 'none'})")
    os.makedirs(runtime_root, exist_ok=True)
    with open(os.path.join(runtime_root, cfg.ACTIVE_PROFILE_FILE),
              "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    return name


def format_profile_list(names, active):
    """One profile per line, the active one marked with '* '. ASCII only."""
    if not names:
        return "no profiles; create one with `racecast profile new <name>`"
    return "\n".join(("* " if n == active else "  ") + n for n in names)


def mask_secret(value):
    """Show enough of a secret URL to recognize it without revealing the key.
    Empty -> '(unset)'; short -> '****'; else first 8 chars + '...'. ASCII only."""
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "****"
    return value[:8] + "..."


def format_profile_show(rcfg, active):
    """Multi-line human view of a ResolvedConfig. The sheet-push-url (carries a
    ?key= secret) is masked; the sheet id is shown (it is link-shared, not a
    secret). ASCII only."""
    tag = "  (active)" if rcfg.profile == active else ""
    return "\n".join([
        f"profile:        {rcfg.profile}{tag}",
        f"name:           {rcfg.name}",
        f"sheet_id:       {rcfg.sheet_id or '(unset)'}",
        f"sheet_push_url: {mask_secret(rcfg.sheet_push_url)}",
        f"intro_url:      {rcfg.intro_url or '(unset)'}",
        f"outro_url:      {rcfg.outro_url or '(unset)'}",
        f"logo:           {rcfg.logo_path or '(none)'}",
        f"profile_dir:    {rcfg.profile_dir}",
        f"runtime_dir:    {rcfg.runtime_dir}",
    ])
