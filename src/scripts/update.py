#!/usr/bin/env python3
"""`racecast update` self-updates the standalone binary from GitHub Releases.
Checks /releases/latest, compares semver tags, downloads the platform archive and
swaps the running binary. Windows needs the rename trick, since a running exe can
be renamed but not overwritten. Frozen-only: a repo checkout updates with
`git pull`. Design: docs/superpowers/specs/2026-06-05-self-update-design.md."""
import argparse, hashlib, json, os, platform as _platform, shutil, sys, tarfile, tempfile
import urllib.error, urllib.parse, urllib.request, zipfile

# Renamed from gt-endurance-racing-broadcast; GitHub redirects the old slug (web,
# git, API), so older binaries keep updating. Never re-create a repo under it.
REPO = "jegr78/gt-racing-broadcast"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(tag):
    """'vX.Y.Z' -> (X, Y, Z); None for anything else, including 'dev'."""
    if not tag or not isinstance(tag, str) or not tag.startswith("v"):
        return None
    parts = tag[1:].split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def asset_name(platform, machine=None):
    """The release asset for a sys.platform value, matching release.yml's matrix.
    Linux ships separate x86-64 and ARM64 archives, and `machine`, defaulting to
    this host's platform.machine(), selects between them, so a self-updating ARM64
    binary fetches the ARM64 archive."""
    if platform.startswith("win"):
        return "racecast-windows.zip"
    if platform == "darwin":
        return "racecast-macos.tar.gz"
    machine = _platform.machine() if machine is None else machine
    if machine.lower() in ("aarch64", "arm64"):
        return "racecast-linux-arm64.tar.gz"
    return "racecast-linux.tar.gz"


def _find_asset_url(release, platform):
    """The download URL of this platform's asset in `release`, or None while it is
    not uploaded yet."""
    want = asset_name(platform)
    for asset in release.get("assets", []):
        if asset.get("name") == want:
            return asset.get("browser_download_url")
    return None


def _is_hex_sha(s, lo=7, hi=40):
    """True iff `s` looks like a short or full commit SHA: lo..hi hex chars."""
    s = (s or "").lower()
    return lo <= len(s) <= hi and all(c in "0123456789abcdef" for c in s)


def _get_json(url, opener=None):
    """GET `url` and parse JSON. `opener(request, timeout)` is injectable for
    tests."""
    req = urllib.request.Request(url, headers={"User-Agent": "racecast-update"})
    opener = urllib.request.urlopen if opener is None else opener
    resp = opener(req, timeout=15)
    try:
        return json.load(resp)
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()


def classify(release, platform, current, frozen=False):
    """The whole update decision. Always a (kind, detail, url) 3-tuple:
    ('dev',        None, None)   running from source -> refuse
    ('error',      message, None)  malformed release data
    ('up-to-date', tag, None)
    ('building',   tag, None)    newer release exists, platform asset not uploaded yet
    ('update',     tag, url)
    An unparseable `current` such as 'dev' refuses in repo mode, but a frozen
    binary built without --version gets the latest release offered instead: it is
    incomparable, so it is never 'up-to-date'."""
    cur = parse_version(current)
    if cur is None and not frozen:
        return ("dev", None, None)
    tag = release.get("tag_name", "")
    new = parse_version(tag)
    if new is None:
        return ("error", f"unexpected tag on the latest release: {tag!r}", None)
    if cur is not None and new <= cur:
        return ("up-to-date", tag, None)
    url = _find_asset_url(release, platform)
    return ("update", tag, url) if url else ("building", tag, None)


def classify_tag(release, platform):
    """Decide how to install one *named* release, the UI's preview and explicit
    path. There is no semver compare: an explicit tag means 'install exactly this'.
    Returns a (kind, tag, url) 3-tuple:
    ('error',    message, None)  malformed release data (no tag_name)
    ('building', tag, None)      release exists, platform asset not uploaded yet
    ('install',  tag, url)"""
    tag = release.get("tag_name", "")
    if not tag:
        return ("error", "release has no tag_name", None)
    url = _find_asset_url(release, platform)
    return ("install", tag, url) if url else ("building", tag, None)


def fetch_release_by_tag(tag, opener=None):
    """GET one release by tag. `opener(request, timeout)` is injectable for tests.
    Raises urllib HTTPError(404) for an unknown tag (caller maps to a friendly
    'no such release')."""
    return _get_json(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}", opener)


def _commit_of(release):
    """Best commit id for a pre-release row: the release target SHA when it really
    is one, else the short SHA embedded in the version or name, such as 'cafef00'
    in 'preview-pr9-cafef00'. GitHub sets target_commitish to a *branch name* for
    branch-targeted releases, so it is only trusted when it parses as a hex SHA."""
    target = (release.get("target_commitish") or "").strip()
    if _is_hex_sha(target):
        return target
    text = release.get("version") or release.get("name") or release.get("tag_name") or ""
    tail = text.rsplit("-", 1)[-1].lower()
    return tail if _is_hex_sha(tail, lo=1, hi=12) else ""


