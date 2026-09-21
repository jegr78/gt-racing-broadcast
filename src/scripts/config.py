#!/usr/bin/env python3
"""Profile-aware configuration resolver for the GT Racing Broadcast
toolkit (binary: racecast).

Single source of truth for resolving which league ("profile") is active and
loading its config. Two layers:

  * machine .env (repo root / next to the binary): RACECAST_* vars, all leagues
  * profiles/<name>/profile.env: the league's SHEET_ID, SHEET_PUSH_URL, NAME, ...

The bounded .env loader here is the CANONICAL copy. The standalone scripts
(relay/racecast-feeds.py, setup-assets.py, relay/get-media.py, relay/get-graphics.py)
keep their own self-contained load_dotenv on purpose: the relay is deliberately
import-free (same rationale as its duplicated detect_tailscale_ip) and all four
run in-process under the frozen binary. Keep the parsing/boundary rules in sync.
"""

import os
from dataclasses import dataclass, field

PROJECT_MARKERS = (".git", ".env.example")

# Default OBS scene-collection name = product prefix + the league NAME, so several
# leagues' collections group together in OBS. An explicit OBS_COLLECTION wins. (#308)
PRODUCT_COLLECTION_PREFIX = "GT Racing Endurance"

# Solo profiles group under their own prefix, inside the same product line. (#303)
SOLO_COLLECTION_PREFIX = "GT Racing Solo"

# Profile kind (#301): endurance = the classic feed-/sheet-driven league; solo =
# a single-event commentary/POV broadcast (no external feeds, no Google Sheet).
# A missing/unknown KIND resolves to endurance so every existing profile is
# backwards-compatible. Solo profiles carry a starter TEMPLATE chosen at creation.
DEFAULT_KIND = "endurance"
KNOWN_KINDS = ("endurance", "solo")
SOLO_TEMPLATES = ("commentary", "pov")


def normalize_kind(raw):
    """Fold a raw KIND value to a known kind: lowercased+trimmed, and anything
    not in KNOWN_KINDS (including blank/None) falls back to DEFAULT_KIND. Pure."""
    k = (raw or "").strip().lower()
    return k if k in KNOWN_KINDS else DEFAULT_KIND


