"""The relay owns YouTube setup, polling and private connection generations."""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_widgets as aw
import audience_youtube as yt
import audience_youtube_connection as connection

VIDEO = "abcdefghijk"
OTHER_VIDEO = "ZYXWVUtsrqp"
CHANNEL = "UC" + "a" * 22
KEY = "fixture-youtube-local-api-key"


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


class API:
    def __init__(self):
        self.calls = []
        self.likes, self.viewers, self.subscribers = "0", "0", "29500"
        self.hidden, self.state, self.failures = False, "live", {}
        self.block, self.entered, self.release = (
            False,
            threading.Event(),
            threading.Event(),
        )

    def __call__(self, resource, params, key):
        self.calls.append((resource, dict(params), key))
        if resource == "videos" and self.block:
            self.entered.set()
            assert self.release.wait(3), "Fixture API request was not released"
        if resource in self.failures:
            raise self.failures[resource]
        if resource == "videos":
            details = {"actualStartTime": "start", "concurrentViewers": self.viewers}
            if self.state == "ended":
                details["actualEndTime"] = "end"
            return {
                "items": [
                    {
                        "id": params["id"],
                        "snippet": {
                            "title": "Output race " + params["id"],
                            "channelId": CHANNEL,
                            "channelTitle": "Producer channel",
                            "liveBroadcastContent": self.state,
                        },
                        "statistics": {"likeCount": self.likes},
                        "liveStreamingDetails": details,
                    }
                ]
            }
        return {
            "items": [
                {
                    "id": params.get("id", CHANNEL),
                    "snippet": {"title": "Producer channel"},
                    "statistics": {
                        "subscriberCount": self.subscribers,
                        "hiddenSubscriberCount": self.hidden,
                    },
                }
            ]
        }


@contextmanager
def fixture():
    with (
        tempfile.TemporaryDirectory() as td,
        patch.object(
            yt.http_util,
            "open_url",
            side_effect=AssertionError("Fixture must not call a provider"),
        ),
    ):
        root, clock, api = Path(td), Clock(), API()
        profile, runtime = root / "profiles/demo", root / "runtime/demo"
        profile.mkdir(parents=True)
        definitions = [
            {"source": "youtube", "metric": "likes"},
            {"source": "youtube", "metric": "subscribers"},
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


def connect(service, video=VIDEO, channel=CHANNEL):
    result = command(service, "verify", video=video, channel=channel, apiKey=KEY)
    assert result["ok"] and result["attempt"]
    service.tick()
    assert service.status()["setup"]["phase"] == "verified", service.status()
    command(service, "activate", attempt=result["attempt"])
    return result["attempt"]


def metric(cache, metric):
    return aw.normalize(
        {
            "id": "audience-" + "a" * 32,
            "source": "youtube",
            "metric": metric,
            "mode": "total",
            "start": 0,
            "target": None,
        },
        cache.read("youtube", metric),
        now=1000,
    )


def t_verify_is_pending_then_requires_explicit_activation_and_never_echoes_key():
    with fixture() as (service, _clock, api, cache, _definitions):
        pending = command(
            service,
            "verify",
            video="https://youtu.be/" + VIDEO,
            channel="@Producer",
            apiKey=KEY,
        )
        assert pending.get("ok") is True, pending
        assert not api.calls and not service.status()["connected"]
        service.tick()
        status = service.status()
        assert status["setup"]["phase"] == "verified" and not status["connected"], (
            status
        )
        assert status["setup"]["targets"]["video"]["id"] == VIDEO
        assert status["setup"]["targets"]["channel"]["id"] == CHANNEL
        assert KEY not in str(status) and service.files.load() is None
        command(service, "activate", attempt=pending["attempt"])
        assert service.status()["connected"] and len(api.calls) == 2
        persisted = service.files.load()
        assert persisted and persisted[1] == {"key": KEY}, "Activated connection was not persisted"
        service.tick()
        assert metric(cache, "likes")["value"] == 0
        subs = metric(cache, "subscribers")
        assert subs["value"] == 29500 and subs["precision"] == {
            "exact": False,
            "significantDigits": 3,
        }
        calls = len(api.calls)
        for _ in range(4):
            service.status()
            service.sample("likes")
        assert len(api.calls) == calls, "Cache reads multiplied external requests"


def t_polling_is_bounded_demanded_and_disconnect_is_immediate():
    with fixture() as (service, clock, api, cache, definitions):
        connect(service)
        api.calls.clear()
        service.tick()
        assert [r[0] for r in api.calls] == ["videos", "channels"]
        clock.advance(29)
        service.tick()
        assert len(api.calls) == 2
        clock.advance(1)
        service.tick()
        assert [r[0] for r in api.calls] == ["videos", "channels", "videos"]
        clock.advance(30)
        service.tick()
        assert [r[0] for r in api.calls] == [
            "videos",
            "channels",
            "videos",
            "videos",
            "channels",
        ]
        definitions.clear()
        clock.advance(60)
        service.tick()
        assert len(api.calls) == 5
        command(service, "disconnect")
        assert not metric(cache, "likes")["available"]
        assert service.files.load() is None and not service.files.secret.exists()
        clock.advance(1000)
        service.tick()
        assert len(api.calls) == 5


def t_ended_viewers_and_hidden_subscribers_never_become_zero():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service)
        service.tick()
        assert metric(cache, "viewers")["value"] == 0
        api.state, api.viewers, api.likes = "ended", "123", "761"
        clock.advance(30)
        service.tick()
        assert service.status()["videoState"] == "ended"
        assert not metric(cache, "viewers")["available"]
        assert metric(cache, "likes")["value"] == 761
        api.hidden = True
        clock.advance(30)
        service.tick()
        assert not metric(cache, "subscribers")["available"]
        assert metric(cache, "subscribers")["value"] is None


