#!/usr/bin/env python3
"""Download the broadcast still-graphics for OBS from the Google Sheet 'Assets' tab.

Each Assets row whose value cell is a Google-Drive share link is downloaded as
'<Label>.png' into the graphics dir (repo: <repo>/runtime/graphics ; distributed
package: <package>/graphics). The Sheet label IS the filename — there is no mapping
table, so keep Sheet labels filesystem-clean. YouTube rows (Intro/Outro) are skipped;
those are handled by get-media.py. Never stored under src/, never committed.

Usage: python3 get-graphics.py [--out DIR] [--sheet-id ID] [--assets-tab NAME]
       [--only "Label[,Label...]"]
"""
import argparse, csv, io, json, os, re, sys
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

# Pure stdlib placeholder helper from src/scripts (resolved both from source and
# the frozen bundle, mirroring get-media.py). It is NOT config.py, so the relay's
# dependency-light contract holds.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "..", "scripts"),
              os.path.join(getattr(sys, "_MEIPASS", _HERE), "src", "scripts")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)
import placeholders  # noqa: E402


def load_dotenv(start):
    """Load KEY=VALUE pairs from a .env at the script dir or the project root into
    os.environ (real env vars win). Bounded to the project (nearest ancestor with a
    .git/.env.example marker). KEEP IN SYNC with the copies in racecast-feeds.py,
    setup-assets.py and get-media.py."""
    candidates, d = [start], start
    for _ in range(4):
        if any(os.path.exists(os.path.join(d, mk)) for mk in (".git", ".env.example")):
            candidates.append(d)
            break
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for c in candidates:
        p = os.path.join(c, ".env")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return p
    return None


