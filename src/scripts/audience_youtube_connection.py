"""Relay-owned YouTube setup and polling, independent of overlay readership."""

from pathlib import Path
import threading
import time
import uuid

from audience_admin import UserError
from audience_connections import ConnectionFiles, StorageError
from audience_widgets import MetricCache, Sample, Store, normalize
import audience_youtube as yt

METRICS = ("likes", "viewers", "subscribers")


def _targets(data):
    """Validate portable resolved targets without accepting arbitrary stored fields."""
    result = {}
    for kind, pattern in (("video", yt.VIDEO), ("channel", yt.CHANNEL)):
        value = data.get(kind)
        if value is None:
            result[kind] = None
            continue
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("id"), str)
            or not pattern.fullmatch(value["id"])
        ):
            raise ValueError("Invalid saved YouTube target")
        result[kind] = {"id": value["id"], "title": _title(value.get("title"))}
        if kind == "video":
            channel = value.get("channelId")
            if not isinstance(channel, str) or not yt.CHANNEL.fullmatch(channel):
                raise ValueError("Invalid saved YouTube channel")
            result[kind].update(
                channelId=channel, channelTitle=_title(value.get("channelTitle"))
            )
    if not any(result.values()):
        raise UserError("Select a video or channel")
    return result


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("Invalid YouTube target title")
    return "".join(c for c in value if ord(c) >= 32).strip()


