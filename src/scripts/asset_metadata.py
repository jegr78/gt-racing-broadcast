"""Header-aware Assets metadata. Human labels never become technical identities."""

import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlparse

NAME_HEADERS = {"name", "label", "asset"}
URL_HEADERS = {"url", "link", "drive link", "download url", "download link"}
INTERNAL_HEADERS = {"internal", "obs only", "obs-only"}
MEDIA = {
    "intro video": "intro.mp4",
    "outro video": "outro.mp4",
    "trailer video": "trailer.mp4",
    "intermission music": "intermission.mp3",
}
MEDIA_NAMES = {v: k.title() for k, v in MEDIA.items()}
TRUTHY = {"x", "yes", "true", "1", "y", "✓"}
_LOCK = threading.RLock()


def norm(value):
    return str(value or "").strip().lstrip("\ufeff").strip().casefold()


def _url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def parse(rows, strict=False):
    """Return (header-aware, records), retaining legacy first-value discovery."""
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        if strict:
            raise ValueError("Empty Assets response")
        return False, []
    if norm(rows[0][0]).startswith(("<!doctype", "<html", "<head", "<?xml")):
        raise ValueError("Assets response is not CSV")
    header = [norm(cell) for cell in rows[0]]
    has_header = (
        header[0] in NAME_HEADERS
        and any(
            cell in URL_HEADERS | INTERNAL_HEADERS | {"alias"} for cell in header[1:]
        )
        and not any(_url(cell) for cell in rows[0][1:])
    )

    def column(names):
        return (
            next((i for i, value in enumerate(header) if value in names), None)
            if has_header
            else None
        )

    ai, ii, ui = column({"alias"}), column(INTERNAL_HEADERS), column(URL_HEADERS)
    records = []
    for row in rows[1:] if has_header else rows:

        def cell(index, row=row):
            return (
                str(row[index] or "").strip()
                if index is not None and index < len(row)
                else ""
            )

        label = cell(0)
        if not label:
            continue
        value = cell(ui) if ui is not None else ""
        if ui is None:
            for i in range(1, len(row)):
                if i in (ai, ii):
                    continue
                candidate = cell(i)
                if candidate.upper() in ("TRUE", "FALSE"):
                    break
                if candidate:
                    value = candidate
                    break
        records.append({
            "name": label,
            "url": value,
            "alias": cell(ai),
            "internal": norm(cell(ii)) in TRUTHY,
        })
    if (
        strict
        and not has_header
        and not any(_url(r["url"]) or norm(r["name"]) in MEDIA for r in records)
    ):
        raise ValueError("Unrecognized Assets CSV; metadata retained")
    return has_header, records


def read(directory):
    try:
        with open(Path(directory) / "manifest.json", encoding="utf-8") as source:
            data = json.loads(source.read(1024 * 1024 + 1))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write(directory, fields):
    """Merge owned fields and atomically replace one manifest with unique temp cleanup."""
    directory = Path(directory)
    with _LOCK:
        data = read(directory)
        data.update(fields)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".manifest-",
                suffix=".tmp",
                dir=directory,
                delete=False,
            ) as output:
                temporary = output.name
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / "manifest.json")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def refresh(directory, rows, kind):
    if kind not in ("graphics", "media"):
        raise ValueError("Unknown asset kind")
    _, records = parse(rows, strict=True)
    owned = [r for r in records if (norm(r["name"]) in MEDIA) == (kind == "media")]
    with _LOCK:
        previous = read(directory).get("aliases", {})
        aliases = (
            {
                k: v
                for k, v in previous.items()
                if (norm(k) in MEDIA) != (kind == "media")
            }
            if isinstance(previous, dict)
            else {}
        )
        aliases.update({r["name"]: r["alias"] for r in owned if r["alias"]})
        fields = {"aliases": aliases}
        previous_internal = read(directory).get("internal", [])
        internal = (
            [
                v
                for v in previous_internal
                if isinstance(v, str) and (norm(v) in MEDIA) != (kind == "media")
            ]
            if isinstance(previous_internal, list)
            else []
        )
        fields["internal"] = sorted(
            set(internal) | {r["name"] for r in owned if r["internal"]}
        )
        write(directory, fields)


def display_name(directory, canonical, fallback=None, public=False):
    fallback = canonical if fallback is None else fallback
    data = read(directory)
    internal = data.get("internal")
    if (
        public
        and isinstance(internal, list)
        and norm(canonical) in {norm(x) for x in internal}
    ):
        return fallback
    aliases = data.get("aliases")
    if isinstance(aliases, dict):
        alias = next(
            (v for k, v in aliases.items() if norm(k) == norm(canonical)), None
        )
        if isinstance(alias, str) and alias.strip():
            return alias.strip()
    return fallback


def file_identity(filename, kind):
    return (
        MEDIA_NAMES.get(filename.lower(), Path(filename).stem)
        if kind == "media"
        else Path(filename).stem
    )
