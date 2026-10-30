# Shared driver radio service

Epic #969, delivery #972. One relay-owned service maintains text/evidence and
work eligibility. Native capture/playback is supplied in #976 and optional
synthesis/explanation adapters in #973. Browser views are controls/text clients.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Shared deterministic work | service refresh/cache and relay bootstrap | multiple clients see identical message IDs | duplicate polling cannot multiply assessments/alerts | concurrent real HTTP clients |
| Fuel urgency and rearming | threshold state machine | warning, escalation, refill reset | unchanged risk, noisy threshold, stale replay | clock-driven snapshots and actual queue states |
| Completed-lap pace notices | comparison evidence key and cooldown | repeated slowdown on new completed lap | packet polling, isolated outlier, cooldown repetition | assessment/message evidence IDs |
| Bounded text-first history/work | message ring and priority queue | immediate English answer/evidence | flood, supersession, expired backlog | internal bounds plus HTTP history |
| Lifecycle invalidation | session/setup/access revision and live state | current request and attachments | profile/session/context correction, pause, revocation, late completion | delayed fake worker and signed HTTP |
| One native playback slot | take/finish ticket and cancellation event | exclusive owner consumes once | concurrent owners, urgent preemption, expiry without stop acknowledgement | threads assert slot remains held until stop acknowledged |
| Relay ownership and shutdown | bootstrap start after port bind, close before hard exit | service works without a browser | no worker on losing relay, close cancels active ticket | lifecycle test seams and synthetic e2e |

## Bounds and transitions

History holds 32 messages; pending playback holds four. Routine/requested text
expires after 30 seconds, urgent fuel after 15. One message has one immutable
binding, settings/setup epoch, creation time, expiry, severity, text and evidence.
Text publishes first. Attachments must match its current epoch, ID and deadline;
late results cannot revive cancelled text or speech. Invalidated history is
cleared on session/context/setup/access changes. Non-live data cancels queued
and active playback immediately. Reconnect reads text only and cannot enqueue
old alerts. A read never claims playback.

AI interpretation has independent eligibility until the message deadline. A
completed, failed or unconfigured audio attempt cannot cancel a still-current
text interpretation or cause audio to replay. Requester revocation and epoch
changes cancel both kinds of work. Bounded follow-up context includes prior
answers and validated interpretations, not only question topics.

Fuel announcements occur at severity crossings. Urgency can escalate once. A
physical refill accumulating at least two litres or a usable range above warning+0.5 laps
rearms the warning. Session/setup changes reset the episode. This avoids chatter
around the warning threshold. Pace notices require a new completed-lap comparison
and a 60-second cooldown. Unsupported or stale data creates no automatic notice.

Requested presets are a fixed enum. The request must echo the current binding;
identical preset requests with unchanged evidence within two seconds share a
message. Automatic alerts and voice questions are never preset substitutes.
Admission is bounded and
returns `rate_limited` when a native queue cannot accept another routine message.
Urgent fuel cancels a routine active ticket and waits for its stop acknowledgement
before granting the only playback slot. Cancelled/expired tickets never free that
slot until the owner has stopped and acknowledged completion. This prevents
simultaneous output even if a worker is slow to react. No browser playback claim
or public audio-control API is added.

## Message source and feature gating

Every public message carries `source`: `coach` for AI Coach messages, `engineer`
for everything else (preset answers, voice-question answers, proactive fuel and
pace notices). The service runs while either feature is switched on, but each
feature's work is gated on its own switch: presets (`request`) and voice
questions (`question`) raise `feature_disabled` with "Enable the AI Race Engineer
for this profile" while the engineer is off, and proactive fuel/pace notices are
not published; `publish_coach` returns nothing while the coach is off. Any
feature switch changes the settings revision and so invalidates current history.

A native voice request for a switched-off feature is refused with that
feature's `feature_disabled` text as a `refusal` message (`source` = the refused
feature, no AI interpretation). It is shown as text and queued for speech while
voice is on and the session is live; it is never stored as an answer. A voice
question while the engineer is off gets the engineer text; a recognised coaching
command or question while the coach is off gets "Enable the AI Coach for this
profile".

`hud_text(source, now=None)` gives the broadcast HUD the deterministic `text` of
the newest message of that source. It is empty (`None`) when that newest
message is superseded, cancelled or expired, when its wall-clock `expires_at` is
reached, when its epoch or binding is no longer current (session, context or
setup change clears history), when the session is not live (pause or replay),
or when the feature is off. It does not fall back to an older message, never
returns the spoken question or an AI interpretation, and skips refusal hints.
It reuses the existing refresh transitions; no separate timer exists.

## Routes

Current assignment and profile enablement are rechecked for every Console route.
`GET /console/driver-radio/snapshot` returns the current allowlisted assessment
and message state; `/messages?since=N` supports bounded incremental text reads.
`POST /console/driver-radio/request` accepts only preset and binding. Existing
setup/service writes operate on the same shared session. Unknown routes, forged
origins, stale bindings and revoked identities keep semantic error responses.
Producer administration can set validated assessment limits; old profile settings
migrate additively with the prior defaults. No credentials, device identifiers,
archive data or arbitrary prompts appear in remote messages.

## Review and runtime evidence

The independent review reproduced two fuel lifecycle defects. Recovery now
cancels old queued/active fuel alerts, and a refill is accumulated across rising
samples rather than requiring a two-litre jump in one packet. Both have red/green
regressions. Cancellation retains the active output slot until acknowledgement.
Profile voice opt-in also gates playback itself, separately from radio text access.

Remote requested work retains up to 16 current requester authorizers. Token and
assignment revocation are rechecked before attachments/output; shared work remains
valid while another current requester authorizes it. No tokens or authorizer
objects are serialized into the public message. Read-only clients share a
250-millisecond snapshot cache, while requests, attachments and playback force a
current-context check. Network response writes occur outside the access lock.

Twenty-six protections were falsified individually at their intended assertions
and restored: acknowledgement identity, occupied playback slot, voice/profile
opt-in, closed request, expiry, history/queue bounds, deduplication, pause,
epoch invalidation, attachment epoch, request binding, urgent preemption/priority,
requester revocation, reader cache, attachment freshness, fuel recovery, gradual
refill, range hysteresis, pace cooldown, audio/explanation bounds, active eviction
and additive profile-limit migration. Concurrent signed HTTP tests share IDs and
prove token revocation during active output. The synthetic e2e suite reports 17
passes plus telemetry replay; the relay POV regression also passes.