class Service:
    def __init__(
        self,
        profile,
        runtime,
        cache=None,
        *,
        definitions=None,
        fetch=yt.request,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        self.owner = Path(profile).name
        self.files = ConnectionFiles(
            Path(profile) / "audience-youtube.json",
            Path(runtime) / "audience-youtube.json",
            self.owner,
        )
        self.cache = cache if cache is not None else MetricCache()
        self.definitions = definitions or (
            lambda: Store(Path(profile) / "audience.json").read()["widgets"]
        )
        self.fetch, self.clock, self.monotonic = fetch, clock, monotonic
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.generation, self.setup_generation = 0, 0
        self.setup = None
        self.targets = {"video": None, "channel": None}
        self.key, self.connected, self.error, self.video_state = (
            None,
            False,
            "",
            "unknown",
        )
        self.storage_error = ""
        self.due = {"videos": 0, "channels": 0}
        self.backoff, self.auth_failed = 0, False
        public = self.files.read_public()
        if public and (
            public.get("enabled") is True
            or public.get("video") is not None
            or public.get("channel") is not None
        ):
            try:
                self.targets = _targets(public)
                if public.get("enabled") is True:
                    self.error = "Enter a local API key to reconnect these imported or unpaired targets"
            except ValueError:
                self.error = "Saved targets are invalid; select them again"
        loaded = self.files.load()
        if loaded:
            try:
                targets = _targets(loaded[0])
                key = yt.api_key(loaded[1].get("key"))
            except ValueError:
                self.error = "Saved connection is invalid; reconnect"
            else:
                self.targets, self.key, self.connected = targets, key, True
                self.error = ""

    def _invalidate(self, reason):
        for metric in METRICS:
            self.cache.publish(
                "youtube", metric, Sample(availability="unavailable", reason=reason)
            )

    def _expire(self):
        if self.setup and self.monotonic() >= self.setup["expires"]:
            self.setup_generation += 1
            self.setup = None

    def status(self):
        with self.lock:
            self._expire()
            setup = {"phase": "idle"}
            if self.setup:
                setup = {
                    name: self.setup[name]
                    for name in ("phase", "attempt", "targets", "error")
                }
                setup["expiresIn"] = max(0, self.setup["expires"] - self.monotonic())
            # Copy nested metadata so callers cannot mutate the active connection.
            return {
                "provider": "youtube",
                "profile": self.owner,
                "connected": self.connected,
                "targets": {
                    kind: dict(value) if value else None
                    for kind, value in self.targets.items()
                },
                "videoState": self.video_state,
                "error": self.storage_error or self.error,
                "metrics": {
                    metric: normalize(
                        {"source": "youtube", "metric": metric, "mode": "total"},
                        self.sample(metric),
                        now=self.clock(),
                    )
                    for metric in METRICS
                },
                "setup": {
                    **setup,
                    **(
                        {
                            "targets": {
                                k: dict(v) if isinstance(v, dict) else v
                                for k, v in setup["targets"].items()
                            }
                        }
                        if "targets" in setup
                        else {}
                    ),
                },
            }

    def sample(self, metric):
        if metric not in METRICS:
            raise ValueError("Unsupported YouTube metric")
        return self.cache.read("youtube", metric)

    def command(self, body):
        if not isinstance(body, dict) or body.get("expectedProfile") != self.owner:
            raise UserError("Active profile changed; reopen the connection settings")
        with self.lock:
            if self.stop.is_set():
                raise UserError("YouTube service is stopping")
            self._expire()
            action = body.get("action")
            if action == "verify":
                video = yt.video_target(body.get("video", ""))
                channel = yt.channel_target(body.get("channel", ""))
                if not video and not channel:
                    raise UserError("Select a video or channel")
                key = body.get("apiKey", "")
                key = yt.api_key(self.key if key == "" else key)
                self.setup_generation += 1
                attempt = uuid.uuid4().hex
                self.setup = {
                    "phase": "pending",
                    "attempt": attempt,
                    "key": key,
                    "video": video,
                    "channel": channel,
                    "targets": {},
                    "error": "",
                    "expires": self.monotonic() + 300,
                }
                self.wake.set()
                return {"ok": True, "attempt": attempt}
            if action == "cancel":
                self.setup_generation += 1
                self.setup = None
            elif action == "activate":
                if (
                    not self.setup
                    or self.setup["phase"] != "verified"
                    or body.get("attempt") != self.setup["attempt"]
                ):
                    raise UserError("Verify the selected targets before activation")
                # Commit both files before replacing the working RAM connection.
                try:
                    self.files.activate(
                        self.setup["targets"], {"key": self.setup["key"]}
                    )
                except StorageError as error:
                    self.storage_error = str(error)
                    raise
                self.storage_error = ""
                self.targets, self.key = self.setup["targets"], self.setup["key"]
                self.connected, self.auth_failed = True, False
                self.generation += 1
                self.setup_generation += 1
                self.setup = None
                self.due, self.backoff, self.error = {"videos": 0, "channels": 0}, 0, ""
                self.video_state = "unknown"
                self._invalidate("Waiting for YouTube metrics")
                self.wake.set()
            elif action == "disconnect":
                self.generation += 1
                self.setup_generation += 1
                self.key, self.connected, self.setup = None, False, None
                self.error, self.video_state = "", "unknown"
                self._invalidate("YouTube disconnected")
                try:
                    self.files.disconnect()
                except StorageError as error:
                    self.storage_error = str(error)
                    raise
                self.storage_error = ""
            else:
                raise UserError("Unsupported YouTube connection action")
            return {"ok": True}

    def _setup_current(self, generation):
        self._expire()
        return (
            self.setup is not None
            and generation == self.setup_generation
            and not self.stop.is_set()
        )

    def _verify(self):
        with self.lock:
            self._expire()
            if not self.setup or self.setup["phase"] != "pending":
                return
            self.setup["phase"] = "verifying"
            candidate, generation = dict(self.setup), self.setup_generation
        targets = {"video": None, "channel": None}
        error = ""
        try:
            if candidate["video"]:
                ident = candidate["video"]
                data = yt.item(
                    self.fetch("videos", {"id": ident}, candidate["key"]), ident
                )
                if data is None:
                    raise ValueError("Selected YouTube video is inaccessible")
                snippet = data.get("snippet")
                if not isinstance(snippet, dict):
                    raise ValueError("Invalid YouTube video metadata")
                targets["video"] = {
                    "id": ident,
                    "title": snippet.get("title"),
                    "channelId": snippet.get("channelId"),
                    "channelTitle": snippet.get("channelTitle"),
                }
            with self.lock:
                if not self._setup_current(generation):
                    return
            if candidate["channel"]:
                payload = self.fetch("channels", candidate["channel"], candidate["key"])
                rows = payload.get("items") if isinstance(payload, dict) else None
                if (
                    not isinstance(rows, list)
                    or len(rows) != 1
                    or not isinstance(rows[0], dict)
                ):
                    raise ValueError("Selected YouTube channel is inaccessible")
                row = rows[0]
                if (
                    "id" in candidate["channel"]
                    and row.get("id") != candidate["channel"]["id"]
                ):
                    raise ValueError("YouTube channel target mismatch")
                snippet = row.get("snippet")
                if not isinstance(snippet, dict):
                    raise ValueError("Invalid YouTube channel metadata")
                targets["channel"] = {
                    "id": row.get("id"),
                    "title": snippet.get("title"),
                }
            targets = _targets(targets)
        except yt.APIError as exc:
            error = str(exc)
        except Exception:
            error = "Could not verify the selected YouTube targets"
        with self.lock:
            if self._setup_current(generation):
                self.setup.update(
                    phase="failed" if error else "verified",
                    targets=targets if not error else {},
                    error=error,
                )
                if error:
                    self.setup["key"] = None

    def _poll(self, resource, generation, target, key):
        metrics = ("likes", "viewers") if resource == "videos" else ("subscribers",)
        failure = None
        try:
            payload = self.fetch(resource, {"id": target["id"]}, key)
            observed, success_wall = self.monotonic(), self.clock()
            if resource == "videos":
                state, values = yt.video_snapshot(payload, target["id"])
            else:
                values = {
                    "subscribers": yt.channel_snapshot(payload, target["id"])["value"]
                }
                state = None
        except yt.APIError as exc:
            failure = exc
        except Exception:
            failure = yt.APIError("temporary", "Invalid YouTube API response", True, 60)
        with self.lock:
            if (
                generation != self.generation
                or not self.connected
                or self.stop.is_set()
            ):
                return
            if failure:
                self.error = str(failure)
                if failure.code == "auth":
                    self.auth_failed = True
                    self._invalidate(self.error)
                elif failure.transient:
                    self.backoff = max(
                        self.backoff, self.monotonic() + max(30, failure.retry_after)
                    )
                    for metric in metrics:
                        self.cache.fail("youtube", metric, self.error)
                else:
                    for metric in metrics:
                        self.cache.publish(
                            "youtube",
                            metric,
                            Sample(availability="unavailable", reason=self.error),
                        )
                return
            self.error = ""
            if state is not None:
                self.video_state = state
            for metric, value in values.items():
                identity = (
                    "youtube:"
                    + ("video:" if resource == "videos" else "channel:")
                    + target["id"]
                    + ":"
                    + metric
                )
                self.cache.publish(
                    "youtube",
                    metric,
                    Sample(
                        value=value,
                        availability="available"
                        if value is not None
                        else "unavailable",
                        last_success=success_wall,
                        identity=identity,
                        age_seconds=max(0, self.monotonic() - observed),
                        reason=""
                        if value is not None
                        else "YouTube count is unavailable",
                    ),
                )

    def tick(self):
        # Also serialize explicit fixture/manual ticks with the production worker.
        if not self.poll_lock.acquire(blocking=False):
            return
        try:
            self._verify()
            try:
                definitions = self.definitions()
                wanted = {
                    w.get("metric")
                    for w in definitions
                    if isinstance(w, dict) and w.get("source") == "youtube"
                }
            except Exception:
                wanted = set()
            for resource, kind, demand, cadence in (
                ("videos", "video", {"likes", "viewers"}, 30),
                ("channels", "channel", {"subscribers"}, 60),
            ):
                with self.lock:
                    now = self.monotonic()
                    target = self.targets[kind]
                    if (
                        self.stop.is_set()
                        or not self.connected
                        or self.auth_failed
                        or not target
                        or not wanted.intersection(demand)
                        or now < max(self.backoff, self.due[resource])
                    ):
                        continue
                    generation, key = self.generation, self.key
                    self.due[resource] = now + cadence
                self._poll(resource, generation, target, key)
        finally:
            self.poll_lock.release()

    def start(self):
        with self.lock:
            if self.thread is not None or self.stop.is_set():
                return
            self.thread = threading.Thread(
                target=self._run, name="audience-youtube", daemon=True
            )
            self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            self.wake.clear()
            self.tick()
            self.wake.wait(1)

    def close(self):
        with self.lock:
            self.generation += 1
            self.setup_generation += 1
            self.setup, self.key = None, None
            self.stop.set()
            self.wake.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)
