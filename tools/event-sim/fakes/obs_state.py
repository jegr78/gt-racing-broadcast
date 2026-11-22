"""The state of a simulated OBS: scenes, program and preview, transitions, streaming and
recording, inputs and scene items. `request()` applies one obs-websocket v5 request the
way OBS does and records the events OBS would emit; the server in `obs_sim.py` puts both
on the wire.

Strict mode answers a missing scene, input or request type with OBS's error codes, so a
check can fail. Lenient mode answers exactly like the old screenshot stand-in (fixed
answers, any scene item exists and is visible, every other request succeeds without
effect) for callers that only want a picture.

Stdlib only; time comes from an injectable clock in seconds."""
import base64
import copy
import json
import math
import os
import threading
import time
from urllib.parse import urlsplit

from obs_render import render_program

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_COLLECTION = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
DEMO_PROFILE_ENV = os.path.join(ROOT, "profiles", "demo", "profile.env")

SUCCESS = 100
UNKNOWN_REQUEST = 204
MISSING_FIELD = 300
OUT_OF_RANGE = 402
OUTPUT_RUNNING = 500
OUTPUT_NOT_RUNNING = 501
OUTPUT_PAUSED = 502
OUTPUT_NOT_PAUSED = 503
STUDIO_MODE_OFF = 506
NOT_FOUND = 600
ALREADY_EXISTS = 601

# obs-websocket v5 EventSubscription bits.
GENERAL, CONFIG, SCENES, INPUTS, TRANSITIONS = 1, 2, 4, 8, 16
OUTPUTS, SCENE_ITEMS, MEDIA_INPUTS, UI = 64, 128, 256, 1024
ALL_EVENTS = 2047
SCENE_ITEM_TRANSFORM = 1 << 19      # high-volume: only on explicit subscription
EVENT_CATEGORY = {
    "CurrentSceneCollectionChanging": CONFIG, "CurrentSceneCollectionChanged": CONFIG,
    "SceneCreated": SCENES, "SceneRemoved": SCENES, "SceneListChanged": SCENES,
    "CurrentProgramSceneChanged": SCENES, "CurrentPreviewSceneChanged": SCENES,
    "InputCreated": INPUTS, "InputRemoved": INPUTS, "InputSettingsChanged": INPUTS,
    "InputMuteStateChanged": INPUTS, "InputVolumeChanged": INPUTS,
    "CurrentSceneTransitionChanged": TRANSITIONS,
    "CurrentSceneTransitionDurationChanged": TRANSITIONS,
    "SceneTransitionStarted": TRANSITIONS, "SceneTransitionEnded": TRANSITIONS,
    "StreamStateChanged": OUTPUTS, "RecordStateChanged": OUTPUTS,
    "SceneItemCreated": SCENE_ITEMS, "SceneItemRemoved": SCENE_ITEMS,
    "SceneItemEnableStateChanged": SCENE_ITEMS, "SceneItemTransformChanged": SCENE_ITEM_TRANSFORM,
    "SceneItemListReindexed": SCENE_ITEMS,
    "MediaInputActionTriggered": MEDIA_INPUTS,
    "StudioModeStateChanged": UI,
}
STARTED, STOPPED = "OBS_WEBSOCKET_OUTPUT_STARTED", "OBS_WEBSOCKET_OUTPUT_STOPPED"
STARTING, STOPPING = "OBS_WEBSOCKET_OUTPUT_STARTING", "OBS_WEBSOCKET_OUTPUT_STOPPING"
PAUSED, RESUMED = "OBS_WEBSOCKET_OUTPUT_PAUSED", "OBS_WEBSOCKET_OUTPUT_RESUMED"
TRANSITIONS_BUILTIN = (("Cut", "cut_transition", True), ("Fade", "fade_transition", False))
TRANSITION_KINDS = {name: kind for name, kind, _ in TRANSITIONS_BUILTIN}
LOG_LIMIT = 5000
_LEGACY = {"GetSceneItemId": {"sceneItemId": 1}, "GetSceneItemEnabled": {"sceneItemEnabled": True},
           "GetInputMute": {"inputMuted": False}, "GetInputList": {"inputs": []},
           "GetSceneItemList": {"sceneItems": []},
           "GetStreamStatus": {"outputActive": False, "outputReconnecting": False,
                               "outputDuration": 0, "outputBytes": 0}}
_LENIENT_LIVE = ("GetCurrentProgramScene", "GetSourceScreenshot")
_READ_ONLY_TRANSFORM = ("sourceWidth", "sourceHeight", "width", "height")


class ObsError(Exception):
    def __init__(self, code, comment):
        super().__init__(comment)
        self.code, self.comment = code, comment


