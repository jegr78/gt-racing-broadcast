"""Device approval is bounded, private, cancellable and separate from activation."""

from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_twitch as tw
import audience_twitch_device as device

CLIENT = "fixtureclientid"
PRIVATE = "fixture-private-device-code"
USER = "ABCDEFGH"
ACCESS = "fixture-access-token"
REFRESH = "fixture-refresh-token"


class API:
    def __init__(self):
        self.calls = []
        self.answers = ["pending", "slow_down", "approved"]
        self.owner = "12345"
        self.scopes = [tw.SCOPE]
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.validation_failure = False

    def __call__(self, operation, client, **values):
        assert client == CLIENT
        self.calls.append((operation, dict(values)))
        if operation == "device":
            return {
                "device_code": PRIVATE,
                "user_code": USER,
                "verification_uri": "https://www.twitch.tv/activate?public=true&device-code="
                + USER,
                "interval": 5,
                "expires_in": 1800,
            }
        if operation == "exchange":
            assert values == {"device": PRIVATE}
            if self.block:
                self.entered.set()
                assert self.release.wait(3), "Fixture exchange not released"
            answer = self.answers.pop(0)
            if answer in ("pending", "slow_down"):
                raise tw.APIError(answer, "fixture private error", True, 5)
            if answer == "lost":
                raise TimeoutError(PRIVATE)
            return {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "expires_in": 14400,
                "token_type": "bearer",
                "scope": self.scopes,
            }
        if operation == "validate":
            assert values == {"access": ACCESS}
            if self.validation_failure:
                raise tw.APIError("temporary", PRIVATE, True, 5)
            return {
                "client_id": CLIENT,
                "user_id": self.owner,
                "login": "authorized_account",
                "scopes": self.scopes,
                "expires_in": 14000,
            }
        if operation == "user":
            assert values == {"access": ACCESS, "target": {"login": "racing"}}
            return {
                "data": [
                    {"id": "12345", "login": "racing", "display_name": "Racing League"}
                ]
            }
        raise AssertionError("Unexpected provider operation")


def fixture(api=None):
    api = api or API()
    now = [0]
    attempt = device.Attempt(
        CLIENT, "https://twitch.tv/racing", fetch=api, monotonic=lambda: now[0]
    )
    return attempt, api, now


def approve(attempt, api, now):
    api.answers = ["approved"]
    attempt.step()
    now[0] = 5
    attempt.step()
    attempt.step()
    attempt.step()
    assert attempt.status()["phase"] == "verified", attempt.status()


def t_device_polling_obeys_pending_slow_down_and_only_public_status_is_exposed():
    attempt, api, now = fixture()
    assert not api.calls and attempt.status()["phase"] == "starting"
    attempt.step()
    assert [c[0] for c in api.calls] == ["device"]
    status = attempt.status()
    assert status["phase"] == "pending" and status["userCode"] == USER
    assert status["verificationUri"].endswith(
        "device-code=" + USER
    ) and PRIVATE not in str(status)
    now[0] = 4
    attempt.step()
    assert len(api.calls) == 1
    now[0] = 5
    attempt.step()
    assert len(api.calls) == 2 and attempt.status()["phase"] == "pending"
    now[0] = 9
    attempt.step()
    assert len(api.calls) == 2
    now[0] = 10
    attempt.step()
    assert len(api.calls) == 3
    now[0] = 19
    attempt.step()
    assert len(api.calls) == 3
    now[0] = 20
    attempt.step()
    assert attempt.status()["phase"] == "validating"
    attempt.step()
    attempt.step()
    status = attempt.status()
    assert (
        status["phase"] == "verified" and status["target"]["title"] == "Racing League"
    )
    assert status["account"]["id"] == "12345" and status["permissions"] == {
        "viewers": True,
        "subscriptions": True,
    }
    assert all(secret not in str(status) for secret in (PRIVATE, ACCESS, REFRESH))
    pair, identity, target = attempt.approved()
    assert (
        pair["refresh"] == REFRESH
        and identity["id"] == "12345"
        and target["id"] == "12345"
    )
    now[0] = 320
    assert attempt.status()["phase"] == "expired"
    try:
        attempt.approved()
    except ValueError:
        pass  # An expired candidate cannot be activated.
    else:
        raise AssertionError("Expired device candidate accepted")


def t_missing_scope_or_wrong_account_keeps_viewer_permission_usable():
    for owner, scopes in (("999", [tw.SCOPE]), ("12345", [])):
        api = API()
        api.owner, api.scopes = owner, scopes
        attempt, api, now = fixture(api)
        approve(attempt, api, now)
        assert attempt.status()["permissions"] == {
            "viewers": True,
            "subscriptions": False,
        }


def t_validation_retry_never_reexchanges_the_consumed_device_code():
    attempt, api, now = fixture()
    api.answers = ["approved"]
    api.validation_failure = True
    attempt.step()
    now[0] = 5
    attempt.step()
    attempt.step()
    assert attempt.status()["phase"] == "validating"
    assert [c[0] for c in api.calls].count("exchange") == 1
    api.validation_failure = False
    now[0] = 10
    attempt.step()
    attempt.step()
    assert attempt.status()["phase"] == "verified"
    assert [c[0] for c in api.calls].count("exchange") == 1


def t_cancel_during_exchange_discards_late_credentials_before_validation():
    attempt, api, now = fixture()
    api.answers = ["approved"]
    api.block = True
    attempt.step()
    now[0] = 5
    worker = threading.Thread(target=attempt.step)
    worker.start()
    try:
        assert api.entered.wait(2)
        attempt.cancel()
        assert attempt.status()["phase"] == "cancelled"
    finally:
        api.release.set()
        worker.join(3)
    assert not worker.is_alive() and [c[0] for c in api.calls] == ["device", "exchange"]
    assert attempt.status()["phase"] == "cancelled" and ACCESS not in str(
        attempt.status()
    )


def t_expired_or_ambiguous_device_exchange_is_not_polled_again():
    attempt, api, now = fixture()
    attempt.step()
    now[0] = 1800
    attempt.step()
    assert attempt.status()["phase"] == "expired" and len(api.calls) == 1
    attempt, api, now = fixture()
    api.answers = ["lost"]
    attempt.step()
    now[0] = 5
    attempt.step()
    assert attempt.status()["phase"] == "failed" and PRIVATE not in str(
        attempt.status()
    )
    now[0] = 10
    attempt.step()
    assert len(api.calls) == 2


def t_validation_transit_expiry_stops_before_resolving_the_channel():
    api = API()
    api.answers = ["approved"]
    now = [0]

    def fetch(operation, client, **values):
        result = api(operation, client, **values)
        if operation == "validate":
            result["expires_in"] = 1
            now[0] += 5
        return result

    attempt = device.Attempt(CLIENT, "racing", fetch=fetch, monotonic=lambda: now[0])
    attempt.step()
    now[0] = 5
    attempt.step()
    attempt.step()
    assert attempt.status()["phase"] == "expired", (
        "Validation transit extended an expired token into channel resolution"
    )
    attempt.step()
    assert [c[0] for c in api.calls] == ["device", "exchange", "validate"]


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
