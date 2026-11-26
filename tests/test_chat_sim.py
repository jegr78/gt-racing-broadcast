#!/usr/bin/env python3
"""The event simulation's broadcast chat stand-in (#1331), read by the relay's own YouTube
chat reader in simulation mode. Loopback only. Run: python3 tests/test_chat_sim.py"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import chat_sim  # noqa: E402
import simulation  # noqa: E402

_spec = importlib.util.spec_from_file_location("chatsimfeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)


def _read(chat, now):
    """What the relay's reader stores after one bootstrap and polls up to `now`."""
    store = relay.BroadcastChatStore()
    reader = relay._BroadcastReader("broadcast", store)
    polls = []
    real = relay._yt_chat_post_json

    def post(url, body, timeout=15):
        polls.append(url)
        if len(polls) > 2:
            reader.stop()
        return real(url, body, timeout)
    saved = {k: os.environ.get(k) for k in chat.env()}
    os.environ.update(chat.env())
    relay._yt_chat_post_json = post
    reader.stop_evt.wait = lambda s: None
    try:
        chat.clock = lambda: now
        reader._poll()
    finally:
        relay._yt_chat_post_json = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return store, polls


def t_the_relays_reader_shows_the_viewer_messages_over_time():
    with chat_sim.ChatSim(interval=10.0, clock=lambda: 100.0) as chat:
        assert chat.env() == {simulation.FLAG: "1", simulation.BROADCAST_CHAT_URL: chat.http_url}
        store, polls = _read(chat, 100.0)
        assert polls and all(p.startswith(chat.http_url + "/youtubei/v1/live_chat/") for p in polls), polls
        texts = [m["text"] for m in store.data()["messages"]]
        assert texts == [chat_sim.MESSAGES[0][1]], f"one message at the start: {texts}"
        store, _ = _read(chat, 125.0)
        msgs = store.data()["messages"]
        assert [m["user"] for m in msgs] == [u for u, _t in chat_sim.MESSAGES[:3]], \
            f"a new viewer message every interval from the start: {msgs}"
        assert chat.snapshot()["polls"] >= 2


def t_the_chat_never_ends_and_runs_out_of_messages_quietly():
    with chat_sim.ChatSim(interval=1.0, clock=lambda: 0.0) as chat:
        store, polls = _read(chat, 10_000.0)
        assert len(store.data()["messages"]) == len(chat_sim.MESSAGES), "every message once"
        assert len(polls) == 3, f"a poll after the last message keeps the chat live: {polls}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
