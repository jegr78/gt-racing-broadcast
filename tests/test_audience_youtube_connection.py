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
        self.starts, self.hook = {}, None
        # Per-video overrides: owning channel and ended broadcasts.
        self.channel_of, self.ended = {}, set()
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
        if self.hook:
            self.hook(resource, params)
        if resource == "videos":
            items = []
            for ident in params["id"].split(","):
                details = {
                    "actualStartTime": self.starts.get(ident, "start"),
                    "concurrentViewers": self.viewers,
                }
                if self.state == "ended" or ident in self.ended:
                    details["actualEndTime"] = "end"
                items.append(
                    {
                        "id": ident,
                        "snippet": {
                            "title": "Output race " + ident,
                            "channelId": self.channel_of.get(ident, CHANNEL),
                            "channelTitle": "Producer channel",
                            "liveBroadcastContent": "none"
                            if ident in self.ended
                            else self.state,
                        },
                        "statistics": {"likeCount": self.likes},
                        "liveStreamingDetails": details,
                    }
                )
            return {"items": items}
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
def fixture(**options):
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
            **options,
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
        assert persisted and persisted[1] == {"key": KEY}, (
            "Activated connection was not persisted"
        )
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
        command(service, "verify", video=OTHER_VIDEO, apiKey="")
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
        metrics = service.status().get("metrics", {})
        assert metrics.get("likes", {}).get("reason") == "Selected video denied", (
            "Channel success hid the video error"
        )
        assert metrics["subscribers"]["available"]
        api.failures["videos"] = yt.APIError("auth", "Invalid API key")
        clock.advance(30)
        service.tick()
        assert not any(metric(cache, name)["available"] for name in connection.METRICS)
        error = service.status()["error"]
        assert "Verify selected targets" in error and "Activate verified targets" in error, (
            "A rejected API key does not name the Control Center buttons"
        )
        calls = len(api.calls)
        clock.advance(899)
        service.tick()
        assert len(api.calls) == calls, "A rejected API key was retried too soon"
        # The producer fixes the key's restrictions in Google Cloud: the slow
        # re-probe recovers without any racecast action.
        del api.failures["videos"]
        clock.advance(1)
        service.tick()
        assert len(api.calls) > calls, "A rejected API key was never re-checked"
        assert metric(cache, "likes")["available"] and not service.status()["error"]


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


def t_response_processing_and_service_lock_wait_cannot_refresh_observation_age():
    with fixture() as (service, clock, _api, cache, _definitions):
        connect(service)
        observed, release = threading.Event(), threading.Event()
        original = yt.video_snapshot

        def delayed(payload, ident):
            result = original(payload, ident)
            observed.set()
            assert release.wait(3), "Fixture parser was not released"
            return result

        with patch.object(yt, "video_snapshot", side_effect=delayed):
            with service.lock:
                worker = threading.Thread(
                    target=service._poll,
                    args=(
                        "videos",
                        service.generation,
                        service.targets["video"],
                        service.key,
                    ),
                )
                worker.start()
                try:
                    assert observed.wait(2)
                    clock.advance(119)
                finally:
                    release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert metric(cache, "likes")["holdFor"] == 1, (
            "Response processing or lock wait extended the last-good deadline"
        )


def t_failed_durable_disconnect_warning_survives_status_reads_until_cleanup():
    import audience_connections as ac

    with fixture() as (service, _clock, _api, cache, _definitions):
        connect(service)
        writer = service.files.writer

        def fail_write(*_args, **_kwargs):
            raise OSError(KEY)

        service.files.writer = fail_write
        with patch.object(Path, "unlink", side_effect=OSError(KEY)):
            try:
                command(service, "disconnect")
            except ac.StorageError as error:
                assert not error.durable and KEY not in str(error)
            else:
                raise AssertionError(
                    "Failed durable disconnection was reported as saved"
                )
        assert (
            not service.status()["connected"]
            and not metric(cache, "likes")["available"]
        )
        assert "restart" in service.status()["error"].lower(), (
            "Settings hid the restart warning after failed storage"
        )
        service.files.writer = writer
        command(service, "disconnect")
        assert service.status()["error"] == "" and service.files.load() is None


def t_disconnecting_an_unconfigured_provider_restarts_without_a_target_error():
    with fixture() as (service, _clock, api, _cache, _definitions):
        command(service, "disconnect")
        restarted = connection.Service(
            service.files.public.parent, service.files.secret.parent, fetch=api
        )
        try:
            status = restarted.status()
            assert not status["connected"] and status["error"] == "", (
                "Empty disabled connection became an invalid target after restart"
            )
            restarted.tick()
            assert not api.calls
        finally:
            restarted.close()


