"""Bounded Twitch HTTP with fixture-only transport and safe diagnostics."""

import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "scripts"))
import audience_twitch as tw
import http_util

CLIENT = "fixtureclient123"
SECRET = "private-fixture-token"


class Response(io.BytesIO):
    pass


def t_transport_uses_fixed_https_no_redirect_headers_and_finite_bounds():
    calls = []

    def opener(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 5
        assert SECRET not in url
        return Response(b'{"data":[]}')

    result = tw.request(
        "viewers", CLIENT, access=SECRET, channel="123", open_url=opener
    )
    assert result == {"data": []} and len(calls) == 1, calls
    assert calls[0][1]["headers"]["Authorization"] == "Bearer " + SECRET
    assert tw.request("refresh", CLIENT, refresh=SECRET, open_url=opener) == {
        "data": []
    }
    assert SECRET.encode() in calls[-1][1]["data"] and calls[-1][1]["method"] == "POST"


def t_transport_bounds_success_and_errors_and_closes_error_streams():
    class Bounded(Response):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024 + 1, size
            return super().read(size)

    for data in (b"x" * (1024 * 1024 + 1), b"[]", b"bad", b"\xff"):
        try:
            tw.request(
                "viewers",
                CLIENT,
                access=SECRET,
                channel="123",
                open_url=lambda *a, data=data, **k: Bounded(data),
            )
        except tw.APIError as error:
            assert (
                error.transient
                and error.code == "temporary"
                and SECRET not in str(error)
            )
        else:
            raise AssertionError("Invalid or oversized response was accepted")
    body = Response(json.dumps({"message": SECRET}).encode())

    def failing(*args, **kwargs):
        raise http_util.HTTPError(
            "https://fixed.invalid/" + SECRET, 401, "reason " + SECRET, {}, body
        )

    try:
        tw.request("validate", CLIENT, access=SECRET, open_url=failing)
    except tw.APIError as error:
        assert error.code == "auth" and SECRET not in str(error), str(error)
    else:
        raise AssertionError("HTTP authentication failure accepted")
    assert body.closed, "HTTP error stream leaked"

    def broken(*args, **kwargs):
        raise OSError(SECRET)

    try:
        tw.request("device", CLIENT, open_url=broken)
    except tw.APIError as error:
        assert error.transient and SECRET not in str(error)
    else:
        raise AssertionError("Network error accepted")


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
