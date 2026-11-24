"""League service widgets (#1255): the definitions a profile keeps for the scene
builders, their markup on the live overlay pages, and the relay's live view with the
Director's on-air switch.

Spec: src/docs/design/league-service.md ("Widgets", "Control"). The definitions live in
profiles/<name>/league-widgets.json, outside overlay/, written when the producer loads an
event. A widget the service stops listing stays in the file marked unlisted, so its
placement and style survive and it stays hidden on air. Every string reaches a page as
text: the markup carries no service text but the escaped builder label. Stdlib only.
"""
import html
import json
import os
import re
import tempfile
import threading

import atomic_replace
import league_service

FILENAME = "league-widgets.json"
SLOT_PREFIX = "league-widget-"
MAX_KEPT = 32           # listed widgets plus the ones a service stopped listing
PAGES = ("hud", "intermission", "splitscreen")
SCRIPT = "/league-widgets.js"
DATA = "/league-widgets/data"

_lock = threading.Lock()


def path_in(profile_dir):
    return os.path.join(profile_dir, FILENAME)


def slot_id(wid):
    return SLOT_PREFIX + wid


def _clean(doc):
    """One stored definition checked again, or None."""
    if not isinstance(doc, dict):
        return None
    w = league_service._widget(doc)
    if w is None:
        return None
    w["listed"] = doc.get("listed") is not False
    return w


def read(path):
    """The stored definitions [{id, title, form, sample?, listed}], [] without a usable file."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return []
    out, ids = [], set()
    for item in doc.get("widgets") if isinstance(doc, dict) and isinstance(doc.get("widgets"), list) else ():
        w = _clean(item)
        if w is not None and w["id"] not in ids:
            ids.add(w["id"])
            out.append(w)
    return out[:MAX_KEPT]


def remember(path, widgets):
    """Store the event's widget list: the listed ones in the service's order, then the
    ones kept from before, marked unlisted. Returns what was written."""
    with _lock:
        listed = [dict(w, listed=True) for w in widgets]
        ids = {w["id"] for w in listed}
        kept = [dict(w, listed=False) for w in read(path) if w["id"] not in ids]
        doc = {"version": 1, "widgets": (listed + kept)[:MAX_KEPT]}
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".league-widgets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            atomic_replace.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return doc["widgets"]


WIDGET_PROPS = ("left", "top", "width", "height", "padding", "fontSize", "fontFamily", "fontWeight",
                "fontStyle", "color", "background", "borderWidth", "borderStyle", "borderColor",
                "borderRadius", "opacity", "rotation", "visible")
WIDGET_CSS = """
.league-widget{position:absolute;box-sizing:border-box;left:1400px;top:70px;width:420px;
height:130px;padding:12px 16px;z-index:10;font-size:24px;line-height:1.2;font-family:inherit;
color:#fff;background:rgba(0,0,0,.8);border:2px solid var(--lw-accent,#27d34d);border-radius:12px;
overflow:hidden}
.league-widget.lw-form-table{height:330px}
.league-widget[hidden],.league-widget [hidden]{display:none!important}
.lw-title{display:block;font-size:.7em;opacity:.85;text-transform:uppercase;letter-spacing:.04em}
.lw-provisional{font-size:.5em;margin-left:.5em;opacity:.7}
.lw-values{display:flex;flex-wrap:wrap;gap:.2em 1em;margin-top:.2em}
.lw-item{display:flex;flex-direction:column;flex:1 1 0;min-width:0}
.lw-label{font-size:.6em;overflow-wrap:anywhere}
.lw-value{font-size:1.4em;font-weight:bold;font-variant-numeric:tabular-nums}
.lw-table{display:grid;grid-template-columns:auto 1fr auto;gap:.1em .6em;margin-top:.2em;font-size:.8em}
.lw-head{grid-column:3;font-size:.7em;opacity:.8;text-align:right}
.lw-rank,.lw-row-value{font-variant-numeric:tabular-nums;text-align:right}
.lw-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
"""


def markup(widget):
    """The hidden slot element of one definition; the title is the escaped builder label."""
    wid = widget["id"]
    if not league_service.WIDGET_ID_RE.fullmatch(wid) or widget.get("form") not in league_service.FORMS:
        raise ValueError("invalid widget")
    label = html.escape("League: " + widget["title"], quote=True)
    return (f'<div id="{slot_id(wid)}" class="league-widget lw-form-{widget["form"]}" data-league-widget="{wid}" '
            f'data-edit="{label}" data-edit-props="{",".join(WIDGET_PROPS)}" hidden></div>')


def enhance_html(base, definitions, placements, live=False):
    """`base` with the slot of every definition whose slot id is in `placements`."""
    selected = [w for w in definitions if slot_id(w["id"]) in placements]
    if not selected:
        return base
    base = base.replace("</style>", WIDGET_CSS + "</style>", 1)
    elements = "".join(markup(w) for w in selected)
    script = f'<script src="{SCRIPT}" data-league-live></script>' if live else ""
    marker = "<script" if "<script" in base else "</body>"
    return base.replace(marker, elements + script + marker, 1)


def live_html(base, overlay_dir, page):
    """`base` with the widgets placed in `page`'s saved layout; the base page on any error."""
    if not overlay_dir or page not in PAGES:
        return base
    try:
        definitions = read(path_in(os.path.dirname(os.path.abspath(overlay_dir))))
        path = os.path.join(overlay_dir, f"layout-{page}.json")
        if not definitions or not os.path.isfile(path):
            return base
        with open(path, encoding="utf-8") as fh:
            slots = json.load(fh).get("slots", {})
        return enhance_html(base, definitions, slots if isinstance(slots, dict) else {}, live=True)
    except (OSError, ValueError, TypeError, AttributeError):
        return base  # a broken widget file must never take the overlay down


