#!/usr/bin/env python3
"""Simulated Discord webhook for the event simulation: it records every message.

Any POST path outside /_sim/ is a webhook; `WebhookSink.url` is the one racecast gets.
JSON bodies and multipart bodies (`payload_json` plus files, as the report send posts)
are accepted. A message is answered with 204 like Discord. An empty or unparseable one,
or one with a field of the wrong type or size, gets Discord's 400 error body; a file
over the 20 MiB per-file limit a 413. Neither is recorded.

    GET  /_sim/messages?since=N     messages after sequence N
    GET  /_sim/state                counters and fault rules
    POST /_sim/fail                 fault rules (loopback_http); targets `path:<path>`, `hook`

A fault with an error answers Discord-style {"message": error, "code": 0}, 400 unless
it names a status. A faulted message is not recorded.
`WebhookSink.env()` gives RACECAST_SIMULATION=1 and RACECAST_SIM_DISCORD_WEBHOOK_URL.

    python3 tools/event-sim/fakes/webhook_sink.py --port 0 --ready-file sink.json

Maintainer tool, not shipped."""
import argparse
import email.parser
import email.policy
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loopback_http  # noqa: E402
import logsetup  # noqa: E402

FLAG_ENV = "RACECAST_SIMULATION"                       # simulation.FLAG
WEBHOOK_URL_ENV = "RACECAST_SIM_DISCORD_WEBHOOK_URL"   # simulation.DISCORD_WEBHOOK_URL
HOOK_PATH = "/api/webhooks/1/sim"
MAX_CONTENT = 2000
MAX_EMBEDS = 10
FILE_LIMIT = 20 << 20                 # Discord's default per-file upload limit
TOO_LARGE = {"message": "Request entity too large", "code": 40005}
EMPTY = {"message": "Cannot send an empty message", "code": 50006}
BAD_JSON = {"message": "The request body contains invalid JSON.", "code": 50109}


def message_text(payload):
    """The text a reader sees: content, username and every embed's title, description,
    field names and values, footer and author."""
    parts = [payload.get("content"), payload.get("username")]
    for embed in payload.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        parts += [embed.get("title"), embed.get("description")]
        for field in embed.get("fields") or []:
            if isinstance(field, dict):
                parts += [field.get("name"), field.get("value")]
        for part in ("footer", "author"):
            if isinstance(embed.get(part), dict):
                parts.append(embed[part].get("text") or embed[part].get("name"))
    return "\n".join(str(p) for p in parts if p)


def parse_multipart(content_type, body):
    """(payload dict or None, [file info]) from a multipart/form-data body."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    payload, files = None, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if part.get_filename():
            files.append({"field": name, "filename": part.get_filename(),
                          "content_type": part.get_content_type(), "size": len(data)})
        elif name == "payload_json":
            payload = json.loads(data.decode("utf-8"))
    return payload, files


def _form_error(code, message):
    return {"_errors": [{"code": code, "message": message}]}


def form_errors(payload):
    """Discord's `errors` object for fields of the wrong type or size, or {}."""
    errors = {}
    content, username, embeds = (payload.get(k) for k in ("content", "username", "embeds"))
    if content is not None and not isinstance(content, str):
        errors["content"] = _form_error("BASE_TYPE_STRING", "Must be a string.")
    elif isinstance(content, str) and len(content) > MAX_CONTENT:
        errors["content"] = _form_error("BASE_TYPE_MAX_LENGTH",
                                        f"Must be {MAX_CONTENT} or fewer in length.")
    if username is not None and not isinstance(username, str):
        errors["username"] = _form_error("BASE_TYPE_STRING", "Must be a string.")
    if embeds is not None:
        if not isinstance(embeds, list):
            errors["embeds"] = _form_error("BASE_TYPE_ARRAY", "Must be an array.")
        elif len(embeds) > MAX_EMBEDS:
            errors["embeds"] = _form_error("BASE_TYPE_MAX_LENGTH",
                                           f"Must be {MAX_EMBEDS} or fewer in length.")
        elif not all(isinstance(e, dict) for e in embeds):
            errors["embeds"] = _form_error("MODEL_TYPE_CONVERT", "Only dictionaries may be used.")
    return errors


