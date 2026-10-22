"""Private Twitch lifecycle, demand, permissions and one-use refresh fixtures."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "scripts"))
import audience_widgets as aw
import audience_twitch as tw
import audience_twitch_connection as connection

CLIENT = "fixtureclient123"
ACCESS = "fixture-private-access"
REFRESH = "fixture-private-refresh"
NEWACCESS = "fixture-private-new-access"
NEWREFRESH = "fixture-private-new-refresh"
PRIVATE = "fixture-private-device"
USER = "ABCDEFGH"


class Clock:
    def __init__(self):
        self.wall, self.ticks = 1000, 0

    def time(self):
        return self.wall

    def mono(self):
        return self.ticks

    def advance(self, seconds):
        self.wall += seconds
        self.ticks += seconds

    def sleep(self, seconds):
        # System sleep: wall time moves, CLOCK_MONOTONIC (macOS/Linux) does not.
        self.wall += seconds


class API:
    def __init__(self):
        self.calls = []
        self.owner = "12345"
        self.scopes = [tw.SCOPE]
        self.viewers = 0
        self.subscriptions = 761
        self.live = True
        self.failures = {}
        self.pending = False
        self.expires = 14400
        self.block = ""
        self.entered = threading.Event()
        self.release = threading.Event()
        self.consumed = set()

    def __call__(self, operation, client, **values):
        assert client == CLIENT
        self.calls.append((operation, dict(values)))
        if self.block == operation:
            self.entered.set()
            assert self.release.wait(3), "Fixture request was not released"
        if operation in self.failures:
            raise self.failures[operation]
        if operation == "device":
            return {
                "device_code": PRIVATE,
                "user_code": USER,
                "verification_uri": "https://www.twitch.tv/activate",
                "expires_in": 1800,
                "interval": 5,
            }
        if operation == "exchange":
            if self.pending:
                raise tw.APIError("pending", "Fixture pending", True)
            return {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "token_type": "bearer",
                "expires_in": self.expires,
            }
        if operation == "refresh":
            # Twitch answers a spent one-use refresh token with HTTP 400.
            if values["refresh"] in self.consumed:
                raise tw.APIError("auth", "Fixture consumed refresh token")
            self.consumed.add(values["refresh"])
            return {
                "access_token": NEWACCESS,
                "refresh_token": NEWREFRESH,
                "token_type": "bearer",
                "expires_in": 14400,
            }
        if operation == "validate":
            if values["access"] == ACCESS and self.expires <= 0:
                raise tw.APIError("auth", "Fixture expired")
            return {
                "client_id": CLIENT,
                "user_id": self.owner,
                "login": "racing",
                "scopes": self.scopes,
                "expires_in": max(1, self.expires)
                if values["access"] == ACCESS
                else 14400,
            }
        if operation == "user":
            return {
                "data": [
                    {"id": "12345", "login": "racing", "display_name": "Racing League"}
                ]
            }
        if operation == "viewers":
            return {
                "data": [
                    {"user_id": "12345", "type": "live", "viewer_count": self.viewers}
                ]
                if self.live
                else []
            }
        if operation == "subscriptions":
            return {
                "data": [{"broadcaster_id": "12345", "user_id": "7"}],
                "total": self.subscriptions,
                "points": 9999,
            }
        raise AssertionError(operation)


@contextmanager
def fixture():
    with (
        tempfile.TemporaryDirectory() as td,
        patch.object(
            tw.http_util,
            "open_url",
            side_effect=AssertionError("Fixture must never contact Twitch"),
        ),
    ):
        root = Path(td)
        profile = root / "profiles/demo"
        runtime = root / "runtime/demo"
        profile.mkdir(parents=True)
        clock, api = Clock(), API()
        definitions = [
            {"source": "twitch", "metric": metric} for metric in connection.METRICS
        ]
        cache = aw.MetricCache(clock=clock.time, monotonic=clock.mono)
        service = connection.Service(
            profile,
            runtime,
            cache,
            definitions=lambda: definitions,
            fetch=api,
            clock=clock.time,
            monotonic=clock.mono,
        )
        try:
            yield service, clock, api, cache, definitions
        finally:
            api.release.set()
            service.close()


def command(service, action, **fields):
    return service.command({"expectedProfile": "demo", "action": action, **fields})


def verify(service, clock):
    result = command(
        service, "verify", clientId=CLIENT, channel="https://twitch.tv/racing"
    )
    service.tick()
    clock.advance(5)
    service.tick()
    service.tick()
    service.tick()
    assert service.status()["setup"]["phase"] == "verified", service.status()
    return result["attempt"]


def connect(service, clock):
    attempt = verify(service, clock)
    command(service, "activate", attempt=attempt)
    return attempt


def metric(service, name):
    return aw.normalize(
        {"source": "twitch", "metric": name, "mode": "total"},
        service.sample(name),
        now=service.clock(),
    )


def t_verification_is_explicit_private_and_public_targets_are_portable():
    with fixture() as (service, clock, api, cache, definitions):
        assert not api.calls and service.thread is None
        attempt = verify(service, clock)
        assert not service.status()["connected"] and not service.files.secret.exists()
        status = str(service.status())
        assert all(secret not in status for secret in (ACCESS, REFRESH, PRIVATE)), (
            status
        )
        command(service, "activate", attempt=attempt)
        service.tick()
        assert metric(service, "viewers")["value"] == 0
        assert metric(service, "subscriptions")["value"] == 761
        public = service.files.public.read_text()
        assert CLIENT in public and "Racing League" in public
        assert all(secret not in public for secret in (ACCESS, REFRESH, PRIVATE))
        private = json.loads(service.files.secret.read_text())
        assert private["credentials"]["pair"]["refresh"] == REFRESH
        if sys.platform != "win32":
            assert service.files.secret.stat().st_mode & 0o777 == 0o600
        command(service, "disconnect")
        assert (
            not service.status()["connected"]
            and not metric(service, "viewers")["available"]
            and not service.files.secret.exists()
        )
        assert definitions and cache is service.cache


def t_missing_subscription_permission_keeps_viewers_and_never_requests_subscription_total():
    for owner, scopes in (("999", [tw.SCOPE]), ("12345", [])):
        with fixture() as (service, clock, api, _cache, _definitions):
            api.owner, api.scopes = owner, scopes
            connect(service, clock)
            service.tick()
            assert metric(service, "viewers")["available"]
            assert not metric(service, "subscriptions")["available"]
            assert not [call for call in api.calls if call[0] == "subscriptions"]
            assert service.status()["permissions"] == {
                "viewers": True,
                "subscriptions": False,
            }


def t_polling_is_central_demanded_and_distinguishes_live_zero_from_offline():
    with fixture() as (service, clock, api, _cache, definitions):
        connect(service, clock)
        service.tick()
        before = len(api.calls)
        for _ in range(20):
            service.status()
            service.sample("viewers")
            service.tick()
        assert len(api.calls) == before, (
            "Readers or frequent ticks repeated provider requests"
        )
        clock.advance(30)
        api.live = False
        service.tick()
        assert (
            not metric(service, "viewers")["available"]
            and service.status()["streamState"] == "offline"
        )
        api.live = True
        clock.advance(30)
        service.tick()
        assert metric(service, "viewers")["value"] == 0
        definitions.clear()
        clock.advance(60)
        before = len(api.calls)
        service.tick()
        assert len(api.calls) == before
        clock.advance(3600)
        service.tick()
        assert [call[0] for call in api.calls[before:]] == ["validate"], (
            "Maintained token was not validated hourly while unused"
        )


def t_rate_limits_hold_original_sample_age_then_hide_it_and_do_not_repeat_requests():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["viewers"] = tw.APIError(
            "rate_limit", "fixture-private-error", True, 90
        )
        clock.advance(30)
        service.tick()
        sample = metric(service, "viewers")
        assert sample["available"] and sample["availability"] == "stale"
        before = len(api.calls)
        clock.advance(89)
        service.tick()
        assert len(api.calls) == before
        clock.advance(1)
        assert not metric(service, "viewers")["available"], (
            "Stale sample survived its original deadline"
        )
        service.tick()
        assert "fixture-private" not in str(service.status())


def t_startup_validates_before_metrics_and_imported_or_interrupted_credentials_do_not_connect():
    with fixture() as (service, clock, api, _cache, definitions):
        connect(service, clock)

        def restart():
            return connection.Service(
                service.files.public.parent,
                service.files.secret.parent,
                definitions=lambda: definitions,
                fetch=api,
                clock=clock.time,
                monotonic=clock.mono,
            )

        before = len(api.calls)
        other = restart()
        try:
            assert not other.status()["connected"] and len(api.calls) == before
            other.tick()
            assert api.calls[before][0] == "validate" and other.status()["connected"]
        finally:
            other.close()
        secret = json.loads(service.files.secret.read_text())
        secret["credentials"] = {"phase": "refreshing"}
        service.files.secret.write_text(json.dumps(secret))
        other = restart()
        try:
            before = len(api.calls)
            other.tick()
            assert len(api.calls) == before and other.status()["phase"] == "reconnect"
        finally:
            other.close()
        service.files.secret.unlink()
        other = restart()
        try:
            assert (
                not other.status()["connected"]
                and other.status()["target"]["id"] == "12345"
            )
        finally:
            other.close()


def t_revoked_grant_reconnects_after_the_refresh_is_rejected_while_scope_changes_preserve_viewers():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.scopes = []
        service.validation_due = 0
        service.tick()
        assert (
            metric(service, "viewers")["available"]
            and not metric(service, "subscriptions")["available"]
        )
        api.failures["validate"] = tw.APIError("auth", "fixture-private-revoked")
        api.failures["refresh"] = tw.APIError("auth", "fixture-private-revoked")
        service.validation_due = 0
        service.tick()
        status = service.status()
        assert status["phase"] == "reconnect" and not status["connected"], status
        assert "Start Twitch authorization" in status["error"], status["error"]
        assert not metric(service, "viewers")["available"]
        assert len([call for call in api.calls if call[0] == "refresh"]) == 1, (
            "A 401 before local expiry did not try exactly one refresh"
        )


def t_expired_authorization_cannot_poll_during_validation_backoff():
    with fixture() as (service, clock, api, _cache, _definitions):
        api.expires = 40
        connect(service, clock)
        service.tick()
        clock.advance(30)
        api.failures["validate"] = tw.APIError("temporary", "fixture-private", True, 90)
        service.validation_due = 0
        service.tick()
        before = len([
            call for call in api.calls if call[0] in ("viewers", "subscriptions")
        ])
        clock.advance(40)
        service.tick()
        after = len([
            call for call in api.calls if call[0] in ("viewers", "subscriptions")
        ])
        assert after == before, (
            "Expired authorization continued metric requests during validation backoff"
        )
        assert (
            not service.status()["connected"]
            and not metric(service, "viewers")["available"]
        )


def t_refresh_stages_before_exchange_and_disk_retry_never_reuses_consumed_token():
    with fixture() as (service, clock, api, _cache, definitions):
        api.expires = 10
        connect(service, clock)
        original_fetch = service.fetch
        stages = []

        def fetch(operation, client, **values):
            if operation == "refresh":
                state = json.loads(service.files.secret.read_text())["credentials"]
                stages.append(state)
                assert state["phase"] == "refreshing", state
            return original_fetch(operation, client, **values)

        service.fetch = fetch
        original_writer = service.files.writer

        def failed_public(path, body, **kwargs):
            if Path(path) == service.files.public:
                raise OSError("fixture-private-disk-error")
            return original_writer(path, body, **kwargs)

        service.files.writer = failed_public
        api.expires = 0
        clock.advance(10)
        service.tick()
        service.tick()
        assert (
            [stage["phase"] for stage in stages] == ["refreshing"]
            and service.status()["phase"] == "storage_error"
        ), service.status()
        staged = service.files.load()[1]
        assert staged["phase"] == "refreshing" and staged["pair"]["refresh"] == REFRESH, (
            "Rollback did not restore the staged refreshing phase with its pair"
        )
        restarted = connection.Service(
            service.files.public.parent,
            service.files.secret.parent,
            definitions=lambda: definitions,
            fetch=api,
            clock=clock.time,
            monotonic=clock.mono,
        )
        try:
            before = len(api.calls)
            restarted.tick()
            # The restored pair's token was already spent: Twitch rejects the
            # resumed exchange definitively and only then is reconnect needed.
            assert [call[0] for call in api.calls[before:]] == ["refresh"]
            assert restarted.status()["phase"] == "reconnect"
        finally:
            restarted.close()
        service.files.writer = original_writer
        clock.advance(5)
        service.tick()
        assert service.status()["connected"] and service.pair["refresh"] == NEWREFRESH
        assert len([call for call in api.calls if call[0] == "refresh"]) == 2, (
            "Storage retry repeated consumed refresh"
        )
        assert service.files.load()[1]["pair"]["refresh"] == NEWREFRESH
        assert not service.status()["error"]


def t_failed_stage_never_exchanges_and_late_metric_after_disconnect_is_discarded():
    with fixture() as (service, clock, api, _cache, _definitions):
        api.expires = 10
        connect(service, clock)

        def failed_writer(*args, **kwargs):
            raise OSError("fixture-private-error")

        service.files.writer = failed_writer
        api.expires = 0
        clock.advance(10)
        service.tick()
        service.tick()
        assert service.status()["phase"] == "storage_error"
        assert not [call for call in api.calls if call[0] == "refresh"], (
            "Refresh happened after a failed phase write"
        )
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        api.block = "viewers"
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            command(service, "disconnect")
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive() and not metric(service, "viewers")["available"]
        assert not service.files.secret.exists() and not service.status()["connected"]


def t_poll_transit_expiry_never_publishes_or_starts_another_metric():
    with fixture() as (service, clock, api, cache, _definitions):
        api.expires = 10
        connect(service, clock)
        original = service.fetch

        def delayed(operation, client, **values):
            payload = original(operation, client, **values)
            if operation == "viewers":
                clock.advance(20)
            return payload

        service.fetch = delayed
        before = len(api.calls)
        published = []
        original_publish = cache.publish

        def record(source, name, sample):
            if source == "twitch" and sample.availability == "available":
                published.append((name, clock.mono()))
            return original_publish(source, name, sample)

        with patch.object(cache, "publish", side_effect=record):
            service.tick()
        assert not published, (
            "An available sample escaped briefly after expiry before a later gate invalidated it"
        )

        assert cache.read("twitch", "viewers").availability == "unavailable", (
            "Poll result was published after authorization expiry"
        )
        assert not [
            call for call in api.calls[before:] if call[0] == "subscriptions"
        ], "Second metric started with expired authorization"
        assert not service.status()["connected"]


def t_profile_bundle_contains_selection_and_baseline_without_local_credentials():
    import profile_io
    import zipfile

    with fixture() as (service, clock, _api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        profile = service.files.public.parent
        (profile / "profile.env").write_text(
            "NAME=Demo Racing\nKIND=endurance\n", encoding="utf-8"
        )
        store = aw.Store(profile / "audience.json")
        widget = store.create({
            "label": "Net active subscriptions",
            "source": "twitch",
            "metric": "subscriptions",
            "mode": "net",
        })
        store.capture(widget["id"], service.sample("subscriptions"))
        with tempfile.TemporaryDirectory() as td:
            bundle = profile_io.export_profile(
                "demo", {"profile_dir": str(profile)}, True, td
            )
            with zipfile.ZipFile(bundle) as archive:
                contents = b"".join(archive.read(name) for name in archive.namelist())
                assert all(
                    secret.encode() not in contents
                    for secret in (ACCESS, REFRESH, PRIVATE)
                ), "Export included Twitch credentials"
                assert b"clientId" in contents and b"Racing League" in contents
                assert b"baseline" in archive.read("profile/audience.json")
            imported = Path(td) / "imported"
            profile_io.import_profile(
                bundle,
                {
                    "profiles_root": str(imported / "profiles"),
                    "runtime_root": str(imported / "runtime"),
                },
            )
            other = connection.Service(
                imported / "profiles/demo",
                imported / "runtime/demo",
                fetch=service.fetch,
                clock=clock.time,
                monotonic=clock.mono,
            )
            try:
                assert (
                    not other.status()["connected"]
                    and other.status()["target"]["id"] == "12345"
                )
                assert "reconnect" in other.status()["error"].lower()
                restored = aw.Store(imported / "profiles/demo/audience.json").read()[
                    "widgets"
                ][0]
                assert restored["baseline"]["value"] == 761
            finally:
                other.close()


def t_metric_401_revalidates_and_reconnects_only_after_the_refresh_is_rejected():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service, clock)
        api.failures["viewers"] = tw.APIError("auth", "fixture-private")
        api.failures["validate"] = tw.APIError("auth", "fixture-private")
        api.failures["refresh"] = tw.APIError("auth", "fixture-private")
        before = len(api.calls)
        service.tick()
        assert service.status()["phase"] == "reconnect", (
            "Metric authorization failure was not immediately revalidated"
        )
        assert [call[0] for call in api.calls[before:]] == [
            "viewers",
            "validate",
            "refresh",
        ], api.calls[before:]
        assert all(
            cache.read("twitch", name).availability == "unavailable"
            for name in connection.METRICS
        )
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        api.failures["subscriptions"] = tw.APIError("auth", "fixture-private")
        api.scopes = []
        service.tick()
        assert service.status()["connected"] and metric(service, "viewers")["available"]
        assert (
            not metric(service, "subscriptions")["available"]
            and not service.status()["permissions"]["subscriptions"]
        )


def t_foreign_profile_commands_fail_before_setup_or_storage():
    with fixture() as (service, _clock, api, _cache, _definitions):
        for body in (
            {
                "action": "verify",
                "expectedProfile": "other",
                "clientId": CLIENT,
                "channel": "racing",
            },
            {"action": "disconnect"},
            [],
        ):
            try:
                service.command(body)
            except ValueError:
                pass  # Profile rejection must precede provider and storage work.
            else:
                raise AssertionError("Foreign profile command accepted")
        assert (
            not api.calls
            and service.setup is None
            and not service.files.secret.exists()
        )


def t_disconnect_during_refresh_cannot_recreate_private_files_after_exchange_returns():
    with fixture() as (service, clock, api, cache, _definitions):
        api.expires = 10
        connect(service, clock)
        api.expires = 0
        clock.advance(10)
        api.block = "refresh"
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            assert service.files.load()[1]["phase"] == "refreshing"
            command(service, "disconnect")
            assert not service.files.secret.exists()
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert not service.files.secret.exists(), (
            "Late refresh recreated disconnected credentials"
        )
        assert not service.files.read_public()["enabled"]
        assert (
            service.status()["phase"] == "disconnected"
            and not service.status()["connected"]
        )
        assert not [
            call
            for call in api.calls
            if call[0] == "validate" and call[1].get("access") == NEWACCESS
        ]
        assert cache.read("twitch", "viewers").availability == "unavailable"


def t_replaced_connection_drops_late_previous_generation_sample():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service, clock)
        api.block = "viewers"
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            candidate = command(service, "verify", clientId=CLIENT, channel="racing")
            setup = service.setup
            setup.step()
            clock.advance(5)
            setup.step()
            setup.step()
            setup.step()
            assert setup.status()["phase"] == "verified"
            command(service, "activate", attempt=candidate["attempt"])
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive() and service.status()["connected"]
        assert cache.read("twitch", "viewers").availability == "unavailable", (
            "Old generation overwrote the replacement connection cache"
        )


def t_rotated_token_validation_honors_retry_after_without_repeating_exchange():
    with fixture() as (service, clock, api, _cache, _definitions):
        api.expires = 10
        connect(service, clock)
        original = service.fetch
        limited = [True]

        def fetch(operation, client, **values):
            if operation == "validate" and values["access"] == NEWACCESS and limited[0]:
                api.calls.append((operation, dict(values)))
                raise tw.APIError("rate_limit", "fixture-private", True, 90)
            return original(operation, client, **values)

        service.fetch = fetch
        api.expires = 0
        clock.advance(10)
        service.tick()
        service.tick()
        assert service.status()["phase"] == "validating"
        before = len(api.calls)
        clock.advance(89)
        service.tick()
        assert len(api.calls) == before, (
            "Rotated token validation ignored provider Retry-After"
        )
        limited[0] = False
        clock.advance(1)
        service.tick()
        assert (
            service.status()["connected"]
            and len([call for call in api.calls if call[0] == "refresh"]) == 1
        )


def t_token_expiring_during_validation_can_refresh_after_reactive_401():
    with fixture() as (service, clock, api, _cache, _definitions):
        api.expires = 10
        connect(service, clock)
        original = service.fetch

        def delayed(operation, client, **values):
            if operation == "validate" and values["access"] == ACCESS:
                clock.advance(15)
                raise tw.APIError("auth", "Fixture token expired during validation")
            return original(operation, client, **values)

        service.fetch = delayed
        service.validation_due = 0
        service.tick()
        assert service.status()["phase"] != "reconnect", (
            "Expiry during validation incorrectly required manual reconnection"
        )
        service.tick()
        assert (
            service.status()["connected"]
            and len([call for call in api.calls if call[0] == "refresh"]) == 1
        )



def _near_expiry(service, clock, seconds):
    # Late in a token's life: validation would report the remaining lifetime.
    service.account["expiresAtMono"] = clock.mono() + seconds


def t_routine_rollover_rotates_before_expiry_without_blanking_samples():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        _near_expiry(service, clock, 400)
        clock.advance(1)
        service.tick()
        assert not [call for call in api.calls if call[0] == "refresh"], (
            "Refresh started long before the rollover window"
        )
        _near_expiry(service, clock, 290)
        for _ in range(3):
            clock.advance(1)
            service.tick()
            for name in connection.METRICS:
                assert metric(service, name)["available"], (
                    name + " blanked during a routine token rollover"
                )
        assert [call[1]["refresh"] for call in api.calls if call[0] == "refresh"] == [
            REFRESH
        ]
        assert service.status()["connected"] and service.pair["refresh"] == NEWREFRESH
        clock.advance(30)
        service.tick()
        assert ("viewers", {"access": NEWACCESS, "channel": "12345"}) in api.calls
        assert metric(service, "viewers")["availability"] == "available"


def t_rate_limited_refresh_retries_after_retry_after_with_the_unconsumed_token():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["refresh"] = tw.APIError(
            "rate_limit", "fixture-private-error", True, 120
        )
        _near_expiry(service, clock, 290)
        clock.advance(1)
        service.tick()
        service.tick()
        status = service.status()
        assert status["phase"] == "refreshing" and status["connected"], status
        assert "fixture-private" not in str(status)
        refreshes = len([call for call in api.calls if call[0] == "refresh"])
        assert refreshes == 1
        clock.advance(30)
        service.tick()
        assert metric(service, "viewers")["availability"] == "available", (
            "The still-valid pair stopped serving metrics while refresh waited"
        )
        clock.advance(88)
        service.tick()
        assert len([call for call in api.calls if call[0] == "refresh"]) == refreshes, (
            "Refresh retry ignored Retry-After"
        )
        del api.failures["refresh"]
        clock.advance(2)
        service.tick()
        assert service.status()["connected"] and service.pair["refresh"] == NEWREFRESH
        assert service.status()["phase"] == "ready"
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["refresh"] = tw.APIError(
            "temporary", "fixture-private-error", True, 60
        )
        _near_expiry(service, clock, 290)
        clock.advance(1)
        service.tick()
        service.tick()
        status = service.status()
        assert status["phase"] == "refreshing" and status["connected"], (
            "An ambiguous lost refresh response required reconnect instead of retrying"
        )
        del api.failures["refresh"]
        clock.advance(60)
        service.tick()
        assert service.status()["phase"] == "ready"
        assert [c[1]["refresh"] for c in api.calls if c[0] == "refresh"] == [
            REFRESH,
            REFRESH,
        ], "The retry did not reuse the same refresh token"


def t_metric_failure_message_clears_after_a_successful_poll():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["viewers"] = tw.APIError("temporary", "fixture", True, 30)
        clock.advance(30)
        service.tick()
        assert "metric" in service.status()["error"].lower()
        del api.failures["viewers"]
        clock.advance(30)
        service.tick()
        assert service.status()["error"] == "", service.status()["error"]


def t_metric_backoff_is_independent_per_metric():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["subscriptions"] = tw.APIError(
            "temporary", "fixture", True, 300
        )
        clock.advance(60)
        service.tick()
        before = len([call for call in api.calls if call[0] == "viewers"])
        clock.advance(30)
        service.tick()
        assert len([call for call in api.calls if call[0] == "viewers"]) == before + 1, (
            "A subscriptions failure paused viewer polling"
        )
        assert metric(service, "viewers")["availability"] == "available"


def _reject_old_access(service, api):
    """Twitch revokes the old access token early; everything else works."""
    original = service.fetch

    def fetch(operation, client, **values):
        if values.get("access") == ACCESS and operation != "refresh":
            api.calls.append((operation, dict(values)))
            raise tw.APIError("auth", "fixture-private-revoked-access")
        return original(operation, client, **values)

    service.fetch = fetch


def t_metric_401_before_expiry_refreshes_and_retries_the_call_without_reconnect():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        _reject_old_access(service, api)
        clock.advance(30)
        before = len(api.calls)
        service.tick()
        status = service.status()
        assert status["phase"] == "ready" and status["connected"], status
        assert not status["error"], status["error"]
        calls = [
            (call[0], call[1].get("access") or call[1].get("refresh"))
            for call in api.calls[before:]
        ]
        assert calls[:4] == [
            ("viewers", ACCESS),
            ("validate", ACCESS),
            ("refresh", REFRESH),
            ("validate", NEWACCESS),
        ], calls
        assert ("viewers", NEWACCESS) in calls, (
            "The rejected metric call was not retried with the refreshed token"
        )
        assert metric(service, "viewers")["available"]
        assert service.files.load()[1]["pair"]["refresh"] == NEWREFRESH


def t_wall_clock_jump_triggers_proactive_rotation_while_monotonic_stood_still():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        clock.sleep(14400 - 100)  # Asleep; only wall time moved.
        service.tick()
        assert [c[1]["refresh"] for c in api.calls if c[0] == "refresh"] == [
            REFRESH
        ], "Wake from sleep did not rotate the token that is near its wall expiry"
        assert service.status()["phase"] == "ready"
        assert service.pair["refresh"] == NEWREFRESH


def t_wall_clock_jump_past_expiry_stops_metrics_before_any_call():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        clock.sleep(14400 + 10)
        assert not service.status()["connected"], (
            "A token past its wall-clock expiry still looked valid after sleep"
        )
        assert not metric(service, "viewers")["available"]
        api.failures["refresh"] = tw.APIError("unsent", "fixture-private", True, 60)
        before = len(api.calls)
        service.tick()
        assert [call[0] for call in api.calls[before:]] == ["refresh"], (
            "An expired token was used for a metric call after sleep"
        )
        assert not service.status()["connected"]


def _restart(service, api, clock, definitions):
    return connection.Service(
        service.files.public.parent,
        service.files.secret.parent,
        definitions=lambda: definitions,
        fetch=api,
        clock=clock.time,
        monotonic=clock.mono,
    )


def t_restart_during_refresh_resumes_the_rotation_with_the_staged_pair():
    with fixture() as (service, clock, api, _cache, definitions):
        connect(service, clock)
        service.files.stage_refresh(service.connection_id)
        other = _restart(service, api, clock, definitions)
        try:
            assert other.status()["phase"] != "reconnect", other.status()
            before = len(api.calls)
            other.tick()
            status = other.status()
            assert status["phase"] == "ready" and status["connected"], status
            assert [c[0] for c in api.calls[before:]][:2] == ["refresh", "validate"], (
                "Resume did not exchange the staged refresh token first"
            )
            saved = other.files.load()[1]
            assert saved["phase"] == "ready" and saved["pair"]["refresh"] == NEWREFRESH
            assert metric(other, "viewers")["available"]
        finally:
            other.close()


def t_restart_with_old_format_staged_file_still_requires_reconnect():
    with fixture() as (service, clock, api, _cache, definitions):
        connect(service, clock)
        secret = json.loads(service.files.secret.read_text())
        secret["credentials"] = {"phase": "refreshing"}
        service.files.secret.write_text(json.dumps(secret))
        other = _restart(service, api, clock, definitions)
        try:
            before = len(api.calls)
            other.tick()
            status = other.status()
            assert len(api.calls) == before and status["phase"] == "reconnect"
            assert "Start Twitch authorization" in status["error"], status["error"]
        finally:
            other.close()


def t_resumed_refresh_never_serves_the_unvalidated_old_token_while_retrying():
    with fixture() as (service, clock, api, _cache, definitions):
        connect(service, clock)
        service.files.stage_refresh(service.connection_id)
        api.failures["refresh"] = tw.APIError("unsent", "fixture-private", True, 60)
        other = _restart(service, api, clock, definitions)
        try:
            before = len(api.calls)
            other.tick()
            status = other.status()
            assert status["phase"] == "refreshing" and not status["connected"], status
            assert [call[0] for call in api.calls[before:]] == ["refresh"], (
                "A resumed refresh used the unvalidated saved access token"
            )
        finally:
            other.close()


def t_token_rejected_while_a_refresh_waits_stops_polling_until_rotation_completes():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service, clock)
        service.tick()
        api.failures["refresh"] = tw.APIError("rate_limit", "fixture-private", True, 60)
        _near_expiry(service, clock, 290)
        clock.advance(1)
        service.tick()
        assert service.status()["connected"]  # The old token still serves.
        _reject_old_access(service, api)
        clock.advance(30)
        service.tick()
        assert not service.status()["connected"]
        before = len(api.calls)
        clock.advance(30)  # The refresh retry is rate limited again.
        service.tick()
        assert [call[0] for call in api.calls[before:]] == ["refresh"]
        clock.advance(30)
        service.tick()
        assert not [
            call for call in api.calls[before:] if call[0] in connection.METRICS
        ], "A token Twitch rejected served metrics again while the refresh waited"
        assert not service.status()["connected"]
        del api.failures["refresh"]
        clock.advance(60)
        service.tick()
        assert service.status()["connected"] and service.pair["refresh"] == NEWREFRESH


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