def t_quota_backoff_keeps_only_bounded_last_good_values():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service)
        service.tick()
        api.failures["videos"] = yt.APIError("quota", "Quota exhausted", True, 900)
        clock.advance(30)
        service.tick()
        count = len(api.calls)
        assert (
            metric(cache, "likes")["available"]
            and metric(cache, "likes")["holdFor"] == 90
        )
        clock.wall = -5000
        clock.ticks = 120
        service.tick()
        assert not metric(cache, "likes")["available"] and len(api.calls) == count
        clock.ticks = 929
        service.tick()
        assert len(api.calls) == count
        api.failures.clear()
        clock.ticks = 930
        service.tick()
        assert len(api.calls) > count and metric(cache, "likes")["available"]


def t_disconnect_during_verification_drops_late_results_and_credentials():
    with fixture() as (service, _clock, api, cache, _definitions):
        api.block = True
        command(service, "verify", video=VIDEO, channel=CHANNEL, apiKey=KEY)
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            command(service, "disconnect")
            assert (
                not service.status()["connected"]
                and not metric(cache, "likes")["available"]
            )
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert not service.files.secret.exists() and service.files.load() is None
        assert len(api.calls) == 1, (
            "A canceled verify continued fetching another target"
        )


def t_failed_activation_preserves_current_connection_and_retries_without_api():
    import audience_connections as ac

    with fixture() as (service, _clock, api, _cache, _definitions):
        connect(service)
        pending = command(
            service, "verify", video=OTHER_VIDEO, channel=CHANNEL, apiKey=""
        )
        service.tick()
        calls = len(api.calls)
        original = service.files.writer

        def fail_public(path, body, private=False):
            if path == service.files.public:
                raise OSError("fixture")
            return original(path, body, private=private)

        service.files.writer = fail_public
        try:
            command(service, "activate", attempt=pending["attempt"])
        except ac.StorageError:
            pass  # Failed target commit must preserve the usable old RAM connection.
        else:
            raise AssertionError("Failed activation accepted")
        assert (
            service.status()["connected"]
            and service.status()["targets"]["video"]["id"] == VIDEO
        )
        assert service.status()["setup"]["phase"] == "verified"
        service.files.writer = original
        command(service, "activate", attempt=pending["attempt"])
        assert service.status()["targets"]["video"]["id"] == OTHER_VIDEO
        assert len(api.calls) == calls, (
            "Persistence retry repeated verification requests"
        )


def t_candidate_expiry_cancel_profile_guard_and_closed_service():
    with fixture() as (service, clock, api, _cache, _definitions):
        connect(service)
        original = service.status()["targets"]
        pending = command(service, "verify", video=OTHER_VIDEO, apiKey="")
        command(service, "cancel")
        service.tick()
        assert service.status()["targets"] == original
        for action in ("disconnect", "verify"):
            try:
                service.command({"expectedProfile": "different", "action": action})
            except ValueError:
                pass  # Rejected state must leave the saved connection unchanged.
            else:
                raise AssertionError("Foreign profile command accepted")
        pending = command(service, "verify", video=OTHER_VIDEO, apiKey="")
        service.tick()
        clock.advance(300)
        try:
            command(service, "activate", attempt=pending["attempt"])
        except ValueError:
            pass  # The forbidden operation must reject before any write.
        else:
            raise AssertionError("Expired candidate activated")
        assert service.status()["targets"] == original
        service.close()
        try:
            command(service, "verify", video=VIDEO, apiKey=KEY)
        except ValueError:
            pass  # The forbidden operation must reject before any write.
        else:
            raise AssertionError("Stopped service retained a new credential")


