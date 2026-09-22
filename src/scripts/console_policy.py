#!/usr/bin/env python3
"""Pure authorization policy for the funnelled /console namespace (#216).

Identity is not authorization: a verified token proves *who* (see console_auth),
the live roster resolves *roles* (see resolve_roles in the relay), and THIS module
decides whether a given role set may reach a given /console subpath. No I/O, no
token or crypto logic, no routes. The _console_auth handler wires identity to
roles to decide().

The matrix mirrors the relay's real segment-list routes (do_GET/do_POST in
src/relay/racecast-feeds.py); keep the two in sync. Spec: the
role-based-funnel-access design, sections C (matrix) and D (step-up).
"""

import collections

# Capabilities. A resolved role set is a subset of {COMMENTATOR, DIRECTOR,
# PRODUCER}. ANY is the policy keyword for "any authenticated identity, whatever
# its roles"; it is never a member of a role set.
COMMENTATOR = "commentator"
DIRECTOR = "director"
PRODUCER = "producer"
# Race Control (#244): a read-only monitoring desk. The role string is
# "race_control" and the director-only HUD Setup banner is "racecontrol", so the
# two never collide.
RACE_CONTROL = "race_control"
ANY = "any"

# Decision outcomes returned by decide().
ALLOW = "allow"
FORBIDDEN = "forbidden"
STEP_UP_REQUIRED = "step_up_required"
NOT_FOUND = "not_found"

Requirement = collections.namedtuple("Requirement", ("capability", "step_up"))