class WebhookSink(loopback_http.LoopbackServer):
    """A running simulated Discord webhook. Messages carry seq, time (`clock`), path,
    username, content, embeds, files, the full payload and `text` (message_text)."""
    POST_TYPES = ("application/json", "multipart/form-data")
    MAX_BODY = 2 * FILE_LIMIT + (1 << 20)   # an oversized file still reaches the per-file check

    def __init__(self, port=0, clock=time.time, faults=None, sleep=None):
        super().__init__(port, faults=faults, sleep=sleep)
        self.clock = clock
        self.lock = threading.Lock()
        self._messages = []
        self.seq = 0
        self.rejected = 0
        self.path = HOOK_PATH

    @property
    def url(self):
        return self.http_url + self.path

    def env(self):
        """The simulation-mode environment that points racecast at this webhook."""
        return {FLAG_ENV: "1", WEBHOOK_URL_ENV: self.url}

    def snapshot(self):
        with self.lock:
            counts = {"seq": self.seq, "rejected": self.rejected}
        return dict(counts, faults=self.faults(), faults_hit=self.faults_hit)

    def fault_keys(self, method, path, query, body):
        if method == "POST" and not path.startswith(loopback_http.CONTROL_PREFIX):
            return ["hook", "path:" + path]
        return []

    def fault_reply(self, rule):
        if rule["error"] is not None:
            return loopback_http.json_reply({"message": rule["error"], "code": 0},
                                            rule["status"] or 400)
        return super().fault_reply(rule)

    def messages(self, since=0):
        with self.lock:
            return [json.loads(json.dumps(m)) for m in self._messages if m["seq"] > since]

    def count(self, needle=None, since=0):
        """Messages whose text contains `needle`, ignoring case; every message without one."""
        return sum(1 for m in self.messages(since)
                   if needle is None or needle.lower() in m["text"].lower())

    def route(self, method, path, query, headers, body):
        reply = loopback_http.json_reply
        if method == "GET" and path == "/_sim/messages":
            since = int(query.get("since", ["0"])[0] or 0)
            with self.lock:
                seq = self.seq
            return reply({"messages": self.messages(since), "seq": seq})
        if method == "GET" and path == "/_sim/state":
            return reply(self.snapshot())
        if method != "POST" or path.startswith(loopback_http.CONTROL_PREFIX):
            return reply({"message": "404: Not Found", "code": 0}, 404)
        ctype = headers.get("Content-Type") or ""
        try:
            if ctype.split(";")[0].strip().lower() == "multipart/form-data":
                payload, files = parse_multipart(ctype, body)
            else:
                payload, files = json.loads(body.decode("utf-8")), []
        except (ValueError, UnicodeDecodeError):
            return self._reject(BAD_JSON)
        if not isinstance(payload, dict):
            payload = {}
        if any(f["size"] > FILE_LIMIT for f in files):
            return self._reject(TOO_LARGE, 413)
        shape = form_errors(payload)
        if shape:
            return self._reject({"message": "Invalid Form Body", "code": 50035, "errors": shape})
        content = payload.get("content")
        if not (content or payload.get("embeds") or files):
            return self._reject(EMPTY)
        with self.lock:
            self.seq += 1
            self._messages.append({
                "seq": self.seq, "time": self.clock(), "path": path,
                "username": payload.get("username"), "content": content,
                "embeds": payload.get("embeds") or [], "files": files,
                "payload": payload, "text": message_text(payload)})
        return 204, "", b""

    def too_large(self, length):
        with self.lock:
            self.rejected += 1
        return loopback_http.json_reply(TOO_LARGE, 413)

    def _reject(self, error, status=400):
        with self.lock:
            self.rejected += 1
        return loopback_http.json_reply(error, status)


def build_parser():
    ap = argparse.ArgumentParser(description="Simulated Discord webhook that records every message.")
    ap.add_argument("--port", type=int, default=0, help="listen port, 0 picks a free one (default)")
    ap.add_argument("--ready-file", help="write {port, url, http_url, env} here once listening")
    return ap


def main(argv=None):
    logsetup.harden_stdio()
    args = build_parser().parse_args(argv)
    sink = WebhookSink(port=args.port).start()
    loopback_http.serve_cli(sink, args.ready_file, {
        "port": sink.port, "url": sink.url, "http_url": sink.http_url, "env": sink.env()},
        f"webhook-sink: {sink.url}")


fetch = loopback_http.fetch

if __name__ == "__main__":
    main()
