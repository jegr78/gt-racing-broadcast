"""Intermission-only visibility, temporary chat overrides and starter geometry.

Video truth stays in OBS. Chat's override lives outside the portable design and
never disables the browser item that also renders video frames.
"""
import json
import os
import tempfile
import threading
import time
import copy
from urllib.parse import urlsplit

SCENE = "Intermission"
SOURCES = {"chat": "Intermission Chat", "webcam": "Solo Webcam", "capture": "Solo Capture"}
# How long a Solo overlay poll reuses its OBS item snapshot. A manual OBS change
# reaches the page within this; a Director/Companion action invalidates it at once.
OVERLAY_STATE_TTL_S = 2.5


def allow_control_origin(headers):
    """Refuse foreign browser requests while retaining headerless Companion GETs."""
    site = headers.get("Sec-Fetch-Site", "").lower()
    if site and site not in ("same-origin", "none"):
        return False
    origins = [headers.get(k) for k in ("Origin", "Referer") if headers.get(k)]
    if not origins:
        return True

    def origin(value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid control origin")
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        scheme = "https" if headers.get("X-Forwarded-Proto") == "https" else "http"
        expected = origin(scheme + "://" + headers.get("Host", ""))
        return all(origin(value) == expected for value in origins)
    except ValueError:
        return False


def read_overrides(path):
    """Missing means design defaults. Unreadable state never reveals video/chat."""
    if not path:
        return None, {}, ""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        chat, video = data["chat"], data.get("video", {})
        if (chat is not None and type(chat) is not bool or not isinstance(video, dict)
                or any(k not in ("webcam", "capture") or type(v) is not bool for k, v in video.items())):
            raise ValueError("invalid visibility override")
        return chat, video, ""
    except FileNotFoundError:
        return None, {}, ""
    except (OSError, ValueError, KeyError, TypeError):
        return False, {"webcam": False, "capture": False}, "Saved visibility override unreadable; restore defaults."


def companion_config(config, solo):
    """Omit unsupported Solo buttons from the active profile's static export."""
    result = copy.deepcopy(config)
    if not solo:
        for page in result.get("pages", {}).values():
            for row in page.get("controls", {}).values():
                for column, button in list(row.items()):
                    urls = [a.get("options", {}).get("url", {}).get("value", "")
                            for step in button.get("steps", {}).values()
                            for actions in step.get("action_sets", {}).values() for a in actions]
                    if any(urlsplit(u).path in ("/obs/intermission/toggle/webcam",
                                                "/obs/intermission/toggle/capture") for u in urls):
                        del row[column]
    return result


def starter_layouts(solo):
    """Slot patches only. Assets, fonts, unrelated slots and goal values stay put."""
    chat = {"left": 1376, "top": 96, "width": 480, "height": 888, "visible": True}
    hidden = {"intermission-webcam": {"visible": False},
              "intermission-capture": {"visible": False}} if solo else {}
    result = [{"id": "background-chat", "label": "Background + chat",
               "slots": dict(hidden, ichat=chat)},
              {"id": "break-goals", "label": "Break + goals",
               "slots": dict(hidden, ichat={"visible": False})}]
    if solo:
        result[1:1] = [
            {"id": "webcam-chat", "label": "Webcam + chat", "slots": {
                "ichat": chat, "intermission-capture": {"visible": False},
                "intermission-webcam": {"left": 64, "top": 264, "width": 1248,
                                        "height": 702, "visible": True}}},
            {"id": "capture-webcam-chat", "label": "Capture + webcam + chat", "slots": {
                "intermission-capture": {"left": 64, "top": 96, "width": 1248,
                                         "height": 702, "visible": True},
                "intermission-webcam": {"left": 1376, "top": 96, "width": 480,
                                        "height": 270, "visible": True},
                "ichat": dict(chat, top=400, height=584)}}]
    return result


class IntermissionControl:
    """Serialize read/modify/write actions; never call a scene or audio command."""
    def __init__(self, obs_getter, solo, defaults_getter, runtime_path=None, clock=None):
        self.obs_getter = obs_getter
        self.solo = bool(solo)
        self.defaults_getter = defaults_getter
        self.path = runtime_path
        self.clock = clock or time.monotonic
        self._overlay_cache = None      # (taken_at, video) for overlay_state()
        self.lock = threading.RLock()
        self.chat, self.video, self.load_note = read_overrides(self.path)

    def _save(self, chat, video):
        if self.path:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"version": 1, "chat": chat, "video": video}, fh)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.chat, self.video = chat, video
        self.load_note = ""

    def _snapshot(self):
        keys = list(SOURCES) if self.solo else ["chat"]
        defaults = self.defaults_getter()
        payload = {"ok": True, "scene": SCENE, "solo": self.solo,
                   "items": dict.fromkeys(keys),
                   "defaults": {k: bool(defaults.get(k, False)) for k in keys},
                   "chatOverride": self.chat, "notes": {}}
        obs = self.obs_getter()
        items, note = obs.get_scene_items(SCENE) if obs else ({}, "OBS unavailable")
        if note:
            payload.update(ok=False, error=note)
            return payload, 503, items
        for key in keys:
            source = SOURCES[key]
            if source not in items:
                payload["notes"][key] = "Scene item missing; update the OBS collection."
                continue
            enabled = items[source]
            if key == "chat":
                enabled = enabled and (self.chat if self.chat is not None else payload["defaults"][key])
            payload["items"][key] = enabled
        if self.load_note:
            payload["notes"]["chat"] = self.load_note
        return payload, 200, items

    def state(self):
        with self.lock:
            payload, status, _ = self._snapshot()
            return payload, status

    def apply(self, verb, key=None):
        if verb not in ("show", "hide", "toggle", "restore") or (
                verb != "restore" and (key not in tuple(SOURCES)
                                       or not self.solo and key != "chat")):
            return {"ok": False, "error": "Unsupported Intermission action or source."}, 400
        with self.lock:
            self._overlay_cache = None   # the next overlay poll sees this action
            before, status, items = self._snapshot()
            if status != 200:
                return before, status
            selected = list(before["items"]) if verb == "restore" else [key]
            if any(before["items"].get(k) is None for k in selected):
                return dict(before, ok=False, error="Scene item missing; update the OBS collection."), 503
            obs = self.obs_getter()
            try:
                if verb == "restore":
                    self._save(None, {})
                    targets = {k: before["defaults"][k] for k in selected if k != "chat"}
                    targets["chat"] = True   # browser also supplies video frames
                else:
                    enabled = not before["items"][key] if verb == "toggle" else verb == "show"
                    if key == "chat":
                        self._save(enabled, self.video)
                        targets = {"chat": True} if enabled and not items[SOURCES["chat"]] else {}
                    else:
                        self._save(self.chat, dict(self.video, **{key: enabled}))
                        targets = {key: enabled}
                for target, enabled in targets.items():
                    ok, note = obs.set_scene_item_enabled(SCENE, SOURCES[target], enabled)
                    if not ok:
                        return dict(self.state()[0], ok=False, error=note or "Could not change scene item."), 503
            except OSError:
                return dict(before, ok=False, error="Could not save the Intermission visibility override."), 503
            return self.state()

    def overlay_state(self):
        """The overlay page's 1/s poll. Endurance has no video controls, so it never
        reads OBS; Solo reuses one OBS snapshot for OVERLAY_STATE_TTL_S instead of
        queueing a GetSceneItemList on the shared control connection every poll."""
        with self.lock:
            if not self.solo:
                return {"chatOverride": self.chat,
                        "video": {"webcam": False, "capture": False}}
            now = self.clock()
            if self._overlay_cache is None or now - self._overlay_cache[0] >= OVERLAY_STATE_TTL_S:
                state, _ = self.state()
                video = {k: state["items"].get(k) is True for k in ("webcam", "capture")}
                self._overlay_cache = (now, video)
            return {"chatOverride": self.chat, "video": dict(self._overlay_cache[1])}