def classify_prereleases(releases, platform):
    """Map the GitHub /releases list to the UI's installable-preview rows.
    Keeps only prereleases; for each returns
    {tag, version, title, commit, published_at, asset_url|None, notes}
    where asset_url is None when this platform's asset is not uploaded yet."""
    rows = []
    for rel in releases:
        if not rel.get("prerelease"):
            continue
        url = _find_asset_url(rel, platform)
        rows.append({
            "tag": rel.get("tag_name", ""),
            "version": rel.get("version") or rel.get("name") or rel.get("tag_name", ""),
            "title": rel.get("name") or rel.get("tag_name", ""),
            "commit": _commit_of(rel),
            "published_at": rel.get("published_at", ""),
            "asset_url": url,
            "notes": rel.get("body") or "",
        })
    return rows


def fetch_releases(per_page=30, opener=None):
    """GET the releases list (newest first). `opener` injectable for tests."""
    return _get_json(
        f"https://api.github.com/repos/{REPO}/releases?per_page={int(per_page)}", opener)


def swap_plan(platform, exe, new):
    """Ordered steps that put `new` in place of the running `exe`.
    The Windows branch uses ntpath so the function stays computable when tests run
    on macOS or Linux, where os.path.dirname cannot split a C:\\ path."""
    if platform.startswith("win"):
        import ntpath
        old = ntpath.join(ntpath.dirname(exe), "racecast-old.exe")
        return [("rename", exe, old), ("move", new, exe)]
    return [("replace", new, exe), ("chmod", exe)]


def safe_member(name):
    """True iff an archive member path is safe to extract: no absolute path, no
    drive letter, no '..'."""
    if not name or name.startswith(("/", "\\")):
        return False
    if len(name) > 1 and name[1] == ":":
        return False
    return ".." not in name.replace("\\", "/").split("/")


# Caps for archive extraction (#99): a malicious or corrupt release asset must not
# exhaust disk via a decompression bomb. The update archive is one onefile binary,
# the macOS .app wrapper and .env.example, so these caps sit far above any real
# build.
MAX_EXTRACT_BYTES = 600 * 1024 * 1024
MAX_EXTRACT_MEMBERS = 10_000


def _check_extract_budget(sizes):
    """Raise ValueError if the archive's member count or total decompressed size
    exceeds the caps. `sizes` is an iterable of per-member byte sizes where None
    counts as 0. Run BEFORE extractall so a bomb is rejected without any write."""
    count = 0
    total = 0
    for s in sizes:
        count += 1
        if count > MAX_EXTRACT_MEMBERS:
            raise ValueError(f"archive has too many members (> {MAX_EXTRACT_MEMBERS})")
        total += s or 0
        if total > MAX_EXTRACT_BYTES:
            raise ValueError(f"archive decompresses past {MAX_EXTRACT_BYTES} bytes")


def _tar_extractall(tf, dest_dir, members):
    """extractall with the stdlib 'data' filter where the interpreter has it,
    Python 3.12 and recent 3.8 to 3.11 patch releases, so no symlink or hardlink
    member can redirect a write outside dest_dir. `members` already excludes link
    and device entries, so the fallback path is safe on older interpreters."""
    if hasattr(tarfile, "data_filter"):
        tf.extractall(dest_dir, members=members, filter="data")
    else:
        tf.extractall(dest_dir, members=members)


def fetch_latest(opener=None):
    """GET the latest-release JSON. `opener(request, timeout)` is injectable for tests."""
    return _get_json(API_LATEST, opener)


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that downgrades off HTTPS during an update download.
    Neither a MITM nor a future API host that 302s to http:// may strip TLS while
    the binary is being fetched."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError("update: refusing non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SECURE_OPENER = urllib.request.build_opener(_HttpsOnlyRedirect())


