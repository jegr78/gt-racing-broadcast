"""Serialize one-use token exchange and retry only safe idempotent work."""

from copy import deepcopy
import threading
import audience_twitch as tw

# Exchange failures that prove the one-use refresh token was NOT consumed:
# a 429 is rejected before processing, "unsent" failed before any byte left
# (DNS / refused connect). Everything else (5xx, timeouts, resets, malformed
# bodies) may have spent the token and so still requires reconnecting.
UNCONSUMED = ("rate_limit", "unsent")


def _pair(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid token pair")
    return tw.token_snapshot({
        "access_token": value.get("access"),
        "refresh_token": value.get("refresh"),
        "expires_in": value.get("expires"),
        "token_type": "bearer",
    })


def _identity(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("id"), str)
        or not tw.ID.fullmatch(value["id"])
    ):
        raise ValueError("Invalid token identity")
    return deepcopy(value)


class Rotation:
    """Storage callbacks run under the shared state lock; network callbacks never do."""

    def __init__(
        self, pair, identity, *, stage, exchange, validate, persist, lock=None
    ):
        self.lock = lock if lock is not None else threading.RLock()
        self.work = threading.Lock()
        self.stage, self.exchange, self.validate, self.persist = (
            stage,
            exchange,
            validate,
            persist,
        )
        self._current = (_pair(pair), _identity(identity))
        self._owner = identity["id"]
        self._pending, self._validated = None, None
        self._phase, self._error, self._generation = "ready", "", 0
        self._retry_after = 5
        self._staged = False  # refreshing phase already durable for this pair

    def current(self):
        with self.lock:
            return deepcopy(self._current)

    def status(self):
        with self.lock:
            return {
                "phase": self._phase,
                "error": self._error,
                "retryAfter": self._retry_after,
            }

    def cancel(self):
        with self.lock:
            self._generation += 1
            self._current, self._pending, self._validated = None, None, None
            self._phase, self._error = "disconnected", ""

    def _reconnect(self, generation, message):
        with self.lock:
            if generation == self._generation:
                self._current, self._pending, self._validated = None, None, None
                self._phase, self._error = "reconnect", message
        return False

    def _retry(self, generation, pair, identity, error):
        # Twitch provably never processed the exchange, so the refresh token
        # is unspent: restore the pair and retry after the provider's delay.
        with self.lock:
            if generation == self._generation:
                self._current = (pair, identity)
                self._retry_after = max(5, min(900, error.retry_after))
                self._phase, self._error = (
                    "refreshing",
                    "Twitch did not accept the token refresh yet; retrying",
                )
        return False

    def step(self):
        if not self.work.acquire(blocking=False):
            return False
        try:
            with self.lock:
                generation = self._generation
                needs_exchange = self._pending is None
                if needs_exchange:
                    if self._current is None:
                        return False
                    pair, identity = self._current
                    try:
                        if not self._staged:
                            self.stage()  # Persist refreshing BEFORE the one-use POST.
                    except Exception:
                        self._phase, self._error = (
                            "storage_error",
                            "Could not mark token refresh; retry local storage",
                        )
                        return False
                    self._staged = True
                    self._current = None
                    self._phase, self._error = "refreshing", ""
                    token = pair["refresh"]
            if needs_exchange:
                try:
                    pair = _pair(self.exchange(token))
                except tw.APIError as error:
                    if error.code in UNCONSUMED:
                        return self._retry(generation, pair, identity, error)
                    return self._reconnect(
                        generation,
                        "Token refresh failed or its response was lost; reconnect",
                    )
                except Exception:
                    return self._reconnect(
                        generation,
                        "Token refresh failed or its response was lost; reconnect",
                    )
                with self.lock:
                    if generation != self._generation:
                        return False
                    self._pending, self._validated = pair, None
                    self._phase = "validating"
            return self._finish(generation)
        finally:
            self.work.release()

    def _finish(self, generation):
        with self.lock:
            if generation != self._generation or self._pending is None:
                return False
            pair, identity = dict(self._pending), deepcopy(self._validated)
        if identity is None:
            try:
                identity = _identity(self.validate(dict(pair)))
                if identity["id"] != self._owner:
                    raise ValueError("Refreshed token changed account")
            except tw.APIError as error:
                if error.transient:
                    with self.lock:
                        if generation == self._generation:
                            self._retry_after = max(5, min(900, error.retry_after))
                            self._phase, self._error = (
                                "validating",
                                "Could not validate refreshed authorization; retrying",
                            )
                    return False
                return self._reconnect(
                    generation, "Refreshed authorization is invalid; reconnect"
                )
            except Exception:
                return self._reconnect(
                    generation,
                    "Refreshed authorization changed identity or is invalid; reconnect",
                )
            with self.lock:
                if generation != self._generation:
                    return False
                self._validated = deepcopy(identity)
        with self.lock:
            if generation != self._generation:
                return False
            try:
                self.persist(dict(pair), deepcopy(identity))
            except tw.APIError as error:
                if not error.transient:
                    return self._reconnect(
                        generation, "Rotated Twitch authorization expired; reconnect"
                    )
                self._retry_after = max(5, min(900, error.retry_after))
                self._phase, self._error = (
                    "validating",
                    "Could not validate rotated Twitch authorization; retrying",
                )
                return False
            except Exception:
                self._retry_after = 5
                self._phase, self._error = (
                    "storage_error",
                    "Could not save rotated authorization; retry local storage",
                )
                return False
            self._current = (dict(pair), deepcopy(identity))
            self._pending, self._validated = None, None
            self._phase, self._error = "ready", ""
            return True
