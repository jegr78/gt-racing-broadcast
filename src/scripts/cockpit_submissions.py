"""Commentator stream-link submission store (#193): pure validation, atomic JSON
writes and best-effort reads that never throw. A commentator submits a
YouTube/Twitch link from the Funnel-exposed cockpit; it lands here as a PENDING
entry that the director approves or rejects from the tailnet-only /panel. It is
never auto-published.

State file: runtime/<profile>/cockpit-pending.json
    {"seq": <int>, "pending": [entry, ...]}
where seq is a monotonically increasing id counter (never reused, even after a
pop, so an Approve/Reject can never act on the wrong entry after the list shifts).

Each entry:
    {"id", "streamer_key", "streamer_name", "target_line", "target_stint",
     "proposed_url", "prev_url", "ts", "mode"}

An append-only audit log (cockpit-submissions.log, one JSON object per line)
records every submit, approve and reject with who, what and when.
"""
import json
import os
import re
import tempfile
import threading

MAX_PENDING = 50                      # bound the file; oldest dropped beyond this
_KEY_RE = re.compile(r"[a-z0-9-]+")
_AUDIT_LOCK = threading.Lock()        # serialize audit appends (best-effort)


def _validate_entry(e):
    if not isinstance(e, dict):
        raise ValueError("entry must be an object")
    if isinstance(e.get("id"), bool) or not isinstance(e.get("id"), int) or e["id"] < 1:
        raise ValueError(f"bad entry id: {e.get('id')!r}")
    key = e.get("streamer_key")
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ValueError(f"bad streamer key: {key!r}")
    line = e.get("target_line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError(f"bad target_line: {line!r}")
    for field in ("streamer_name", "target_stint", "proposed_url", "prev_url"):
        if not isinstance(e.get(field), str):
            raise ValueError(f"bad {field}: {e.get(field)!r}")
    if isinstance(e.get("ts"), bool) or not isinstance(e.get("ts"), (int, float)):
        raise ValueError(f"bad ts: {e.get('ts')!r}")
    mode = e.get("mode", "race")
    if mode not in ("race", "qualifying"):
        raise ValueError(f"bad mode: {mode!r}")
    return {"id": e["id"], "streamer_key": key, "streamer_name": e["streamer_name"],
            "target_line": line, "target_stint": e["target_stint"],
            "proposed_url": e["proposed_url"], "prev_url": e["prev_url"],
            "ts": e["ts"], "mode": mode}


def validate_pending(payload):
    """{"seq": int>=0, "pending": [entry,...]} -> (seq, [entry,...]). Raises
    ValueError on any malformed shape."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    pending = payload.get("pending")
    if not isinstance(pending, list):
        raise ValueError("missing 'pending' list")
    entries = [_validate_entry(e) for e in pending]
    seq = payload.get("seq", 0)
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError(f"bad seq: {seq!r}")
    # seq must be at least the highest id seen, or a pop could let an id repeat.
    seq = max([seq] + [e["id"] for e in entries])
    return seq, entries


def _load(path):
    """(seq, entries) from disk, or (0, []) when missing or corrupt. Best-effort:
    a bad file must never wedge the relay."""
    try:
        with open(path, encoding="utf-8") as fh:
            return validate_pending(json.load(fh))
    except (OSError, ValueError):
        return 0, []


def _write(path, seq, entries):
    """Atomically persist {"seq", "pending"}: same-filesystem temp file plus
    replace, with the temp unlinked on failure."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"seq": seq, "pending": entries}, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # cleanup is best-effort; re-raise the original write failure
        raise


def list_pending(path):
    """Stored pending entries (oldest-first), or [] when missing/corrupt."""
    return _load(path)[1]


def add_pending(path, *, streamer_key, streamer_name, target_line, target_stint,
                proposed_url, prev_url, now, mode="race", max_pending=MAX_PENDING):
    """Append a new pending entry with a fresh monotonic id, cap the list to
    *max_pending* (dropping the oldest), persist, and return the stored entry.
    `mode` ('race'/'qualifying') records which schedule tab the entry targets so
    approve writes back to the correct tab."""
    seq, entries = _load(path)
    seq += 1
    entry = {"id": seq, "streamer_key": streamer_key, "streamer_name": streamer_name,
             "target_line": int(target_line), "target_stint": target_stint,
             "proposed_url": proposed_url, "prev_url": prev_url, "ts": now, "mode": mode}
    entries.append(_validate_entry(entry))
    if len(entries) > max_pending:
        entries = entries[-max_pending:]
    _write(path, seq, entries)
    return entry


def pop_pending(path, entry_id):
    """Remove the entry whose id == *entry_id*, persist, and return it. Returns
    None and leaves the list unchanged when no such id exists. seq is NEVER
    decreased."""
    seq, entries = _load(path)
    keep, popped = [], None
    for e in entries:
        if popped is None and e["id"] == entry_id:
            popped = e
        else:
            keep.append(e)
    if popped is not None:
        _write(path, seq, keep)
    return popped


def append_audit(path, record):
    """Append one JSON line to the audit log. Best-effort: a failed write must
    never break a submit, approve or reject."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _AUDIT_LOCK, open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # audit logging is best-effort; never break a submit/approve/reject