def min_capability(segments, method="GET"):
    """Map a /console request to its minimum Requirement, or None if the route is
    not a recognized console route. *segments* is the path AFTER the /console
    prefix (e.g. /console/set/stint/4 -> ["set","stint","4"]). Ordering is
    most-specific-first, matching the relay's own dispatch."""
    p = list(segments)

    # Producer plus step-up: irreversible broadcast-control ops (spec D).
    if len(p) == 3 and p[:2] == ["set", "stint"]:
        return Requirement(PRODUCER, True)
    # takeover/* are the producer-takeover PULL endpoints (spec section H). They
    # are console-only, not relay routes; the live takeover is /set/stint/<n>.
    if p and p[0] == "takeover" and len(p) >= 2:
        return Requirement(PRODUCER, True)
    if p == ["cockpit", "versions"]:
        return Requirement(PRODUCER, True)

    # Producer view; opening the page alone needs no step-up.
    if p == ["prod"]:
        return Requirement(PRODUCER, False)

    # Director: feed, schedule, timer, setup and pov control.
    # Switching race against qualifying is a Director-Panel control, auth-free on the
    # tailnet, so over the Funnel it is director-tier without step-up: as
    # producer plus step-up the panel's plain relayCall got a 403. `["mode"]` alone
    # is not a route and falls through to NOT_FOUND.
    if len(p) == 2 and p[0] == "mode":
        return Requirement(DIRECTOR, False)
    if p == ["next"]:
        return Requirement(DIRECTOR, False)
    if len(p) == 2 and p[0] == "next":
        return Requirement(DIRECTOR, False)
    if len(p) == 2 and p[0] == "prev":
        return Requirement(DIRECTOR, False)
    if p == ["reload"] or (len(p) == 2 and p[0] == "reload"):
        return Requirement(DIRECTOR, False)
    if len(p) == 3 and p[0] == "set":          # ["set", A|B, n]; stint handled above
        return Requirement(DIRECTOR, False)
    if len(p) == 3 and p[:2] == ["resync", "stint"]:   # in-session director recovery
        return Requirement(DIRECTOR, False)
    if p == ["panel"]:
        return Requirement(DIRECTOR, False)
    if p and p[0] == "pov":                     # all /pov/* are control
        return Requirement(DIRECTOR, False)
    if len(p) == 3 and p[0] == "feed" and p[2] in ("activate", "deactivate", "quality"):
        return Requirement(DIRECTOR, False)   # feed arm/disarm (#492) + quality profile (#493)
    if p and p[0] == "obs":                     # all /obs/* are relay-mediated OBS control
        return Requirement(DIRECTOR, False)
    if p and p[0] == "parts":                   # relay-mediated broadcast Part control (#395)
        return Requirement(DIRECTOR, False)
    if p and p[0] == "buttons":                 # /console/buttons/* -> Companion proxy (#236)
        return Requirement(DIRECTOR, False)
    if p and p[0] == "setup" and p != ["setup", "data"]:
        return Requirement(DIRECTOR, False)
    if len(p) >= 2 and p[0] == "timer" and p[1] != "data":
        return Requirement(DIRECTOR, False)
    if p == ["schedule", "set"] or p == ["qualifying", "set"]:
        return Requirement(DIRECTOR, False)
    if p == ["schedule", "data"] or p == ["qualifying", "data"]:
        # These carry per-stint stream URLs, so a commentator must not read them
        # over the Funnel. The panel is the sole consumer.
        return Requirement(DIRECTOR, False)
    if p == ["event", "title"]:
        return Requirement(DIRECTOR, False)
    if p == ["submissions"] or (len(p) == 2 and p[0] == "submissions"):
        return Requirement(DIRECTOR, False)
    if p and p[0] == "cues":                    # /cues/send|data|presets|reload
        return Requirement(DIRECTOR, False)
    if p and p[0] == "substitution":            # /substitution/latest (GET) + /note (POST)
        return Requirement(DIRECTOR, False)

    # Race control: the page, its redacted-schedule data endpoint, and the
    # quick-note send plus presets (#244, #376). The desk reuses the ANY cockpit
    # monitors below; only these are race_control-gated.
    if p == ["race-control"] or (len(p) == 2 and p[0] == "race-control"
                                 and p[1] in ("data", "cues", "presets")):
        return Requirement(RACE_CONTROL, False)

    # Health monitor: the page and its combined data endpoint. It carries no stream
    # URLs, so any authenticated console subject may view it, the same tier as the
    # cockpit monitors. takeover/health is the producer step-up pull, already
    # matched above.
    if p == ["health-monitor"] or p == ["health-monitor", "data"] or \
       (len(p) == 3 and p[:2] == ["health-monitor", "assets"]):
        return Requirement(ANY, False)

    # Event notes: one shared read-only list for director, commentator and race
    # control. No stream URLs, so the same tier as the cockpit monitors.
    if p == ["event-notes", "data"]:
        return Requirement(ANY, False)

    # Commentator: own-row stream-link submission.
    if p == ["submit"] or p == ["cockpit", "submit"]:
        return Requirement(COMMENTATOR, False)

    # Any authenticated subject: read-only monitors and identity-forced chat.
    # ["console"], ["data"] and ["program"] are console-only shell and landing
    # pages, not relay-route mirrors.
    if p == ["logo"]:
        return Requirement(ANY, False)
    if p in ([], ["status"], ["console"], ["data"], ["program"]):
        return Requirement(ANY, False)
    if p and p[0] in ("hud", "preview", "splitscreen"):
        return Requirement(ANY, False)
    if len(p) == 3 and p[:2] == ["overlay", "fonts"]:
        return Requirement(ANY, False)
    if p in (["timer", "data"], ["setup", "data"]):
        return Requirement(ANY, False)
    if p in (["chat", "data"], ["chat", "reload"], ["chat", "send"]):
        return Requirement(ANY, False)
    if p in (["cockpit"], ["cockpit", "data"], ["cockpit", "program"],
             ["cockpit", "program-audio"],  # on-air program-audio MP3 stream
             ["cockpit", "timer"], ["cockpit", "chat", "data"],
             ["cockpit", "chat", "send"],
             ["cockpit", "cues"], ["cockpit", "cues", "ack"],
             ["cockpit", "rc-notes"],      # RC->commentator notes read (#376)
             ["cockpit", "cue-back"]):     # commentator->director cue-back send (#377)
        return Requirement(ANY, False)

    # Cockpit graphics browser: read-only list plus file serve, the same tier as
    # /cockpit/program. The file route is 3 segments; resolve_graphic validates
    # the filename server-side, not here.
    if p == ["cockpit", "graphics"]:
        return Requirement(ANY, False)
    if len(p) == 3 and p[:2] == ["cockpit", "graphics"]:
        return Requirement(ANY, False)

    # Root graphics browser: the same read-only list and file serve as
    # /cockpit/graphics, reached without the /cockpit prefix so the Director Panel
    # widget also loads on the token-less tailnet /panel. Under /console it must be
    # ANY so the gate's generic ALLOW fall-through serves any authenticated
    # subject. The file route is 2 segments, validated by resolve_graphic.
    if p == ["graphics"]:
        return Requirement(ANY, False)
    if len(p) == 2 and p[0] == "graphics":
        return Requirement(ANY, False)

    return None


def decide(roles, segments, method="GET", has_step_up=False):
    """Policy decision for a /console request. Identity is assumed already
    verified by the caller; *roles* is the resolved capability set (possibly
    empty), *has_step_up* the caller's shared-producer-secret check result.
    Returns ALLOW / FORBIDDEN / STEP_UP_REQUIRED / NOT_FOUND."""
    req = min_capability(segments, method)
    if req is None:
        return NOT_FOUND
    if req.capability != ANY and req.capability not in roles:
        return FORBIDDEN
    if req.step_up and not has_step_up:
        return STEP_UP_REQUIRED
    return ALLOW
