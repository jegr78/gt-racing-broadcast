"""A local foreign port holder must never receive provider credentials."""

from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
from http_server import NumericThreadingHTTPServer as ThreadingHTTPServer
import audience_proxy as proxy


def t_identity_is_checked_before_secret_post_and_again_after_response():
    posts, state = (
        [],
        {"profile": "foreign", "protocol": "other-service", "provider": "youtube"},
    )

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The forbidden operation must reject before any write.

        def do_GET(self):
            body = json.dumps({"ok": True, **state}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps({
                "ok": True,
                **state,
                "profile": state.get("postProfile", state["profile"]),
            }).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = proxy.Client("demo", server.server_address[1])
        body = {
            "action": "verify",
            "expectedProfile": "demo",
            "apiKey": "fixture-local-key",
        }
        for metadata in (
            {"profile": "foreign", "protocol": "other-service"},
            {"profile": "demo", "protocol": "other-service"},
            {"profile": "foreign", "protocol": "racecast-audience-admin-v1"},
            {
                "profile": "demo",
                "protocol": "racecast-audience-admin-v1",
                "provider": "twitch",
            },
        ):
            state.update(provider="youtube")
            state.update(metadata)
            try:
                client.command(body)
            except ValueError:
                pass  # Rejected state must leave the saved connection unchanged.
            else:
                raise AssertionError("Foreign service accepted")
            assert not posts, (
                "Credential forwarded before profile/protocol verification"
            )
        state.update(
            profile="demo", protocol="racecast-audience-admin-v1", provider="youtube"
        )
        assert client.command(body)["ok"] and len(posts) == 1
        assert client.status()["profile"] == "demo"
        state["postProfile"] = "other"
        try:
            client.command(body)
        except ValueError:
            pass  # A swapped success response must not claim the write succeeded.
        else:
            raise AssertionError("Success response from another profile accepted")
    finally:
        server.shutdown()
        server.server_close()


def t_never_polled_sample_gets_the_normal_needs_fresh_value_rejection():
    from dataclasses import asdict
    import tempfile
    import audience_widgets as aw

    never_polled = asdict(aw.MetricCache().read("twitch", "subscriptions"))
    assert never_polled["age_seconds"] is None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Quiet fixture server.

        def do_GET(self):
            body = json.dumps({
                "ok": True,
                "protocol": "racecast-audience-admin-v1",
                "profile": "demo",
                "provider": "twitch",
                "sample": never_polled,
            }).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = proxy.Client("demo", server.server_address[1], provider="twitch")
        try:
            sample = client.sample("subscriptions")
        except ValueError as error:
            raise AssertionError(
                "Never-polled sample rejected as malformed: " + str(error)
            ) from None
        assert sample.availability != "available" and sample.age_seconds is None
        with tempfile.TemporaryDirectory() as td:
            store = aw.Store(Path(td) / "audience.json")
            goal = store.create({
                "label": "Active subscriptions",
                "source": "twitch",
                "metric": "subscriptions",
                "mode": "net",
            })
            try:
                store.capture(goal["id"], sample)
            except ValueError as error:
                assert str(error) == "Baseline needs a fresh, exact source value"
            else:
                raise AssertionError("Never-polled sample captured a baseline")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    t_identity_is_checked_before_secret_post_and_again_after_response()
    print("PASS provider proxy identity")
    t_never_polled_sample_gets_the_normal_needs_fresh_value_rejection()
    print("PASS never-polled provider sample")
