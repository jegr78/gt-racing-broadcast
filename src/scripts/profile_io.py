"""Pure logic for whole-profile export/import: zip a league profile (its
profiles/<name>/ tree + optional runtime graphics/media) into a portable bundle,
and import such a bundle on another machine. No argv parsing, no network.
Mirrors backup_admin's discipline: validate before writing, atomic, fail-safe.
Standalone (no sibling imports) so it loads in a bare test the same way.

Bundle layout (a .zip):
  manifest.json   {kind, schema, name, display, created, includes_assets, counts}
  profile/...     the whole profiles/<name>/ tree (profile.env, overlay/, logo, …)
  graphics/...    runtime/<name>/graphics/   (only when includes_assets)
  media/...       runtime/<name>/media/      (only when includes_assets)
"""
import datetime
import json
import os
import re
import shutil
import tempfile
import zipfile

KIND = "profile-export"
SCHEMA = 1
ASSET_SECTIONS = ("graphics", "media", "brands")   # top-level subtrees beside profile/
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
# Defense-in-depth (#99): reject a decompression-bomb bundle before extraction.
# A real profile is a handful of CSS/PNG files + the two Intro/Outro clips, so
# 1 GiB / 50k members is far above any legitimate league export.
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 50_000


def slugify(name):
    """Free-form league name -> directory-safe slug. Doubles as path-traversal
    defense ('../etc' -> 'etc'). Mirrors profile_admin.slugify; keep in sync."""
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-_")


def _iso_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_tree(zf, src_dir, arc_prefix):
    """Add every file under src_dir to the zip under arc_prefix/. Returns the count
    added (0 when src_dir is missing/empty)."""
    n = 0
    if not src_dir or not os.path.isdir(src_dir):
        return n
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            zf.write(full, f"{arc_prefix}/{rel}")
            n += 1
    return n


def _read_display(profile_dir):
    """The NAME= value from profile.env, or '' when absent/unreadable."""
    try:
        with open(os.path.join(profile_dir, "profile.env"), encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == "NAME":
                    return v.strip()
    except OSError:  # unreadable profile.env -> fall back to the slug
        pass
    return ""


def export_profile(name, sources, include_assets, dest):
    """Zip a profile into a portable bundle. `sources` = {profile_dir, graphics,
    media}. `dest` is a target .zip path, or a directory (then <slug>-profile.zip
    inside it). Returns the written path. Raises ValueError on a bad name or a
    profile dir that is missing / has no profile.env."""
    slug = slugify(name)
    if not slug:
        raise ValueError(f"invalid profile name: {name!r}")
    profile_dir = sources.get("profile_dir")
    if not profile_dir or not os.path.isdir(profile_dir):
        raise ValueError(f"profile dir not found: {profile_dir}")
    if not os.path.isfile(os.path.join(profile_dir, "profile.env")):
        raise ValueError("profile has no profile.env")
    path = os.path.join(dest, f"{slug}-profile.zip") if os.path.isdir(dest) else dest
    display = _read_display(profile_dir) or slug
    counts = {}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            counts["profile"] = _add_tree(zf, profile_dir, "profile")
            if include_assets:
                for sect in ASSET_SECTIONS:
                    counts[sect] = _add_tree(zf, sources.get(sect), sect)
            manifest = {"kind": KIND, "schema": SCHEMA, "name": slug,
                        "display": display, "created": _iso_utc(),
                        "includes_assets": bool(include_assets), "counts": counts}
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # best-effort cleanup of temp file on error
            pass
        raise
    return path


def _read_manifest_from(zf):
    try:
        return json.loads(zf.read("manifest.json"))
    except (KeyError, ValueError) as exc:
        raise ValueError("bundle has no readable manifest.json") from exc


def _safe_members(zf, manifest):
    """Validate every member: manifest.json, or under profile/ or an ASSET_SECTION,
    with no absolute path and no '..'. profile/profile.env must be present and the
    manifest kind must match. Returns the member list or raises ValueError."""
    if manifest.get("kind") != KIND:
        raise ValueError("not a profile export (wrong or missing kind)")
    members = zf.namelist()
    if len(members) > MAX_BUNDLE_MEMBERS:
        raise ValueError(f"bundle has too many members (> {MAX_BUNDLE_MEMBERS})")
    if sum(i.file_size for i in zf.infolist()) > MAX_BUNDLE_BYTES:
        raise ValueError(f"bundle decompresses past {MAX_BUNDLE_BYTES} bytes")
    if "profile/profile.env" not in members:
        raise ValueError("profile export missing profile/profile.env")
    allowed_top = ("profile",) + ASSET_SECTIONS
    for name in members:
        if name == "manifest.json":
            continue
        norm = name.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError(f"unsafe path in bundle: {name!r}")
        if norm.split("/", 1)[0] not in allowed_top:
            raise ValueError(f"unexpected entry in bundle: {name!r}")
    return members


def _swap_dir(staged, live):
    """Replace live with staged atomically-ish via an .old backup."""
    parent = os.path.dirname(live)
    os.makedirs(parent, exist_ok=True)
    old = live + ".old"
    if os.path.exists(old):
        shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(live):
        os.replace(live, old)
    shutil.move(staged, live)
    shutil.rmtree(old, ignore_errors=True)


def read_manifest(path):
    """The manifest dict from a bundle zip, or {} when unreadable. (For UI/list.)"""
    try:
        with zipfile.ZipFile(path) as zf:
            return json.loads(zf.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def import_profile(src_zip, roots, force=False):
    """Create profiles/<slug>/ (+ runtime/<slug>/graphics|media when present) from a
    bundle. `roots` = {profiles_root, runtime_root}. Returns {name, display,
    includes_assets}. Validates the whole archive BEFORE touching any live dir;
    raises ValueError (malformed/unsafe) or FileExistsError (slug taken, no force).
    A `force` re-import replaces the profile tree and only the asset sections the
    bundle carries: a config-only (no-assets) bundle leaves existing
    runtime/<slug>/graphics|media untouched rather than wiping them."""
    if not os.path.exists(src_zip):
        raise ValueError(f"bundle not found: {src_zip}")
    tmp = tempfile.mkdtemp(prefix="profimport-")
    try:
        with zipfile.ZipFile(src_zip) as zf:
            manifest = _read_manifest_from(zf)
            _safe_members(zf, manifest)       # raises before any extract
            zf.extractall(tmp)                # safe: names validated above
        slug = slugify(manifest.get("name") or "")
        if not slug:
            raise ValueError("bundle manifest has no usable profile name")
        target = os.path.join(roots["profiles_root"], slug)
        if os.path.exists(target) and not force:
            raise FileExistsError(f"profile already exists: {slug} (use force to replace)")
        _swap_dir(os.path.join(tmp, "profile"), target)
        for sect in ASSET_SECTIONS:
            staged = os.path.join(tmp, sect)
            if os.path.isdir(staged):
                _swap_dir(staged, os.path.join(roots["runtime_root"], slug, sect))
        return {"name": slug, "display": manifest.get("display") or slug,
                "includes_assets": bool(manifest.get("includes_assets"))}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
