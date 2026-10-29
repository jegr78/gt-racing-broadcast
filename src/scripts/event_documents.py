"""Public document links from the optional Documents Sheet tab. No document I/O."""
import csv
import io
from urllib.parse import urlsplit


def public_link(value):
    """An absolute browser HTTP(S) link, kept verbatim after trimming."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or any(c.isspace() for c in parsed.netloc)
                or parsed.username is not None or parsed.password is not None):
            return ""
        _ = parsed.port  # Reject malformed/out-of-range ports too.
    except ValueError:
        return ""
    return value


def parse_event_documents(text):
    """Header-located Title/URL columns; Name/Label and Link are accepted aliases."""
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return []
    header = [v.strip().lstrip("\ufeff").strip().casefold() for v in rows[0]]
    title_i = next((i for i, h in enumerate(header) if h in ("title", "name", "label")), None)
    url_i = next((i for i, h in enumerate(header) if h in ("url", "link")), None)
    if title_i is None or url_i is None:
        return []
    documents = []
    for row in rows[1:]:
        if len(row) <= max(title_i, url_i):
            continue
        title, url = row[title_i].strip(), public_link(row[url_i])
        if title and url:
            documents.append({"title": title, "url": url})
    return documents
