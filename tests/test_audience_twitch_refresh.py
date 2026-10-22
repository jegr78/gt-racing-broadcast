"""A public refresh token is exchanged once, with durable phase staging first."""

from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_twitch_refresh as refresh
import audience_twitch as tw

OLD = {
    "access": "fixture-access-old",
    "refresh": "fixture-refresh-old",
    "expires": 14400,
}
NEW = {
    "access": "fixture-access-new",
    "refresh": "fixture-refresh-new",
    "expires": 14400,
}
META = {"id": "12345", "login": "racing", "scopes": [tw.SCOPE], "expires": 14400}


class Backend:
    def __init__(self):
        self.phase, self.events, self.fail = "ready", [], ""
        self.block, self.entered, self.release = (
            False,
            threading.Event(),
            threading.Event(),
        )
        self.identity = dict(META)

    def stage(self):
        self.events.append("stage")
        if self.fail == "stage":
            raise OSError("fixture secret must stay private")
        self.phase = "refreshing"

    def exchange(self, token):
        assert self.phase == "refreshing", (
            "Refresh token sent before the phase was saved"
        )
        assert token == OLD["refresh"]
        self.events.append("exchange")
        if self.block:
            self.entered.set()
            assert self.release.wait(3), "Fixture exchange was not released"
        if self.fail == "exchange":
            raise TimeoutError("fixture-private-token")
        if isinstance(self.fail, Exception):
            raise self.fail
        return dict(NEW)

    def validate(self, pair):
        assert pair == NEW
        self.events.append("validate")
        if self.fail == "validate":
            raise tw.APIError(
                "temporary", "Fixture temporary validation error", True, 60
            )
        return dict(self.identity)

    def persist(self, pair, identity):
        assert pair == NEW and identity == self.identity
        self.events.append("persist")
        if self.fail == "persist":
            raise OSError("fixture-private-token")
        self.phase = "ready"


def manager(backend):
    return refresh.Rotation(
        OLD,
        META,
        stage=backend.stage,
        exchange=backend.exchange,
        validate=backend.validate,
        persist=backend.persist,
    )


def t_staging_precedes_one_exchange_and_storage_retry_never_repeats_it():
    backend = Backend()
    backend.fail = "persist"
    rotation = manager(backend)
    assert rotation.step() is False
    assert backend.events == ["stage", "exchange", "validate", "persist"]
    assert (
        backend.phase == "refreshing" and rotation.status()["phase"] == "storage_error"
    )
    assert rotation.current() is None
    assert "fixture-private" not in str(rotation.status())
    backend.fail = ""
    assert rotation.step() is True
    assert backend.events == ["stage", "exchange", "validate", "persist", "persist"], (
        "Storage retry repeated provider work"
    )
    assert rotation.current() == (NEW, META) and backend.phase == "ready"


def t_failed_stage_never_exchanges_and_lost_response_retries_without_restaging():
    backend = Backend()
    backend.fail = "stage"
    rotation = manager(backend)
    assert rotation.step() is False and backend.events == ["stage"]
    assert rotation.current() == (OLD, META) and backend.phase == "ready"
    backend.fail = "exchange"
    assert rotation.step() is False and rotation.status()["phase"] == "refreshing"
    assert backend.phase == "refreshing" and backend.events == [
        "stage",
        "stage",
        "exchange",
    ]
    backend.fail = ""
    assert rotation.step() is True
    assert backend.events == [
        "stage",
        "stage",
        "exchange",
        "exchange",
        "validate",
        "persist",
    ], backend.events
    assert "fixture-private" not in str(rotation.status())


def t_validation_retry_is_idempotent_and_changed_account_requires_reconnect():
    backend = Backend()
    backend.fail = "validate"
    rotation = manager(backend)
    assert not rotation.step()
    assert backend.events == ["stage", "exchange", "validate"]
    backend.fail = ""
    assert rotation.step() and backend.events == [
        "stage",
        "exchange",
        "validate",
        "validate",
        "persist",
    ]
    backend = Backend()
    backend.identity = {**META, "id": "999"}
    rotation = manager(backend)
    assert not rotation.step() and rotation.status()["phase"] == "reconnect"
    assert "persist" not in backend.events and rotation.current() is None


def t_disconnect_during_exchange_discards_late_pair_without_persistence():
    backend = Backend()
    backend.block = True
    rotation = manager(backend)
    worker = threading.Thread(target=rotation.step)
    worker.start()
    try:
        assert backend.entered.wait(2)
        rotation.cancel()
        assert (
            rotation.current() is None and rotation.status()["phase"] == "disconnected"
        )
    finally:
        backend.release.set()
        worker.join(3)
    assert not worker.is_alive() and backend.events == ["stage", "exchange"]
    assert rotation.current() is None and rotation.status()["phase"] == "disconnected"


