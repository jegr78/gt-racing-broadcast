#!/usr/bin/env python3
"""Fetch national-flag assets for the countries used by the HUD.

Reads the Country column from the Google Sheet's Configuration tab (gviz CSV,
no API key), then downloads any flag that is not already present into
`src/assets/flags/<asset_key>.svg`. Existing flags are kept untouched and only
missing ones are added, so old seasons' flags survive and new seasons' countries
are filled in automatically.

Source: flagcdn.com (ISO 3166-1 codes; public-domain flags), mapped from the
country name via flagcdn's own name list. Naming matches what the relay serves:
`asset_key(country)` (lowercase, spaces -> '-').

Usage:
  python3 tools/fetch-flags.py                 # add missing flags
  python3 tools/fetch-flags.py --dry-run       # show what would be downloaded
  python3 tools/fetch-flags.py --force         # re-download even if present
  python3 tools/fetch-flags.py --sheet-id ID --config-tab Configuration

Maintainer tool, not shipped in the distributable package.
"""
import argparse, csv, importlib.util, io, json, os, sys
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "src", "assets")
FLAGS_DIR = os.path.join(ASSETS_DIR, "flags")
CODES_URL = "https://flagcdn.com/en/codes.json"
FLAG_URL = "https://flagcdn.com/{code}.svg"   # scalable, tiny

# Names that differ between the sheet and flagcdn's list. Keys are lowercased
# sheet values; values are flagcdn country names (also matched lowercased).
ALIASES = {
    "uk": "united kingdom",
    "usa": "united states",
    "uae": "united arab emirates",
    "south korea": "south korea",
    "czech republic": "czechia",
    "russia": "russia",
}

# Non-country flagcdn codes (hyphenated) worth shipping anyway: the GB home
# nations are common driver nationalities in sim racing. Everything else with a
# hyphen (US states, etc.) is a subdivision we skip in --all mode.
EXTRA_SUBDIVISIONS = ("gb-eng", "gb-sct", "gb-wls", "gb-nir")