def is_drive_url(url):
    """True iff the URL's HOST is drive.google.com (or a subdomain). A plain
    substring check would also match e.g. https://evil.example/?drive.google.com."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "drive.google.com" or host.endswith(".drive.google.com")


def drive_id(url):
    """Extract a Google-Drive file ID from a share or download URL, else None."""
    if not url:
        return None
    m = (re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
         or re.search(r"[?&]id=([A-Za-z0-9_-]+)", url))
    return m.group(1) if m else None


def to_download_url(file_id):
    """Direct-download endpoint for a Drive file ID (no API key)."""
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def drive_confirm_url(url, data):
    """Resolve the real file URL from a Drive large-file interstitial.

    `data` is the HTML body (bytes) Drive returned instead of the file. Drive
    has two interstitial formats: the current one is a <form> that GETs the
    drive.usercontent.google.com/download endpoint with hidden inputs (id,
    export, authuser, confirm, uuid); the legacy one embeds a `confirm=<token>`
    query param in a download link. Returns the URL to GET the file, or None if
    the body carries neither (the caller then raises)."""
    text = data.decode("utf-8", "replace")
    form = re.search(r"<form[^>]*\baction=\"([^\"]+)\"", text)
    if form:
        action = form.group(1).replace("&amp;", "&")
        params = {}
        for tag in re.findall(r"<input\b[^>]*>", text):
            name = re.search(r"name=\"([^\"]*)\"", tag)
            if not (name and name.group(1)):
                continue
            value = re.search(r"value=\"([^\"]*)\"", tag)
            params[name.group(1)] = value.group(1).replace("&amp;", "&") if value else ""
        if params:
            sep = "&" if "?" in action else "?"
            return action + sep + urlencode(params)
    m = re.search(r"confirm=([0-9A-Za-z_-]+)", text)
    if m:
        return url + "&confirm=" + m.group(1)
    return None


def safe_filename(label):
    """'<trimmed label>.png', or None if the label is empty or contains a path
    separator / control char. Spaces are allowed (OBS already uses them)."""
    name = (label or "").strip().strip(".")
    if not name or "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
        return None
    return f"{name}.png"


# Asset labels owned by get-media.py (downloaded as MP4/MP3, NOT graphics). They
# must be skipped here even when their value cell is a Drive link: a Drive-hosted
# Intermission Music MP3 would otherwise be downloaded as 'Intermission Music.png'
# and fail the PNG signature check. Intro/Outro only escape incidentally when
# YouTube-hosted. KEEP IN SYNC with MEDIA_LABELS + MUSIC_LABEL in get-media.py.
MEDIA_LABELS = {"intro video", "outro video", "trailer video", "intermission music"}

# Assets tab "Internal" checkbox (OBS-only assets hidden from the console Graphics
# browser). Located by header name — mirrors the Crew/Brand header lookup in
# racecast-feeds.py; truthy tokens mirror its CREW_TRUTHY. A Google-Sheets checkbox
# exports as TRUE/FALSE in the gviz CSV. Parsed independently of the download link so a
# ticked row without a link (e.g. a placeholder-seeded graphic) is still marked. With no
# header / no Internal column the set is empty and the browser shows everything.
ASSET_NAME_HEADERS = ("name", "label", "asset")
ASSET_INTERNAL_HEADERS = ("internal", "obs only", "obs-only")
ASSET_TRUTHY = frozenset({"x", "yes", "true", "1", "y", "✓"})

# Sidecar manifest the relay's list_graphics() reads to hide internal assets. The
# filename is a shared contract with racecast-feeds.py (which cannot import this
# dependency-light script) — keep the literal in sync.
MANIFEST_NAME = "manifest.json"


def _asset_truthy(v):
    return (v or "").strip().lower() in ASSET_TRUTHY


def internal_from_csv(rows):
    """Set of Assets-tab labels whose 'Internal' checkbox is ticked. Requires a header
    row with an ASSET_INTERNAL_HEADERS column; the label is read from the
    ASSET_NAME_HEADERS column (default col 0). Empty set when there is no header / no
    Internal column (backward compatible)."""
    if not rows:
        return set()
    header = [(h or "").strip().lower() for h in rows[0]]
    ii = next((header.index(h) for h in ASSET_INTERNAL_HEADERS if h in header), None)
    if ii is None:
        return set()
    ni = next((header.index(h) for h in ASSET_NAME_HEADERS if h in header), 0)
    out = set()
    for row in rows[1:]:
        if len(row) <= ii or not _asset_truthy(row[ii]):
            continue
        label = (row[ni] if ni < len(row) else "").strip()
        if label:
            out.add(label)
    return out


def write_manifest(out_dir, internal_labels):
    """Write <out_dir>/manifest.json = {"internal": [<sorted labels>]} recording the
    OBS-only assets the console Graphics browser must hide. Best-effort: an IO error is a
    warning, never fatal. Not a *.png, so list_graphics/resolve_graphic never touch it."""
    path = os.path.join(out_dir, MANIFEST_NAME)
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"internal": sorted(internal_labels)}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"WARNING: could not write {MANIFEST_NAME}: {e}")


def graphics_from_csv(rows):
    """Assets-tab rows -> {label: drive_url} for every row whose first non-empty value
    cell is a Google-Drive link. YouTube / non-Drive rows and get-media.py's own
    MEDIA_LABELS rows are skipped. Label verbatim."""
    out = {}
    for row in rows:
        if not row:
            continue
        label = (row[0] or "").strip()
        if not label or label.lower() in MEDIA_LABELS:
            continue
        for cell in row[1:]:
            v = (cell or "").strip()
            if not v:
                continue
            if is_drive_url(v) and drive_id(v):
                out[label] = v
            break  # only the first non-empty value cell matters
    return out


def graphics_dir(here):
    """Where graphics live when --out is not given. Mirrors get-media.media_dir():
    repo (src/relay) -> <repo>/runtime/graphics ; package (relay) -> <pkg>/graphics."""
    if os.path.basename(here) == "relay" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime", "graphics")
    return os.path.join(os.path.dirname(here), "graphics")


def obs_template_dir(here):
    """Dir holding the bundled OBS collection template, a sibling of this script's
    parent in every layout: repo src/relay -> src/obs ; package relay/ -> ../obs ;
    frozen _MEIPASS/src/relay -> _MEIPASS/src/obs."""
    return os.path.join(os.path.dirname(here), "obs")


def unlinked_graphic_targets(expected, linked, only_labels=None):
    """The OBS-referenced graphics the Sheet has NO link for -> reset to the
    placeholder (issue #387). `expected` is the '<name>.png' list from the OBS
    collection; `linked` is the Sheet's {label: url} for linked graphics; a
    graphic counts as linked iff its '<label>.png' matches. When `only_labels`
    is given (a --only run), the reset is scoped to those labels. Returns the
    sorted target names."""
    linked_names = {safe_filename(lbl) for lbl in linked}
    linked_names.discard(None)
    targets = [n for n in expected if n not in linked_names]
    if only_labels is not None:
        only_names = {safe_filename(lbl) for lbl in only_labels}
        only_names.discard(None)
        targets = [n for n in targets if n in only_names]
    return sorted(targets)


def reset_unlinked_graphics(out_dir, here, linked, only_labels=None):
    """Overwrite the transparent placeholder onto every OBS-referenced graphic the
    Sheet has no link for, so a removed/absent link reverts a stale real graphic
    (issue #387). Best-effort; returns the sorted names written."""
    tpl = placeholders.find_obs_template(obs_template_dir(here))
    if not tpl:
        return []
    try:
        with open(tpl, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    targets = unlinked_graphic_targets(
        placeholders.expected_graphics_from_template(text), linked, only_labels)
    return placeholders.reset_placeholders(
        targets, out_dir, placeholders.graphic_placeholder_path())


def seed_missing_graphics(out_dir, here):
    """Drop the transparent placeholder for any OBS-collection-referenced graphic
    still missing in out_dir — covers graphics a league never put in the Sheet
    (e.g. weather overlays). Best-effort; returns the sorted names written."""
    tpl = placeholders.find_obs_template(obs_template_dir(here))
    if not tpl:
        return []
    try:
        with open(tpl, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    refs = placeholders.expected_graphics_from_template(text)
    return placeholders.fill_missing(refs, out_dir, placeholders.graphic_placeholder_path())


def fetch_assets_csv(sheet_id, tab, timeout=15):
    """Fetch the Assets tab as CSV via the public gviz endpoint (no API key)."""
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")
    req = Request(url, headers={"User-Agent": "racecast-graphics/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def download(url, out_path, timeout=60):
    """GET a Drive file to out_path as a PNG. Handles the large-file confirm
    interstitial. Writes atomically; verifies the PNG signature before committing."""
    req = Request(url, headers={"User-Agent": "racecast-graphics/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        data = resp.read()
    if ctype.startswith("text/html"):
        confirm_url = drive_confirm_url(url, data)
        if not confirm_url:
            raise RuntimeError("Drive returned an HTML interstitial with no confirm token")
        req2 = Request(confirm_url, headers={"User-Agent": "racecast-graphics/1.0"})
        with urlopen(req2, timeout=timeout) as resp2:
            data = resp2.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("downloaded data is not a PNG")
    tmp = out_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, out_path)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(here)
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=graphics_dir(here),
                    help="Target dir for <Label>.png files (default: graphics_dir).")
    ap.add_argument("--sheet-id", default=os.environ.get("RACECAST_SHEET_ID"),
                    help="Google Sheet ID holding the Assets tab. Default: env RACECAST_SHEET_ID.")
    ap.add_argument("--assets-tab", default="Assets")
    ap.add_argument("--only", default=None,
                    help="Comma-separated labels to fetch (default: all graphic rows).")
    a = ap.parse_args()

    if not a.sheet_id:
        sys.exit("ERROR: no Sheet ID (set SHEET_ID in the active profile or pass --sheet-id).")
    try:
        csv_text = fetch_assets_csv(a.sheet_id, a.assets_tab)
    except Exception as e:
        sys.exit(f"ERROR: could not read sheet Assets tab: {e}")

    rows = list(csv.reader(io.StringIO(csv_text)))
    all_graphics = graphics_from_csv(rows)
    if not all_graphics:
        sys.exit("ERROR: no graphic (Drive-link) rows found in the Assets tab.")
    wanted = None
    graphics = all_graphics
    if a.only:
        wanted = {x.strip() for x in a.only.split(",") if x.strip()}
        graphics = {k: v for k, v in all_graphics.items() if k in wanted}

    os.makedirs(a.out, exist_ok=True)
    write_manifest(a.out, internal_from_csv(rows))
    failed = []
    for label in sorted(graphics):
        fname = safe_filename(label)
        if not fname:
            print(f"WARNING: skipping unsafe label {label!r}")
            failed.append(label)
            continue
        out_path = os.path.join(a.out, fname)
        print(f"Downloading {label}: {fname}")
        try:
            download(to_download_url(drive_id(graphics[label])), out_path)
            print(f"OK -> {out_path}")
        except Exception as e:
            print(f"WARNING: download failed for {label}: {e}")
            failed.append(label)

    # Reset any OBS-referenced graphic the Sheet no longer links to its placeholder,
    # so a removed/absent link replaces a stale real graphic (issue #387). Download
    # failures stay in `all_graphics` (they are linked) so a transient error never
    # clobbers a good file; scoped to --only when that filter is active.
    reset = reset_unlinked_graphics(a.out, here, all_graphics, wanted)
    if reset:
        print(f"Reset {len(reset)} graphic(s) with no Sheet link to the placeholder: "
              f"{', '.join(reset)}")

    seeded = seed_missing_graphics(a.out, here)
    if seeded:
        print(f"Wrote transparent placeholder for {len(seeded)} graphic(s) still "
              f"missing (no Sheet asset): {', '.join(seeded)}")

    if failed:
        sys.exit(f"Incomplete: {', '.join(sorted(failed))} not downloaded.")


if __name__ == "__main__":
    main()