LEAGUE = "@League"
CHANNEL_B = "UC" + "b" * 22
VID_A, VID_B = "vidAAAAAAAA", "vidBBBBBBBB"
STARTS = {VID_A: "2026-10-23T18:00:00Z", VID_B: "2026-10-23T19:00:00Z"}


class Sheet:
    """Fake Channel tab rows, live lookup and yt-dlp channel-ID resolution."""

    def __init__(self, rows=(LEAGUE,), loaded=True):
        self.rows = list(rows) if rows is not None else None
        self.live, self.ids, self.lookups = [], {}, []
        # None leaves the optional channels_loaded hook out (older callers).
        self.loaded = loaded
        self.error = None  # the Channel tab read error the relay reports
        self.stale = None  # the read error while cached rows stand in

    def channels_error(self):
        return self.error

    def channels_stale(self):
        return self.stale

    def channels_loaded(self):
        return self.loaded

    def channels(self):
        return list(self.rows) if self.rows is not None else None

    def live_lookup(self, text):
        self.lookups.append(text)
        return list(self.live) if self.live is not None else None

    def channel_id_lookup(self, text):
        return self.ids.get(text)

    def options(self):
        hooks = {
            "channels": self.channels,
            "live_lookup": self.live_lookup,
            "channel_id_lookup": self.channel_id_lookup,
        }
        if self.loaded is not None:
            hooks["channels_loaded"] = self.channels_loaded
            hooks["channels_error"] = self.channels_error
            hooks["channels_stale"] = self.channels_stale
        return hooks


@contextmanager
def follow_fixture(sheet=None):
    sheet = sheet or Sheet()
    with fixture(**sheet.options()) as (service, clock, api, cache, _definitions):
        api.starts.update(STARTS)
        yield service, clock, api, cache, sheet


def follow_connect(service, text=LEAGUE):
    result = command(service, "verify", mode="follow", followChannel=text, apiKey=KEY)
    service.tick()
    assert service.status()["setup"]["phase"] == "verified", service.status()
    command(service, "activate", attempt=result["attempt"])
    return result["attempt"]


def rejected(service, body):
    try:
        command(service, "verify", **body)
    except connection.UserError as error:
        return str(error)
    raise AssertionError("Follow verification accepted")


def t_follow_verify_pins_the_channel_id():
    import json

    with follow_fixture() as (service, _clock, api, _cache, sheet):
        sheet.live = [VID_A]
        command(service, "verify", mode="follow", followChannel=LEAGUE, apiKey=KEY)
        service.tick()
        setup = service.status()["setup"]
        assert setup["phase"] == "verified", setup
        assert setup["targets"]["follow"]["channelId"] == CHANNEL
        assert setup["live"] == {"id": VID_A, "title": "Output race " + VID_A}
        assert api.calls == [
            ("channels", {"forHandle": LEAGUE}, KEY),
            ("videos", {"id": VID_A}, KEY),
        ], api.calls
        sheet.live = []
        api.calls.clear()
        follow_connect(service)
        assert [c[0] for c in api.calls] == ["channels"], "Not live made a video call"
        public = json.loads(service.files.public.read_text(encoding="utf-8"))
        assert public["mode"] == "follow" and public["video"] is None
        assert public["follow"]["channelId"] == CHANNEL
        assert public["follow"]["channel"] == LEAGUE
        assert public["channel"]["id"] == CHANNEL
        assert KEY not in json.dumps(public)
        assert service.files.load()[1] == {"key": KEY}
        status = service.status()
        assert status["connected"] and status["mode"] == "follow"
        assert status["channels"] == [LEAGUE]
        assert status["follow"]["channelId"] == CHANNEL
        assert status["follow"]["state"] == "unknown"


def t_follow_verify_rejects_missing_tab_and_unknown_row():
    body = {"mode": "follow", "followChannel": LEAGUE, "apiKey": KEY}
    with follow_fixture(Sheet(rows=None)) as (service, _clock, api, _cache, _sheet):
        assert rejected(service, body) == "Follow mode needs the Sheet Channel tab"
        assert not api.calls and service.status()["setup"]["phase"] == "idle"
    with fixture() as (service, _clock, api, _cache, _definitions):
        assert rejected(service, body) == "Follow mode needs the Sheet Channel tab"
        assert service.status()["channels"] is None
    with follow_fixture() as (service, _clock, api, _cache, _sheet):
        assert (
            rejected(service, {**body, "followChannel": "@Other"})
            == "Choose a YouTube channel from the Sheet Channel tab"
        )
        service.tick()
        assert not api.calls


