#!/usr/bin/env python3
"""Discord voice-channel join via the local desktop-client RPC IPC socket.

Pure helpers (socket-path candidates, frame codec, message builders, the
channel-link parser, the Sheet->env target resolver and token-cache logic) plus a
thin DiscordVoiceClient that ties them to a real socket and the http_util OAuth
token exchange. The desktop client is driven so its audio plays on the machine's
audio device, where OBS's PipeWire plugin captures it. Spec:
docs/superpowers/specs/2026-07-04-discord-voice-join-design.md.

Secrets (client_secret, tokens) are never logged, printed, or returned."""
import csv
import io
import json
import os
import socket
import struct
import threading
import time

import http_util

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2
TOKEN_URL = "https://discord.com/api/oauth2/token"
REDIRECT_URI = "http://localhost"
CONFIG_TAB = "Configuration"
VOICE_HEADER = "Discord Voice"
# Per-read timeouts (seconds) so a hung/headless Discord can never block forever.
# Normal RPC frames answer instantly; the AUTHORIZE response waits for a human to
# click the consent popup, so it gets the longer window.
READ_TIMEOUT_S = 10
CONSENT_TIMEOUT_S = 120


def ipc_candidates(os_name, env):
    """Ordered IPC endpoints where a running Discord client may listen. Both
    branches build fixed-OS paths with explicit separators, NOT os.path.join,
    which uses the RUNNING OS's separator and would turn the POSIX candidates into
    backslash paths on the Windows runner. Windows is a fixed named-pipe string;
    POSIX joins the runtime bases with the socket name, including the snap and
    flatpak subdirs."""
    if os_name == "nt":
        return [r"\\?\pipe\discord-ipc-{}".format(n) for n in range(10)]
    bases = [env.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")]
    bases = [b for b in bases if b] + ["/tmp"]
    out = []
    for base in bases:
        prefix = base.rstrip("/")
        for sub in ("", "app/com.discordapp.Discord/", "snap.discord/"):
            dirpath = prefix + "/" + sub.rstrip("/") if sub else prefix
            for n in range(10):
                out.append(dirpath + "/discord-ipc-{}".format(n))
    return out


def encode_frame(op, payload):
    data = json.dumps(payload).encode("utf-8")
    return struct.pack("<II", op, len(data)) + data


def frame_header(buf8):
    """(opcode, payload_length) from a frame's first 8 bytes (little-endian)."""
    return struct.unpack("<II", buf8)


def msg_handshake(client_id):
    return OP_HANDSHAKE, {"v": 1, "client_id": str(client_id)}


def msg_authorize(client_id):
    return OP_FRAME, {"cmd": "AUTHORIZE",
                      "args": {"client_id": str(client_id), "scopes": ["rpc"]},
                      "nonce": "authorize"}


def msg_authenticate(access_token):
    return OP_FRAME, {"cmd": "AUTHENTICATE",
                      "args": {"access_token": access_token}, "nonce": "authenticate"}


def msg_select_voice(channel_id):
    return OP_FRAME, {"cmd": "SELECT_VOICE_CHANNEL",
                      "args": {"channel_id": str(channel_id), "force": True},
                      "nonce": "select-voice"}


def msg_leave():
    return OP_FRAME, {"cmd": "SELECT_VOICE_CHANNEL",
                      "args": {"channel_id": None, "force": True}, "nonce": "leave"}


def parse_channel_link(link):
    """'https://discord.com/channels/<guild>/<channel>' (or discord://) ->
    (guild, channel) of digit strings, else None. A structural parse, with no host
    substring check (CodeQL py/incomplete-url-substring-sanitization)."""
    if not link:
        return None
    marker = "/channels/"
    i = str(link).find(marker)
    if i < 0:
        return None
    parts = str(link)[i + len(marker):].strip("/").split("/")
    if len(parts) < 2:
        return None
    guild, channel = parts[0], parts[1]
    if guild.isdigit() and channel.isdigit():
        return guild, channel
    return None


def discord_voice_from_csv(csv_text):
    """First non-empty `Discord Voice` cell from the Configuration-tab CSV, or ''."""
    rows = list(csv.reader(io.StringIO(csv_text or "")))
    if not rows:
        return ""
    try:
        idx = rows[0].index(VOICE_HEADER)
    except ValueError:
        return ""
    for row in rows[1:]:
        if idx < len(row) and row[idx].strip():
            return row[idx].strip()
    return ""


def resolve_voice_target(sheet_value, env_value):
    """Sheet override wins; else the profile.env fallback. (guild, channel) | None."""
    for value in (sheet_value, env_value):
        target = parse_channel_link(value)
        if target:
            return target
    return None


def token_valid(cache, now, skew=60):
    """True if the cached access token is present and not within `skew` s of expiry."""
    tok, exp = cache.get("access_token"), cache.get("expires_at")
    return bool(tok) and isinstance(exp, (int, float)) and now < exp - skew


def needs_refresh(cache, now, skew=60):
    """True if the access token is unusable but a refresh_token can silently renew it."""
    return (not token_valid(cache, now, skew)) and bool(cache.get("refresh_token"))


def store_token(resp, now):
    """OAuth token response -> cache dict with an ABSOLUTE expiry."""
    return {"access_token": resp.get("access_token"),
            "refresh_token": resp.get("refresh_token"),
            "expires_at": now + int(resp.get("expires_in", 0)),
            "scope": resp.get("scope", "")}


def token_exchange_body(client_id, client_secret, code):
    return {"client_id": client_id, "client_secret": client_secret,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT_URI}


def token_refresh_body(client_id, client_secret, refresh_token):
    return {"client_id": client_id, "client_secret": client_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token}


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    os.replace(tmp, path)


class _PipeConn:
    """Windows named-pipe wrapper exposing the same sendall/recv/close as a socket."""
    def __init__(self, path):
        self._f = open(path, "r+b", buffering=0)   # noqa: SIM115  kept open across sendall/recv calls
    def sendall(self, data):
        self._f.write(data); self._f.flush()
    def recv(self, n):
        return self._f.read(n)
    def close(self):
        try:
            self._f.close()
        except OSError:
            pass  # already closed; close() is best-effort


def _default_connect(endpoint):
    if os.name == "nt":
        return _PipeConn(endpoint)              # \\?\pipe\discord-ipc-N
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(endpoint)
    sock.settimeout(READ_TIMEOUT_S)   # bound connect/read; _read_frame overrides per call
    return sock


def _default_post_form(url, form):
    import urllib.parse
    body = urllib.parse.urlencode(form).encode("utf-8")
    try:
        with http_util.open_url(
                url, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
            return json.loads(r.read().decode("utf-8"))
    except http_util.HTTPError as exc:
        # Surface Discord's error body instead of a bare "HTTP Error 400": the
        # detail is what tells the operator how to fix it, for instance an
        # unregistered http://localhost redirect. Discord never echoes the client
        # secret, so this is safe to log.
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = payload.get("error_description") or payload.get("error") or ""
        except Exception:  # noqa: BLE001  the body may be empty or non-JSON
            pass
        raise RuntimeError(
            "Discord token endpoint returned HTTP %s%s"
            % (exc.code, (": " + detail) if detail else "")) from exc


class DiscordVoiceClient:
    """Drives the local Discord desktop client into and out of a voice channel.
    Every failure path returns (False, note) and never raises to the caller."""

    def __init__(self, client_id, client_secret, cache_path,
                 *, connect=None, http_post_form=None, now=None, endpoints=None,
                 read_timeout=None, consent_timeout=None):
        self.cid, self.sec, self.cache_path = client_id, client_secret, cache_path
        self._connect = connect or _default_connect
        self._post = http_post_form or _default_post_form
        self._now = now or time.time
        self._endpoints_override = endpoints        # tests inject a fake list
        self._read_timeout = READ_TIMEOUT_S if read_timeout is None else read_timeout
        self._consent_timeout = CONSENT_TIMEOUT_S if consent_timeout is None else consent_timeout

    def _endpoints(self):
        """Candidate IPC endpoints to try. POSIX filters to existing sockets.
        Windows keeps every pipe path, because existence is not reliably
        checkable, and the connect attempt in _open picks the one that opens."""
        if self._endpoints_override is not None:
            return self._endpoints_override
        cands = ipc_candidates(os.name, os.environ)
        if os.name == "nt":
            return cands
        return [c for c in cands if os.path.exists(c)]

    def _open(self):
        """First endpoint that actually connects, or (None, None)."""
        for ep in self._endpoints():
            try:
                return self._connect(ep), ep
            except OSError:
                continue
        return None, None

    def _read_frame(self, conn, timeout):
        """Read one RPC frame, bounded by `timeout` seconds so a hung or headless
        Discord can never block forever. A socket uses its native timeout; the
        Windows pipe (no settimeout) is bounded by a watchdog that closes it,
        which makes the blocked read fail."""
        if hasattr(conn, "settimeout"):
            conn.settimeout(timeout)
            return self._read_frame_bytes(conn)
        watchdog = threading.Timer(timeout, self._safe_close, args=(conn,))
        watchdog.start()
        try:
            return self._read_frame_bytes(conn)
        finally:
            watchdog.cancel()

    @staticmethod
    def _safe_close(conn):
        try:
            conn.close()
        except Exception:  # noqa: BLE001  watchdog close is best-effort
            pass

    @staticmethod
    def _read_frame_bytes(conn):
        head = b""
        while len(head) < 8:
            chunk = conn.recv(8 - len(head))
            if not chunk:
                raise OSError("Discord closed the connection")
            head += chunk
        _op, length = frame_header(head)
        body = b""
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                raise OSError("Discord closed the connection")
            body += chunk
        return frame_header(head)[0], (json.loads(body) if body else {})

    def _send(self, conn, msg):
        conn.sendall(encode_frame(*msg))

    def _access_token(self, conn, allow_consent):
        """Return a usable access token, cheapest first: cached, then refresh, then
        the interactive AUTHORIZE consent flow when allow_consent is set. Auto-join
        passes allow_consent=False, so an unattended box never blocks on a consent
        popup nobody will click. Raises RuntimeError with a secret-free message on
        failure."""
        cache = load_cache(self.cache_path)
        now = self._now()
        if token_valid(cache, now):
            return cache["access_token"]
        if needs_refresh(cache, now):
            resp = self._post(TOKEN_URL, token_refresh_body(self.cid, self.sec, cache["refresh_token"]))
            if resp.get("access_token"):
                save_cache(self.cache_path, store_token(resp, self._now()))
                return resp["access_token"]
        if not allow_consent:
            raise RuntimeError(
                "no cached Discord authorization. Run `racecast discord join` once to grant it")
        # The AUTHORIZE response waits for the human to click, so it gets the
        # longer consent timeout.
        self._send(conn, msg_authorize(self.cid))
        _op, msg = self._read_frame(conn, self._consent_timeout)
        data = msg.get("data") or {}
        if msg.get("evt") == "ERROR":
            # AUTHORIZE itself failed, most commonly invalid_scope: the `rpc` scope
            # is gated to the app's Owner and App Testers, and owner status alone is
            # NOT enough. Add the account under Dev Portal, App Testers, then restart
            # Discord. data["code"] here is a NUMERIC RPC error code, not an OAuth
            # code; forwarding it to the token endpoint yields a misleading
            # "400 Invalid code".
            raise RuntimeError(
                "Discord AUTHORIZE failed: "
                + str(data.get("message") or data.get("code") or "unknown error"))
        code = data.get("code")
        if not isinstance(code, str) or not code:
            raise RuntimeError("Discord authorization was declined or timed out")
        resp = self._post(TOKEN_URL, token_exchange_body(self.cid, self.sec, code))
        if not resp.get("access_token"):
            raise RuntimeError("token exchange failed (check the app's http://localhost redirect)")
        save_cache(self.cache_path, store_token(resp, self._now()))
        return resp["access_token"]

    def _run(self, action_msg, ok_note, allow_consent):
        conn = None
        try:
            conn, endpoint = self._open()
            if conn is None:
                return False, "Discord desktop app is not running (no IPC socket)"
            self._send(conn, msg_handshake(self.cid))
            _op, ready = self._read_frame(conn, self._read_timeout)
            if ready.get("evt") != "READY":
                return False, "Discord did not accept the client id"
            token = self._access_token(conn, allow_consent)
            self._send(conn, msg_authenticate(token))
            self._read_frame(conn, self._read_timeout)
            self._send(conn, action_msg)
            _op, res = self._read_frame(conn, self._read_timeout)
            if res.get("evt") == "ERROR":
                return False, str((res.get("data") or {}).get("message", "voice command failed"))
            name = (res.get("data") or {}).get("name")
            return True, ok_note(name)
        except (OSError, RuntimeError, ValueError) as exc:
            return False, str(exc)
        finally:
            if conn is not None:
                conn.close()

    def join(self, guild, channel, allow_consent=True):
        return self._run(msg_select_voice(channel),
                         lambda name: "joined voice channel " + (name or channel),
                         allow_consent)

    def leave(self, allow_consent=True):
        return self._run(msg_leave(), lambda name: "left the voice channel", allow_consent)
