"""Serialize one-use token exchange; retry until Twitch answers definitively."""

from copy import deepcopy
import threading
import audience_twitch as tw

# Exchange failures that prove the one-use refresh token was NOT consumed:
# a 429 is rejected before processing, "unsent" failed before any byte left
# (DNS / refused connect).
UNCONSUMED = ("rate_limit", "unsent")
# Every other failure except a definitive rejection ("auth": HTTP 400/401/403
# on the refresh call) is ambiguous: 5xx, timeouts, resets, malformed or lost
# bodies. Those retry the SAME refresh token with backoff. If Twitch did consume
# it, the retry gets a 400 and ends in the reconnect a lost pair always needed;
# if it did not, the retry succeeds without any manual step. The token never
# leaves this machine, so the retry cannot make anything worse.
AMBIGUOUS_RETRY = 60


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
    """Storage callbacks run under the shared state lock; network callbacks never do.

    `staged=True` resumes a rotation whose durable refreshing phase an earlier
    process already wrote, so the phase is not staged a second time."""

    def __init__(
        self,
        pair,
        identity,
        *,
        stage,
        exchange,
        validate,
        persist,
        lock=None,
        staged=False,
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
        self._held = deepcopy(self._current[1])  # identity while a pair is in flight
        self._pending, self._validated = None, None
        self._phase, self._error, self._generation = "ready", "", 0
        self._retry_after = 5
        self._staged = bool(staged)  # refreshing phase already durable for this pair

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
        # Only a definitive provider answer gets here: Twitch rejected the
        # refresh token (spent or revoked grant) or the token changed account.
        # Twitch then requires the user to approve a new device-code grant.
        with self.lock:
            if generation == self._generation:
                self._current, self._pending, self._validated = None, None, None
                self._phase, self._error = "reconnect", message
        return False

    def _retry(self, generation, pair, error, message=None):
        # Keep a pair whose refresh token is (possibly) unspent and exchange it
        # again after a bounded delay, staying in the refreshing phase.
        with self.lock:
            if generation == self._generation:
                self._current = (pair, deepcopy(self._held))
                self._pending, self._validated = None, None
                self._retry_after = max(5, min(900, error.retry_after or AMBIGUOUS_RETRY))
                if message is None:
                    message = (
                        "Twitch did not accept the token refresh yet; retrying"
                        if error.code in UNCONSUMED
                        else "Twitch token refresh did not complete; retrying "
                        "with the same token"
                    )
                self._phase, self._error = "refreshing", message
        return False

    def _revalidate(self, generation, error):
        with self.lock:
            if generation == self._generation:
                self._retry_after = max(5, min(900, error.retry_after or AMBIGUOUS_RETRY))
                self._phase, self._error = (
                    "validating",
                    "Could not validate refreshed authorization; retrying",
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
                            "Could not mark the token refresh in local storage; "
                            "retrying automatically",
                        )
                        return False
                    self._staged = True
                    self._held = deepcopy(identity)
                    self._current = None
                    self._phase, self._error = "refreshing", ""
                    token = pair["refresh"]
            if needs_exchange:
                try:
                    fresh = _pair(self.exchange(token))
                except tw.APIError as error:
                    if error.code == "auth":
                        return self._reconnect(
                            generation,
                            tw.reauthorize("Twitch rejected the refresh token"),
                        )
                    return self._retry(generation, pair, error)
                except Exception:
                    return self._retry(
                        generation,
                        pair,
                        tw.APIError("temporary", "", True, AMBIGUOUS_RETRY),
                    )
                with self.lock:
                    if generation != self._generation:
                        return False
                    self._pending, self._validated = fresh, None
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
                    raise tw.IdentityError("Refreshed token changed account")
            except tw.IdentityError:
                return self._reconnect(
                    generation,
                    tw.reauthorize(
                        "The refreshed Twitch authorization belongs to another "
                        "account or application"
                    ),
                )
            except tw.APIError as error:
                if error.transient:
                    return self._revalidate(generation, error)
                # Twitch rejected the access token it just issued (or it expired
                # in transit); the matching refresh token is still unspent.
                return self._retry(
                    generation,
                    pair,
                    error,
                    "Twitch rejected the refreshed access token; refreshing again",
                )
            except Exception:
                # Malformed validation body: ambiguous, validate again later.
                return self._revalidate(
                    generation, tw.APIError("temporary", "", True, AMBIGUOUS_RETRY)
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
                    # Expired before it could be saved: its refresh token is unspent.
                    return self._retry(
                        generation,
                        pair,
                        error,
                        "Rotated Twitch authorization expired before saving; "
                        "refreshing again",
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
                    "Could not save rotated authorization to local storage; "
                    "retrying automatically",
                )
                return False
            self._current = (dict(pair), deepcopy(identity))
            self._pending, self._validated = None, None
            self._phase, self._error = "ready", ""
            return True
