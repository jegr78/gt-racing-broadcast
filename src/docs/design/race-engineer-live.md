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
