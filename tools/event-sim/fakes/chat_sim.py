"""A stand-in for the YouTube live chat of the event's broadcast channel, which the relay's
broadcast chat reader reads in simulation mode (RACECAST_SIM_BROADCAST_CHAT_URL).

    GET  /live_chat?is_popout=1&v=<id>               the page the reader bootstraps from
    POST /youtubei/v1/live_chat/get_live_chat?key=   the Innertube poll
    GET  /_sim/state                                 polls and messages handed out

The poll hands out MESSAGES, one every `interval` seconds from the start, and keeps the
chat live after the last one. Maintainer tool, not shipped."""
import html
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loopback_http  # noqa: E402

FLAG_ENV = "RACECAST_SIMULATION"
URL_ENV = "RACECAST_SIM_BROADCAST_CHAT_URL"
API_KEY = "sim-chat"
POLL_MS = 3000
MESSAGES = (("Mia K", "Good luck to all teams!"), ("RaceFan88", "Great start, close at the front"),
            ("Tobi", "Which stint is this?"), ("Lena", "That pit stop was quick"),
            ("Sam R", "Hello from the stream"), ("Jo", "Love the track map"))


def _continuation(n):
    return f"sim-{n}"


class ChatSim(loopback_http.LoopbackServer):
    def __init__(self, port=0, messages=MESSAGES, interval=20.0, clock=time.time):
        super().__init__(port)
        self.messages, self.interval, self.clock = tuple(messages), float(interval), clock
        self.started = clock()
        self.lock = threading.Lock()
        self.polls = self.served = 0

    def env(self):
        """The simulation-mode environment that points the relay's chat reader here."""
        return {FLAG_ENV: "1", URL_ENV: self.http_url}

    def snapshot(self):
        with self.lock:
            return {"polls": self.polls, "served": self.served}

    def released(self):
        """How many messages are out by now."""
        return min(len(self.messages), 1 + int(max(0.0, self.clock() - self.started) // self.interval))

    def _page(self, video):
        cfg = json.dumps({"INNERTUBE_API_KEY": API_KEY, "INNERTUBE_CONTEXT_CLIENT_VERSION": "2.20240101.00.00"},
                         separators=(",", ":"))
        data = json.dumps({"contents": {"liveChatRenderer": {"continuations": [
            {"invalidationContinuationData": {"continuation": _continuation(0)}}]}}}, separators=(",", ":"))
        return (f"<html><head><title>{html.escape(video)}</title></head><body><script>ytcfg.set({cfg});"
                f"var ytInitialData = {data};</script></body></html>").encode()

    def _poll(self, body):
        try:
            token = json.loads(body).get("continuation") or ""
            start = int(token.split("-", 1)[1])
        except (ValueError, AttributeError, IndexError):
            return loopback_http.json_reply({"error": "bad continuation"}, 400)
        end = self.released()
        actions = []
        for i in range(min(start, end), end):
            user, text = self.messages[i]
            actions.append({"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                "id": f"sim-msg-{i}", "authorName": {"simpleText": user},
                "message": {"runs": [{"text": text}]},
                "timestampUsec": str(int((self.started + i * self.interval) * 1_000_000))}}}})
        with self.lock:
            self.polls += 1
            self.served += len(actions)
        return loopback_http.json_reply({"continuationContents": {"liveChatContinuation": {
            "continuations": [{"timedContinuationData": {"continuation": _continuation(max(start, end)),
                                                          "timeoutMs": POLL_MS}}],
            "actions": actions}}})

    def route(self, method, path, query, headers, body):
        if method == "GET" and path == "/_sim/state":
            return loopback_http.json_reply(self.snapshot())
        if method == "GET" and path == "/live_chat":
            return 200, "text/html; charset=utf-8", self._page((query.get("v") or [""])[0])
        if method == "POST" and path == "/youtubei/v1/live_chat/get_live_chat":
            return self._poll(body)
        return loopback_http.json_reply({"error": "not found"}, 404)
