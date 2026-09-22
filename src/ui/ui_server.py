"""Control Center HTTP server: serves the static page, the JSON status API,
job control, and SSE streams (job output + service log tails) on localhost.
Same construction as the relay's control server (ThreadingHTTPServer +
make_handler closure). v1 binds 127.0.0.1 only; the bind/auth seams for the
v2 Tailscale+password feature are this module's serve() and _allowed().
Spec: docs/superpowers/specs/2026-06-07-control-center-design.md."""
import json, os, queue, shutil, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import logsetup   # re-open-per-poll log tailing (scripts/ on sys.path via racecast)
import http_util
import bundle_cache

APP_ID = "racecast-control-center"
DEFAULT_PORT = 8089
TAIL_LINES = 40          # how much history a log stream starts with
MAX_IMPORT_BYTES = 2 * 1024 * 1024 * 1024   # 2 GiB — profile bundles include media
MAX_FONT_BYTES = 8 * 1024 * 1024            # 8 MiB — an overlay font is tiny


def ui_port(env):
    """Port from RACECAST_UI_PORT (.env/environment), falling back to 8089."""
    try:
        return int(env.get("RACECAST_UI_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def classify_ping(body):
    """'ours' when a racecast Control Center answered the ping, else 'foreign'."""
    try:
        return "ours" if json.loads(body.decode()).get("app") == APP_ID else "foreign"
    except Exception:
        return "foreign"


def probe_instance(host, port, fetch=None):
    """Is something on host:port? 'free' (nothing), 'ours' (a running Control
    Center), 'foreign' (another app)."""
    fetch = fetch or _fetch_ping
    try:
        body = fetch(host, port)
    except Exception:
        # a foreign server that errors on /api/ping reads as 'free' — the
        # subsequent bind then fails with OSError and prints the RACECAST_UI_PORT hint
        return "free"
    return classify_ping(body)


def _fetch_ping(host, port):
    return http_util.get_bytes(f"http://{host}:{port}/api/ping", timeout=2)


def sse_frame(line):
    return f"data: {line}\n\n".encode("utf-8")


def sse_done(exit_code):
    return f"event: done\ndata: {exit_code}\n\n".encode("utf-8")


def _allowed(_handler):
    """Auth seam: always allowed in v1 (localhost-only bind is the boundary).
    v2 (Tailscale + RACECAST_UI_PASSWORD session cookie) changes only this."""
    return True


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _host_label(value):
    """The hostname part of a 'host[:port]' Host header (handles [::1]:port and
    a bare ::1); '' when absent."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith("["):                       # [::1]:8089 / [::1]
        end = v.find("]")
        return v[1:end] if end != -1 else v[1:]
    if v.count(":") == 1:                        # host:port
        return v.rsplit(":", 1)[0]
    return v                                     # bare host, or raw IPv6 (::1)


def request_csrf_ok(headers):
    """Reject cross-origin / DNS-rebind requests at the localhost trust boundary.
    The UI binds 127.0.0.1 only and has no auth, so a malicious web page the
    operator merely visits must not be able to drive the API (write .env, switch
    profile, run ops). A foreign Host header (DNS-rebinding away from a loopback
    name) or a cross-origin Origin/Referer (classic CSRF) is refused. Browsers
    always attach Origin to cross-origin POSTs; a non-browser client (no Origin)
    still has to carry a loopback Host. Pure — `headers` is any .get()-able map."""
    host = _host_label(headers.get("Host"))
    if host and host not in LOOPBACK_HOSTS:
        return False
    origin = headers.get("Origin") or headers.get("Referer")
    if origin:
        hostname = urlparse(origin).hostname
        if hostname is not None and hostname not in LOOPBACK_HOSTS:
            return False
    return True


def make_handler(ctx):
    """ctx: version, page_path, status() -> dict, relay_live() -> dict,
    obs_collection() -> dict, update_check(force) -> dict, streams_read() -> dict,
    streams_write(entries) -> dict, docs() -> dict,
    docs_content(key) -> (ctype, bytes)|None,
    docs_slides_serve(relpath) -> (path, ctype)|None (offline onboarding decks),
    ops {name: argv},
    build_argv(name, params) -> argv (raises ValueError), assets() -> dict,
    asset_files() -> dict, asset_roots() -> {kind: dir} (resolved live per call),
    tools() -> dict, apps() -> dict, preflight() -> dict, speedtest() -> dict,
    env_read() -> dict, env_write(entries) -> dict,
    devices_enumerate() -> dict (OBS video-device list for the solo capture
    input), devices_write(webcam, capture) -> dict (upsert into the .env),
    ps_discover() -> dict (GT7 console LAN discovery), ps_write(ip) -> dict
    (upsert the PS4/PS5 IP into the .env),
    init_plan(browser) -> dict (wizard plan: per-step done/kind/op/instruction),
    init_step(key) -> dict (run one non-job wizard step, {ok, done} | {ok: False, error}),
    profile_export(name, assets) -> dict, profile_import(path, force) -> dict,
    jobs (ui_jobs.JobManager), log_sources {name: {files, dir, archives, read}},
    favicon_path (the brand SVG served at /favicon.svg),
    shutdown() (installed by serve())."""

    # Read the bundled page ONCE, now, while the extraction dir is certain to
    # exist. Lazy caching would not save a page that nobody opens before the
    # OS temp-dir cleaner runs — and this server is meant to be left running.
    _pages = bundle_cache.BundleCache()
    _pages.prewarm([ctx["page_path"]])

    class Handler(BaseHTTPRequestHandler):
        _CTYPES = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp",
                   ".gif": "image/gif", ".svg": "image/svg+xml",
                   ".mp4": "video/mp4",
                   ".webm": "video/webm", ".mov": "video/quicktime",
                   ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
                   ".aac": "audio/aac", ".ogg": "audio/ogg",
                   ".oga": "audio/ogg", ".wav": "audio/wav",
                   ".flac": "audio/flac",
                   ".html": "text/html; charset=utf-8",
                   ".md": "text/plain; charset=utf-8",
                   ".txt": "text/plain; charset=utf-8"}

        def log_message(self, *args):
            pass                                  # quiet — one consumer, localhost

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self, what="not found"):
            self._json({"ok": False, "error": what}, code=404)

        def _sse_headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def _body_json(self):
            """Parsed JSON POST body; {} when absent/empty, None when malformed."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8")) or {}
            except Exception:
                return None

        def _serve_file(self, full):
            ctype = self._CTYPES.get(os.path.splitext(full)[1].lower(),
                                     "application/octet-stream")
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                return self._not_found("asset not found")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return None

        def _download_file(self, full, filename, cleanup=False):
            """Stream a file back as an attachment. Deletes it after (cleanup)."""
            try:
                size = os.path.getsize(full)
            except OSError:
                return self._not_found("bundle not found")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(full, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
            finally:
                if cleanup:
                    try:
                        os.unlink(full)
                    except OSError:  # best-effort temp cleanup
                        pass
            return None

        def _body_bytes(self, max_bytes):
            """Raw request body (<= max_bytes), or None when absent/oversized/short."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > max_bytes:
                return None
            data = self.rfile.read(length)
            return data if len(data) == length else None

        def _serve_bytes(self, data, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return None

        def _body_to_tempfile(self, max_bytes):
            """Stream the request body to a temp file in chunks. Returns the path,
            or None when there is no body, it exceeds max_bytes, or the client sent
            fewer bytes than Content-Length (a truncated upload — never handed on)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > max_bytes:
                return None
            fd, tmp = tempfile.mkstemp(prefix="upload-", suffix=".zip")
            remaining = length
            try:
                with os.fdopen(fd, "wb") as out:
                    while remaining > 0:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:  # best-effort temp cleanup
                    pass
                return None
            if remaining > 0:                      # client sent a short body
                try:
                    os.unlink(tmp)
                except OSError:  # best-effort temp cleanup
                    pass
                return None
            return tmp

        def do_GET(self):
            if not _allowed(self):
                return self._json({"ok": False, "error": "unauthorized"}, code=401)
            if not request_csrf_ok(self.headers):
                return self._json({"ok": False, "error": "cross-origin request blocked"},
                                  code=403)
            path = urlparse(self.path).path
            if path == "/":
                return self._page()
            if path == "/favicon.svg":          # the racecast "rc" mark (#57)
                return self._serve_file(ctx["favicon_path"])
            if path == "/api/ping":
                return self._json({"app": APP_ID, "version": ctx["version"]})
            if path == "/api/status":
                try:
                    return self._json({"ok": True, **ctx["status"]()})
                except Exception as exc:        # a broken probe must not 500-hang the poll
                    return self._json({"ok": False, "error": f"status failed: {exc}"}, code=500)
            if path == "/api/resources":
                try:
                    return self._json(ctx["resources"]())
                except Exception as exc:
                    return self._json({"available": False, "error": str(exc)}, code=500)
            if path == "/api/assets":
                try:
                    return self._json(ctx["assets"]())
                except Exception as exc:    # sheet/probe failure must stay JSON
                    return self._json({"ok": False,
                                       "error": f"assets check failed: {exc}"},
                                      code=500)
            if path == "/api/producer-schedule":
                try:
                    return self._json(ctx["producer_schedule"]())
                except Exception as exc:    # sheet/probe failure must stay JSON
                    return self._json({"ok": False,
                                       "error": f"producer schedule failed: {exc}"},
                                      code=500)
            if path == "/api/assets/files":
                try:
                    return self._json(ctx["asset_files"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"asset listing failed: {exc}"},
                                      code=500)
            if path.startswith("/api/assets/file/"):
                rest = path[len("/api/assets/file/"):]
                kind, _, raw = rest.partition("/")
                name = unquote(raw)
                # Resolved LIVE per request (a callable, not a startup snapshot):
                # the listing route /api/assets/files resolves the runtime dir on
                # every call, so serving must too, or the two diverge (#55 — the
                # gallery listed files that serving then 404'd).
                roots = ctx["asset_roots"]()
                # Reject traversal: name must be a bare basename within the root.
                if (kind not in roots or not name
                        or name != os.path.basename(name)
                        or name in (".", "..")):
                    return self._not_found("asset not found")
                # Resolve, then require the normalized path to stay inside the
                # trusted root before it reaches any filesystem call. The guard
                # is its own statement (realpath normalize + startswith barrier —
                # the sanitizer CodeQL recognizes; a compound `or` condition
                # defeats that recognition).
                root = os.path.realpath(roots[kind])
                full = os.path.realpath(os.path.join(root, name))
                if not full.startswith(root + os.sep):
                    return self._not_found("asset not found")
                if not os.path.isfile(full):
                    return self._not_found("asset not found")
                return self._serve_file(full)
            if path == "/api/setup":
                try:
                    return self._json({"tools": ctx["tools"](),
                                       "apps": ctx["apps"]()})
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"setup check failed: {exc}"},
                                      code=500)
            if path == "/api/preflight":
                try:
                    return self._json(ctx["preflight"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"preflight check failed: {exc}"},
                                      code=500)
            if path == "/api/speedtest":
                try:
                    return self._json(ctx["speedtest"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"speedtest read failed: {exc}"},
                                      code=500)
            if path == "/api/relay-live":
                try:
                    return self._json(ctx["relay_live"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"relay stats failed: {exc}"},
                                      code=500)
            if path == "/api/event-title":
                try:
                    return self._json(ctx["event_title_read"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"event title read failed: {exc}"},
                                      code=500)
            if path == "/api/tailscale-peers":
                try:
                    return self._json({"ok": True, "peers": ctx["tailscale_peers"]()})
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"tailscale peers failed: {exc}"},
                                      code=500)
            if path == "/api/obs-collection":
                try:
                    return self._json(ctx["obs_collection"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"obs collection check failed: {exc}"},
                                      code=500)
            if path == "/api/update":
                force = "force=1" in (urlparse(self.path).query or "")
                try:
                    return self._json(ctx["update_check"](force))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"update check failed: {exc}"},
                                      code=500)
            if path == "/api/previews":
                force = "force=1" in (urlparse(self.path).query or "")
                try:
                    return self._json(ctx["previews"](force))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"preview list failed: {exc}"},
                                      code=500)
            if path == "/api/streams":
                try:
                    return self._json(ctx["streams_read"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"streams config read failed: {exc}"},
                                      code=500)
            if path == "/api/docs":
                try:
                    return self._json(ctx["docs"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"docs listing failed: {exc}"},
                                      code=500)
            if path.startswith("/api/docs/file/"):
                # key is looked up in an allowlist (docs_content -> None for
                # anything unknown), so no path can be traversed out of the doc
                # set. Markdown is rendered to HTML; HTML is served as-is.
                key = unquote(path[len("/api/docs/file/"):])
                doc = ctx["docs_content"](key)
                if not doc:
                    return self._not_found("doc not found")
                ctype, body = doc
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return None
            if path == "/docs/slides" or path.startswith("/docs/slides/"):
                # static, read-only serve of the bundled onboarding decks (incl. the
                # cheat sheet) so they open OFFLINE; docs_slides_serve guards traversal.
                rel = unquote(path[len("/docs/slides"):]).lstrip("/")
                serve = ctx.get("docs_slides_serve")
                hit = serve(rel) if serve else None
                if not hit:
                    return self._not_found("slides resource not found")
                with open(hit[0], "rb") as fh:
                    return self._serve_bytes(fh.read(), hit[1])
            if path == "/api/env":
                try:
                    return self._json(ctx["env_read"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read .env: {exc}"},
                                      code=500)
            if path == "/api/devices":
                try:
                    return self._json(ctx["devices_enumerate"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not enumerate devices: {exc}"},
                                      code=500)
            if path == "/api/profiles":
                try:
                    return self._json(ctx["profiles"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"profiles listing failed: {exc}"},
                                      code=500)
            if path == "/api/profile/env":
                try:
                    return self._json(ctx["profile_env_read"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read profile .env: {exc}"},
                                      code=500)
            if path == "/api/crew":
                try:
                    return self._json(ctx["crew_read"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read crew roster: {exc}"},
                                      code=500)
            if path == "/api/profile/logo":
                p = ctx["profile_logo"]()
                return self._serve_file(p) if p else self._not_found("no logo")
            if path == "/api/console/status":
                try:
                    return self._json(ctx["console_status"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"console status failed: {exc}"},
                                      code=500)
            if path == "/api/overlay":
                try:
                    page = (parse_qs(urlparse(self.path).query or "").get(
                        "page") or ["hud"])[0]
                    return self._json(ctx["overlay_read"](page))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read overlay css: {exc}"},
                                      code=500)
            if path == "/api/overlay/slots":
                try:
                    page = (parse_qs(urlparse(self.path).query or "").get(
                        "page") or ["hud"])[0]
                    return self._json(ctx["overlay_slots"](page))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read overlay slots: {exc}"},
                                      code=500)
            if path == "/api/overlay/layout":
                try:
                    page = (parse_qs(urlparse(self.path).query or "").get(
                        "page") or ["hud"])[0]
                    return self._json(ctx["overlay_layout_read"](page))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not read overlay layout: {exc}"},
                                      code=500)
            if path == "/api/overlay/fonts":
                try:
                    return self._json(ctx["overlay_fonts"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not list overlay fonts: {exc}"},
                                      code=500)
            if path == "/api/fonts":
                try:
                    return self._json(ctx["machine_fonts"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not list font library: {exc}"},
                                      code=500)
            if path == "/api/fonts/catalog":
                try:
                    return self._json(ctx["font_catalog"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not load font catalog: {exc}"},
                                      code=500)
            if path == "/api/overlay/bg":
                p = ctx["overlay_bg"]()
                return self._serve_file(p) if p else self._not_found("no overlay frame")
            if path.startswith("/api/overlay/font/"):
                name = unquote(path[len("/api/overlay/font/"):])
                hit = ctx["overlay_font_serve"](name)
                if not hit:
                    return self._not_found("font not found")
                with open(hit[0], "rb") as fh:
                    return self._serve_bytes(fh.read(), hit[1])
            if path.startswith("/api/overlay/asset/"):
                rest = path[len("/api/overlay/asset/"):].split("/", 1)
                hit = (ctx["overlay_asset_serve"](rest[0], unquote(rest[1]))
                       if len(rest) == 2 else None)
                if not hit:
                    return self._not_found("asset not found")
                with open(hit[0], "rb") as fh:
                    return self._serve_bytes(fh.read(), hit[1])
            if path == "/api/backup":
                try:
                    return self._json(ctx["backup_list"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not list backups: {exc}"},
                                      code=500)
            if path == "/api/init/plan":
                browser = parse_qs(urlparse(self.path).query or "").get(
                    "browser", ["firefox"])[0]
                try:
                    return self._json(ctx["init_plan"](browser))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"init plan failed: {exc}"},
                                      code=500)
            if path == "/api/profile/export":
                q = parse_qs(urlparse(self.path).query or "")
                name = (q.get("name") or [None])[0]
                assets = (q.get("assets") or ["1"])[0] != "0"
                try:
                    result = ctx["profile_export"](name, assets)
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"export failed: {exc}"}, code=500)
                if not result.get("ok"):
                    return self._json(result, code=400)
                return self._download_file(result["path"],
                                           f"{result['slug']}-profile.zip",
                                           cleanup=True)
            if path.startswith("/api/jobs/") and path.endswith("/stream"):
                job_id = path.split("/")[3]
                return self._stream_job(job_id) if job_id else self._not_found("unknown job")
            if path.startswith("/api/jobs/"):
                job_id = path.split("/")[3]
                snap = ctx["jobs"].snapshot(job_id) if job_id else None
                return self._json({"ok": True, **snap}) if snap else self._not_found("unknown job")
            if path.startswith("/api/logs/") and path.endswith("/stream"):
                name = path.split("/")[3]   # "aggregate" is just another registry source
                return self._stream_log(name) if name else self._not_found("unknown log")
            if path.startswith("/api/logs/") and path.endswith("/archives"):
                name = path.split("/")[3]
                return self._log_archives(name)
            if path.startswith("/api/logs/") and "/file" in path:
                name = path.split("/")[3]
                qs = parse_qs(urlparse(self.path).query or "")
                return self._log_file(name, qs.get("token", [""])[0])
            return self._not_found()

        def do_POST(self):
            if not _allowed(self):
                return self._json({"ok": False, "error": "unauthorized"}, code=401)
            if not request_csrf_ok(self.headers):
                return self._json({"ok": False, "error": "cross-origin request blocked"},
                                  code=403)
            path = urlparse(self.path).path
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                result = ctx["jobs"].cancel(job_id) if job_id else None
                if result is None:
                    return self._not_found("unknown job")
                return self._json({"ok": True, "cancelled": result})
            if path == "/api/env":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["env_write"](body.get("entries") or [])
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write .env: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/devices/select":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["devices_write"](body.get("webcam"), body.get("capture"),
                                                  body.get("mic"), body.get("tyres"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not save device selection: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/ps/discover":
                try:
                    return self._json(ctx["ps_discover"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not discover PlayStation: {exc}"},
                                      code=500)
            if path == "/api/ps/save":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["ps_write"](body.get("ip"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not save PS IP: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/event-title":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["event_title_write"](body.get("title"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not set event title: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/obs/stream-target":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body",
                                       "note": "malformed request"}, code=400)
                try:
                    result = ctx["obs_stream_target"]((body.get("part") or "").strip())
                except Exception as exc:               # noqa: BLE001 — provider is best-effort
                    return self._json({"ok": False, "note": str(exc)}, code=400)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/report/generate":
                try:
                    return self._json(ctx["report_generate"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not generate report: {exc}"},
                                      code=500)
            if path == "/api/report/send":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["report_send"](body.get("path"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not send report: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/streams":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["streams_write"](body.get("entries") or [])
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write streams config: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/profile/use":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["profile_use"](body.get("name"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not switch profile: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/profile/new":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["profile_new"](
                        body.get("name"), body.get("from"),
                        kind=body.get("kind"), template=body.get("template"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not create profile: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/profile/env":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["profile_env_write"](body.get("entries") or [])
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write profile .env: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/crew":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["crew_write"](body.get("row"), body.get("name"),
                                               body.get("director"), body.get("producer"),
                                               body.get("commentator"), body.get("race_control"),
                                               body.get("discord"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write crew row: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/crew/delete":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["crew_delete"](body.get("row"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not delete crew row: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/console/funnel":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                result = ctx["console_funnel"](bool(body.get("on")))
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/console/funnel-auto":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                result = ctx["console_set_funnel_auto"](bool(body.get("auto")))
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/console/revoke":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                result = ctx["console_revoke"](body.get("streamer") or "")
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/console/post-link":
                result = ctx["console_post_link"]()
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/overlay":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["overlay_write"](body.get("page"), body.get("content"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write overlay css: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/overlay/layout":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["overlay_layout_write"](body.get("page"),
                                                         body.get("layout"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not write overlay layout: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/overlay/fonts":
                name = (parse_qs(urlparse(self.path).query or "").get(
                    "name") or [None])[0]
                data = self._body_bytes(MAX_FONT_BYTES)
                if data is None:
                    return self._json({"ok": False,
                                       "error": "upload too large or unreadable"},
                                      code=413)
                try:
                    result = ctx["overlay_font_upload"](name, data)
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not upload font: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/fonts/download":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["machine_font_download"](body.get("name"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not fetch Google font: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/fonts/delete":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["machine_font_delete"](body.get("name"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not delete font: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/backup":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["backup_create"](
                        body.get("label"), body.get("force"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not create backup: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/backup/restore":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["backup_restore"](body.get("slug"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not restore backup: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/backup/delete":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["backup_delete"](body.get("slug"))
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not delete backup: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path.startswith("/api/init/step/"):
                key = unquote(path[len("/api/init/step/"):])
                try:
                    result = ctx["init_step"](key)
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"init step failed: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
            if path == "/api/profile/import":
                q = parse_qs(urlparse(self.path).query or "")
                force = (q.get("force") or ["0"])[0] == "1"
                tmp = self._body_to_tempfile(MAX_IMPORT_BYTES)
                if tmp is None:
                    return self._json({"ok": False,
                                       "error": "upload too large or unreadable"},
                                      code=413)
                try:
                    result = ctx["profile_import"](tmp, force)
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"import failed: {exc}"}, code=500)
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:  # best-effort temp cleanup
                        pass
                return self._json(result, code=200 if result.get("ok") else 400)
            if path.startswith("/api/op/"):
                name = path[len("/api/op/"):]
                if name not in ctx["ops"]:
                    return self._not_found(f"unknown operation: {name}")
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    argv = ctx["build_argv"](name, body.get("params"))
                except ValueError as exc:
                    return self._json({"ok": False, "error": str(exc)}, code=400)
                job_id, err = ctx["jobs"].start(name, argv)
                if err:
                    return self._json({"ok": False, "error": err}, code=409)
                return self._json({"ok": True, "job_id": job_id})
            if path == "/api/quit":
                self._json({"ok": True})
                # shutdown() blocks until serve_forever() returns — never call
                # it from a request thread directly.
                threading.Thread(target=ctx["shutdown"], daemon=True).start()
                return None
            return self._not_found()

        def _page(self):
            # Served from memory after the first read: a frozen build unpacks
            # this page into the OS temp dir, which the OS reaps on a schedule
            # while we keep running (macOS after 3 days). Reading per request
            # made a Control Center that had been up for two weeks answer every
            # request with an error although nothing had crashed.
            try:
                body = _pages.read(ctx["page_path"])
            except OSError:
                return self._not_found(bundle_cache.eviction_hint(ctx["page_path"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # Defense-in-depth (#101): the page is fully self-contained (inline
            # CSS/JS, same-origin assets), so a strict-ish CSP costs nothing and
            # contains any future XSS — blocks external/object script loads and
            # <base> hijacking. 'unsafe-inline' is required by the inline app code.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "media-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None

        def _stream_job(self, job_id):
            if ctx["jobs"].snapshot(job_id) is None:
                return self._not_found("unknown job")
            self._sse_headers()
            since = 0
            try:
                while True:
                    chunk, since, code = ctx["jobs"].lines_since(job_id, since)
                    for line in chunk:
                        self.wfile.write(sse_frame(line))
                    if chunk:
                        self.wfile.flush()
                    # when the last lines and the exit code arrive together, the
                    # done frame fires one iteration later (empty-chunk pass)
                    if code is not None and not chunk:
                        self.wfile.write(sse_done(code))
                        self.wfile.flush()
                        return None
                    if not chunk:
                        time.sleep(0.4)
            except (BrokenPipeError, ConnectionResetError):
                return None                       # browser tab closed mid-stream

        def _src(self, name):
            return ctx.get("log_sources", {}).get(name)

        def _log_archives(self, name):
            src = self._src(name)
            if src is None:
                return self._not_found(f"unknown log: {name}")
            return self._json({"ok": True, "tokens": src["archives"]()})

        def _log_file(self, name, token):
            src = self._src(name)
            if src is None:
                return self._not_found(f"unknown log: {name}")
            # Structural guard up front (defense-in-depth; the source read() guards too).
            if not token or "/" in token or "\\" in token or ".." in token:
                return self._json({"ok": False, "error": "bad token"}, code=400)
            text = src["read"](token)
            return self._json({"ok": True, "text": text or ""})

        def _tail_files(self, files, label_of):
            """Yield (label, line) for the last TAIL_LINES of each file then new
            lines as they arrive (arrival order). Used by single-source + aggregate.
            Returns (queue, stop); the caller sets stop['v'] = True on disconnect so
            the daemon reader threads exit."""
            q = queue.Queue(maxsize=2000)   # bounded: a stalled (not-yet-closed) client
            stop = {"v": False}             # must not let a busy log grow memory forever
            def emit(item):
                try:
                    q.put_nowait(item)
                except queue.Full:
                    pass  # consumer stalled — drop the line rather than grow unbounded
            def follow(path):
                # Seed with the last TAIL_LINES of history, then poll for new
                # lines by RE-OPENING the file each pass (logsetup.read_new_lines)
                # — never holding it open. A continuously-held read handle blocks
                # the relay's midnight rollover on Windows (rename of an open file
                # fails), which silently breaks logging; see logsetup.read_new_lines.
                pos = 0
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                        pos = fh.tell()
                    for ln in data.decode("utf-8", "replace").splitlines()[-TAIL_LINES:]:
                        emit((label_of(path), ln))
                except OSError:
                    pos = 0  # not there yet — start from the top once it appears
                while not stop["v"]:
                    lines, pos = logsetup.read_new_lines(path, pos)
                    if lines:
                        for ln in lines:
                            emit((label_of(path), ln))
                    else:
                        time.sleep(0.4)
            for p in files:
                threading.Thread(target=follow, args=(p,), daemon=True).start()
            return q, stop

        def _stream_source(self, files, label_of):
            self._sse_headers()
            if not files:
                self.wfile.write(sse_frame("(no log yet — waiting)")); self.wfile.flush()
            q, stop = self._tail_files(files, label_of)
            try:
                while True:
                    try:
                        label, line = q.get(timeout=0.4)
                        self.wfile.write(sse_frame(f"[{label}] {line}"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n"); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                stop["v"] = True                  # browser tab closed mid-stream
                return None                        # ConnectionAbortedError = Windows (WinError 10053)

        def _stream_log(self, name):
            # "aggregate" is just another registry source (its files = the union).
            src = self._src(name)
            if src is None:
                return self._not_found(f"unknown log: {name}")
            return self._stream_source(
                src["files"](), lambda p: os.path.basename(p).split(".log")[0])

    return Handler


def serve(ctx, host, port):
    """Build the server (caller runs serve_forever) and install ctx['shutdown'].
    Raises OSError when the port is taken — callers turn that into the
    RACECAST_UI_PORT hint."""
    httpd = ThreadingHTTPServer((host, port), make_handler(ctx))
    httpd.daemon_threads = True                  # SSE threads die with the process
    ctx["shutdown"] = httpd.shutdown
    return httpd
