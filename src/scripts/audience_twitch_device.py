"""One in-memory device authorization candidate with no implicit activation."""

from copy import deepcopy
import threading
import time
import uuid
import audience_twitch as tw


class Attempt:
    def __init__(
        self,
        client,
        target,
        *,
        fetch,
        monotonic=time.monotonic,
        clock=time.time,
        lock=None,
    ):
        self.client, self.selection = tw.client_id(client), tw.channel_target(target)
        self.fetch, self.monotonic, self.clock = fetch, monotonic, clock
        self.lock = lock if lock is not None else threading.RLock()
        self.work = threading.Lock()
        self.attempt = uuid.uuid4().hex
        self._phase, self._error, self._generation = "starting", "", 0
        self._expires, self._next, self._interval = monotonic() + 300, 0, 5
        self._device = self._user = self._uri = None
        self._pair = self._account = self._target = None
        self._token_deadline = 0

    def _clear(self, phase, error=""):
        self._generation += 1
        self._phase, self._error = phase, error
        self._device = self._user = self._uri = None
        self._pair = self._account = self._target = None

    def _expire(self):
        if (
            self._phase not in ("failed", "cancelled", "expired")
            and self.monotonic() >= self._expires
        ):
            self._clear(
                "expired", "Twitch approval or verified selection expired; start again"
            )

    def cancel(self):
        with self.lock:
            self._clear("cancelled")

    def status(self):
        with self.lock:
            self._expire()
            account = (
                {key: deepcopy(self._account[key]) for key in ("id", "login", "scopes")}
                if self._account
                else None
            )
            permissions = {"viewers": False, "subscriptions": False}
            if self._phase == "verified":
                permissions = {
                    "viewers": True,
                    "subscriptions": self._target["id"] == account["id"]
                    and tw.SCOPE in account["scopes"],
                }
            return {
                "phase": self._phase,
                "attempt": self.attempt,
                "error": self._error,
                "expiresIn": max(0, self._expires - self.monotonic()),
                "userCode": self._user,
                "verificationUri": self._uri,
                "account": account,
                "target": deepcopy(self._target),
                "permissions": permissions,
            }

    def approved(self):
        with self.lock:
            self._expire()
            if self._phase != "verified":
                raise ValueError("Finish Twitch verification before activation")
            return deepcopy((self._pair, self._account, self._target))

    def _failure(self, generation, operation, error):
        with self.lock:
            self._expire()
            if generation != self._generation:
                return
            if (
                operation == "exchange"
                and isinstance(error, tw.APIError)
                and error.code in ("pending", "slow_down", "rate_limit")
            ):
                if error.code == "slow_down":
                    self._interval = min(300, self._interval + 5)
                self._phase = "pending"
                self._next = self.monotonic() + max(
                    self._interval, min(900, error.retry_after)
                )
                self._error = (
                    "Twitch rate limited approval; retrying"
                    if error.code == "rate_limit"
                    else ""
                )
            elif (
                operation in ("validate", "user")
                and isinstance(error, tw.APIError)
                and error.transient
            ):
                self._next = self.monotonic() + max(5, min(900, error.retry_after))
                self._error = "Could not validate Twitch account or output; retrying"
            else:
                message = (
                    "Twitch approval exchange failed or its response was lost; start authorization again"
                    if operation == "exchange"
                    else "Could not verify Twitch application, account or selected output"
                )
                self._clear("failed", message)

    def step(self):
        if not self.work.acquire(blocking=False):
            return False
        try:
            with self.lock:
                self._expire()
                if self.monotonic() < self._next:
                    return False
                phase, generation = self._phase, self._generation
                if phase == "starting":
                    operation, values = "device", {}
                elif phase == "pending":
                    operation, values = "exchange", {"device": self._device}
                    self._phase = "exchanging"
                elif phase == "validating":
                    operation, values = "validate", {"access": self._pair["access"]}
                elif phase == "resolving":
                    operation, values = (
                        "user",
                        {
                            "access": self._pair["access"],
                            "target": dict(self.selection),
                        },
                    )
                else:
                    return False
                started = self.monotonic()
            try:
                payload = self.fetch(operation, self.client, **values)
                if operation == "device":
                    result = tw.device_snapshot(payload)
                elif operation == "exchange":
                    result = tw.token_snapshot(payload)
                elif operation == "validate":
                    result = tw.validation_snapshot(payload, self.client)
                else:
                    result = tw.channel_snapshot(payload, self.selection)
            except Exception as error:
                self._failure(generation, operation, error)
                return False
            with self.lock:
                self._expire()
                if generation != self._generation:
                    return False
                now = self.monotonic()
                self._next, self._error = now, ""
                if operation == "device":
                    self._device, self._user, self._uri = (
                        result["device"],
                        result["user"],
                        result["uri"],
                    )
                    self._interval = result["interval"]
                    self._expires, self._next = (
                        started + result["expires"],
                        now + self._interval,
                    )
                    self._phase = "pending"
                elif operation == "exchange":
                    self._pair = result
                    self._token_deadline = started + result["expires"]
                    self._expires = min(self._token_deadline, now + 300)
                    self._device = self._user = self._uri = None
                    self._phase = "validating"
                elif operation == "validate":
                    deadline = min(self._token_deadline, started + result["expires"])
                    self._account = {
                        **result,
                        "expiresAtMono": deadline,
                        "expiresAtWall": self.clock() + max(0, deadline - now),
                    }
                    self._phase = "resolving"
                else:
                    self._target = result
                    self._expires = min(self._account["expiresAtMono"], now + 300)
                    self._phase = "verified"
                self._expire()
                return self._phase == "verified"
        finally:
            self.work.release()