def t_follow_verify_resolves_custom_urls_via_yt_dlp():
    url = "https://www.youtube.com/c/league"
    sheet = Sheet(rows=[url])
    sheet.ids[url] = CHANNEL_B
    with follow_fixture(sheet) as (service, _clock, api, _cache, sheet):
        follow_connect(service, url)
        assert api.calls == [("channels", {"id": CHANNEL_B}, KEY)], api.calls
        assert service.status()["follow"]["channelId"] == CHANNEL_B
        assert service.status()["targets"]["channel"]["id"] == CHANNEL_B
        sheet.ids.clear()
        api.calls.clear()
        command(service, "verify", mode="follow", followChannel=url, apiKey="")
        service.tick()
        setup = service.status()["setup"]
        assert setup["phase"] == "failed", setup
        assert setup["error"] == "Use an @handle or channel ID in the Sheet Channel tab"
        assert not [c for c in api.calls if c[0] == "channels" and "forHandle" in c[1]]


def t_follow_cycle_switches_to_the_newer_live_video():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]
        api.likes = "12"
        api.calls.clear()
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "live" and follow["video"]["id"] == VID_A, follow
        assert cache.read("youtube", "likes").identity == "youtube:video:" + VID_A + ":likes"
        assert metric(cache, "likes")["value"] == 12
        assert ("channels", {"id": CHANNEL}, KEY) in api.calls
        seen = {}

        def hook(resource, params):
            if resource == "videos" and params["id"] == VID_B:
                seen["likes"] = metric(cache, "likes")

        api.hook = hook
        sheet.live = [VID_A, VID_B]
        api.calls.clear()
        clock.advance(30)
        service.tick()
        assert api.calls[0] == ("videos", {"id": VID_A + "," + VID_B}, KEY), api.calls
        assert not seen["likes"]["available"], "Old video's likes shown under new one"
        assert seen["likes"]["reason"] == "Waiting for YouTube metrics"
        follow = service.status()["follow"]
        assert follow["state"] == "live" and follow["video"] == {
            "id": VID_B,
            "title": "Output race " + VID_B,
        }
        assert cache.read("youtube", "likes").identity == "youtube:video:" + VID_B + ":likes"
        # The same single live video again needs no extra lookup call.
        sheet.live = [VID_B]
        api.calls.clear()
        clock.advance(30)
        service.tick()
        assert [c for c in api.calls if c[0] == "videos"] == [
            ("videos", {"id": VID_B}, KEY)
        ]


def t_follow_mode_lookup_failure_keeps_or_waits():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]
        service.tick()
        sheet.live = None
        api.calls.clear()
        clock.advance(30)
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "lookup-failed" and follow["video"]["id"] == VID_A
        assert api.calls[0] == ("videos", {"id": VID_A}, KEY), api.calls
        assert metric(cache, "likes")["available"]
    with follow_fixture() as (service, _clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = None
        api.calls.clear()
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "lookup-failed" and follow["video"] is None
        assert not [c for c in api.calls if c[0] == "videos"]
        assert not metric(cache, "likes")["available"]
        assert not metric(cache, "viewers")["available"]


def t_follow_cycle_not_live_and_ended_hide_video_metrics():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]
        service.tick()
        assert metric(cache, "likes")["available"]
        sheet.live = []
        api.calls.clear()
        clock.advance(30)
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "not-live" and follow["video"] is None
        for name in ("likes", "viewers"):
            assert not metric(cache, name)["available"]
            assert metric(cache, name)["reason"] == "Not live right now"
        assert not [c for c in api.calls if c[0] == "videos"]
        sheet.live = [VID_A]
        clock.advance(30)
        service.tick()
        assert service.status()["follow"]["state"] == "live"
        api.state = "ended"
        clock.advance(30)
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "not-live" and follow["video"] is None, follow
        for name in ("likes", "viewers"):
            assert not metric(cache, name)["available"]
            assert metric(cache, name)["reason"] == "Not live right now", metric(
                cache, name
            )


