#!/usr/bin/env python3
"""Pure Discord webhook payload builders for producer events (#317): producer
takeover and OBS stream start/stop. No I/O; the CLI (`_post_discord_webhook`) and
the relay (`Relay._discord_post`) send the returned dict.

The shape matches the relay's `discord_health_payload` so every racecast post reads
alike: it posts as "GT Racecast", an `@here` mention sits in top-level `content`
because Discord ignores mentions inside an embed, the message rides in one titled
embed, and the footer shows `<event_title> · <producer>`, which says who triggered
the event. Important events, a takeover or a stopped stream, ping `@here`; an
informational stream-start does not.
"""

USERNAME = "GT Racecast"

# Embed colors (hex ints), distinct per event so the crew reads severity at a glance.
COLOR_TAKEOVER = 0x8B5CF6      # violet: a producer handover
COLOR_STREAM_START = 0x16A34A  # green: stream is live
COLOR_STREAM_STOP = 0xDC2626   # red: off air


def _footer(event_title="", producer=""):
    """Footer text combining event title and producer: `<title> · <producer>`,
    one of them alone, or None when both are empty."""
    parts = [p for p in ((event_title or "").strip(), (producer or "").strip()) if p]
    return " · ".join(parts) or None


def _payload(title, desc, color, *, ping, event_title="", producer=""):
    embed = {"title": title, "description": desc, "color": color}
    footer = _footer(event_title, producer)
    if footer:
        embed["footer"] = {"text": footer}
    out = {"username": USERNAME, "embeds": [embed]}
    if ping:
        out["content"] = "@here"
        out["allowed_mentions"] = {"parse": ["everyone"]}
    return out


def takeover_discord_payload(producer, from_producer, stint, source, event_title=""):
    """Announce a producer takeover: `producer` took over (optionally from
    `from_producer`) at 1-based `stint`, with `source` the feed it landed on.
    It pings `@here`, because a handover is crew-relevant."""
    who = producer or "A producer"
    frm = (from_producer or "").strip()
    desc = f"**{who}** took over the broadcast at stint {stint}"
    if frm:
        desc += f", from **{frm}**"
    desc += f" ({source})."
    return _payload("🎬 Producer takeover", desc, COLOR_TAKEOVER,
                    ping=True, event_title=event_title, producer=producer)


def obs_stream_discord_payload(started, producer, event_title=""):
    """Announce an OBS stream start, informational and unpinged, or a stop, which
    is off air and pings `@here`."""
    if started:
        return _payload("▶️ OBS stream started",
                        "OBS has started streaming, so the broadcast is live.",
                        COLOR_STREAM_START, ping=False,
                        event_title=event_title, producer=producer)
    return _payload("⏹️ OBS stream stopped",
                    "OBS has stopped streaming, so the broadcast is off air.",
                    COLOR_STREAM_STOP, ping=True,
                    event_title=event_title, producer=producer)


COLOR_SUBSTITUTION = 0xF59E0B  # amber: an ad-hoc on-air stream swap


COLOR_FEED_CHURN = 0xF97316    # orange: a feed is repeatedly dropping and recovering


def feed_recovery_churn_payload(feed, count, window_min, producer, event_title=""):
    """CHURN alert: Feed `feed` has auto-recovered `count` times within the last
    `window_min` minutes. That is sustained instability the crew must see, so it
    DOES ping `@here`; a single self-healed drop is recorded silently."""
    desc = (f"Feed {feed} has dropped and auto-recovered {count} times in the last "
            f"{window_min} min, so the upstream stream or connection looks unstable. "
            f"Consider switching to a backup feed or Standby.")
    return _payload("⚠️ Feed unstable: repeated drops", desc, COLOR_FEED_CHURN, ping=True,
                    event_title=event_title, producer=producer)


def substitution_discord_payload(feed, stint, producer, event_title=""):
    """Announce an ad-hoc on-air stream substitution: the producer swapped the
    on-air commentator stream to an alternative mid-stint. `feed` is "A" or "B",
    `stint` is 1-based. Amber and no `@here`: the outage that prompted the swap
    already pinged, and this is the follow-up recovery marker."""
    desc = (f"The on-air stream on Feed {feed} was substituted mid-stint "
            f"(stint {stint}).")
    return _payload("🔁 Stream substituted", desc, COLOR_SUBSTITUTION, ping=False,
                    event_title=event_title, producer=producer)


COLOR_REPORT = 0x3B82F6        # blue: a post-event report


def report_discord_payload(title, fields, host=None, description=""):
    """Post-event report embed with the headline KPI fields as inline content.
    `fields` is a list of (name, value) strings. `host`, when given, is the producer
    machine's name shown in the embed footer. `description`, when given, is the
    report's finding (#586), shown above the fields. The caller attaches the zipped
    HTML separately. No `@here`, since a report is not time-critical."""
    embed = {"title": f"📊 Post-event report: {title or 'Event'}",
             "color": COLOR_REPORT,
             "fields": [{"name": n, "value": v, "inline": True} for n, v in fields]}
    if description:
        embed["description"] = description
    if host:
        embed["footer"] = {"text": f"Produced on {host}"}
    return {"username": USERNAME, "embeds": [embed]}
