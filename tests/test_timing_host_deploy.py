#!/usr/bin/env python3
"""Deploying the league's timing host through the Cloudflare REST API (#1161).
Run: python3 tests/test_timing_host_deploy.py"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import timing_host_deploy as d  # noqa: E402

ACC, TOKEN = "acc123", "tok-SECRET-value"
WORKER, PAGE = b"export default {};", "<html>Live timing</html>"


class _Api:
    """Fake Cloudflare API: records every request, answers from a script."""

    def __init__(self, subdomain="league-sub", scripts=(), version=None, fail=None):
        self.calls, self.subdomain, self.scripts = [], subdomain, list(scripts)
        self.version, self.fail = version, fail

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body, headers or {}))
        if self.fail and self.fail in url:
            return 403, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}
        if url.endswith("/workers/subdomain"):
            if self.subdomain is None:
                return 404, {"success": False, "errors": [{"code": 10007, "message": "not found"}]}
            return 200, {"success": True, "result": {"subdomain": self.subdomain}}
        if url.endswith("/workers/scripts") and method == "GET":
            return 200, {"success": True, "result": [{"id": n, "migration_tag": t} for n, t in self.scripts]}
        if "/workers/scripts/" in url and url.endswith("/subdomain"):
            return 200, {"success": True, "result": {"enabled": True}}
        if "/workers/scripts/" in url and method == "PUT":
            return 200, {"success": True, "result": {"migration_tag": "v1"}}
        if url.endswith("/version"):
            return (200, self.version) if self.version else (404, None)
        raise AssertionError(f"unexpected {method} {url}")

    def upload(self):
        return [c for c in self.calls if c[0] == "PUT"]


def _meta(call):
    body, ctype = call[2], call[3]["Content-Type"]
    boundary = ctype.split("boundary=", 1)[1].encode()
    for part in body.split(b"--" + boundary):
        if b'name="metadata"' in part:
            return json.loads(part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
    raise AssertionError("no metadata part")


def _deploy(api, key="k" * 32):
    return d.deploy(ACC, TOKEN, "Demo League", WORKER, PAGE, key, request=api)


def t_missing_subdomain_explains_dashboard():
    r = _deploy(_Api(subdomain=None))
    assert not r["ok"] and "Workers & Pages" in r["error"] and "Subdomain" in r["error"], r
    r = _deploy(_Api(subdomain=""))
    assert not r["ok"] and "Subdomain" in r["error"], r


def t_first_deploy_sends_migration_and_bindings():
    api = _Api()
    r = _deploy(api)
    assert r["ok"], r
    meta = _meta(api.upload()[0])
    assert meta["main_module"] == "index.js" and meta["compatibility_date"], meta
    kinds = {b["name"]: b for b in meta["bindings"]}
    assert kinds["ROOMS"] == {"type": "durable_object_namespace", "name": "ROOMS", "class_name": "TimingRoom"}
    assert kinds["PUBLISH_KEY"] == {"type": "secret_text", "name": "PUBLISH_KEY", "text": "k" * 32}
    assert kinds["RACECAST_VERSION"]["type"] == "plain_text"
    assert kinds["RACECAST_VERSION"]["text"] == d.bundle_version(WORKER, PAGE)
    assert meta["migrations"] == {"new_tag": "v1", "new_sqlite_classes": ["TimingRoom"]}, meta


def t_update_deploy_sends_no_migration():
    api = _Api(scripts=[(d.script_name("Demo League"), "v1")])
    assert _deploy(api)["ok"]
    assert "migrations" not in _meta(api.upload()[0]), "a deployed tag v1 takes no new migration"


def t_upload_carries_three_modules():
    api = _Api()
    _deploy(api)
    body = api.upload()[0][2]
    for name in (b'filename="index.js"', b'filename="worker.js"', b'filename="page.js"'):
        assert name in body, name
    assert b"application/javascript+module" in body
    assert json.dumps(PAGE).encode() in body, "the page travels as a JS string module"


def t_deploy_enables_workers_dev_and_returns_url():
    api = _Api()
    r = _deploy(api)
    name = d.script_name("Demo League")
    assert r["url"] == f"https://{name}.league-sub.workers.dev", r
    enable = [c for c in api.calls if c[0] == "POST" and c[1].endswith(f"/scripts/{name}/subdomain")]
    assert enable and json.loads(enable[0][2]) == {"enabled": True, "previews_enabled": False}


def t_every_api_call_carries_the_token_header():
    api = _Api()
    _deploy(api)
    for method, url, _b, headers in api.calls:
        if url.startswith(d.API):
            assert headers.get("Authorization") == f"Bearer {TOKEN}", (method, url)


def t_auth_error_never_echoes_the_token():
    r = _deploy(_Api(fail="/workers/subdomain"))
    assert not r["ok"] and TOKEN not in r["error"], r
    assert "Workers Scripts" in r["error"], "names the permission to check"


def t_short_publish_key_is_refused():
    r = _deploy(_Api(), key="short")
    assert not r["ok"] and "publish key" in r["error"], r


def t_status_reports_update_needed():
    name = d.script_name("Demo League")
    api = _Api(scripts=[(name, "v1")], version=b"old")
    st = d.status(ACC, TOKEN, "Demo League", WORKER, PAGE, request=api)
    assert st["ok"] and st["deployed"] and not st["up_to_date"], st
    api = _Api(scripts=[(name, "v1")], version=d.bundle_version(WORKER, PAGE).encode())
    st = d.status(ACC, TOKEN, "Demo League", WORKER, PAGE, request=api)
    assert st["up_to_date"] and st["url"].endswith(".league-sub.workers.dev"), st


def t_default_request_keeps_version_bytes_raw():
    import io

    class _R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    saved = d.http_util.open_url
    d.http_util.open_url = lambda *a, **k: _R(b"1234567890123456")
    try:
        assert d._request("GET", "https://x/version") == (200, b"1234567890123456"), \
            "an all-digit version hash must not turn into a number"
    finally:
        d.http_util.open_url = saved


def t_status_without_deploy():
    st = d.status(ACC, TOKEN, "Demo League", WORKER, PAGE, request=_Api())
    assert st["ok"] and not st["deployed"] and st["url"] is None, st


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