def t_follow_cycle_detects_a_changed_channel_tab():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]
        service.tick()
        sheet.rows = ["@Other"]
        api.calls.clear()
        lookups = len(sheet.lookups)
        clock.advance(60)
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "channel-changed" and follow["video"] is None
        assert metric(cache, "likes")["reason"] == "Channel tab changed; verify again"
        assert not metric(cache, "likes")["available"]
        assert api.calls == [("channels", {"id": CHANNEL}, KEY)], api.calls
        assert metric(cache, "subscribers")["available"]
        assert len(sheet.lookups) == lookups
        sheet.rows = None
        clock.advance(60)
        service.tick()
        assert service.status()["follow"]["state"] == "no-channel-tab"
        assert service.status()["channels"] is None
        assert (
            metric(cache, "viewers")["reason"] == "Follow mode needs the Sheet Channel tab"
        )


def t_follow_mode_survives_restart():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        restarted = connection.Service(
            service.files.public.parent,
            service.files.secret.parent,
            cache,
            definitions=lambda: [{"source": "youtube", "metric": "viewers"}],
            fetch=api,
            clock=clock.time,
            monotonic=clock.mono,
            **sheet.options(),
        )
        try:
            status = restarted.status()
            assert status["connected"] and status["mode"] == "follow"
            assert status["follow"]["channelId"] == CHANNEL
            sheet.live = [VID_A]
            restarted.tick()
            follow = restarted.status()["follow"]
            assert follow["state"] == "live" and follow["video"]["id"] == VID_A
        finally:
            restarted.close()


def t_follow_mode_without_key_needs_rekey():
    with follow_fixture() as (service, clock, api, _cache, sheet):
        follow_connect(service)
        service.files.secret.unlink()
        api.calls.clear()
        sheet.lookups.clear()
        restarted = connection.Service(
            service.files.public.parent,
            service.files.secret.parent,
            fetch=api,
            clock=clock.time,
            monotonic=clock.mono,
            **sheet.options(),
        )
        try:
            status = restarted.status()
            assert not status["connected"] and status["mode"] == "follow"
            assert connection.REKEY in status["error"], status["error"]
            sheet.live = [VID_A]
            restarted.tick()
            assert not api.calls and not sheet.lookups
        finally:
            restarted.close()


def restart(service, clock, api, cache, sheet, definitions=None):
    return connection.Service(
        service.files.public.parent,
        service.files.secret.parent,
        cache,
        definitions=definitions
        or (lambda: [{"source": "youtube", "metric": "likes"}]),
        fetch=api,
        clock=clock.time,
        monotonic=clock.mono,
        **sheet.options(),
    )


def t_follow_waits_for_the_first_channel_tab_load():
    with follow_fixture() as (service, clock, api, cache, sheet):
        follow_connect(service)
        # A relay start: the Channel tab has not been read yet.
        sheet.rows, sheet.loaded, sheet.live = [], False, [VID_A]
        restarted = restart(service, clock, api, cache, sheet)
        try:
            status = restarted.status()
            assert status["channelsLoaded"] is False and status["channels"] == []
            restarted.tick()
            follow = restarted.status()["follow"]
            assert follow["state"] == "live" and follow["video"]["id"] == VID_A, follow
            assert metric(cache, "likes")["available"], metric(cache, "likes")
            try:
                command(
                    restarted, "verify", mode="follow", followChannel=LEAGUE, apiKey=KEY
                )
            except connection.UserError as error:
                assert str(error) == connection.CHANNELS_LOADING, error
            else:
                raise AssertionError("Verify accepted before the Channel tab loaded")
            # Once loaded, a missing row is a real change.
            sheet.rows, sheet.loaded = ["@Other"], True
            clock.advance(30)
            restarted.tick()
            assert restarted.status()["channelsLoaded"] is True
            assert restarted.status()["follow"]["state"] == "channel-changed"
        finally:
            restarted.close()
    # A hook set without channels_loaded counts as loaded (older callers).
    with follow_fixture(Sheet(loaded=None)) as (service, _clock, _api, _cache, _s):
        assert service.status()["channelsLoaded"] is True


def t_follow_overlap_costs_one_videos_list():
    with follow_fixture() as (service, clock, api, _cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A, VID_B]
        api.calls.clear()
        for _ in range(4):
            service.tick()
            clock.advance(30)
        lookups = [c for c in api.calls if c[0] == "videos" and "," in c[1]["id"]]
        assert lookups == [("videos", {"id": VID_A + "," + VID_B}, KEY)], api.calls
        assert service.status()["follow"]["video"]["id"] == VID_B


