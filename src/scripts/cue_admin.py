"""Pure logic for the director text-cue channel (runtime/<profile>/cues.json).

No network, no argv parsing: cue sanitization, the active-set filter, prune,
the takeover validation gate, and an atomic file write/load. Imported by the
relay (CueStore) and the `racecast event takeover` cue pull so both agree on the
on-disk shape and the caps. Mirrors chat_admin.py.
"""
import json
import os
import tempfile

MAX_CUES = 100          # ring-buffer cap (oldest dropped)
MAX_CUE_TEXT = 200      # per-cue character cap
MAX_NAME = 40           # sender-label / target character cap
INFO_CUE_TTL_S = 30     # info-cue auto-expiry window (seconds)
LEVELS = ("info", "critical")
DEFAULT_FROM = "Director"

# Race Control -> commentator notes (#376). They ride the same store but carry
# origin="race_control": they never render as a director toast/banner, never
# expire by the info TTL (reference context the commentator looks back at), and
# are kept as a rolling window instead. A director cue has NO origin key, so the
# on-disk shape is unchanged. ORIGIN_DIRECTOR is the implicit default (omitted).
ORIGIN_DIRECTOR = "director"
ORIGIN_RACE_CONTROL = "race_control"
# Commentator -> director cue-back (#377): the reverse direction. Same store, but
# shown ONLY on the Director Panel (never in any cockpit), 'from' is the sender's
# commentator name (identity-forced server-side, like chat).
ORIGIN_COMMENTATOR = "commentator"
ORIGINS = (ORIGIN_DIRECTOR, ORIGIN_RACE_CONTROL, ORIGIN_COMMENTATOR)
# Origins that carry the key (everything except the implicit director default).
NOTE_ORIGINS = (ORIGIN_RACE_CONTROL, ORIGIN_COMMENTATOR)
RACE_CONTROL_FROM = "Race Control"      # sender label stamped on an RC note
CUE_BACK_TARGET = "director"            # sentinel target for a cue-back (all directors)
RC_NOTE_KEEP = 30       # per-origin note retention on disk (rolling window) by prune
RC_NOTE_SHOW = 5        # RC notes shown per commentator in the cockpit card
CUE_BACK_SHOW = 20      # cue-backs shown on the Director Panel


def _clean_text(value):
    """Strip control characters, fold every line/paragraph separator to one
    space (cues render single-line). ASCII CR/LF/TAB plus Unicode NEL/LS/PS."""
    if not isinstance(value, str):
        return ""
    line_breaks = ("\t", "\n", "\r", "\x85", "\u2028", "\u2029")
    out = []
    for ch in value:
        if ch in line_breaks:
            out.append(" ")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            continue
        else:
            out.append(ch)
    return "".join(out)


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sanitize_cue(raw):
    """Coerce one raw cue dict into {id, ts, target, level, text, from, ack} or
    None if unusable. Used on add, on load, and on every takeover pull."""
    if not isinstance(raw, dict) or not _is_num(raw.get("ts")):
        return None
    if isinstance(raw.get("id"), bool) or not isinstance(raw.get("id"), int) or raw["id"] < 1:
        return None
    if raw.get("level") not in LEVELS:
        return None
    target = _clean_text(raw.get("target")).strip()
    if not target:
        return None
    text = _clean_text(raw.get("text")).strip()
    if not text:
        return None
    frm = _clean_text(raw.get("from")).strip() or DEFAULT_FROM
    ack = raw.get("ack")
    if not (isinstance(ack, dict) and _is_num(ack.get("ts"))):
        ack = None
    else:
        ack = {"ts": float(ack["ts"])}
    out = {"id": int(raw["id"]), "ts": float(raw["ts"]),
           "target": target[:MAX_NAME], "level": raw["level"],
           "text": text[:MAX_CUE_TEXT], "from": frm[:MAX_NAME], "ack": ack}
    # A note-style cue (RC note / cue-back) carries its origin; a director cue
    # omits the key so the legacy 7-key shape (and every existing cues.json) is
    # unchanged.
    if raw.get("origin") in NOTE_ORIGINS:
        out["origin"] = raw["origin"]
    return out


