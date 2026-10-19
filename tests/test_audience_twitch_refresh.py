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


def t_failed_stage_or_lost_response_never_reuses_a_consumed_refresh():
    backend = Backend()
    backend.fail = "stage"
    rotation = manager(backend)
    assert rotation.step() is False and backend.events == ["stage"]
    assert rotation.current() == (OLD, META) and backend.phase == "ready"
    backend.fail = "exchange"
    assert rotation.step() is False and rotation.status()["phase"] == "reconnect"
    assert backend.phase == "refreshing" and backend.events == [
        "stage",
        "stage",
        "exchange",
    ]
    assert rotation.step() is False and backend.events == ["stage", "stage", "exchange"]
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


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