def t_follow_ended_but_listed_video_costs_no_repeated_fetch():
    with follow_fixture() as (service, clock, api, _cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]
        service.tick()
        assert service.status()["follow"]["state"] == "live"
        # The video poll sees the end while yt-dlp still lists the video.
        api.ended.add(VID_A)
        clock.advance(30)
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "not-live" and follow["video"] is None, follow
        api.calls.clear()
        for _ in range(3):
            clock.advance(30)
            service.tick()
        assert not [c for c in api.calls if c[0] == "videos"], api.calls
        assert service.status()["follow"]["state"] == "not-live"
        # A new broadcast in the set is looked up once, without the ended one.
        sheet.live = [VID_A, VID_B]
        clock.advance(30)
        service.tick()
        assert service.status()["follow"]["video"]["id"] == VID_B
        assert not [c for c in api.calls if VID_A in c[1].get("id", "")], api.calls
    with follow_fixture() as (service, clock, api, _cache, sheet):
        # The follow lookup itself finds the listed video ended.
        follow_connect(service)
        api.ended.add(VID_A)
        sheet.live = [VID_A]
        api.calls.clear()
        for _ in range(3):
            service.tick()
            clock.advance(30)
        assert [c for c in api.calls if c[0] == "videos"] == [
            ("videos", {"id": VID_A}, KEY)
        ], api.calls
        assert service.status()["follow"]["state"] == "not-live"


def t_follow_fetch_success_clears_a_stale_error():
    sheet = Sheet()
    with fixture(**sheet.options()) as (service, clock, api, _cache, definitions):
        definitions[:] = [{"source": "youtube", "metric": "likes"}]
        follow_connect(service)
        sheet.live = [VID_A]
        api.failures["videos"] = yt.APIError("temporary", "Fixture outage", True, 30)
        service.tick()
        assert "Fixture outage" in service.status()["error"], service.status()
        del api.failures["videos"]
        api.ended.add(VID_A)  # Success with no live video: no metric poll follows.
        clock.advance(60)
        service.tick()
        status = service.status()
        assert status["follow"]["state"] == "not-live", status["follow"]
        assert status["error"] == "", status["error"]


def t_follow_ignores_live_videos_of_another_channel():
    with follow_fixture() as (service, clock, api, _cache, sheet):
        sheet.live = [VID_A, VID_B]
        api.channel_of[VID_B] = CHANNEL_B  # newer, but not the pinned channel
        result = command(
            service, "verify", mode="follow", followChannel=LEAGUE, apiKey=KEY
        )
        service.tick()
        setup = service.status()["setup"]
        assert setup["live"]["id"] == VID_A, setup
        command(service, "activate", attempt=result["attempt"])
        service.tick()
        assert service.status()["follow"]["video"]["id"] == VID_A
        sheet.live = [VID_B]  # only the foreign video is listed
        clock.advance(30)
        service.tick()
        assert service.status()["follow"]["state"] == "not-live"

def t_follow_reports_why_the_channel_tab_is_not_loaded():
    sheet = Sheet(rows=[], loaded=False)
    with follow_fixture(sheet) as (service, _clock, api, _cache, sheet):
        status = service.status()
        assert status["channelsLoaded"] is False and status["channelsError"] is None
        sheet.error = "HTTPError: HTTP Error 404: Not Found"
        status = service.status()
        assert status["channelsError"] == "HTTPError: HTTP Error 404: Not Found"
        message = rejected(
            service, {"mode": "follow", "followChannel": LEAGUE, "apiKey": KEY}
        )
        assert message == (
            "Could not read the Sheet Channel tab (HTTPError: HTTP Error 404: "
            "Not Found); retrying"
        ), message
        sheet.error = "x" * 1000 + "\n\x00tail"
        error = service.status()["channelsError"]
        assert len(error) <= 200 and "\n" not in error and "\x00" not in error, error
        # Loaded: an old read error is no longer reported.
        sheet.rows, sheet.loaded = [LEAGUE], True
        assert service.status()["channelsError"] is None
        assert not api.calls
    with fixture() as (service, _clock, _api, _cache, _definitions):
        assert service.status()["channelsError"] is None


def t_follow_commit_drops_a_video_the_poll_saw_end():
    with follow_fixture() as (service, _clock, api, cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A]

        def ended_meanwhile(resource, params):
            # The video poll marks VID_A ended while this follow lookup runs.
            if resource == "videos":
                with service.lock:
                    service.follow_dead.add(VID_A)

        api.hook = ended_meanwhile
        api.calls.clear()
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "not-live" and follow["video"] is None, follow
        assert metric(cache, "likes")["reason"] == "Not live right now"
        assert [c for c in api.calls if c[0] == "videos"] == [
            ("videos", {"id": VID_A}, KEY)
        ], api.calls
    with follow_fixture() as (service, _clock, _api, _cache, _sheet):
        with service.lock:
            service.follow_dead.add(VID_A)
            service._follow_set("live", {"id": VID_A, "title": "Old"})
            assert service.follow_state == "not-live" and service.follow_video is None