def resolve_target(raw_target, on_air_key, normalize):
    """Map a panel target selection to a concrete cue target string, or None.
    'all' -> 'all'; 'on-air' -> the on-air streamer_key (or None when nobody is
    on air); anything else -> normalize(name) (the relay passes asset_key)."""
    t = (raw_target or "").strip()
    if t == "all":
        return "all"
    if t in ("on-air", "on_air", "onair"):
        return on_air_key or None
    return normalize(t) or None


def active_cues_for(cues, streamer_key, now, info_ttl=INFO_CUE_TTL_S):
    """The cues a given commentator should currently see: target is their key or
    'all', and the cue is still active: info while now < ts+ttl, critical while
    unacked."""
    out = []
    for c in cues:
        if c.get("origin"):
            continue                     # only plain director cues render here
        if c.get("target") not in (streamer_key, "all"):
            continue
        if c.get("level") == "critical":
            if c.get("ack") is None:
                out.append(c)
        elif now < c.get("ts", 0) + info_ttl:
            out.append(c)
    return out


def race_control_notes_for(cues, streamer_key, limit=RC_NOTE_SHOW):
    """The Race Control notes a given commentator should see: origin
    'race_control', target their key or 'all'. Reference context (no TTL): the
    most-recent *limit*, in id order. Backs the cockpit's rolling RC card."""
    out = [c for c in cues
           if c.get("origin") == ORIGIN_RACE_CONTROL
           and c.get("target") in (streamer_key, "all")]
    return out[-limit:]


def cue_backs(cues, limit=CUE_BACK_SHOW):
    """The commentator->director cue-backs for the Director Panel: origin
    'commentator', most-recent *limit*, in id order. Each carries 'from' (the
    sender's name) and 'ts'. Reference context (no TTL)."""
    out = [c for c in cues if c.get("origin") == ORIGIN_COMMENTATOR]
    return out[-limit:]


def prune(cues, now, info_ttl=INFO_CUE_TTL_S):
    """Drop expired info + acked critical cues; bound to MAX_CUES. Applied on
    load and on a takeover pull (a restart/handover carries no stale cues).
    Note-style cues (RC notes, cue-backs) are reference context, so they survive
    the TTL, each origin kept as its OWN rolling window of the most-recent
    RC_NOTE_KEEP, in original order (a flood of one origin never evicts another)."""
    per_origin = {}
    for c in cues:
        o = c.get("origin")
        if o:
            per_origin.setdefault(o, []).append(c["id"])
    keep_ids = set()
    for ids in per_origin.values():
        keep_ids.update(ids[-RC_NOTE_KEEP:])
    keep = []
    for c in cues:
        if c.get("origin"):
            if c["id"] in keep_ids:
                keep.append(c)
        elif c.get("level") == "critical":
            if c.get("ack") is None:
                keep.append(c)
        elif now < c.get("ts", 0) + info_ttl:
            keep.append(c)
    return keep[-MAX_CUES:]


def validate_payload(payload):
    """Validate a {'cues': [...]} object for a takeover pull. Returns the cleaned,
    id-sorted, capped list. Empty is valid; raises ValueError ONLY on a malformed
    shape (not a dict, or 'cues' not a list). Bad entries are dropped, not fatal."""
    if not isinstance(payload, dict) or not isinstance(payload.get("cues"), list):
        raise ValueError("expected an object with a 'cues' list")
    clean = [c for c in (sanitize_cue(x) for x in payload["cues"]) if c]
    clean.sort(key=lambda c: c["id"])
    return clean[-MAX_CUES:]


def write_cues(path, cues):
    """Atomically write {'cues': [...]} to path (temp file + os.replace)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"cues": list(cues)}, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # cleanup is best-effort; re-raise the original write failure below
        raise


def load_cues(path):
    """Read cues.json -> sanitized, capped list. Missing/corrupt -> []."""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []
    try:
        return validate_payload(payload)
    except ValueError:
        return []


def apply_pulled(path, payload, now, info_ttl=INFO_CUE_TTL_S):
    """Validate (raises on malformed shape), prune to active-only, THEN overwrite
    path. On failure the local file is untouched. Returns the count written."""
    cues = prune(validate_payload(payload), now, info_ttl)
    write_cues(path, cues)
    return len(cues)