def find_project_root(start, markers=PROJECT_MARKERS, max_levels=4):
    """Walk up from `start`, checking `start` itself and up to `max_levels-1`
    ancestors (default: 4 dirs), and return the nearest one holding a marker,
    or None. Bounded on purpose: never reaches an unrelated parent (mirrors the
    scripts' `for _ in range(4)` load_dotenv walk)."""
    d = start
    for _ in range(max_levels):
        if any(os.path.exists(os.path.join(d, mk)) for mk in markers):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def parse_env_text(text):
    """Parse KEY=VALUE lines into a dict. Ignores blank lines, '#' comments and
    lines without '='; strips surrounding whitespace and any wrapping
    single/double quotes. '=' inside a value is preserved. Mirrors the
    standalone scripts' load_dotenv parser (not a strict matched-pair quote
    check). Pure."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_machine_env(start):
    """Read the machine .env (from `start` or the project root above it) into a
    dict. Does NOT mutate os.environ; callers decide precedence. Bounded to the
    project (same boundary as find_project_root). Returns {} if no .env."""
    candidates = [start]
    root = find_project_root(start)
    if root and root != start:
        candidates.append(root)
    for c in candidates:
        p = os.path.join(c, ".env")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return parse_env_text(fh.read())
    return {}


PROFILE_ENV_NAME = "profile.env"


def profiles_dir(root):
    return os.path.join(root, "profiles")


def list_profiles(root):
    """Sorted names of profiles/<name>/ dirs that contain a profile.env.
    'example' (the shipped template) is excluded: it is not a usable league."""
    pdir = profiles_dir(root)
    if not os.path.isdir(pdir):
        return []
    names = []
    for name in sorted(os.listdir(pdir)):
        if name == "example":
            continue
        if os.path.isfile(os.path.join(pdir, name, PROFILE_ENV_NAME)):
            names.append(name)
    return names


def parse_profile(root, name):
    """Read profiles/<name>/profile.env into a dict. Raises FileNotFoundError if
    the profile.env is missing."""
    p = os.path.join(profiles_dir(root), name, PROFILE_ENV_NAME)
    with open(p, encoding="utf-8") as fh:
        return parse_env_text(fh.read())


ACTIVE_PROFILE_FILE = "active-profile"   # lives under runtime/


def read_active_pointer(runtime_root):
    """Return the persisted active-profile name from runtime/active-profile, or
    None if the pointer file is absent/empty."""
    p = os.path.join(runtime_root, ACTIVE_PROFILE_FILE)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip() or None
    return None


class ProfileError(Exception):
    """The active profile could not be resolved (none / ambiguous / unknown)."""


def resolve_active_profile(available, *, override=None, env_value=None,
                           pointer=None):
    """Resolve the active profile by precedence:
        override (--profile) > env_value (RACECAST_PROFILE) > pointer
        (runtime/active-profile) > the sole profile when exactly one exists.
    `available` is the list of known profile names. Raises ProfileError with a
    helpful message on an unknown name, ambiguity, or no profiles at all."""
    for source, value in (("--profile", override),
                          ("RACECAST_PROFILE", env_value),
                          ("active-profile", pointer)):
        if value:
            if value not in available:
                raise ProfileError(
                    f"{source}={value!r} is not a known profile "
                    f"(available: {', '.join(available) or 'none'})")
            return value
    if len(available) == 1:
        return available[0]
    if not available:
        raise ProfileError(
            "no profiles found. Create one under profiles/<name>/profile.env")
    raise ProfileError(
        "multiple profiles exist; choose one with --profile or "
        f"'racecast profile use <name>' (available: {', '.join(available)})")


@dataclass
class ResolvedConfig:
    profile: str
    name: str
    sheet_id: str
    kind: str = DEFAULT_KIND     # endurance (default) | solo (#301)
    template: str = ""           # solo starter template (commentary|pov); "" for endurance
    sheet_push_url: str = ""
    intro_url: str = ""
    outro_url: str = ""
    trailer_url: str = ""
    discord_webhook_url: str = ""  # league Discord webhook for live health alerts (optional)
    obs_collection: str = ""     # OBS scene-collection name; falls back to NAME
    console_secret: str = ""     # per-league HMAC secret signing /console identity tokens (#216)
    discord_client_id: str = ""      # per-league Discord OAuth app (console login)
    discord_client_secret: str = ""  # never leaves the producer machine
    discord_voice_url: str = ""      # league voice channel (fallback; Sheet override wins)
    event_title: str = ""        # optional free-text event title (Panel/Cockpit/Discord, #207)
    logo_path: str = ""          # absolute path, or "" if unset/missing
    profile_dir: str = ""
    runtime_dir: str = ""
    machine_env: dict = field(default_factory=dict)


def profile_runtime_dir(root, name):
    """Profile-scoped runtime dir: <root>/runtime/<name>."""
    return os.path.join(root, "runtime", name)


def sheet_edit_url(sheet_id):
    """The human-readable Google-Sheet edit URL for a profile's SHEET_ID, or ''
    when no id is set. Deterministic counterpart to the CSV-export URLs the relay
    and asset downloaders build from the same id. Pure."""
    sheet_id = (sheet_id or "").strip()
    if not sheet_id:
        return ""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def resolve_config(root, *, override=None, runtime_root=None, environ=None):
    """Machine .env + active profile -> ResolvedConfig. `root` is the project
    root; `runtime_root` defaults to <root>/runtime; `environ` defaults to
    os.environ (a real RACECAST_PROFILE wins over the machine .env's). Raises
    ProfileError if no profile can be resolved."""
    environ = os.environ if environ is None else environ
    runtime_root = runtime_root or os.path.join(root, "runtime")
    machine = load_machine_env(root)
    available = list_profiles(root)
    name = resolve_active_profile(
        available,
        override=override,
        env_value=environ.get("RACECAST_PROFILE") or machine.get("RACECAST_PROFILE"),
        pointer=read_active_pointer(runtime_root),
    )
    prof = parse_profile(root, name)
    pdir = os.path.join(profiles_dir(root), name)
    logo = prof.get("LOGO", "")
    logo_path = os.path.join(pdir, logo) if logo else ""
    if logo_path and not os.path.isfile(logo_path):
        logo_path = ""
    resolved_name = prof.get("NAME", name)
    kind = normalize_kind(prof.get("KIND", ""))
    return ResolvedConfig(
        profile=name,
        name=resolved_name,
        sheet_id=prof.get("SHEET_ID", ""),
        kind=kind,
        template=prof.get("TEMPLATE", ""),
        sheet_push_url=prof.get("SHEET_PUSH_URL", ""),
        intro_url=prof.get("INTRO_URL", ""),
        outro_url=prof.get("OUTRO_URL", ""),
        trailer_url=prof.get("TRAILER_URL", ""),
        discord_webhook_url=prof.get("DISCORD_WEBHOOK_URL", ""),
        obs_collection=prof.get("OBS_COLLECTION") or (
            f"{SOLO_COLLECTION_PREFIX} — {resolved_name}"
            if kind == "solo"
            else f"{PRODUCT_COLLECTION_PREFIX} — {resolved_name}"),
        console_secret=prof.get("CONSOLE_SECRET", ""),
        discord_client_id=prof.get("DISCORD_CLIENT_ID", ""),
        discord_client_secret=prof.get("DISCORD_CLIENT_SECRET", ""),
        discord_voice_url=prof.get("DISCORD_VOICE_URL", ""),
        event_title=prof.get("EVENT_TITLE", ""),
        logo_path=logo_path,
        profile_dir=pdir,
        runtime_dir=profile_runtime_dir(root, name),
        machine_env=machine,
    )
