# Shared live telemetry preparation

Issue #824, part of #822. One worker consumes accepted parsed packets when the
profile-owned Engineer setting is enabled. Browser count never controls collection
or recording. The existing recorder and broadcast paths keep their own lifecycle.

| Requirement | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Authoritative timing and observed provisional prefix | Shared incremental `gt7_laps._Replay` and `_indexed_lap` | Same circle packets match the offline index | Unconfirmed laps never enter confirmed best | Worker publishes after GT7 time confirmation |
| Stable identities and incompatible sessions | Generation, collection epoch, session sequence and lap sequence | Repeated lap numbers remain distinct | Car/session/recognized track changes separate pools | Independent snapshot/detail readers |
| Receipt and stale state | Monotonic receipt clock, immutable instrument values | Pause/menu/loading reported separately | Five seconds without input is stale, values never advance | Snapshot reads during idle |
| Finite history | Bounded packet queue, active samples, details, summaries and sessions | Recent details and active lap survive eviction | Older details explicitly evicted | Long synthetic sessions |
| Asynchronous shared recovery | One bounded recovery queue and cache | Recording detail restored with original identity | Volatile or deleted detail stays unavailable | Concurrent readers share one task |
| Context revision | Worker reads applicable context and annotates with existing helper | Manual provenance and revision retained | Context errors leave explicit limitations | Revised comparison snapshot |
| Responsive ingestion | Nonblocking enqueue, analysis outside UDP path | Packet submission while preparation is blocked | Queue overflow invalidates capture quality | Timed load fixture |

Limits are per relay: 2048 queued packets, 20,001 active distance samples, four
pending lap closures, eight session summaries, 512 lap summaries, sixteen complete
details and 16 MiB of serialized detail data. Detail entries are at most 1 MiB.
Recovery uses one worker, sixteen queued requests and an eight-entry/16 MiB cache.
Summaries omit all traces/positions. The active lap is separate and never evicted.
Each trace uses the existing 40 km distance bound and 5 m stations. Python object
overhead exceeds serialized size; these item and distance caps also bound it.

Prior-session details go first. In a long current session the oldest completed
details go next. Lap/session summaries are finite too, so discarded summaries no
longer have selectable identities. Recording-backed recovery is local and shared;
it never replaces current instruments, generation or active lap. Without recording
history disappears on restart. Pre-restart recordings must be explicitly granted
by the producer before the Console can request their preparation.

The recorder's session counter can differ from live collection, especially when
collection is toggled or recording begins mid-session. Recovery matches the
observed absolute lap start and GT7 lap number instead of assuming those counters
are equal. A context session that cannot yet be mapped stays unconfirmed. Valid
background recovery can establish the mapping without replacing live instruments.
Context caches include the bound recording identity and revision; a replacement,
missing or invalid sidecar clears prior confirmations until a valid read returns.

The independent review reproduced and corrected worker termination after source
deletion, cached-summary reads after identity replacement, late recovery target
loss, context revision collisions across sources, stale confirmation after context
failure, epoch-offset reuse and unvalidated context source replacement. Focused
fixtures cover each case, including recovery after valid context returns.

Twenty-eight assurances were individually broken at their intended assertions
and restored: collection/profile ownership, queue and history/sample/byte caps,
GT7 confirmation, stale receipt, source/context identity, context failure,
late recovery targets, prefix byte/integrity/append behavior, original measurement
identity, pending/expiry boundaries, recorder lifecycle and known comparison
conditions. The budget fixtures force actual serialized context growth. Active
recording tests include both a mid-lap prefix and a prefix closed before its GT7
time arrives. An expired confirmation is unavailable rather than retried forever.

Track-definition stamps include device/inode identity and ctime as well as size
and mtime, so atomic replacements with equal size/mtime invalidate live and
recording-backed preparation. Integration tests await exact public fingerprints
and context revisions from the worker. Evidence: `engineer-definition-invalidation.md`.