def placements(overlay_dir):
    """{widget id: [pages it is placed in]} from the saved layouts."""
    out = {}
    for page in PAGES:
        try:
            with open(os.path.join(overlay_dir, f"layout-{page}.json"), encoding="utf-8") as fh:
                slots = json.load(fh).get("slots", {})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        for sid in slots if isinstance(slots, dict) else ():
            if isinstance(sid, str) and sid.startswith(SLOT_PREFIX):
                out.setdefault(sid[len(SLOT_PREFIX):], []).append(page)
    return out


ACTIONS = ("show", "hide", "toggle")
PLACE_RE = re.compile(r"[1-9]")      # ASCII only: str.isdigit() also takes "²"


def order_key(snap):
    """The running order of a dashboard snapshot: its mode and the entries in order."""
    snap = snap or {}
    mode = snap.get("mode")
    rows = snap.get("qualifying") if mode == "qualifying" else snap.get("entries")
    return mode, tuple(r.get("entry") for r in rows or () if isinstance(r, dict))


def _stamp(path):
    try:
        return os.stat(path).st_mtime_ns if path else None
    except OSError:
        return None


class Live:
    """The relay's live sender and widget view. It owns the league_service.Client that
    posts the live state cut from `snapshot()` (the undelayed dashboard snapshot) for
    the loaded event, and the Director's on-air switch, which starts hidden and lives
    as long as the relay. `settings()` gives (Settings or None, problem)."""

    def __init__(self, settings, snapshot, event_path, overlay_dir=None, env_path=None, **client_kw):
        self._settings = settings
        self._snapshot = snapshot
        self._event_path = event_path
        self._overlay_dir = overlay_dir
        self._env_path = env_path
        self._shown = set()
        self._lock = threading.Lock()
        self._order = None
        self._env_stamp = _stamp(env_path)
        self.client = league_service.Client(lambda: self._settings()[0], self.body, self.forms, **client_kw)

    def body(self):
        """The LiveState to send, or None without a loaded event or a snapshot."""
        ev = league_service.read(self._event_path)
        snap = self._snapshot()
        if ev is None or snap is None:
            return None
        return league_service.live_state(ev["ref"], ev["current"], snap)

    def observe(self, snap):
        """The dashboard tick's hand-off: send at once when the running order changed,
        re-read the settings when profile.env changed. It never waits for the service."""
        stamp = _stamp(self._env_path)
        if stamp != self._env_stamp:
            self._env_stamp = stamp
            self.client.reload()
        key = order_key(snap)
        if self._order is not None and key != self._order:
            self.client.wake()
        self._order = key

    def run(self, stop):
        self.client.run(stop)

    def _event_widgets(self):
        ev = league_service.read(self._event_path)
        return ev["widgets"] if ev else []

    def forms(self):
        """{widget id: form} of the loaded event, for the live sender."""
        return {w["id"]: w["form"] for w in self._event_widgets()}

    def _kept(self):
        if not self._overlay_dir:
            return []
        return read(path_in(os.path.dirname(os.path.abspath(self._overlay_dir))))

    def set(self, target, action):
        """Show, hide or toggle a widget named by id or by its 1-based place in the
        event's list. (ok, id or error text)."""
        if action not in ACTIONS:
            return False, "action must be show, hide or toggle"
        ids = [w["id"] for w in self._event_widgets()]
        if isinstance(target, str) and PLACE_RE.fullmatch(target):
            n = int(target)
            if not 1 <= n <= len(ids):
                return False, f"the event lists {len(ids)} widget(s)"
            wid = ids[n - 1]
        elif isinstance(target, str) and league_service.WIDGET_ID_RE.fullmatch(target) and target in ids:
            wid = target
        else:
            return False, "the loaded event lists no such widget"
        with self._lock:
            on = wid not in self._shown if action == "toggle" else action == "show"
            (self._shown.add if on else self._shown.discard)(wid)
        return True, wid

    def data(self):
        """What the overlay pages render: each widget the Director shows that has a
        value inside the hold, with the seconds it may still be shown."""
        held = self.client.held()
        with self._lock:
            shown = set(self._shown)
        out = []
        for w in self._event_widgets():
            value = held.get(w["id"])
            if w["id"] in shown and value is not None:
                out.append({"id": w["id"], "form": w["form"], "title": w["title"],
                            "value": value[0], "holdFor": round(value[1], 1)})
        return {"ok": True, "widgets": out}

    def director(self):
        """The Director Panel's list: every widget of the event and every placed one the
        service stopped listing, each with its on-air switch and, when it cannot show,
        the reason."""
        st = dict(self.client.status(), problem=self._settings()[1])
        held = self.client.held()
        placed = placements(self._overlay_dir) if self._overlay_dir else {}
        with self._lock:
            shown = set(self._shown)
        listed = self._event_widgets()
        rows = []
        for i, w in enumerate(listed, 1):
            rows.append({"id": w["id"], "index": i, "title": w["title"], "form": w["form"], "listed": True,
                         "shown": w["id"] in shown, "live": w["id"] in held,
                         "placed": placed.get(w["id"], []), "reason": self._reason(w["id"], st, held, placed)})
        ids = {w["id"] for w in listed}
        for w in self._kept():
            if w["id"] not in ids and w["id"] in placed:
                rows.append({"id": w["id"], "index": None, "title": w["title"], "form": w["form"],
                             "listed": False, "shown": False, "live": False, "placed": placed[w["id"]],
                             "reason": "the league service no longer lists this widget"})
        return {"configured": st["configured"], "problem": st["problem"], "last_ok_age_s": st["last_ok_age_s"],
                "last_error": st["last_error"], "hold_s": league_service.HOLD_S, "widgets": rows}

    @staticmethod
    def _reason(wid, st, held, placed):
        if not st["configured"]:
            return st["problem"] or "no league service configured"
        if wid not in held:
            age = st["last_ok_age_s"]
            why = f": {st['last_error']}" if st["last_error"] else ""
            if age is None:
                return "no answer from the league service yet" + why
            if age > league_service.HOLD_S:
                return f"no good answer from the league service for {int(age)} s" + why
            return "the league service sent no value for this widget"
        if wid not in placed:
            return "not placed in any scene: place it in the Overlay builder"
        return ""