def _load_relay_helpers():
    """Reuse asset_key / load_dotenv from the relay so naming stays in sync."""
    spec = importlib.util.spec_from_file_location(
        "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _get(url, timeout=20):
    return urlopen(Request(url, headers={"User-Agent": "racecast-feeds/1.0"}),
                   timeout=timeout).read()


def _sheet_countries(sheet_id, tab):
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")
    rows = list(csv.reader(io.StringIO(_get(url).decode("utf-8", "replace"))))
    if not rows:
        sys.exit("ERROR: Configuration tab is empty or unreachable.")
    header = [(h or "").strip().lower() for h in rows[0]]
    try:
        ci = header.index("country")
    except ValueError:
        sys.exit("ERROR: no 'Country' column in the Configuration tab header.")
    seen, out = set(), []
    for r in rows[1:]:
        if len(r) > ci:
            c = (r[ci] or "").strip()
            if c and c.lower() not in seen:
                seen.add(c.lower()); out.append(c)
    return out


def _fetch_all(m, args):
    """--all: download every country flag from flagcdn (plus the GB home nations)
    into src/assets/flags/<asset_key(name)>.svg. Existing files are kept unless
    --force. Naming matches what the relay serves, so any country named in a
    league's Sheet resolves a flag with no per-season fetch."""
    os.makedirs(FLAGS_DIR, exist_ok=True)
    codes = json.loads(_get(CODES_URL).decode("utf-8"))
    targets = [(c, n) for c, n in codes.items()
               if (len(c) == 2 and "-" not in c) or c in EXTRA_SUBDIVISIONS]
    print(f"flagcdn catalog: {len(targets)} flags to consider")

    added, kept, failed, seen = [], [], [], {}
    for code, name in sorted(targets, key=lambda t: t[1]):
        key = m.asset_key(name)
        if not key:
            continue
        if key in seen:                      # two names collapse to one asset_key
            print(f"  ~ {name:28s} -> {key}.svg already taken by '{seen[key]}', skipped")
            continue
        seen[key] = name
        existing = m.resolve_asset(ASSETS_DIR, "flags", key)
        if existing and not args.force:
            kept.append(key)
            continue
        if args.dry_run:
            added.append((name, code, f"flags/{key}.svg (dry-run)"))
            continue
        try:
            data = _get(FLAG_URL.format(code=code))
        except Exception as e:
            failed.append(f"{name} ({type(e).__name__})")
            continue
        with open(os.path.join(FLAGS_DIR, f"{key}.svg"), "wb") as fh:
            fh.write(data)
        added.append((name, code, f"flags/{key}.svg ({len(data)}B)"))

    for name, code, info in added:
        print(f"  + {name:28s} -> {code} -> {info}")
    print(f"\nDone (--all): {len(added)} added, {len(kept)} kept, {len(failed)} failed.")
    if failed:
        for f in failed:
            print(f"  ! {f}")
        sys.exit(1)


def main():
    m = _load_relay_helpers()
    m.load_dotenv(ROOT)

    ap = argparse.ArgumentParser(description="Fetch missing HUD country flags.")
    ap.add_argument("--sheet-id", default=os.environ.get("RACECAST_SHEET_ID"),
                    help="Google Sheet ID (default: env RACECAST_SHEET_ID / .env).")
    ap.add_argument("--config-tab", default="Configuration",
                    help="Tab holding the Country column (default 'Configuration').")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be downloaded; write nothing.")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if a flag already exists.")
    ap.add_argument("--all", action="store_true",
                    help="Ignore the sheet; fetch EVERY country flag from flagcdn "
                         "(plus the GB home nations) into src/assets/flags/.")
    args = ap.parse_args()

    if args.all:
        _fetch_all(m, args)
        return

    if not args.sheet_id:
        sys.exit("ERROR: no sheet id. Set RACECAST_SHEET_ID in .env or pass --sheet-id.")

    os.makedirs(FLAGS_DIR, exist_ok=True)
    countries = _sheet_countries(args.sheet_id, args.config_tab)
    print(f"Countries in '{args.config_tab}': {len(countries)}")

    name2code = {v.strip().lower(): k
                 for k, v in json.loads(_get(CODES_URL).decode("utf-8")).items()}

    added, kept, missing = [], [], []
    for country in countries:
        key = m.asset_key(country)
        if not key:
            continue
        existing = m.resolve_asset(ASSETS_DIR, "flags", key)
        if existing and not args.force:
            kept.append((country, os.path.basename(existing[0])))
            continue
        norm = country.strip().lower()
        code = name2code.get(norm) or name2code.get(ALIASES.get(norm, ""))
        if not code:
            missing.append(country)
            continue
        dest = os.path.join(FLAGS_DIR, f"{key}.svg")
        if args.dry_run:
            added.append((country, code, f"flags/{key}.svg (dry-run)"))
            continue
        try:
            data = _get(FLAG_URL.format(code=code))
        except Exception as e:
            missing.append(f"{country} (download failed: {type(e).__name__})")
            continue
        with open(dest, "wb") as fh:
            fh.write(data)
        added.append((country, code, f"flags/{key}.svg ({len(data)}B)"))

    for c, code, info in added:
        print(f"  + {c:24s} -> {code} -> {info}")
    for c, fn in kept:
        print(f"  = {c:24s} -> kept {fn}")
    for c in missing:
        print(f"  ! {c:24s} -> NO MATCH (add a flag manually to src/assets/flags/"
              f"{m.asset_key(c) if isinstance(c, str) else '?'}.svg, or extend ALIASES)")

    print(f"\nDone: {len(added)} added, {len(kept)} kept, {len(missing)} unmatched.")
    if missing and not args.dry_run:
        sys.exit(1)


if __name__ == "__main__":
    main()
