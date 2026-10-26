# Private coaching history and debrief

History belongs to the exact profile and server-derived `local:producer` or
`console:<verified subject>` identity. Opening a desk, Engineer access or a shared
recording grant does not select another driver's history. Display names and raw
owner selectors are never accepted from a browser.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
|---|---|---|---|---|
| Private stable identity | Profile-local hashed journal selector | Local and full-length Console owners retain their own sessions | Foreign profile/owner, malformed selector, forged HTTP owner | Signed HTTP owner matrix |
| Compact provenance | Strict journal projection from accepted actor evidence | Original baseline, worked focus, latest observed change and source IDs survive without a recording | Trace/audio/transcript/provider fields never enter the journal | Real synthetic lap checkpoint and JSON inspection |
| Honest debrief | Completed/interrupted/context-invalidated segment summary | Stopped training retains provisional versus repeated improvement and unknown cause | A crash cannot invent a completed session or a causal result | Restart and session-end integration |
| Current reuse | Compatible current context plus explicit skill action | A prior skill may be suggested after current personal calibration | No historic timing becomes a new baseline; changed car/track/definition/grant cannot authorize reuse | Fresh-session replay |
| Bounded private storage | Atomic bounded JSON, advisory lock, private directory | At most 32 recent segments per owner, expired records pruned on access | Symlink/path/corruption/oversize/conflicting edits fail closed | Actual temporary filesystem, OS CI |
| Responsive controls | Existing actor worker performs journal IO outside state locks | Stop acknowledges and cancels speech while persistence is blocked | Late checkpoints cannot reactivate stopped/deleted history | Blocking IO integration |
| Retention and deletion | Owner-only revisioned deletion and bounded tombstones | Delete one/all; later checkpoints cannot restore deleted active training | Wrong owner/revision and delayed read after revocation | HTTP and browser concurrency |
| Private views | Shared detailed driver/producer component | Own history and end debrief are readable while another driver is training | Late responses cannot restore previous identity/deleted private DOM | Desktop/tablet/phone browser evidence |

The journal stores compact structured measurements and provenance, never raw
traces, audio, transcripts, credentials or arbitrary agent output. A training may
contain several context/calibration segments. Each segment preserves its own
baseline and definition; history never compares measurements across segments.
The limit is 32 segments and 90 days per owner, pruned on that owner's reads and
writes. Explicit deletion is available independently of recording retention.
Deleting an active segment also suppresses further retention for that training,
including later context segments, until an explicit fresh Start. At most eight
recent worked exercises are retained within each segment's byte bound.

Persistence uses the existing actor worker outside short state/access/telemetry
locks. Stop changes the actor token immediately. If a final checkpoint never
reaches disk, the debrief honestly uses the last stored checkpoint and remains
partial. A journal constructor performs no IO. After a successful relay bind,
reading or starting training marks older-generation unfinished entries interrupted;
a failed second bind cannot touch the running relay's history.

Historical observations remain historical. Reference provenance is not a retained
archive grant, and reference-derived details require current permission/source
checks before display or reuse. A next-session skill suggestion requires a known
compatible car, track, definition and conditions, explicit application, and fresh
personal calibration. Improvement records continue to state that cause is unknown.
History verifies at most four archive sources per response, matching the bounded
reference chooser. Unverified reference details remain hidden; the driver's own
worked skill category and measured lap observation can still form a partial debrief.

The existing exact coaching route returns private history on GET. A deletion uses
`expected/action/options`, with `action=delete_history`, `options={id:<segment or null>}`
and the history revision in `expected`. It never uses the actor revision or a
client-selected owner. Ordinary control/Stop replies perform no journal IO. A
failed history read leaves current coaching available. Responses recheck driver
and separate archive permissions after IO; browser state tracks history revisions
independently so an older response cannot restore a deleted summary.
GETs started while a command is in flight cannot establish browser state: their
newer request serial may still contain history from before the command committed.
The command acknowledgement wins in either response order, followed by a fresh
poll. Authorization failures are still handled immediately. Browser acceptance
first synchronizes the independent journal revision, then explicitly exercises
both GET/DELETE arrival orders without background polling masking a lost acknowledgement.

Archived history proofs bind raw recording and context-sidecar file identities,
plus the applied definition revision and at most four relevant local/bundled
definition paths. Physical definition changes that the engine has not applied
fail closed. Read the same provenance before and after obtaining detail and check
it again after the final archive and driver authorization I/O. Resolve that final
verdict before taking the last live snapshot; history rendering itself uses only
cached verdicts. A context/definition edit during a slow grant read cannot preserve
old focus details merely because the recording path and raw grant still match.
