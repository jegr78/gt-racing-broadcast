#!/usr/bin/env python3
"""Download per-league brand-logo overrides for the HUD from the Google Sheet
'Brands' tab.

Each Brands row whose logo cell is a Google-Drive share link is downloaded as
'<asset_key(brand)>.png' into the brands dir (repo: <repo>/runtime/brands ;
distributed package: <package>/brands). The relay serves /hud/assets/brands/<key>
OVERRIDE-FIRST: a file here wins over the committed src/assets/brands set, so a
league can replace a built-in logo (e.g. bmw) or add a new manufacturer (e.g.
cupra). The key is normalized with the SAME asset_key() the HUD uses on the
Configuration-tab brand text, so the stem always lines up. Never stored under
src/, never committed.

Usage: python3 get-brands.py [--out DIR] [--sheet-id ID] [--brands-tab NAME]
"""
import argparse, csv, io, os, re, sys
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

# Header names located case-insensitively (first match wins), mirroring the
# relay's tab parsers. The KEY column holds the brand text; the LOGO column holds
# the Drive share link.
BRAND_KEY_HEADERS = ("brand key", "brand", "brand name")
BRAND_LOGO_HEADERS = ("logo", "logo url", "image")


def load_dotenv(start):
    """Load KEY=VALUE pairs from a .env at the script dir or the project root into
    os.environ (real env vars win). Bounded to the project (nearest ancestor with a
    .git/.env.example marker). KEEP IN SYNC with the copies in racecast-feeds.py,
    setup-assets.py, get-media.py and get-graphics.py."""
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


def asset_key(s):
    """Normalize free text (country/brand) to an asset filename stem."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"[^a-z0-9-]", "", s)


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


def safe_filename(key):
    """'<key>.png' for a valid normalized brand key ([a-z0-9-]+), else None.
    The input is expected to already be asset_key()-normalized."""
    if not key or not re.fullmatch(r"[a-z0-9-]+", key):
        return None
    return f"{key}.png"


def brands_from_csv(rows):
    """Brands-tab rows -> {asset_key(brand): drive_url}. Columns are header-located
    (BRAND_KEY_HEADERS / BRAND_LOGO_HEADERS, first match wins, case-insensitive).
    A row is kept only when its logo cell is a Google-Drive link. No header row ->
    {} (we never positionally guess, to avoid mis-downloading)."""
    if not rows:
        return {}
    header = [(h or "").strip().lower() for h in rows[0]]
    ki = next((header.index(h) for h in BRAND_KEY_HEADERS if h in header), None)
    li = next((header.index(h) for h in BRAND_LOGO_HEADERS if h in header), None)
    if ki is None or li is None:
        return {}
    out = {}
    for row in rows[1:]:
        if len(row) <= ki or len(row) <= li:
            continue
        key = asset_key(row[ki])
        url = (row[li] or "").strip()
        if key and is_drive_url(url) and drive_id(url):
            out[key] = url
    return out


def brands_dir(here):
    """Where brand overrides live when --out is not given. Mirrors
    get-graphics.graphics_dir(): repo (src/relay) -> <repo>/runtime/brands ;
    package (relay) -> <pkg>/brands."""
    if os.path.basename(here) == "relay" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime", "brands")
    return os.path.join(os.path.dirname(here), "brands")


def fetch_brands_csv(sheet_id, tab, timeout=15):
    """Fetch the Brands tab as CSV via the public gviz endpoint (no API key)."""
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")
    req = Request(url, headers={"User-Agent": "racecast-brands/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def download(url, out_path, timeout=60):
    """GET a Drive file to out_path as a PNG. Handles the large-file confirm
    interstitial. Writes atomically; verifies the PNG signature before committing."""
    req = Request(url, headers={"User-Agent": "racecast-brands/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        data = resp.read()
    if ctype.startswith("text/html"):
        confirm_url = drive_confirm_url(url, data)
        if not confirm_url:
            raise RuntimeError("Drive returned an HTML interstitial with no confirm token")
        req2 = Request(confirm_url, headers={"User-Agent": "racecast-brands/1.0"})
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
    ap.add_argument("--out", default=brands_dir(here),
                    help="Target dir for <key>.png files (default: brands_dir).")
    ap.add_argument("--sheet-id", default=os.environ.get("RACECAST_SHEET_ID"),
                    help="Google Sheet ID holding the Brands tab. Default: env RACECAST_SHEET_ID.")
    ap.add_argument("--brands-tab", default="Brands")
    a = ap.parse_args()

    if not a.sheet_id:
        sys.exit("ERROR: no Sheet ID (set SHEET_ID in the active profile or pass --sheet-id).")
    try:
        csv_text = fetch_brands_csv(a.sheet_id, a.brands_tab)
    except Exception as e:
        sys.exit(f"ERROR: could not read sheet Brands tab: {e}")

    brands = brands_from_csv(list(csv.reader(io.StringIO(csv_text))))
    if not brands:
        # No Brands tab / no override rows is NOT an error: the committed base set
        # is still served. Exit 0 so `racecast brands` is safe to run on any league.
        print("No brand-override rows in the Brands tab — base logos unchanged.")
        return

    os.makedirs(a.out, exist_ok=True)
    failed = []
    for key in sorted(brands):
        fname = safe_filename(key)
        if not fname:
            print(f"WARNING: skipping unsafe brand key {key!r}")
            failed.append(key)
            continue
        out_path = os.path.join(a.out, fname)
        print(f"Downloading {key}: {fname}")
        try:
            download(to_download_url(drive_id(brands[key])), out_path)
            print(f"OK -> {out_path}")
        except Exception as e:
            print(f"WARNING: download failed for {key}: {e}")
            failed.append(key)

    if failed:
        sys.exit(f"Incomplete: {', '.join(sorted(failed))} not downloaded.")


if __name__ == "__main__":
    main()
