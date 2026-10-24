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
VIDEO_METRICS = ("likes", "viewers")
# A rejected API key (invalid, restricted, API not enabled) is re-checked this
# often, so a fix made in Google Cloud recovers without any racecast action.
AUTH_RECHECK = 900
# Follow mode re-resolves the followed channel's live video this often.
FOLLOW_CADENCE = 30
# The one manual step: only the producer can create or enter an API key, and
# the key never travels with a profile.
REKEY = (
    "open YouTube connection in the Control Center, enter the API key, press "
    "Verify selected targets, then press Activate verified targets. Other "
    "audience widgets keep running"
)
NO_CHANNEL_TAB = "Follow mode needs the Sheet Channel tab"
CHANNELS_LOADING = "The Sheet Channel tab is still loading; try again in a few seconds"
UNKNOWN_ROW = "Choose a YouTube channel from the Sheet Channel tab"
UNRESOLVED_CHANNEL = "Use an @handle or channel ID in the Sheet Channel tab"
CHANNEL_CHANGED = "Channel tab changed; verify again"
NOT_LIVE = "Not live right now"
WAITING = "Waiting for YouTube metrics"
LOOKUP_FAILED = "Live broadcast lookup failed; retrying"


def _row_text(value):
    """A Channel tab row text as stored in follow mode (bounded, printable)."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid YouTube channel row")
    return value


def _follow_targets(data):
    follow, channel = data.get("follow"), data.get("channel")
    if not isinstance(follow, dict) or data.get("video") is not None:
        raise ValueError("Invalid saved YouTube follow target")
    ident = follow.get("channelId")
    if not isinstance(ident, str) or not yt.CHANNEL.fullmatch(ident):
        raise ValueError("Invalid saved YouTube channel")
    title = _title(follow.get("title"))
    if (
        not isinstance(channel, dict)
        or channel.get("id") != ident
        or _title(channel.get("title")) != title
    ):
        raise ValueError("Invalid saved YouTube channel")
    return {
        "mode": "follow",
        "follow": {
            "channel": _row_text(follow.get("channel")),
            "channelId": ident,
            "title": title,
        },
        "video": None,
        "channel": {"id": ident, "title": title},
    }


def _targets(data):
    """Validate portable resolved targets without accepting arbitrary stored fields.

    A missing mode is the fixed-video mode, so files written before follow mode
    keep working unchanged."""
    mode = data.get("mode", "video")
    if mode == "follow":
        return _follow_targets(data)
    if mode != "video":
        raise ValueError("Invalid saved YouTube mode")
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


def _live_ids(value):
    """Validated live video IDs (at most 5), or None when the lookup failed."""
    if not isinstance(value, list):
        return None
    ids = []
    for ident in value:
        if not isinstance(ident, str) or not yt.VIDEO.fullmatch(ident):
            return None
        if ident not in ids:
            ids.append(ident)
    return ids[:5]


class Service:
    """Relay-owned YouTube connection: setup, follow cycle and metric polls.

    Follow mode needs relay hooks, each a callable (without any of the first
    three, follow mode is off):
    - channels() -> list of the Sheet Channel tab's YouTube row texts, or None
      when there is no Channel tab; it must never wait on the network.
    - live_lookup(text) -> list of live video IDs, [] when not live, None when
      the lookup failed.
    - channel_id_lookup(text) -> a UC... channel ID or None (custom URLs).
    - channels_loaded() -> bool, optional: False until the Channel tab was read
      once, so the [] of a fresh relay is not taken as "row removed". Until
      then the pinned row is still followed and verify asks to retry. Without
      this hook the rows always count as loaded."""

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
        channels=None,
        live_lookup=None,
        channel_id_lookup=None,
        channels_loaded=None,
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
        # Relay-provided Sheet Channel tab hooks; any missing one disables follow.
        self.channels = channels
        self.live_lookup = live_lookup
        self.channel_id_lookup = channel_id_lookup
        self.channels_loaded = channels_loaded
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
        self.follow_state, self.follow_video = "unknown", None
        self._follow_reset()
        self.storage_error = ""
        self.due = {"videos": 0, "channels": 0, "follow": 0}
        self.backoff = 0
        public = self.files.read_public()
        if public and (
            public.get("enabled") is True
            or public.get("video") is not None
            or public.get("channel") is not None
        ):
            try:
                self.targets = _targets(public)
                if public.get("enabled") is True:
                    self.error = (
                        "No local API key for these imported or unpaired targets: "
                        + REKEY
                    )
            except ValueError:
                self.error = "Saved targets are invalid; select them again"
        loaded = self.files.load()
        if loaded:
            try:
                targets = _targets(loaded[0])
                key = yt.api_key(loaded[1].get("key"))
            except ValueError:
                self.error = "Saved YouTube connection is invalid: " + REKEY
            else:
                self.targets, self.key, self.connected = targets, key, True
                self.error = ""

    def _invalidate(self, reason, metrics=METRICS):
        for metric in metrics:
            self.cache.publish(
                "youtube", metric, Sample(availability="unavailable", reason=reason)
            )

    def _follow_reset(self):
        """Forget the follow decisions; the caller holds self.lock (or __init__).

        follow_memo is (live-ID tuple, chosen video or None) of the last
        videos.list, so an unchanged live set costs no quota. follow_dead holds
        IDs the video poll saw end while yt-dlp may still list them."""
        self.follow_memo, self.follow_dead = None, set()

    def _expire(self):
        if self.setup and self.monotonic() >= self.setup["expires"]:
            self.setup_generation += 1
            self.setup = None

    def _channel_rows(self):
        """Channel tab YouTube row texts, or None (no tab, no hook, failed read).

        Callers run it outside self.lock: the hooks are relay-owned."""
        if None in (self.channels, self.live_lookup, self.channel_id_lookup):
            return None
        try:
            rows = self.channels()
        except Exception:
            return None
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, str)]

    def _channels_ready(self):
        """Whether the Channel tab rows were read at least once.

        Callers read it before _channel_rows(): rows are set before the flag."""
        if self.channels_loaded is None:
            return True
        try:
            return bool(self.channels_loaded())
        except Exception:
            return False

    def status(self):
        loaded = self._channels_ready()
        rows = self._channel_rows()
        with self.lock:
            self._expire()
            setup = {"phase": "idle"}
            if self.setup:
                setup = {
                    name: self.setup[name]
                    for name in ("phase", "attempt", "targets", "error")
                }
                setup["expiresIn"] = max(0, self.setup["expires"] - self.monotonic())
                if "live" in self.setup:
                    live = self.setup["live"]
                    setup["live"] = dict(live) if live else None
            follow = self.targets.get("follow") or {}
            # Copy nested metadata so callers cannot mutate the active connection.
            return {
                "provider": "youtube",
                "profile": self.owner,
                "connected": self.connected,
                "targets": {
                    kind: dict(value) if isinstance(value, dict) else value
                    for kind, value in self.targets.items()
                },
                "mode": self.targets.get("mode", "video"),
                "channels": rows,
                "channelsLoaded": loaded,
                "follow": {
                    "channel": follow.get("channel"),
                    "channelId": follow.get("channelId"),
                    "state": self.follow_state,
                    "video": dict(self.follow_video) if self.follow_video else None,
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
        mode = body.get("mode", "video")
        rows, loaded = None, True
        if body.get("action") == "verify" and mode == "follow":
            loaded = self._channels_ready()
            rows = self._channel_rows()
        with self.lock:
            if self.stop.is_set():
                raise UserError("YouTube service is stopping")
            self._expire()
            action = body.get("action")
            if action == "verify":
                follow = video = channel = None
                if mode == "follow":
                    if rows is None:
                        raise UserError(NO_CHANNEL_TAB)
                    if not loaded:
                        raise UserError(CHANNELS_LOADING)
                    follow = body.get("followChannel")
                    try:
                        if follow not in rows:
                            raise ValueError(UNKNOWN_ROW)
                        follow = _row_text(follow)
                    except (TypeError, ValueError):
                        raise UserError(UNKNOWN_ROW) from None
                elif mode == "video":
                    video = yt.video_target(body.get("video", ""))
                    channel = yt.channel_target(body.get("channel", ""))
                    if not video and not channel:
                        raise UserError("Select a video or channel")
                else:
                    raise UserError("Unsupported YouTube connection mode")
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
                    "follow": follow,
                    "targets": {},
                    "error": "",
                    "expires": self.monotonic() + 300,
                }
                if follow is not None:
                    self.setup["live"] = None
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
                self.connected = True
                self.generation += 1
                self.setup_generation += 1
                self.setup = None
                self.due = {"videos": 0, "channels": 0, "follow": 0}
                self.backoff, self.error = 0, ""
                self.video_state = "unknown"
                self.follow_state, self.follow_video = "unknown", None
                self._follow_reset()
                self._invalidate(WAITING)
                self.wake.set()
            elif action == "disconnect":
                self.generation += 1
                self.setup_generation += 1
                self.key, self.connected, self.setup = None, False, None
                self.error, self.video_state = "", "unknown"
                self.follow_state, self.follow_video = "unknown", None
                self._follow_reset()
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
        if candidate["follow"] is not None:
            self._verify_follow(candidate, generation)
            return
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
                targets["channel"] = self._channel_row(
                    candidate["channel"], candidate["key"]
                )
            targets = _targets(targets)
        except yt.APIError as exc:
            error = str(exc)
        except Exception:
            error = "Could not verify the selected YouTube targets"
        self._verified(generation, targets, error)

    def _verified(self, generation, targets, error, **extra):
        with self.lock:
            if self._setup_current(generation):
                self.setup.update(
                    phase="failed" if error else "verified",
                    targets=targets if not error else {},
                    error=error,
                    **extra,
                )
                if error:
                    self.setup["key"] = None

    def _channel_row(self, params, key):
        """One channels.list lookup -> {"id", "title"} of exactly that channel."""
        payload = self.fetch("channels", params, key)
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("Selected YouTube channel is inaccessible")
        row = rows[0]
        if "id" in params and row.get("id") != params["id"]:
            raise ValueError("YouTube channel target mismatch")
        snippet = row.get("snippet")
        if not isinstance(snippet, dict):
            raise ValueError("Invalid YouTube channel metadata")
        return {"id": row.get("id"), "title": snippet.get("title")}

    def _verify_follow(self, candidate, generation):
        """Pin the Channel tab row to a channel ID; being live is optional."""
        text, key = candidate["follow"], candidate["key"]
        targets, live, error = {}, None, ""
        try:
            try:
                params = yt.channel_target(text)
            except ValueError:
                params = None  # e.g. /c/customname: only yt-dlp resolves it.
            if params is None:
                ident = self.channel_id_lookup(text)
                if not isinstance(ident, str) or not yt.CHANNEL.fullmatch(ident):
                    raise UserError(UNRESOLVED_CHANNEL)
                params = {"id": ident}
            with self.lock:
                if not self._setup_current(generation):
                    return
            # A handle/username lookup already returns the title; an ID needs one.
            row = self._channel_row(params, key)
            targets = _targets(
                {
                    "mode": "follow",
                    "follow": {
                        "channel": text,
                        "channelId": row["id"],
                        "title": row["title"],
                    },
                    "video": None,
                    "channel": row,
                }
            )
            with self.lock:
                if not self._setup_current(generation):
                    return
            ids = _live_ids(self.live_lookup(text))
            if ids:
                found = yt.newest_live(
                    self.fetch("videos", {"id": ",".join(ids)}, key),
                    ids,
                    channel=row["id"],
                )
                live = {"id": found[0], "title": found[1]} if found else None
        except (yt.APIError, UserError) as exc:
            error = str(exc)
        except Exception:
            error = "Could not verify the selected YouTube targets"
        self._verified(generation, targets, error, live=None if error else live)

    def _failed(self, failure, metrics):
        """Record one API failure; the caller holds self.lock."""
        self.error = str(failure)
        if failure.code == "auth":
            # Pause every request with this key, then re-check it.
            self.error = (
                str(failure) + ". Rechecked every 15 minutes; for a new key, " + REKEY
            )
            self.backoff = max(self.backoff, self.monotonic() + AUTH_RECHECK)
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

    def _follow_set(self, state, video, reason=""):
        """Apply one follow outcome; the caller holds self.lock.

        A different video invalidates the old video's cached counts at once and
        makes the next video poll due immediately."""
        previous = self.follow_video
        self.follow_state, self.follow_video = state, video
        if video is not None and (previous is None or previous["id"] != video["id"]):
            self.due["videos"] = 0
            reason = WAITING
        if (previous is None) != (video is None) or reason == WAITING:
            self.video_state = "unknown"
        if reason:
            self._invalidate(reason, VIDEO_METRICS)

    def _follow(self, wanted):
        """One follow cycle: which video does the pinned channel stream now?

        A videos.list runs only when the live-ID set differs from the set the
        last lookup decided (follow_memo), so a steady broadcast or a two-live
        overlap costs no quota per cycle."""
        with self.lock:
            now = self.monotonic()
            if (
                self.stop.is_set()
                or not self.connected
                or self.targets.get("mode") != "follow"
                or not wanted.intersection(VIDEO_METRICS)
                or now < max(self.backoff, self.due["follow"])
            ):
                return
            self.due["follow"] = now + FOLLOW_CADENCE
            generation, key = self.generation, self.key
            follow, current = self.targets["follow"], self.follow_video
            memo, dead = self.follow_memo, set(self.follow_dead)
        text = follow["channel"]
        outcome, ids, candidates = None, None, ()
        loaded = self._channels_ready()
        rows = self._channel_rows()
        if rows is None:
            outcome = ("no-channel-tab", None, NO_CHANNEL_TAB)
        elif loaded and text not in rows:
            # Before the first Channel tab read the pinned row is still followed.
            outcome = ("channel-changed", None, CHANNEL_CHANGED)
        else:
            try:
                ids = _live_ids(self.live_lookup(text))
            except Exception:
                ids = None
            if ids is None:
                # Keep the current video: its own poll drops it once it ends.
                outcome = ("lookup-failed", current, "" if current else LOOKUP_FAILED)
            else:
                candidates = tuple(ident for ident in ids if ident not in dead)
                if not candidates:
                    outcome = ("not-live", None, NOT_LIVE)
                elif current and candidates == (current["id"],):
                    outcome = ("live", current, "")  # Its own poll checks it.
                elif memo is not None and memo[0] == candidates:
                    outcome = (
                        ("not-live", None, NOT_LIVE)
                        if memo[1] is None
                        else ("live", dict(memo[1]), "")
                    )
        failure = decided = None
        if outcome is None:
            try:
                found = yt.newest_live(
                    self.fetch("videos", {"id": ",".join(candidates)}, key),
                    candidates,
                    channel=follow["channelId"],
                )
            except yt.APIError as exc:
                failure = exc
            except Exception:
                failure = yt.APIError(
                    "temporary", "Invalid YouTube API response", True, 60
                )
            else:
                video = None if found is None else {"id": found[0], "title": found[1]}
                decided = (candidates, video)
                outcome = (
                    ("not-live", None, NOT_LIVE)
                    if video is None
                    else ("live", dict(video), "")
                )
        with self.lock:
            if (
                generation != self.generation
                or not self.connected
                or self.stop.is_set()
            ):
                return
            if ids is not None:
                # Forget ended IDs that yt-dlp no longer lists.
                self.follow_dead.intersection_update(ids)
            if failure:
                self._failed(failure, VIDEO_METRICS)
                outcome = ("lookup-failed", current, "")
            elif decided is not None:
                self.follow_memo, self.error = decided, ""
            self._follow_set(*outcome)

    def _poll(self, resource, generation, target, key):
        metrics = VIDEO_METRICS if resource == "videos" else ("subscribers",)
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
            following = resource == "videos" and self.targets.get("mode") == "follow"
            if following and (
                self.follow_video is None or self.follow_video["id"] != target["id"]
            ):
                return  # The followed video changed while this request ran.
            if failure:
                self._failed(failure, metrics)
                return
            self.error = ""
            if following and state in ("ended", "not-live", "inaccessible"):
                # Never show an ended broadcast's counts as the live one's, and
                # never pick it again while yt-dlp still lists it.
                self.follow_dead.add(target["id"])
                self._follow_set("not-live", None, NOT_LIVE)
                return
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
            self._follow(wanted)
            for resource, kind, demand, cadence in (
                ("videos", "video", set(VIDEO_METRICS), 30),
                ("channels", "channel", {"subscribers"}, 60),
            ):
                with self.lock:
                    now = self.monotonic()
                    target = self.targets[kind]
                    if kind == "video" and self.targets.get("mode") == "follow":
                        target = self.follow_video
                    if (
                        self.stop.is_set()
                        or not self.connected
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