def t_expired_pending_credentials_rotate_again_instead_of_endless_storage_retry():
    backend = Backend()
    rotation = manager(backend)
    expired = [True]
    tokens = []

    def exchange(token):
        tokens.append(token)
        return dict(NEW) if token == OLD["refresh"] else {
            **NEW, "access": "fixture-access-third", "refresh": "fixture-refresh-third"
        }

    def validate(pair):
        return dict(META)

    def persist(pair, identity):
        if expired[0]:
            expired[0] = False
            raise tw.APIError("auth", "Pending credentials expired")

    rotation.exchange, rotation.validate, rotation.persist = exchange, validate, persist
    assert not rotation.step()
    assert rotation.status()["phase"] == "refreshing", (
        "Expired credentials were treated as a retryable disk error or a reconnect"
    )
    assert rotation.current()[0] == NEW, "The unspent new pair was not kept"
    assert rotation.step() is True
    assert tokens == [OLD["refresh"], NEW["refresh"]], tokens


def t_refused_or_unsent_exchange_keeps_the_unconsumed_pair_and_retries_later():
    for code in ("rate_limit", "unsent"):
        backend = Backend()
        backend.fail = tw.APIError(code, "fixture-private-token", True, 120)
        rotation = manager(backend)
        assert rotation.step() is False
        state = rotation.status()
        assert state["phase"] == "refreshing" and state["retryAfter"] == 120, state
        assert "retry" in state["error"].lower() and "fixture-private" not in str(state)
        assert rotation.current() == (OLD, META), (
            "A refresh token Twitch never processed was discarded"
        )
        backend.fail = ""
        assert rotation.step() is True and rotation.current() == (NEW, META)
        assert backend.events == [
            "stage",
            "exchange",
            "exchange",
            "validate",
            "persist",
        ], backend.events  # The durable refreshing phase is staged once.


def t_ambiguous_exchange_failures_retry_the_same_token_until_twitch_answers():
    for failure in (
        tw.APIError("temporary", "fixture-private-token", True, 60),
        TimeoutError("fixture-private-token"),
        ValueError("fixture-private-token"),
    ):
        backend = Backend()
        backend.fail = failure
        rotation = manager(backend)
        assert rotation.step() is False
        state = rotation.status()
        assert state["phase"] == "refreshing", (failure, state)
        assert 5 <= state["retryAfter"] <= 900, state
        assert "retry" in state["error"].lower() and "fixture-private" not in str(state)
        assert rotation.current() == (OLD, META), (
            "An ambiguous exchange failure discarded the pair instead of retrying"
        )
        backend.fail = ""
        assert rotation.step() is True and rotation.current() == (NEW, META)
        assert backend.events == [
            "stage",
            "exchange",
            "exchange",
            "validate",
            "persist",
        ], backend.events  # Same token retried; the phase is staged once.


def t_definitive_refresh_rejection_still_requires_reconnect():
    backend = Backend()
    backend.fail = tw.APIError("auth", "fixture-private-token")
    rotation = manager(backend)
    assert rotation.step() is False
    state = rotation.status()
    assert state["phase"] == "reconnect", state
    assert "Start Twitch authorization" in state["error"], (
        "The manual step does not name the Control Center button"
    )
    assert rotation.current() is None
    assert rotation.step() is False and backend.events.count("exchange") == 1


def t_resumed_rotation_does_not_stage_again():
    backend = Backend()
    backend.phase = "refreshing"  # Staged by the interrupted previous process.
    rotation = refresh.Rotation(
        OLD,
        META,
        stage=backend.stage,
        exchange=backend.exchange,
        validate=backend.validate,
        persist=backend.persist,
        staged=True,
    )
    assert rotation.step() is True and rotation.current() == (NEW, META)
    assert backend.events == ["exchange", "validate", "persist"], backend.events


def t_rejected_fresh_access_token_rotates_again_with_its_unspent_refresh_token():
    backend = Backend()
    seen, rejected = [], [True]

    def exchange(token):
        seen.append(token)
        assert backend.phase == "refreshing"
        if token == OLD["refresh"]:
            return dict(NEW)
        assert token == NEW["refresh"], token
        return {**NEW, "access": "fixture-access-third", "refresh": "fixture-refresh-third"}

    def validate(pair):
        if rejected[0]:
            rejected[0] = False
            raise tw.APIError("auth", "fixture-private-token")
        return dict(META)

    def persist(pair, identity):
        backend.events.append(("persist", pair["refresh"]))

    rotation = refresh.Rotation(
        OLD,
        META,
        stage=backend.stage,
        exchange=exchange,
        validate=validate,
        persist=persist,
    )
    assert rotation.step() is False
    state = rotation.status()
    assert state["phase"] == "refreshing", (
        "A rejected fresh access token required reconnect although its refresh token is unspent"
    )
    assert rotation.step() is True
    assert seen == [OLD["refresh"], NEW["refresh"]], seen
    assert backend.events == ["stage", ("persist", "fixture-refresh-third")]


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