def t_follow_verify_survives_a_failing_live_lookup():
    def broken(_text):
        raise RuntimeError("yt-dlp exploded")

    with follow_fixture() as (service, _clock, api, _cache, _sheet):
        service.live_lookup = broken
        command(service, "verify", mode="follow", followChannel=LEAGUE, apiKey=KEY)
        service.tick()
        setup = service.status()["setup"]
        assert setup["phase"] == "verified", setup
        assert setup["live"] is None and setup["error"] == "", setup
        assert setup["targets"]["follow"]["channelId"] == CHANNEL
        assert [c[0] for c in api.calls] == ["channels"], api.calls

def t_follow_reports_a_cached_channel_tab():
    with follow_fixture() as (service, _clock, _api, _cache, sheet):
        assert service.status()["channelsStale"] is None
        sheet.stale = "HTTPError: HTTP Error 404: Not Found\n"
        status = service.status()
        assert status["channelsLoaded"] is True and status["channelsError"] is None
        assert status["channelsStale"] == "HTTPError: HTTP Error 404: Not Found"
    with fixture() as (service, _clock, _api, _cache, _definitions):
        assert service.status()["channelsStale"] is None


def t_follow_dropped_dead_pick_reresolves_at_once():
    with follow_fixture() as (service, _clock, api, _cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A, VID_B]

        def ended_meanwhile(resource, params):
            # The overlap lookup picks the newer VID_B; its poll ends it meanwhile.
            if resource == "videos" and "," in params["id"]:
                with service.lock:
                    service.follow_dead.add(VID_B)

        api.hook = ended_meanwhile
        api.calls.clear()
        service.tick()
        assert service.status()["follow"]["state"] == "not-live"
        service.tick()   # no 30 s wait: the next tick resolves the rest of the set
        follow = service.status()["follow"]
        assert follow["state"] == "live" and follow["video"]["id"] == VID_A, follow
        for _ in range(3):
            service.tick()
        videos = [c[1]["id"] for c in api.calls if c[0] == "videos"]
        assert videos[:2] == [VID_A + "," + VID_B, VID_A], videos
        assert videos.count(VID_A + "," + VID_B) == 1 and VID_A in videos[1:], videos
        assert all(v == VID_A for v in videos[1:]), videos


def t_follow_poll_end_in_an_overlap_reresolves_at_once():
    with follow_fixture() as (service, clock, api, _cache, sheet):
        follow_connect(service)
        sheet.live = [VID_A, VID_B]
        service.tick()
        assert service.status()["follow"]["video"]["id"] == VID_B
        api.ended.add(VID_B)   # the video poll sees the newer stream end
        clock.advance(30)
        service.tick()
        assert service.status()["follow"]["state"] == "not-live"
        api.calls.clear()
        service.tick()
        follow = service.status()["follow"]
        assert follow["state"] == "live" and follow["video"]["id"] == VID_A, follow
        lists = [c for c in api.calls if c[0] == "videos" and c[1]["id"] == VID_A]
        assert len(lists) == 2, api.calls   # one follow videos.list + its metric poll
    with follow_fixture() as (service, clock, api, _cache, sheet):
        # Nothing else live: the immediate re-check spends no videos.list.
        follow_connect(service)
        sheet.live = [VID_A]
        service.tick()
        api.ended.add(VID_A)
        clock.advance(30)
        service.tick()
        api.calls.clear()
        service.tick()
        service.tick()
        assert not [c for c in api.calls if c[0] == "videos"], api.calls
        assert service.status()["follow"]["state"] == "not-live"


def t_video_mode_unchanged():
    import json

    with fixture() as (service, _clock, _api, cache, _definitions):
        connect(service)
        public = json.loads(service.files.public.read_text(encoding="utf-8"))
        assert "mode" not in public and "follow" not in public
        status = service.status()
        assert status["mode"] == "video" and status["channels"] is None
        assert status["follow"] == {
            "channel": None,
            "channelId": None,
            "state": "unknown",
            "video": None,
        }
        service.tick()
        assert cache.read("youtube", "likes").identity == "youtube:video:" + VIDEO + ":likes"


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
