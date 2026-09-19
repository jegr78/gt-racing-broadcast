#!/usr/bin/env python3
"""Stdlib unit checks for the pure Discord payload builders (notify.py).
Run: python3 tests/test_notify.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import notify as n


def _embed(payload):
    assert payload["username"] == "GT Racecast"
    assert payload["embeds"] and len(payload["embeds"]) == 1
    return payload["embeds"][0]


def t_takeover_pings_here_and_names_both_producers():
    p = n.takeover_discord_payload("Bob", "Alice", 7, "Feed A", event_title="GTEC R4")
    assert p["content"] == "@here"
    assert p["allowed_mentions"] == {"parse": ["everyone"]}
    e = _embed(p)
    assert "Bob" in e["description"]            # who took over
    assert "Alice" in e["description"]          # from whom
    assert "7" in e["description"]              # the on-air stint
    assert e["footer"]["text"] == "GTEC R4 · Bob"   # event title · producer


def t_takeover_without_from_producer_or_title():
    p = n.takeover_discord_payload("Bob", "", 3, "Feed B")
    e = _embed(p)
    assert "Bob" in e["description"]
    assert e["footer"]["text"] == "Bob"        # producer only
    # no "from" clause when the outgoing producer is unknown
    assert "from " not in e["description"].lower()


def t_obs_stream_started_is_info_no_ping():
    p = n.obs_stream_discord_payload(True, "Bob", event_title="GTEC R4")
    assert "content" not in p or not p.get("content")   # info, NOT an @here ping
    e = _embed(p)
    assert "start" in e["title"].lower()
    assert e["footer"]["text"] == "GTEC R4 · Bob"
    assert e["color"] == n.COLOR_STREAM_START


def t_obs_stream_stopped_pings_here_off_air():
    p = n.obs_stream_discord_payload(False, "Bob")
    assert p["content"] == "@here"
    assert p["allowed_mentions"] == {"parse": ["everyone"]}
    e = _embed(p)
    assert "stop" in e["title"].lower()
    assert "off air" in e["description"].lower()
    assert e["footer"]["text"] == "Bob"
    assert e["color"] == n.COLOR_STREAM_STOP


def t_no_footer_when_neither_title_nor_producer():
    p = n.obs_stream_discord_payload(True, "")
    e = _embed(p)
    assert "footer" not in e


def t_substitution_payload():
    p = n.substitution_discord_payload("A", 3, "JeGr", event_title="6h Spa")
    assert p["username"] == n.USERNAME
    assert "content" not in p                       # ping=False: no @here
    e = p["embeds"][0]
    assert e["color"] == n.COLOR_SUBSTITUTION
    assert "Feed A" in e["description"] and "stint 3" in e["description"]
    assert e["footer"]["text"] == "6h Spa · JeGr"   # event_title · producer


def t_feed_recovery_churn_payload():
    # A CHURN alert DOES ping @here (a single self-healed drop is recorded silently and
    # never posts — that's handled relay-side, not here).
    p = n.feed_recovery_churn_payload("A", 4, 5, "JeGr", event_title="6h Spa")
    assert p["username"] == n.USERNAME
    assert p.get("content") == "@here"               # churn pings
    e = p["embeds"][0]
    assert e["color"] == n.COLOR_FEED_CHURN
    assert "Feed A" in e["description"] and "4" in e["description"] and "5 min" in e["description"]
    assert e["footer"]["text"] == "6h Spa · JeGr"


def t_report_payload():
    p = n.report_discord_payload("Test Event", [("Uptime", "98.0%"), ("Incidents", "2")])
    assert p["username"] == "GT Racecast"
    assert p["embeds"][0]["title"].endswith("Test Event")
    names = [f["name"] for f in p["embeds"][0]["fields"]]
    assert names == ["Uptime", "Incidents"]
    assert all(f["inline"] for f in p["embeds"][0]["fields"])
    assert "description" not in p["embeds"][0]            # no finding -> no description


def t_report_payload_carries_the_finding():
    # #586: the report's verdict leads the embed.
    p = n.report_discord_payload("E", [("Uptime", "98.0%")],
                                 description="Output ran behind live for 41m of 56m.")
    assert p["embeds"][0]["description"] == "Output ran behind live for 41m of 56m."


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("all notify tests passed")
