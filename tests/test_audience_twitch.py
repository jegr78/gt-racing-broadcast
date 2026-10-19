"""Explicit Twitch targets, OAuth metadata and metric semantics stay independent."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_twitch as tw

CLIENT = "fixtureclientid"
CHANNEL = "12345"
ACCESS = "fixture-access-token-one"
REFRESH = "fixture-refresh/%+=token"
DEVICE = "fixture-device-private-code"
USER_CODE = "ABCDEFGH"


def rejects(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return  # Invalid metadata must fail before publication.
    raise AssertionError("Invalid provider metadata was accepted")


def t_channel_selection_is_local_explicit_and_canonical():
    assert tw.channel_target(" Racing_League ") == {"login": "racing_league"}
    assert tw.channel_target("https://www.twitch.tv/Racing_League") == {
        "login": "racing_league"
    }
    assert tw.channel_target("twitch.tv/racing_league") == {"login": "racing_league"}
    assert tw.channel_target(CHANNEL) == {"id": CHANNEL}
    assert tw.channel_target("@12345") == {"login": "12345"}
    for value in (
        "https://evil.invalid/racing",
        "https://twitch.tv.evil.invalid/racing",
        "https://user@twitch.tv/racing",
        "https://twitch.tv:443/racing",
        "https://twitch.tv/popout/racing",
        "file:///tmp/racing",
        True,
        "",
    ):
        rejects(tw.channel_target, value)
    payload = {
        "data": [
            {"id": CHANNEL, "login": "racing_league", "display_name": "Racing League"}
        ]
    }
    assert tw.channel_snapshot(payload, {"login": "racing_league"}) == {
        "id": CHANNEL,
        "login": "racing_league",
        "title": "Racing League",
    }
    rejects(tw.channel_snapshot, payload, {"login": "other"})
    rejects(tw.channel_snapshot, {"data": []}, {"login": "racing_league"})


def t_current_live_viewers_distinguish_offline_missing_and_real_zero():
    row = {"user_id": CHANNEL, "type": "live", "viewer_count": 0}
    assert tw.stream_snapshot({"data": [row]}, CHANNEL) == ("live", 0)
    assert tw.stream_snapshot({"data": []}, CHANNEL) == ("offline", None)
    for value in (None, True, -1, "not a count", 1.5, float("nan")):
        assert (
            tw.stream_snapshot({"data": [{**row, "viewer_count": value}]}, CHANNEL)[1]
            is None
        )
    rejects(tw.stream_snapshot, {"data": [{**row, "user_id": "999"}]}, CHANNEL)


def t_active_subscriptions_use_total_rather_than_page_length_or_points():
    payload = {
        "data": [{"broadcaster_id": CHANNEL, "user_id": "777"}],
        "total": 761,
        "points": 2400,
        "pagination": {"cursor": "next"},
    }
    assert tw.subscriptions_snapshot(payload, CHANNEL) == 761
    assert (
        tw.subscriptions_snapshot({"data": [], "total": 0, "points": 0}, CHANNEL) == 0
    )
    assert tw.subscriptions_snapshot({"data": [], "points": 750}, CHANNEL) is None
    for total in (True, -1, 1.5, float("inf")):
        assert tw.subscriptions_snapshot({"data": [], "total": total}, CHANNEL) is None
    rejects(
        tw.subscriptions_snapshot,
        {**payload, "data": [{"broadcaster_id": "999"}]},
        CHANNEL,
    )


def t_validated_identity_and_granted_scopes_are_authoritative():
    data = {
        "client_id": CLIENT,
        "user_id": CHANNEL,
        "login": "racing_league",
        "scopes": ["channel:read:subscriptions"],
        "expires_in": 3600,
    }
    result = tw.validation_snapshot(data, CLIENT)
    assert result and result["id"] == CHANNEL and result["login"] == "racing_league"
    assert (
        result["scopes"] == ["channel:read:subscriptions"] and result["expires"] == 3600
    )
    assert tw.validation_snapshot({**data, "scopes": []}, CLIENT)["scopes"] == []
    for changes in (
        {"client_id": "other"},
        {"user_id": None},
        {"login": None},
        {"scopes": {}},
        {"expires_in": True},
        {"expires_in": 0},
    ):
        rejects(tw.validation_snapshot, {**data, **changes}, CLIENT)


def t_device_approval_exposes_only_the_validated_public_code_and_official_uri():
    url = "https://www.twitch.tv/activate?public=true&device-code=" + USER_CODE
    data = {
        "device_code": DEVICE,
        "user_code": USER_CODE,
        "verification_uri": url,
        "expires_in": 1800,
        "interval": 5,
    }
    parsed = tw.device_snapshot(data)
    assert parsed and parsed["uri"] == url and parsed["user"] == USER_CODE
    assert (
        parsed["device"] == DEVICE
        and parsed["expires"] == 1800
        and parsed["interval"] == 5
    )
    for uri in (
        "https://evil.invalid/activate",
        "https://www.twitch.tv.evil.invalid/activate",
        "https://user@www.twitch.tv/activate",
        "https://www.twitch.tv:443/activate",
        "https://www.twitch.tv/activate?public=true&device-code=" + DEVICE,
        url + "&extra=one",
        url + "#fragment",
    ):
        rejects(tw.device_snapshot, {**data, "verification_uri": uri})
    for changes in (
        {"interval": 0},
        {"expires_in": True},
        {"expires_in": 0},
        {"user_code": DEVICE},
    ):
        rejects(tw.device_snapshot, {**data, **changes})


def t_complete_opaque_token_pair_is_required_before_any_activation():
    data = {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_in": 14400,
        "token_type": "bearer",
        "scope": ["channel:read:subscriptions"],
    }
    result = tw.token_snapshot(data)
    assert (
        result
        and result["access"] == ACCESS
        and result["refresh"] == REFRESH
        and result["expires"] == 14400
    )
    for changes in (
        {"access_token": ""},
        {"refresh_token": None},
        {"access_token": "secret\r\ninjected"},
        {"expires_in": True},
        {"expires_in": 0},
        {"token_type": "wrong"},
    ):
        rejects(tw.token_snapshot, {**data, **changes})


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
