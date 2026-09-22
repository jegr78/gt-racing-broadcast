"""Pure logic for the crew-chat history file (runtime/<profile>/chat.json).

No network, no argv parsing: message sanitization, the pull/import validation
gate, and an atomic file write/load. Imported by the relay (ChatStore) and the
`racecast chat` CLI so both agree on the on-disk shape and the size/length caps.
"""
import json
import os
import tempfile

MAX_MESSAGES = 400      # ring-buffer cap (oldest dropped)
MAX_TEXT = 500          # per-message character cap
MAX_NAME = 40           # display-name character cap
DEFAULT_NAME = "Crew"   # fallback when no/blank name is supplied


def _clean_text(value):
    """Strip control characters and fold every line separator to a space.
    Returns a str; the caller enforces the non-empty and length caps. Chat
    messages render in one row, so ASCII CR/LF/TAB and the Unicode line breaks
    NEL/LS/PS (U+0085/U+2028/U+2029) all become spaces."""
    if not isinstance(value, str):
        return ""
    line_breaks = ("\t", "\n", "\r", "\x85", "\u2028", "\u2029")
    out = []
    for ch in value:
        if ch in line_breaks:
            out.append(" ")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            continue        # drop other control chars
        else:
            out.append(ch)
    return "".join(out)


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sanitize_message(raw):
    """Coerce one raw message dict into {ts, user, text} or None if unusable.
    ts must be numeric; text must be non-empty after cleaning; user falls back
    to DEFAULT_NAME. Lengths are capped. Used on send AND on every import/pull."""
    if not isinstance(raw, dict) or not _is_num(raw.get("ts")):
        return None
    text = _clean_text(raw.get("text")).strip()
    if not text:
        return None
    user = _clean_text(raw.get("user")).strip() or DEFAULT_NAME
    return {"ts": float(raw["ts"]), "user": user[:MAX_NAME], "text": text[:MAX_TEXT]}


def validate_payload(payload):
    """Validate a /chat/data-shaped object for pull/import. Returns the cleaned,
    ts-sorted, capped message list. A well-formed but empty list is valid. Raises
    ValueError ONLY on a malformed shape (not a dict, or 'messages' not a list).
    Individual bad entries are dropped, not fatal."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("expected an object with a 'messages' list")
    clean = [m for m in (sanitize_message(x) for x in payload["messages"]) if m]
    clean.sort(key=lambda m: m["ts"])
    return clean[-MAX_MESSAGES:]


def write_messages(path, messages):
    """Atomically write {"messages": [...]} to path: temp file in the same dir,
    then os.replace. A crash mid-write never corrupts an existing chat.json."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"messages": list(messages)}, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # cleanup is best-effort; re-raise the original write failure below
        raise


def load_messages(path):
    """Read chat.json -> sanitized, capped message list. Missing/corrupt -> []."""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    try:
        return validate_payload(payload)
    except ValueError:
        return []


def apply_pulled(path, payload):
    """Validate payload (raises ValueError on a malformed shape) and ONLY THEN
    overwrite path. On failure the local file is left untouched. Returns the
    number of messages written. Used by `racecast chat pull` and `chat import`."""
    messages = validate_payload(payload)     # raises before any write
    write_messages(path, messages)
    return len(messages)
