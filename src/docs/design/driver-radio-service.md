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

Fuel announcements occur at severity crossings. Urgency can escalate once. A
physical refill of at least two litres or a usable range above warning+0.5 laps
rearms the warning. Session/setup changes reset the episode. This avoids chatter
around the warning threshold. Pace notices require a new completed-lap comparison
and a 60-second cooldown. Unsupported or stale data creates no automatic notice.

Requested presets are a fixed enum. The request must echo the current binding;
identical requests within two seconds share a message. Admission is bounded and
returns `rate_limited` when a native queue cannot accept another routine message.
Urgent fuel cancels a routine active ticket and waits for its stop acknowledgement
before granting the only playback slot. Cancelled/expired tickets never free that
slot until the owner has stopped and acknowledged completion. This prevents
simultaneous output even if a worker is slow to react. No browser playback claim
or public audio-control API is added.

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