def _need(data, *names):
    for name in names:
        if data.get(name) is None:
            raise ObsError(MISSING_FIELD, f"Your request is missing the `{name}` field.")
    return [data[n] for n in names]


def _db(mul):
    return round(20 * math.log10(mul), 2) if mul > 0 else -100.0


def _timecode(ms):
    s, ms = divmod(int(ms), 1000)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


def _transform():
    return {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0,
            "alignment": 5, "boundsType": "OBS_BOUNDS_NONE", "boundsAlignment": 0,
            "boundsWidth": 0.0, "boundsHeight": 0.0, "cropLeft": 0, "cropRight": 0,
            "cropTop": 0, "cropBottom": 0, "sourceWidth": 1920.0, "sourceHeight": 1080.0,
            "width": 1920.0, "height": 1080.0}


def feed_url(settings):
    """The network URL of a media input, or "" for a local file or another kind."""
    if settings.get("is_local_file"):
        return ""
    url = settings.get("input")
    return url.strip() if isinstance(url, str) else ""


def demo_collection_name(path=DEMO_PROFILE_ENV):
    """The demo profile's OBS_COLLECTION, the name `event start` switches to, or ""."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("OBS_COLLECTION="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass  # no demo profile in this checkout
    return ""


def _redacted(request_type, data):
    data = copy.deepcopy(data)
    if request_type == "SetStreamServiceSettings":
        settings = data.get("streamServiceSettings") or {}
        if "key" in settings:
            settings["key"] = "<redacted>"
    return data


class ObsState:
    """One simulated OBS instance. Thread-safe; every public method takes the lock."""

    def __init__(self, collection=None, scene=None, strict=True, clock=time.monotonic,
                 collection_names=None):
        """`collection_names` are further names OBS lists for the loaded collection
        (default: the demo profile's OBS_COLLECTION), so a profile's switch succeeds."""
        self.strict, self.clock = strict, clock
        self.lock = threading.RLock()
        self.listeners = []
        self.events, self.requests = [], []
        self.event_seq = self.request_seq = 0
        self.reads = {}
        self.inputs, self.scenes, self.scene_order = {}, {}, []
        self.collection = "Untitled"
        self.next_item_id = {}
        self.transition = {"name": "Fade", "duration": 300, "active": False, "ends_at": None}
        self.studio_mode, self.preview = False, None
        self.stream = {"active": False, "started_at": None, "service": {}}
        self.record = {"active": False, "paused": False, "started_at": None, "paused_ms": 0,
                       "paused_at": None, "path": None, "count": 0}
        self.media_frozen = set()
        self.media_started = {}
        self.image, self.labels = None, {}
        if collection is not None:
            self._load(collection)
        extra = [demo_collection_name()] if collection_names is None else list(collection_names)
        self.collections = [self.collection] + [n for n in extra if n and n != self.collection]
        self.program = scene or (self.scene_order[0] if self.scene_order else "Scene")
        if self.program not in self.scenes:
            self.scenes[self.program] = []
            self.scene_order.append(self.program)

    # -- seeding ---------------------------------------------------------------------
    def _load(self, collection):
        if isinstance(collection, str):
            with open(collection, encoding="utf-8") as fh:
                collection = json.load(fh)
        self.collection = collection.get("name") or self.collection
        sources = collection.get("sources") or []
        for src in sources:
            if src.get("id") == "scene":
                continue
            self.inputs[src["name"]] = {
                "kind": src.get("id") or "",
                "settings": copy.deepcopy(src.get("settings") or {}),
                "muted": bool(src.get("muted")), "volume": float(src.get("volume", 1.0))}
        for src in sources:
            if src.get("id") != "scene":
                continue
            items = []
            for it in (src.get("settings") or {}).get("items") or []:
                items.append({"id": int(it.get("id") or len(items) + 1), "source": it["name"],
                              "enabled": bool(it.get("visible", True)),
                              "transform": _transform()})
            self.scenes[src["name"]] = items
            self.next_item_id[src["name"]] = max([i["id"] for i in items] or [0]) + 1
        order = [s.get("name") for s in collection.get("scene_order") or []]
        self.scene_order = [n for n in order if n in self.scenes]
        self.scene_order += [n for n in self.scenes if n not in self.scene_order]
        if collection.get("current_transition") in TRANSITION_KINDS:
            self.transition["name"] = collection["current_transition"]
        if isinstance(collection.get("transition_duration"), int):
            self.transition["duration"] = collection["transition_duration"]
        current = collection.get("current_program_scene")
        if current in self.scenes:
            self.scene_order.remove(current)
            self.scene_order.insert(0, current)

    # -- events and logs -------------------------------------------------------------
    def subscribe(self, callback):
        """`callback(event)` runs, under the lock, for every event emitted from now on."""
        with self.lock:
            self.listeners.append(callback)

    def unsubscribe(self, callback):
        with self.lock:
            if callback in self.listeners:
                self.listeners.remove(callback)

    def _emit(self, event_type, data):
        self.event_seq += 1
        event = {"seq": self.event_seq, "t": self.clock(), "eventType": event_type,
                 "eventIntent": EVENT_CATEGORY.get(event_type, GENERAL), "eventData": data}
        self.events.append(event)
        del self.events[:-LOG_LIMIT]
        for callback in list(self.listeners):
            try:
                callback(event)
            except Exception:  # noqa: BLE001  a dead listener must not stop OBS
                self.listeners.remove(callback)

    def events_since(self, seq=0):
        with self.lock:
            self.tick()
            return [e for e in self.events if e["seq"] > seq]

    def requests_since(self, seq=0):
        """Logged requests after `seq`. Reads (`Get*`) are only counted, in `reads`, so
        the relay's polling never pushes a write out of the log."""
        with self.lock:
            return [r for r in self.requests if r["seq"] > seq]

    def tick(self):
        """Finish a transition whose duration has passed."""
        with self.lock:
            t = self.transition
            if t["active"] and self.clock() >= t["ends_at"]:
                t["active"], t["ends_at"] = False, None
                self._emit("SceneTransitionEnded", {"transitionName": t["name"]})

    # -- the request entry point ----------------------------------------------------
    def request(self, request_type, data=None, origin="ws"):
        """Apply one request. Returns the responseData dict or raises ObsError."""
        data = data or {}
        with self.lock:
            self.tick()
            entry = {"code": SUCCESS}
            if str(request_type).startswith("Get"):
                self.reads[request_type] = self.reads.get(request_type, 0) + 1
            else:
                self.request_seq += 1
                entry = {"seq": self.request_seq, "t": self.clock(), "origin": origin,
                         "requestType": request_type, "requestData": _redacted(request_type, data),
                         "code": SUCCESS}
                self.requests.append(entry)
                del self.requests[:-LOG_LIMIT]
            if not self.strict and request_type not in _LENIENT_LIVE:
                return copy.deepcopy(_LEGACY.get(request_type, {}))
            handler = getattr(self, "_r_" + str(request_type), None)
            try:
                if handler is None:
                    raise ObsError(UNKNOWN_REQUEST, f"Your request type is not valid: {request_type}")
                return handler(data)
            except ObsError as exc:
                if not self.strict and exc.code in (UNKNOWN_REQUEST, NOT_FOUND):
                    return copy.deepcopy(_LEGACY.get(request_type, {}))
                entry["code"] = exc.code
                raise

    # -- lookups ---------------------------------------------------------------------
    def _scene(self, name):
        if name not in self.scenes:
            raise ObsError(NOT_FOUND, f"No source was found by the name of `{name}`.")
        return self.scenes[name]

    def _input(self, name):
        if name not in self.inputs:
            raise ObsError(NOT_FOUND, f"No source was found by the name of `{name}`.")
        return self.inputs[name]

    def _item(self, data):
        scene, item_id = _need(data, "sceneName", "sceneItemId")
        for item in self._scene(scene):
            if item["id"] == item_id:
                return scene, item
        raise ObsError(NOT_FOUND, f"No scene items were found in scene `{scene}` with the ID `{item_id}`.")

    def _item_json(self, scene, item):
        nested = item["source"] in self.scenes
        return {"sceneItemId": item["id"], "sourceName": item["source"],
                "sceneItemEnabled": item["enabled"], "sceneItemIndex": self.scenes[scene].index(item),
                "inputKind": None if nested else (self.inputs.get(item["source"]) or {}).get("kind"),
                "sourceType": "OBS_SOURCE_TYPE_SCENE" if nested else "OBS_SOURCE_TYPE_INPUT",
                "isGroup": False if nested else None,
                "sceneItemTransform": copy.deepcopy(item["transform"])}

    def _add_item(self, scene, source, enabled):
        item_id = self.next_item_id.get(scene, 1)
        self.next_item_id[scene] = item_id + 1
        item = {"id": item_id, "source": source, "enabled": bool(enabled), "transform": _transform()}
        self.scenes[scene].append(item)
        self._emit("SceneItemCreated", {"sceneName": scene, "sourceName": source,
                                        "sceneItemId": item_id,
                                        "sceneItemIndex": len(self.scenes[scene]) - 1})
        return item_id

    # -- general ---------------------------------------------------------------------
    def _r_GetVersion(self, data):
        return {"obsVersion": "31.0.0", "obsWebSocketVersion": "5.5.0", "rpcVersion": 1,
                "availableRequests": sorted(n[3:] for n in dir(self) if n.startswith("_r_")),
                "supportedImageFormats": ["png"], "platform": "simulation",
                "platformDescription": "racecast obs-sim"}

    def _r_GetStats(self, data):
        return {"cpuUsage": 3.0, "memoryUsage": 512.0, "availableDiskSpace": 100000.0,
                "activeFps": 60.0, "averageFrameRenderTime": 1.0, "renderSkippedFrames": 0,
                "renderTotalFrames": 1000, "outputSkippedFrames": 0, "outputTotalFrames": 1000,
                "webSocketSessionIncomingMessages": self.request_seq,
                "webSocketSessionOutgoingMessages": self.request_seq}

    def _r_GetVideoSettings(self, data):
        return {"fpsNumerator": 60, "fpsDenominator": 1, "baseWidth": 1920, "baseHeight": 1080,
                "outputWidth": 1920, "outputHeight": 1080}

    def _r_GetSceneCollectionList(self, data):
        return {"currentSceneCollectionName": self.collection, "sceneCollections": list(self.collections)}

    def _r_SetCurrentSceneCollection(self, data):
        (name,) = _need(data, "sceneCollectionName")
        if name not in self.collections:
            raise ObsError(NOT_FOUND, "Your specified scene collection was not found.")
        if name != self.collection:
            self._emit("CurrentSceneCollectionChanging", {"sceneCollectionName": self.collection})
            self.collection = name
            self._emit("CurrentSceneCollectionChanged", {"sceneCollectionName": name})
        return {}

    def _r_GetStudioModeEnabled(self, data):
        return {"studioModeEnabled": self.studio_mode}

    def _r_SetStudioModeEnabled(self, data):
        (on,) = _need(data, "studioModeEnabled")
        if bool(on) != self.studio_mode:
            self.studio_mode = bool(on)
            self.preview = self.program if on else None
            self._emit("StudioModeStateChanged", {"studioModeEnabled": self.studio_mode})
        return {}

    # -- scenes ----------------------------------------------------------------------
    def _r_GetSceneList(self, data):
        n = len(self.scene_order)
        return {"currentProgramSceneName": self.program, "currentPreviewSceneName": self.preview,
                "scenes": [{"sceneName": s, "sceneIndex": n - 1 - i}
                           for i, s in enumerate(reversed(self.scene_order))]}

    def _r_GetCurrentProgramScene(self, data):
        return {"currentProgramSceneName": self.program, "sceneName": self.program}

    def _r_SetCurrentProgramScene(self, data):
        (name,) = _need(data, "sceneName")
        self._scene(name)
        self._switch(name)
        return {}

    def _switch(self, name):
        t = self.transition
        if t["active"]:
            t["active"] = False
            self._emit("SceneTransitionEnded", {"transitionName": t["name"]})
        self._emit("SceneTransitionStarted", {"transitionName": t["name"]})
        self.program = name
        self._emit("CurrentProgramSceneChanged", {"sceneName": name})
        duration = 0 if t["name"] == "Cut" else t["duration"]
        t["active"], t["ends_at"] = True, self.clock() + duration / 1000.0
        self.tick()

    def _r_GetCurrentPreviewScene(self, data):
        if not self.studio_mode:
            raise ObsError(STUDIO_MODE_OFF, "Studio mode is not active.")
        return {"currentPreviewSceneName": self.preview, "sceneName": self.preview}

    def _r_SetCurrentPreviewScene(self, data):
        if not self.studio_mode:
            raise ObsError(STUDIO_MODE_OFF, "Studio mode is not active.")
        (name,) = _need(data, "sceneName")
        self._scene(name)
        self.preview = name
        self._emit("CurrentPreviewSceneChanged", {"sceneName": name})
        return {}

    def _r_TriggerStudioModeTransition(self, data):
        if not self.studio_mode:
            raise ObsError(STUDIO_MODE_OFF, "Studio mode is not active.")
        old = self.program
        self._switch(self.preview)
        self.preview = old
        self._emit("CurrentPreviewSceneChanged", {"sceneName": old})
        return {}

    def _r_CreateScene(self, data):
        (name,) = _need(data, "sceneName")
        if name in self.scenes or name in self.inputs:
            raise ObsError(ALREADY_EXISTS, "A source already exists by that scene name.")
        self.scenes[name] = []
        self.scene_order.append(name)
        self._emit("SceneCreated", {"sceneName": name, "isGroup": False})
        return {"sceneUuid": f"sim-scene-{name}"}

    def _r_RemoveScene(self, data):
        (name,) = _need(data, "sceneName")
        self._scene(name)
        del self.scenes[name]
        self.scene_order.remove(name)
        for items in self.scenes.values():
            items[:] = [i for i in items if i["source"] != name]
        if self.program == name:
            self.program = self.scene_order[0] if self.scene_order else ""
            self._emit("CurrentProgramSceneChanged", {"sceneName": self.program})
        self._emit("SceneRemoved", {"sceneName": name, "isGroup": False})
        return {}

    # -- transitions -----------------------------------------------------------------
    def _r_GetSceneTransitionList(self, data):
        return {"currentSceneTransitionName": self.transition["name"],
                "currentSceneTransitionKind": TRANSITION_KINDS[self.transition["name"]],
                "transitions": [{"transitionName": n, "transitionKind": k, "transitionFixed": f,
                                 "transitionConfigurable": not f}
                                for n, k, f in TRANSITIONS_BUILTIN]}

    def _r_GetCurrentSceneTransition(self, data):
        name = self.transition["name"]
        fixed = name == "Cut"
        return {"transitionName": name, "transitionKind": TRANSITION_KINDS[name],
                "transitionFixed": fixed,
                "transitionDuration": None if fixed else self.transition["duration"],
                "transitionConfigurable": not fixed, "transitionSettings": {}}

    def _r_SetCurrentSceneTransition(self, data):
        (name,) = _need(data, "transitionName")
        if name not in TRANSITION_KINDS:
            raise ObsError(NOT_FOUND, "No scene transition was found by that name.")
        if name != self.transition["name"]:
            self.transition["name"] = name
            self._emit("CurrentSceneTransitionChanged", {"transitionName": name})
        return {}

    def _r_SetCurrentSceneTransitionDuration(self, data):
        (ms,) = _need(data, "transitionDuration")
        if not isinstance(ms, int) or not 50 <= ms <= 20000:
            raise ObsError(OUT_OF_RANGE, "The field value of `transitionDuration` is out of range.")
        self.transition["duration"] = ms
        self._emit("CurrentSceneTransitionDurationChanged", {"transitionDuration": ms})
        return {}

    # -- inputs ----------------------------------------------------------------------
    def _r_GetInputList(self, data):
        kind = data.get("inputKind")
        return {"inputs": [{"inputName": n, "inputUuid": f"sim-input-{n}", "inputKind": i["kind"],
                            "unversionedInputKind": i["kind"]}
                           for n, i in self.inputs.items() if not kind or i["kind"] == kind]}

    def _r_GetInputKindList(self, data):
        return {"inputKinds": sorted({i["kind"] for i in self.inputs.values()} |
                                     {"ffmpeg_source", "image_source", "browser_source"})}

    def _r_GetInputSettings(self, data):
        (name,) = _need(data, "inputName")
        inp = self._input(name)
        return {"inputSettings": copy.deepcopy(inp["settings"]), "inputKind": inp["kind"]}

    def _r_SetInputSettings(self, data):
        name, settings = _need(data, "inputName", "inputSettings")
        inp = self._input(name)
        if data.get("overlay", True):
            inp["settings"].update(copy.deepcopy(settings))
        else:
            inp["settings"] = copy.deepcopy(settings)
        self._emit("InputSettingsChanged", {"inputName": name,
                                            "inputSettings": copy.deepcopy(inp["settings"])})
        return {}

    def _r_GetInputMute(self, data):
        (name,) = _need(data, "inputName")
        return {"inputMuted": self._input(name)["muted"]}

    def _set_mute(self, name, muted):
        inp = self._input(name)
        if inp["muted"] != muted:
            inp["muted"] = muted
            self._emit("InputMuteStateChanged", {"inputName": name, "inputMuted": muted})

    def _r_SetInputMute(self, data):
        name, muted = _need(data, "inputName", "inputMuted")
        self._set_mute(name, bool(muted))
        return {}

    def _r_ToggleInputMute(self, data):
        (name,) = _need(data, "inputName")
        self._set_mute(name, not self._input(name)["muted"])
        return {"inputMuted": self.inputs[name]["muted"]}

    def _r_GetInputVolume(self, data):
        (name,) = _need(data, "inputName")
        mul = self._input(name)["volume"]
        return {"inputVolumeMul": mul, "inputVolumeDb": _db(mul)}

    def _r_SetInputVolume(self, data):
        (name,) = _need(data, "inputName")
        inp = self._input(name)
        if data.get("inputVolumeMul") is not None:
            inp["volume"] = float(data["inputVolumeMul"])
        elif data.get("inputVolumeDb") is not None:
            inp["volume"] = 10 ** (float(data["inputVolumeDb"]) / 20)
        else:
            raise ObsError(MISSING_FIELD, "You must specify one volume parameter.")
        self._emit("InputVolumeChanged", {"inputName": name, "inputVolumeMul": inp["volume"],
                                          "inputVolumeDb": _db(inp["volume"])})
        return {}

    def _r_CreateInput(self, data):
        scene, name, kind = _need(data, "sceneName", "inputName", "inputKind")
        self._scene(scene)
        if name in self.inputs or name in self.scenes:
            raise ObsError(ALREADY_EXISTS, "A source already exists by that input name.")
        self.inputs[name] = {"kind": kind, "settings": copy.deepcopy(data.get("inputSettings") or {}),
                             "muted": False, "volume": 1.0}
        self._emit("InputCreated", {"inputName": name, "inputKind": kind,
                                    "inputSettings": copy.deepcopy(self.inputs[name]["settings"])})
        item_id = self._add_item(scene, name, data.get("sceneItemEnabled", True))
        return {"inputUuid": f"sim-input-{name}", "sceneItemId": item_id}

    def _r_RemoveInput(self, data):
        (name,) = _need(data, "inputName")
        self._input(name)
        del self.inputs[name]
        for scene, items in self.scenes.items():
            for item in [i for i in items if i["source"] == name]:
                items.remove(item)
                self._emit("SceneItemRemoved", {"sceneName": scene, "sourceName": name,
                                                "sceneItemId": item["id"]})
        self._emit("InputRemoved", {"inputName": name})
        return {}

    def _r_GetInputPropertiesListPropertyItems(self, data):
        self._input(_need(data, "inputName", "propertyName")[0])
        return {"propertyItems": []}

    def _r_PressInputPropertiesButton(self, data):
        self._input(_need(data, "inputName", "propertyName")[0])
        return {}

    def _r_GetMediaInputStatus(self, data):
        (name,) = _need(data, "inputName")
        inp = self._input(name)
        if inp["kind"] != "ffmpeg_source":
            raise ObsError(NOT_FOUND, "The specified input is not a media input.")
        if name in self.media_frozen:
            cursor = self.media_started[name]
        else:
            cursor = int((self.clock() - self.media_started.setdefault(name, self.clock())) * 1000)
        live = bool(feed_url(inp["settings"]))
        return {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaDuration": None if live else 60000,
                "mediaCursor": cursor}

    def freeze_media(self, name, frozen=True):
        """Stop (or restart) a media input's cursor, the relay's freeze signal. While frozen,
        media_started holds the stopped cursor in ms instead of a start time."""
        with self.lock:
            if frozen and name not in self.media_frozen:
                started = self.media_started.get(name, self.clock())
                self.media_started[name] = int((self.clock() - started) * 1000)
                self.media_frozen.add(name)
            elif not frozen and name in self.media_frozen:
                self.media_frozen.discard(name)
                self.media_started[name] = self.clock() - self.media_started[name] / 1000.0

    # -- scene items -----------------------------------------------------------------
    def _r_GetSceneItemList(self, data):
        (scene,) = _need(data, "sceneName")
        return {"sceneItems": [self._item_json(scene, i) for i in self._scene(scene)]}

    def _r_GetSceneItemId(self, data):
        scene, source = _need(data, "sceneName", "sourceName")
        for item in self._scene(scene):
            if item["source"] == source:
                return {"sceneItemId": item["id"]}
        raise ObsError(NOT_FOUND, "No scene items were found in the specified scene by that name.")

    def _r_GetSceneItemEnabled(self, data):
        return {"sceneItemEnabled": self._item(data)[1]["enabled"]}

    def _r_SetSceneItemEnabled(self, data):
        scene, item = self._item(data)
        (enabled,) = _need(data, "sceneItemEnabled")
        if item["enabled"] != bool(enabled):
            item["enabled"] = bool(enabled)
            self._emit("SceneItemEnableStateChanged", {"sceneName": scene, "sceneItemId": item["id"],
                                                       "sceneItemEnabled": item["enabled"]})
        return {}

    def _r_GetSceneItemTransform(self, data):
        return {"sceneItemTransform": copy.deepcopy(self._item(data)[1]["transform"])}

    def _r_SetSceneItemTransform(self, data):
        scene, item = self._item(data)
        (tr,) = _need(data, "sceneItemTransform")
        item["transform"].update({k: v for k, v in tr.items() if k not in _READ_ONLY_TRANSFORM})
        self._emit("SceneItemTransformChanged", {"sceneName": scene, "sceneItemId": item["id"],
                                                 "sceneItemTransform": copy.deepcopy(item["transform"])})
        return {}

    def _r_GetSceneItemIndex(self, data):
        scene, item = self._item(data)
        return {"sceneItemIndex": self.scenes[scene].index(item)}

    def _r_SetSceneItemIndex(self, data):
        scene, item = self._item(data)
        (index,) = _need(data, "sceneItemIndex")
        items = self.scenes[scene]
        items.remove(item)
        items.insert(max(0, min(int(index), len(items))), item)
        self._emit("SceneItemListReindexed", {"sceneName": scene, "sceneItems": [
            {"sceneItemId": i["id"], "sceneItemIndex": n} for n, i in enumerate(items)]})
        return {}

    def _r_CreateSceneItem(self, data):
        scene, source = _need(data, "sceneName", "sourceName")
        self._scene(scene)
        if source not in self.inputs and source not in self.scenes:
            raise ObsError(NOT_FOUND, "No source was found by that name.")
        return {"sceneItemId": self._add_item(scene, source, data.get("sceneItemEnabled", True))}

    def _r_RemoveSceneItem(self, data):
        scene, item = self._item(data)
        self.scenes[scene].remove(item)
        self._emit("SceneItemRemoved", {"sceneName": scene, "sourceName": item["source"],
                                        "sceneItemId": item["id"]})
        return {}

    # -- outputs ---------------------------------------------------------------------
    def _elapsed_ms(self, started_at):
        return int((self.clock() - started_at) * 1000) if started_at is not None else 0

    def _r_GetStreamStatus(self, data):
        ms = self._elapsed_ms(self.stream["started_at"])
        active = self.stream["active"]
        return {"outputActive": active, "outputReconnecting": False,
                "outputTimecode": _timecode(ms), "outputDuration": ms, "outputCongestion": 0.0,
                "outputBytes": ms * 750 if active else 0, "outputSkippedFrames": 0,
                "outputTotalFrames": ms * 60 // 1000}

    def _set_stream(self, active):
        if active == self.stream["active"]:
            raise ObsError(OUTPUT_RUNNING if active else OUTPUT_NOT_RUNNING,
                           "The stream output is already " + ("running." if active else "stopped."))
        self._emit("StreamStateChanged", {"outputActive": False,
                                          "outputState": STARTING if active else STOPPING})
        self.stream["active"] = active
        self.stream["started_at"] = self.clock() if active else None
        self._emit("StreamStateChanged", {"outputActive": active,
                                          "outputState": STARTED if active else STOPPED})

    def _r_StartStream(self, data):
        self._set_stream(True)
        return {}

    def _r_StopStream(self, data):
        self._set_stream(False)
        return {}

    def _r_ToggleStream(self, data):
        self._set_stream(not self.stream["active"])
        return {"outputActive": self.stream["active"]}

    def _r_GetStreamServiceSettings(self, data):
        svc = self.stream["service"]
        return {"streamServiceType": svc.get("type", "rtmp_common"),
                "streamServiceSettings": copy.deepcopy(svc.get("settings", {}))}

    def _r_SetStreamServiceSettings(self, data):
        kind, settings = _need(data, "streamServiceType", "streamServiceSettings")
        self.stream["service"] = {"type": kind, "settings": copy.deepcopy(settings)}
        return {}

    def _record_ms(self):
        rec = self.record
        if not rec["active"]:
            return 0
        paused = rec["paused_ms"] + (self._elapsed_ms(rec["paused_at"]) if rec["paused"] else 0)
        return self._elapsed_ms(rec["started_at"]) - paused

    def _r_GetRecordStatus(self, data):
        ms = self._record_ms()
        return {"outputActive": self.record["active"], "outputPaused": self.record["paused"],
                "outputTimecode": _timecode(ms), "outputDuration": ms,
                "outputBytes": ms * 1000 if self.record["active"] else 0}

    def _r_StartRecord(self, data):
        rec = self.record
        if rec["active"]:
            raise ObsError(OUTPUT_RUNNING, "The record output is already running.")
        self._emit("RecordStateChanged", {"outputActive": False, "outputState": STARTING,
                                          "outputPath": None})
        rec["count"] += 1
        rec.update(active=True, paused=False, started_at=self.clock(), paused_ms=0, paused_at=None,
                   path=f"sim-recording-{rec['count']}.mkv")
        self._emit("RecordStateChanged", {"outputActive": True, "outputState": STARTED,
                                          "outputPath": rec["path"]})
        return {}

    def _r_StopRecord(self, data):
        rec = self.record
        if not rec["active"]:
            raise ObsError(OUTPUT_NOT_RUNNING, "The record output is not running.")
        self._emit("RecordStateChanged", {"outputActive": False, "outputState": STOPPING,
                                          "outputPath": None})
        rec.update(active=False, paused=False, started_at=None, paused_at=None)
        self._emit("RecordStateChanged", {"outputActive": False, "outputState": STOPPED,
                                          "outputPath": rec["path"]})
        return {"outputPath": rec["path"]}

    def _r_ToggleRecord(self, data):
        if self.record["active"]:
            self._r_StopRecord(data)
        else:
            self._r_StartRecord(data)
        return {"outputActive": self.record["active"]}

    def _r_PauseRecord(self, data):
        rec = self.record
        if not rec["active"]:
            raise ObsError(OUTPUT_NOT_RUNNING, "The record output is not running.")
        if rec["paused"]:
            raise ObsError(OUTPUT_PAUSED, "The record output is already paused.")
        rec.update(paused=True, paused_at=self.clock())
        self._emit("RecordStateChanged", {"outputActive": True, "outputState": PAUSED,
                                          "outputPath": None})
        return {}

    def _r_ResumeRecord(self, data):
        rec = self.record
        if not rec["active"]:
            raise ObsError(OUTPUT_NOT_RUNNING, "The record output is not running.")
        if not rec["paused"]:
            raise ObsError(OUTPUT_NOT_PAUSED, "The record output is not paused.")
        rec["paused_ms"] += self._elapsed_ms(rec["paused_at"])
        rec.update(paused=False, paused_at=None)
        self._emit("RecordStateChanged", {"outputActive": True, "outputState": RESUMED,
                                          "outputPath": None})
        return {}

    # -- screenshots -----------------------------------------------------------------
    def set_image(self, raw):
        """Serve these fixed image bytes for every screenshot instead of a rendering."""
        with self.lock:
            self.image = raw

    def set_labels(self, labels):
        """Text a feed's test pattern shows, by input name; None removes a label."""
        with self.lock:
            for name, text in labels.items():
                if text is None:
                    self.labels.pop(name, None)
                else:
                    self.labels[name] = str(text)

    def screenshot(self, source, width=640):
        """(PNG or fixed image bytes, format) of a scene or input."""
        with self.lock:
            if self.image is not None:
                return self.image, "png" if self.image[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
            if source in self.scenes:
                feeds = [f["input"] for f in self.feeds(source)]
            else:
                inp = self.inputs.get(source) or {}
                feeds = [source] if feed_url(inp.get("settings") or {}) else []
            return render_program(source, feeds, width, self.labels), "png"

    def _r_GetSourceScreenshot(self, data):
        source, _ = _need(data, "sourceName", "imageFormat")
        if self.image is None and source not in self.scenes and source not in self.inputs:
            raise ObsError(NOT_FOUND, f"No source was found by the name of `{source}`.")
        raw, fmt = self.screenshot(source, data.get("imageWidth") or 640)
        return {"imageData": f"data:image/{fmt};base64," + base64.b64encode(raw).decode()}

    # -- what checks read ------------------------------------------------------------
    def visible(self, scene):
        """Names of the enabled items of `scene`, nested scenes expanded, bottom first."""
        with self.lock:
            out, seen = [], set()

            def walk(name):
                if name in seen:
                    return
                seen.add(name)
                for item in self.scenes.get(name, []):
                    if item["enabled"]:
                        out.append(item["source"])
                        walk(item["source"])
            walk(scene)
            return out

    def feeds(self, scene):
        """The visible network media inputs of `scene`, bottom first: what the picture shows."""
        with self.lock:
            out = []
            for name in self.visible(scene):
                inp = self.inputs.get(name)
                url = feed_url(inp["settings"]) if inp and inp["kind"] == "ffmpeg_source" else ""
                if url:
                    out.append({"input": name, "url": url, "port": urlsplit(url).port,
                                "muted": inp["muted"]})
            return out

    def snapshot(self):
        """Everything a check reads, as plain JSON. `scene` is the program scene."""
        with self.lock:
            self.tick()
            t = self.transition
            svc = self.stream["service"].get("settings", {})
            return {
                "scene": self.program, "preview": self.preview, "studio_mode": self.studio_mode,
                "collection": self.collection, "collections": list(self.collections),
                "scene_order": list(self.scene_order),
                "transition": {"name": t["name"], "duration": t["duration"], "active": t["active"]},
                "stream": {"active": self.stream["active"],
                           "duration_ms": self._elapsed_ms(self.stream["started_at"]),
                           "service": svc.get("service"), "key_set": bool(svc.get("key"))},
                "record": {"active": self.record["active"], "paused": self.record["paused"],
                           "duration_ms": self._record_ms(), "path": self.record["path"],
                           "count": self.record["count"]},
                "scenes": {s: [{"id": i["id"], "source": i["source"], "enabled": i["enabled"]}
                               for i in items] for s, items in self.scenes.items()},
                "inputs": {n: {"kind": i["kind"], "settings": copy.deepcopy(i["settings"]),
                               "muted": i["muted"], "volume_db": _db(i["volume"]),
                               "frozen": n in self.media_frozen}
                           for n, i in self.inputs.items()},
                "program": {"scene": self.program, "visible": self.visible(self.program),
                            "feeds": self.feeds(self.program)},
                "labels": dict(self.labels),
                "seq": {"events": self.event_seq, "requests": self.request_seq},
                "reads": dict(self.reads),
            }
