"""Relay-owned Twitch authorization and demanded metrics with private credentials."""

from copy import deepcopy
from pathlib import Path
import threading
import time

from audience_connections import StorageError
from audience_twitch_storage import CredentialFiles
from audience_widgets import MetricCache, Sample, Store, normalize
from audience_twitch_device import Attempt
from audience_twitch_refresh import Rotation
import audience_twitch as tw

METRICS = ("viewers", "subscriptions")


def selection(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid saved Twitch selection")
    client = tw.client_id(value.get("clientId"))
    target = value.get("target")
    if not isinstance(target, dict):
        raise ValueError("Invalid saved Twitch target")
    target = tw.channel_snapshot(
        {
            "data": [
                {
                    "id": target.get("id"),
                    "login": target.get("login"),
                    "display_name": target.get("title"),
                }
            ]
        },
        {"id": target.get("id")},
    )
    return client, target


def credentials(pair, account):
    return {
        "phase": "ready",
        "pair": dict(pair),
        "account": {
            key: deepcopy(account[key])
            for key in ("id", "login", "scopes", "expires", "expiresAtWall")
        },
    }


class Service:
    def __init__(
        self,
        profile,
        runtime,
        cache=None,
        *,
        definitions=None,
        fetch=tw.request,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        self.owner = Path(profile).name
        self.files = CredentialFiles(
            Path(profile) / "audience-twitch.json",
            Path(runtime) / "audience-twitch.json",
            self.owner,
        )
        self.cache = cache if cache is not None else MetricCache()
        self.definitions = definitions or (
            lambda: Store(Path(profile) / "audience.json").read()["widgets"]
        )
        self.fetch, self.clock, self.monotonic = fetch, clock, monotonic
        self.lock, self.work = threading.RLock(), threading.Lock()
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.generation = 0
        self.setup = self.rotation = None
        self.client, self.target = "", None
        self.pair = self.account = None
        self.connection_id = None
        self.ready = False
        self.error = self.storage_error = ""
        self.phase = "disconnected"
        self.stream_state = "unknown"
        self.validation_due = self.retry_due = self.backoff = 0
        self.due = {metric: 0 for metric in METRICS}
        public = self.files.read_public()
        if public and public.get("target") is not None:
            try:
                self.client, self.target = selection(public)
                if public.get("enabled") is True:
                    self.error = "Reconnect Twitch on this machine to use imported or unpaired targets"
            except ValueError:
                self.error = "Saved Twitch target is invalid; select it again"
        loaded = self.files.load()
        if loaded:
            try:
                client, target = selection(loaded[0])
                private = loaded[1]
                if private.get("phase") != "ready":
                    raise ValueError("Interrupted refresh")
                raw = private.get("pair", {})
                pair = tw.token_snapshot({
                    "access_token": raw.get("access"),
                    "refresh_token": raw.get("refresh"),
                    "expires_in": raw.get("expires"),
                    "token_type": "bearer",
                })
                raw = private.get("account", {})
                account = tw.validation_snapshot(
                    {
                        "client_id": client,
                        "user_id": raw.get("id"),
                        "login": raw.get("login"),
                        "scopes": raw.get("scopes"),
                        "expires_in": raw.get("expires"),
                    },
                    client,
                )
                wall = raw.get("expiresAtWall")
                if (
                    isinstance(wall, bool)
                    or not isinstance(wall, (int, float))
                    or not 0 < wall < 1e12
                ):
                    raise ValueError("Invalid saved expiry")
                account.update(
                    expiresAtWall=wall,
                    expiresAtMono=self.monotonic() + max(0, wall - self.clock()),
                )
                self.client, self.target, self.pair, self.account = (
                    client,
                    target,
                    pair,
                    account,
                )
                self.connection_id = loaded[0]["connectionId"]
                self.phase, self.error = "validating", ""
            except (ValueError, TypeError, AttributeError):
                self.error = (
                    "Saved Twitch authorization is incomplete or interrupted; reconnect"
                )
                self.phase = "reconnect"

    def _invalidate(self, reason, metrics=METRICS):
        for metric in metrics:
            self.cache.publish(
                "twitch", metric, Sample(availability="unavailable", reason=reason)
            )

    def _expire_authorization(self):
        if (
            self.ready
            and self.account
            and self.monotonic() >= self.account["expiresAtMono"]
        ):
            self.ready, self.phase = False, "validating"
            self._invalidate("Twitch authorization expired; waiting for validation")

    def sample(self, metric):
        if metric not in METRICS:
            raise ValueError("Unsupported Twitch metric")
        with self.lock:
            self._expire_authorization()
            return self.cache.read("twitch", metric)

    def _permissions(self):
        return {
            "viewers": self.ready,
            "subscriptions": bool(
                self.ready
                and self.target
                and self.account
                and self.target["id"] == self.account["id"]
                and tw.SCOPE in self.account["scopes"]
            ),
        }

    def status(self):
        with self.lock:
            self._expire_authorization()
            return {
                "provider": "twitch",
                "profile": self.owner,
                "connected": self.ready,
                "phase": self.phase,
                "clientId": self.client,
                "target": deepcopy(self.target),
                "account": {
                    k: deepcopy(self.account[k]) for k in ("id", "login", "scopes")
                }
                if self.account
                else None,
                "permissions": self._permissions(),
                "streamState": self.stream_state,
                "error": self.storage_error or self.error,
                "metrics": {
                    metric: normalize(
                        {"source": "twitch", "metric": metric, "mode": "total"},
                        self.sample(metric),
                        now=self.clock(),
                    )
                    for metric in METRICS
                },
                "setup": self.setup.status() if self.setup else {"phase": "idle"},
            }

    def command(self, body):
        if not isinstance(body, dict) or body.get("expectedProfile") != self.owner:
            raise ValueError("Active profile changed; reopen Twitch settings")
        with self.lock:
            if self.stop.is_set():
                raise ValueError("Twitch service is stopping")
            action = body.get("action")
            if action == "verify":
                candidate = Attempt(
                    body.get("clientId"),
                    body.get("channel"),
                    fetch=self.fetch,
                    clock=self.clock,
                    monotonic=self.monotonic,
                    lock=self.lock,
                )
                if self.setup:
                    self.setup.cancel()
                self.setup = candidate
                self.wake.set()
                return {"ok": True, "attempt": candidate.attempt}
            if action == "cancel":
                if self.setup:
                    self.setup.cancel()
                self.setup = None
            elif action == "activate":
                if not self.setup or body.get("attempt") != self.setup.attempt:
                    raise ValueError("Verify this Twitch selection before activation")
                pair, account, target = self.setup.approved()
                client = self.setup.client
                try:
                    activated = self.files.activate(
                        {"clientId": client, "target": target},
                        credentials(pair, account),
                    )
                except StorageError as error:
                    self.storage_error = str(error)
                    raise
                self._cancel_active()
                self.connection_id = activated["connectionId"]
                self.pair, self.account, self.target, self.client = (
                    pair,
                    account,
                    target,
                    client,
                )
                self.ready, self.phase = True, "ready"
                self.validation_due = self.monotonic() + min(
                    3600, max(1, account["expiresAtMono"] - self.monotonic())
                )
                self.setup.cancel()
                self.setup = None
                self.error = self.storage_error = ""
                self._invalidate("Waiting for Twitch metrics")
                self.wake.set()
            elif action == "disconnect":
                self._cancel_active()
                if self.setup:
                    self.setup.cancel()
                self.setup = None
                self.phase = "disconnected"
                self.error = ""
                self._invalidate("Twitch disconnected")
                try:
                    self.files.disconnect()
                except StorageError as error:
                    self.storage_error = str(error)
                    raise
                self.storage_error = ""
            else:
                raise ValueError("Unsupported Twitch connection action")
            return {"ok": True}

    def _cancel_active(self):
        self.generation += 1
        if self.rotation:
            self.rotation.cancel()
        self.rotation = None
        self.pair = self.account = None
        self.ready = False
        self.due = {metric: 0 for metric in METRICS}
        self.retry_due = self.backoff = self.validation_due = 0
        self.stream_state = "unknown"

    def _current(self, generation):
        return generation == self.generation and not self.stop.is_set()

    def _reconnect(self, reason):
        self._cancel_active()
        self.phase, self.error = "reconnect", reason
        self._invalidate(reason)

    def _new_rotation(self):
        client, target = self.client, deepcopy(self.target)
        deadline = [0]

        def stage():
            self.files.stage_refresh(self.connection_id)

        def exchange(token):
            started = self.monotonic()
            pair = tw.token_snapshot(self.fetch("refresh", client, refresh=token))
            deadline[0] = started + pair["expires"]
            return pair

        def validate(pair):
            started = self.monotonic()
            account = tw.validation_snapshot(
                self.fetch("validate", client, access=pair["access"]), client
            )
            limit = min(deadline[0], started + account["expires"])
            if limit <= self.monotonic():
                raise tw.APIError("auth", "Refreshed Twitch authorization expired")
            return {
                **account,
                "expiresAtMono": limit,
                "expiresAtWall": self.clock() + limit - self.monotonic(),
            }

        def persist(pair, account):
            if account["expiresAtMono"] <= self.monotonic():
                raise tw.APIError("auth", "Rotated authorization expired before saving")
            activated = self.files.activate(
                {"clientId": client, "target": target}, credentials(pair, account)
            )
            self.connection_id = activated["connectionId"]

        self.rotation = Rotation(
            self.pair,
            self.account,
            stage=stage,
            exchange=exchange,
            validate=validate,
            persist=persist,
            lock=self.lock,
        )
        self.ready = False
        self.phase = "refreshing"
        self._invalidate("Refreshing Twitch authorization")

    def _maintain(self):
        with self.lock:
            self._expire_authorization()
            if self.stop.is_set() or self.monotonic() < self.retry_due:
                return
            rotation = self.rotation
            if rotation is None:
                if self.pair is None or self.monotonic() < self.validation_due:
                    return
                generation = self.generation
                pair, client, owner = (
                    deepcopy(self.pair),
                    self.client,
                    self.account["id"],
                )
                expired = self.monotonic() >= self.account["expiresAtMono"]
            else:
                generation = self.generation
        if rotation:
            rotation.step()
            with self.lock:
                if not self._current(generation) or self.rotation is not rotation:
                    return
                current = rotation.current()
                state = rotation.status()
                if state["phase"] == "ready" and current:
                    pair, account = current
                    if account["expiresAtMono"] <= self.monotonic():
                        self._reconnect(
                            "Rotated Twitch authorization expired; reconnect"
                        )
                        return
                    self.pair, self.account = pair, account
                    self.rotation = None
                    self.ready, self.phase = True, "ready"
                    self.error = self.storage_error = ""
                    self.validation_due = min(
                        self.monotonic() + 3600, account["expiresAtMono"]
                    )
                    self.due = {metric: 0 for metric in METRICS}
                elif state["phase"] == "reconnect":
                    self._reconnect(state["error"])
                else:
                    self.phase, self.error = state["phase"], state["error"]
                    if state["phase"] == "storage_error":
                        self.storage_error = state["error"]
                    self.retry_due = self.monotonic() + max(
                        5, min(900, state["retryAfter"])
                    )
            return
        started = self.monotonic()
        failure = None
        try:
            account = tw.validation_snapshot(
                self.fetch("validate", client, access=pair["access"]), client
            )
            if account["id"] != owner:
                raise ValueError("Account changed")
            limit = started + account["expires"]
            if limit <= self.monotonic():
                raise tw.APIError(
                    "temporary", "Twitch validation expired in transit", True, 5
                )
            account.update(
                expiresAtMono=limit,
                expiresAtWall=self.clock() + limit - self.monotonic(),
            )
        except tw.APIError as error:
            failure = error
        except Exception:
            failure = tw.APIError("auth", "Twitch authorization is invalid; reconnect")
        with self.lock:
            if not self._current(generation):
                return
            if failure:
                if failure.code == "auth" and expired:
                    self._new_rotation()
                    self.retry_due = 0
                elif failure.transient:
                    self.error = "Could not validate Twitch authorization; retrying"
                    self.retry_due = self.monotonic() + max(
                        5, min(900, failure.retry_after)
                    )
                    if expired or not self.ready:
                        self.ready = False
                        self._invalidate(self.error)
                    else:
                        for metric in METRICS:
                            self.cache.fail("twitch", metric, self.error)
                else:
                    self._reconnect(
                        "Twitch authorization was revoked or changed; reconnect"
                    )
                return
            self.account = account
            self.ready, self.phase = True, "ready"
            self.validation_due = min(self.monotonic() + 3600, account["expiresAtMono"])
            self.error = ""
            if not self._permissions()["subscriptions"]:
                self._invalidate(
                    "Subscription permission or broadcaster ownership is missing",
                    ("subscriptions",),
                )

    def _poll(self, metric, generation, client, pair, target):
        failure = None
        try:
            payload = self.fetch(
                metric, client, access=pair["access"], channel=target["id"]
            )
            observed, wall = self.monotonic(), self.clock()
            if metric == "viewers":
                state, value = tw.stream_snapshot(payload, target["id"])
            else:
                state, value = None, tw.subscriptions_snapshot(payload, target["id"])
        except tw.APIError as error:
            failure = error
        except Exception:
            failure = tw.APIError(
                "temporary", "Invalid Twitch metric response", True, 60
            )
        with self.lock:
            self._expire_authorization()
            if not self._current(generation) or not self.ready:
                return
            if failure:
                if failure.code == "auth":
                    self._invalidate("Twitch authorization needs validation", (metric,))
                    self.validation_due = 0
                elif failure.transient:
                    self.backoff = max(
                        self.backoff,
                        self.monotonic() + max(30, min(900, failure.retry_after)),
                    )
                    self.cache.fail(
                        "twitch", metric, "Twitch metric temporarily unavailable"
                    )
                else:
                    self._invalidate(
                        "Twitch metric permission is unavailable", (metric,)
                    )
                self.error = "Twitch metric request failed; check metric availability and permissions"
                return failure.code == "auth"
            if state is not None:
                self.stream_state = state
            self.cache.publish(
                "twitch",
                metric,
                Sample(
                    value=value,
                    availability="available" if value is not None else "unavailable",
                    last_success=wall,
                    identity="twitch:channel:" + target["id"] + ":" + metric,
                    age_seconds=max(0, self.monotonic() - observed),
                    reason=""
                    if value is not None
                    else "Twitch stream is offline or the count is unavailable",
                ),
            )
        return False

    def tick(self):
        if not self.work.acquire(blocking=False):
            return
        try:
            with self.lock:
                setup = self.setup
            if setup:
                setup.step()
            self._maintain()
            try:
                wanted = {
                    w.get("metric")
                    for w in self.definitions()
                    if isinstance(w, dict) and w.get("source") == "twitch"
                }
            except Exception:
                wanted = set()
            for metric, cadence in (("viewers", 30), ("subscriptions", 60)):
                with self.lock:
                    self._expire_authorization()
                    now = self.monotonic()
                    if (
                        self.stop.is_set()
                        or not self.ready
                        or metric not in wanted
                        or not self._permissions()[metric]
                        or now < max(self.backoff, self.due[metric])
                    ):
                        continue
                    generation, client, pair, target = (
                        self.generation,
                        self.client,
                        deepcopy(self.pair),
                        deepcopy(self.target),
                    )
                    self.due[metric] = now + cadence
                if self._poll(metric, generation, client, pair, target):
                    self._maintain()
                    break
        finally:
            self.work.release()

    def start(self):
        with self.lock:
            if self.thread is not None or self.stop.is_set():
                return
            self.thread = threading.Thread(
                target=self._run, name="audience-twitch", daemon=True
            )
            self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            self.wake.clear()
            self.tick()
            self.wake.wait(1)

    def close(self):
        with self.lock:
            self._cancel_active()
            if self.setup:
                self.setup.cancel()
            self.setup = None
            self.stop.set()
            self.wake.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)