def t_resource_denial_is_independent_but_auth_failure_invalidates_all_metrics():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service)
        service.tick()
        api.failures["videos"] = yt.APIError("denied", "Selected video denied")
        clock.advance(60)
        service.tick()
        assert not metric(cache, "likes")["available"]
        assert metric(cache, "subscribers")["available"]
        api.failures["videos"] = yt.APIError("auth", "Invalid API key")
        clock.advance(30)
        service.tick()
        assert not any(metric(cache, name)["available"] for name in connection.METRICS)
        calls = len(api.calls)
        clock.advance(900)
        service.tick()
        assert len(api.calls) == calls


def t_poll_disconnect_fences_late_success_and_restart_uses_only_paired_credentials():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service)
        service.tick()
        loaded = connection.Service(
            service.files.public.parent,
            service.files.secret.parent,
            fetch=api,
            clock=clock.time,
            monotonic=clock.mono,
        )
        try:
            assert loaded.status()["connected"]
        finally:
            loaded.close()
        api.block = True
        clock.advance(30)
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            command(service, "disconnect")
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert not any(metric(cache, name)["available"] for name in connection.METRICS)
        loaded = connection.Service(
            service.files.public.parent, service.files.secret.parent
        )
        try:
            assert not loaded.status()["connected"]
        finally:
            loaded.close()


def t_portable_targets_survive_look_and_import_requires_a_local_key():
    import backup_admin
    import profile_io
    import zipfile

    with fixture() as (service, _clock, api, _cache, _definitions):
        connect(service)
        profile = service.files.public.parent
        (profile / "profile.env").write_text("NAME=Demo Racing\n", encoding="utf-8")
        overlay = profile / "overlay"
        overlay.mkdir()
        (overlay / "hud.css").write_text("body {}", encoding="utf-8")
        sources = {
            "overlay": str(overlay),
            "backups": str(profile.parent.parent / "backups"),
        }
        look = backup_admin.create_backup("look", sources, "demo")
        connect(service, video=OTHER_VIDEO)
        backup_admin.restore_backup(look, sources)
        assert service.files.load()[0]["video"]["id"] == OTHER_VIDEO
        before = len(api.calls)
        bundle = profile_io.export_profile(
            "demo",
            {"profile_dir": str(profile)},
            True,
            str(profile.parent.parent / "bundle.zip"),
        )
        with zipfile.ZipFile(bundle) as archive:
            assert all(
                KEY.encode() not in archive.read(name) for name in archive.namelist()
            )
        destination = profile.parent.parent / "receiving"
        profile_io.import_profile(
            bundle,
            {
                "profiles_root": str(destination / "profiles"),
                "runtime_root": str(destination / "runtime"),
            },
        )
        imported = connection.Service(
            destination / "profiles/demo", destination / "runtime/demo", fetch=api
        )
        try:
            assert not imported.status()["connected"]
            assert (
                imported.status()["targets"]["video"]
                == service.status()["targets"]["video"]
            ), "Imported selection was lost instead of asking for a local key"
            imported.tick()
            assert len(api.calls) == before
        finally:
            imported.close()


def t_replacement_generation_discards_an_old_success_while_new_connection_is_active():
    with fixture() as (service, clock, api, cache, _definitions):
        connect(service)
        service.tick()
        candidate = command(
            service, "verify", video=OTHER_VIDEO, channel=CHANNEL, apiKey=""
        )
        service.tick()
        assert service.status()["setup"]["phase"] == "verified"
        api.block = True
        clock.advance(30)
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            command(service, "activate", attempt=candidate["attempt"])
            assert service.status()["connected"]
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert not metric(cache, "likes")["available"], (
            "Old target published after a new connection activated"
        )
        service.tick()
        assert OTHER_VIDEO in cache.read("youtube", "likes").identity


def t_superseded_verification_cannot_overwrite_the_new_candidate():
    with fixture() as (service, _clock, api, _cache, _definitions):
        api.block = True
        command(service, "verify", video=VIDEO, channel=CHANNEL, apiKey=KEY)
        worker = threading.Thread(target=service.tick)
        worker.start()
        try:
            assert api.entered.wait(2)
            current = command(
                service, "verify", video=OTHER_VIDEO, channel=CHANNEL, apiKey=KEY
            )
        finally:
            api.release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert service.status()["setup"]["phase"] == "pending", (
            "Superseded request overwrote the new attempt"
        )
        assert len(api.calls) == 1, "Superseded request fetched another target"
        service.tick()
        assert service.status()["setup"]["attempt"] == current["attempt"]
        assert service.status()["setup"]["targets"]["video"]["id"] == OTHER_VIDEO


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