def archive_sha256(path):
    """SHA-256 hex digest of the file at `path`, streamed in constant memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_digest(release, platform):
    """The SHA-256 hex of this platform's asset as GitHub computed it server-side,
    the release-asset `digest` field formatted 'sha256:<hex>', or None for older
    releases that predate that field. Verified against the download before the
    binary swap, so a corrupted or tampered asset is rejected."""
    want = asset_name(platform)
    for asset in release.get("assets", []):
        if asset.get("name") != want:
            continue
        dig = (asset.get("digest") or "").strip().lower()
        if dig.startswith("sha256:"):
            hexpart = dig.split(":", 1)[1]
            if len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart):
                return hexpart
        return None
    return None


def download(url, dst, opener=None):
    """Fetch `url` to file path `dst`: HTTPS only, cert-verified, no downgrade."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"update: refusing non-HTTPS download URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "racecast-update"})
    opener = _SECURE_OPENER.open if opener is None else opener
    resp = opener(req, timeout=120)
    try:
        with open(dst, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()


def extract_binary(archive, dest_dir):
    """Extract the zip or tar.gz archive into dest_dir behind the safe_member
    guard and return the path of the contained racecast binary, or None."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            infos = [i for i in zf.infolist() if safe_member(i.filename)]
            _check_extract_budget(i.file_size for i in infos)
            zf.extractall(dest_dir, members=[i.filename for i in infos])
    else:
        with tarfile.open(archive, "r:gz") as tf:
            # Drop symlink, hardlink and device members: only regular files and
            # dirs belong in a release archive, and a recreated symlink could let a
            # following member escape dest_dir on a pre-3.12 interpreter.
            members = [mem for mem in tf.getmembers()
                       if safe_member(mem.name) and (mem.isfile() or mem.isdir())]
            _check_extract_budget(mem.size for mem in members)
            _tar_extractall(tf, dest_dir, members)
    for name in ("racecast.exe", "racecast"):
        path = os.path.join(dest_dir, name)
        if os.path.isfile(path):
            return path
    return None


def ui_asset_name(platform):
    """The racecast-ui artifact name inside the release archive, matching
    release.yml's matrix: a .exe on Windows, a .app bundle on macOS, a bare binary
    on Linux."""
    if platform.startswith("win"):
        return "racecast-ui.exe"
    if platform == "darwin":
        return "racecast-ui.app"
    return "racecast-ui"


def ui_old_path(dst):
    """Where a locked, running racecast-ui.exe is renamed aside during an update,
    the same idea as the main binary's racecast-old.exe. cleanup_old_binary removes
    it on the next launch."""
    base, ext = os.path.splitext(dst)
    return base + "-old" + ext


def _vacate_ui(dst, platform, remove):
    """Free `dst` for the incoming racecast-ui. On Windows the GUI launcher is
    almost always RUNNING when it self-updates, because the Control Center fires the
    update, and a running .exe is locked against deletion. It CAN still be renamed
    aside, the same trick swap_plan uses for racecast-old.exe, so the new launcher
    lands and cleanup_old_binary sweeps the leftover next launch. Elsewhere a plain
    remove suffices, because POSIX unlinks a running binary and the live process
    keeps its inode, so a failure there is a real error and must propagate."""
    try:
        remove(dst)
    except OSError:
        if not platform.startswith("win"):
            raise
        os.replace(dst, ui_old_path(dst))   # rename the locked running exe aside


def install_ui(src_dir, target_dir, platform, remove=os.remove):
    """Place the extracted racecast-ui artifact next to the racecast binary,
    replacing any existing one. The archive ships racecast and racecast-ui
    together, so the GUI launcher travels with every update. Returns the install
    path, or None when the archive carried no racecast-ui, as pre-1.2 releases did.
    The caller treats an OSError as non-fatal, since the racecast swap has already
    succeeded by then. `remove` is an injectable seam for the unit test."""
    name = ui_asset_name(platform)
    src = os.path.join(src_dir, name)
    if not os.path.exists(src):
        return None
    dst = os.path.join(target_dir, name)
    if os.path.isdir(dst) and not os.path.islink(dst):
        shutil.rmtree(dst)          # macOS .app is a directory bundle
    elif os.path.lexists(dst):
        _vacate_ui(dst, platform, remove)
    shutil.move(src, dst)
    if not platform.startswith("win") and os.path.isfile(dst):
        os.chmod(dst, os.stat(dst).st_mode | 0o755)
    return dst


def perform(plan):
    """Execute a swap_plan. The steps are tiny on purpose: the logic lives in
    swap_plan(), where it is unit-tested."""
    for step in plan:
        if step[0] == "rename":
            os.replace(step[1], step[2])
        elif step[0] in ("move", "replace"):
            shutil.move(step[1], step[2]) if step[0] == "move" else os.replace(step[1], step[2])
        elif step[0] == "chmod":
            os.chmod(step[1], os.stat(step[1]).st_mode | 0o755)


def confirmed(answer):
    return answer.strip().lower().startswith("y")


def _download_and_swap(url, tag, digest=None):
    """Download the archive at `url`, swap the running binary and reinstall
    racecast-ui. Shared by the latest-release flow and the --tag flow. Prints
    progress. When `digest`, GitHub's published SHA-256, is given, the download is
    verified before anything is extracted or swapped, and a mismatch aborts with
    nothing changed."""
    exe = sys.executable
    # Tempdir NEXT TO the binary so the final swap is an atomic same-filesystem
    # rename: a system tempdir can be another filesystem (EXDEV) and a
    # copy-overwrite of a running ELF fails with ETXTBSY. If that dir is not
    # writable, fail with a clear message rather than an opaque traceback.
    try:
        td_ctx = tempfile.TemporaryDirectory(dir=os.path.dirname(exe))
    except OSError as exc:
        sys.exit(f"update: cannot write next to the binary ({exc}). "
                 "Move racecast to a writable folder and retry.")
    with td_ctx as td:
        archive = os.path.join(td, asset_name(sys.platform))
        print("Downloading:", url)
        download(url, archive)
        if digest:
            actual = archive_sha256(archive)
            if actual != digest:
                sys.exit(f"update: checksum mismatch. Expected {digest[:12]}..., got "
                         f"{actual[:12]}.... Aborted, the binary is unchanged.")
            print(f"verified sha256 {actual[:12]}…")
        else:
            print("note: this release published no checksum, integrity not verified.")
        new = extract_binary(archive, td)
        if not new:
            sys.exit("update: archive did not contain the racecast binary. Aborted, nothing changed.")
        try:
            perform(swap_plan(sys.platform, exe, new))
        except OSError as exc:
            hint = (" Restore by renaming racecast-old.exe back to racecast.exe."
                    if sys.platform.startswith("win") and not os.path.exists(exe) else "")
            sys.exit(f"update: swap failed ({exc}).{hint}")
        try:
            ui_path = install_ui(td, os.path.dirname(exe), sys.platform)
        except OSError as exc:
            ui_path = None
            print(f"update: note, racecast-ui not installed ({exc}). "
                  "Use `racecast ui` from the CLI, or reinstall the archive.")
    print(f"updated to {tag}. Restart racecast to use it.")
    if ui_path:
        print(f"installed {os.path.basename(ui_path)} next to racecast.")
        print("If the Control Center is open, fully quit and relaunch it to load "
              "the new version; the running one is still the old build.")
    if sys.platform.startswith("win"):
        print("(old binaries are kept as *-old.exe and removed on the next start)")


def main():
    ap = argparse.ArgumentParser(prog="update", add_help=True)
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--current", default="dev", help=argparse.SUPPRESS)  # injected by racecast
    ap.add_argument("--tag", help="install this exact release tag (UI preview/pin path)")
    a = ap.parse_args()

    # Frozen one-shots run in-process in the binary, so sys.frozen is visible here,
    # while repo mode runs under a plain python3. A frozen 'dev' binary, a local
    # build without --version, gets the latest release offered instead.
    frozen = bool(getattr(sys, "frozen", False))
    if parse_version(a.current) is None and not frozen:
        sys.exit("update: running from source. Update with `git pull` instead.")

    if a.tag:
        try:
            release = fetch_release_by_tag(a.tag)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                sys.exit(f"update: no release tagged {a.tag!r}.")
            sys.exit(f"update: cannot fetch release {a.tag!r} ({exc}).")
        except Exception as exc:
            sys.exit(f"update: cannot reach GitHub ({exc}). Check your connection.")
        kind, tag, url = classify_tag(release, sys.platform)
        if kind == "error":
            sys.exit(f"update: {tag}")
        if kind == "building":
            sys.exit(f"update: {tag} has no {asset_name(sys.platform)} asset yet. "
                     "Retry in a few minutes.")
        if a.check:
            print(f"update --check: {tag} is available for this platform "
                   "(run without --check to install).")
            return
        print(f"update: installing {tag}")
        if not a.yes and not confirmed(input("Download and replace this binary? [y/N] ")):
            print("aborted.")
            return
        _download_and_swap(url, tag, expected_digest(release, sys.platform))
        return

    try:
        release = fetch_latest()
    except Exception as exc:
        sys.exit(f"update: cannot reach GitHub releases ({exc}). Check your connection.")

    action = classify(release, sys.platform, a.current, frozen)
    if action[0] == "error":
        sys.exit(f"update: {action[1]}")
    if action[0] == "up-to-date":
        print(f"up to date ({a.current}; latest release is {action[1]}).")
        return
    if action[0] == "building":
        sys.exit(f"update: {action[1]} is out but the binaries are still building. "
                 "Retry in a few minutes.")

    _, tag, url = action
    if a.check:
        print(f"update available: {a.current} -> {tag}  (run `racecast update` to install)")
        return
    print(f"update: {a.current} -> {tag}")
    if not a.yes and not confirmed(input("Download and replace this binary? [y/N] ")):
        print("aborted.")
        return
    _download_and_swap(url, tag, expected_digest(release, sys.platform))


if __name__ == "__main__":
    main()
