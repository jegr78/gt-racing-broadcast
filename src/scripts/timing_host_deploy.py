"""Deploy and check the league's timing host Worker through the Cloudflare REST API
(#1161). No Wrangler, no Node: one multipart upload of three ES modules (index.js,
worker.js, page.js), a SQLite Durable Object binding and the publish key as a secret.
The request shapes were checked against a Workers Free account (see the spec)."""
import hashlib
import json
import urllib.error
import uuid

import http_util
from timing_host import script_name

API = "https://api.cloudflare.com/client/v4/accounts"
COMPATIBILITY_DATE = "2025-11-01"
MIGRATION_TAG = "v1"
ROOM_CLASS = "TimingRoom"
MIN_KEY = 16
JS_MODULE = "application/javascript+module"
INDEX_MODULE = (b'import page from "./page.js";\n'
                b'import worker, { setPage, TimingRoom } from "./worker.js";\n'
                b"setPage(page);\nexport default worker;\nexport { TimingRoom };\n")
SUBDOMAIN_HELP = ("The Cloudflare account has no workers.dev subdomain yet. Set one once in the "
                  "Cloudflare dashboard: Workers & Pages, Account details, Subdomain.")
AUTH_HELP = ("Cloudflare refused the API token. It needs the account permissions "
             "Workers Scripts: Edit and Account Settings: Read.")


def bundle_version(worker_src, page_src):
    """What the deployed Worker reports at /version: a hash of its code and page."""
    h = hashlib.sha256(worker_src)
    h.update(page_src.encode("utf-8"))
    return h.hexdigest()[:16]


def page_module(page_src):
    return b"export default " + json.dumps(page_src).encode("utf-8") + b";\n"


def build_metadata(publish_key, version, first):
    meta = {"main_module": "index.js", "compatibility_date": COMPATIBILITY_DATE,
            "bindings": [{"type": "durable_object_namespace", "name": "ROOMS", "class_name": ROOM_CLASS},
                         {"type": "secret_text", "name": "PUBLISH_KEY", "text": publish_key},
                         {"type": "plain_text", "name": "RACECAST_VERSION", "text": version}]}
    if first:
        meta["migrations"] = {"new_tag": MIGRATION_TAG, "new_sqlite_classes": [ROOM_CLASS]}
    return meta


def multipart(metadata, parts):
    """(body, content type) for the upload: the metadata part, then one part per module."""
    boundary = "racecast" + uuid.uuid4().hex
    out = bytearray()

    def part(name, filename, ctype, data):
        fn = f'; filename="{filename}"' if filename else ""
        out.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"{fn}\r\n"
                    f"Content-Type: {ctype}\r\n\r\n").encode())
        out.extend(data)
        out.extend(b"\r\n")
    part("metadata", None, "application/json", json.dumps(metadata).encode())
    for name, data, ctype in parts:
        part(name, name, ctype, data)
    out.extend(f"--{boundary}--\r\n".encode())
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _request(method, url, body=None, headers=None):
    """(status, raw body bytes) without following redirects."""
    try:
        with http_util.open_url(url, data=body, headers=headers, method=method, timeout=60,
                                allow_redirects=False) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _CfError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _cf(request, token, method, path, body=None, ctype="application/json"):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": ctype}
    status, doc = request(method, API + path, body, headers)
    if isinstance(doc, bytes):
        try:
            doc = json.loads(doc or b"null")
        except ValueError:
            doc = None
    if status in (401, 403):
        raise _CfError(AUTH_HELP)
    if not isinstance(doc, dict) or not doc.get("success"):
        errors = (doc or {}).get("errors") if isinstance(doc, dict) else None
        text = "; ".join(str(e.get("message")) for e in errors or [] if isinstance(e, dict))
        raise _CfError(f"Cloudflare answered {status}: {text or 'no details'}", status)
    return doc.get("result")


def _subdomain(request, account_id, token):
    try:
        result = _cf(request, token, "GET", f"/{account_id}/workers/subdomain")
    except _CfError as e:
        if e.status == 404:       # the account has no workers.dev subdomain yet
            raise _CfError(SUBDOMAIN_HELP) from e
        raise
    sub = (result or {}).get("subdomain") or ""
    if not sub:
        raise _CfError(SUBDOMAIN_HELP)
    return sub


def _migration_tag(request, account_id, token, name):
    for script in _cf(request, token, "GET", f"/{account_id}/workers/scripts") or []:
        if isinstance(script, dict) and script.get("id") == name:
            return script.get("migration_tag") or ""
    return None


def deploy(account_id, token, profile, worker_src, page_src, publish_key, request=None):
    """Upload or update the league's Worker: {ok, url} or {ok: False, error}."""
    request = request or _request
    if not account_id or not token:
        return {"ok": False, "error": "Set TIMING_HOST_ACCOUNT_ID and TIMING_HOST_API_TOKEN first."}
    if not publish_key or len(publish_key) < MIN_KEY:
        return {"ok": False, "error": "The publish key is missing or too short."}
    name = script_name(profile)
    try:
        sub = _subdomain(request, account_id, token)
        first = _migration_tag(request, account_id, token, name) != MIGRATION_TAG
        body, ctype = multipart(
            build_metadata(publish_key, bundle_version(worker_src, page_src), first),
            [("index.js", INDEX_MODULE, JS_MODULE), ("worker.js", worker_src, JS_MODULE),
             ("page.js", page_module(page_src), JS_MODULE)])
        _cf(request, token, "PUT", f"/{account_id}/workers/scripts/{name}", body, ctype)
        _cf(request, token, "POST", f"/{account_id}/workers/scripts/{name}/subdomain",
            json.dumps({"enabled": True, "previews_enabled": False}).encode())
    except _CfError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Cloudflare is not reachable: {e}"}
    return {"ok": True, "url": f"https://{name}.{sub}.workers.dev"}


def status(account_id, token, profile, worker_src, page_src, request=None):
    """{ok, deployed, up_to_date, url} or {ok: False, error}."""
    request = request or _request
    if not account_id or not token:
        return {"ok": True, "deployed": False, "up_to_date": False, "url": None,
                "configured": False}
    name = script_name(profile)
    try:
        sub = _subdomain(request, account_id, token)
        if _migration_tag(request, account_id, token, name) is None:
            return {"ok": True, "configured": True, "deployed": False, "up_to_date": False, "url": None}
    except _CfError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Cloudflare is not reachable: {e}"}
    url = f"https://{name}.{sub}.workers.dev"
    try:
        code, body = request("GET", url + "/version", None, {})
    except OSError:
        code, body = 0, None
    deployed = body.decode("utf-8", "replace").strip() if code == 200 and isinstance(body, bytes) else None
    return {"ok": True, "configured": True, "deployed": True, "url": url,
            "up_to_date": deployed == bundle_version(worker_src, page_src)}
