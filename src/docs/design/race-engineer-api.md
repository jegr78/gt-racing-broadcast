# Race Engineer analysis API

Requirements and evidence for #825, within #822.

| Requirement | Production path | Positive evidence | Failure evidence | Runtime evidence |
| --- | --- | --- | --- | --- |
| Current identity, role, profile opt-in on every read | Console gate and profile access store | Signed Engineer and Producer requests | Role removal, disabled profile, unsupported methods | Real ephemeral HTTP server |
| Explicit reference grants on lists, details and comparisons | API selector and access store | Granted recording recovered asynchronously | Forged ID, revoked/deleted/replaced reference and cross-profile query | Real recording and HTTP requests |
| Public versioned allowlist | API projection | Instruments, identities, quality, trace and context | Injected private fields absent at every depth | Serialized HTTP payload |
| Thin current values and version-aware history | Live publication | Current trace omitted, unchanged histories omitted | Context/session changes advance history revision | Concurrent readers |
| Shared numerical and context rules | Shared comparison helper, lap/context/definition modules | Offline trace, sectors, delta and eligibility equality | Unknown track, compound/strategy conflicts, provisional future | Synthetic packets and recording |
| Shared bounded asynchronous preparation | One comparison worker, existing recovery worker | Identical clients reuse a prepared pair | Slow preparation cannot block HTTP or ingestion, bounded queue/cache | Barrier-controlled preparation |
| Recorded sector selection with independent live identity | Worker definition decoration | Recording session selection matches offline analysis | Different live/recorded counters must not choose a default variant | Annotated live row retains its opaque live IDs |
| Current context and reviewed definitions | Live/recovery worker annotation | Context revisions and read-only notes | Invalid context and definition remain unconfirmed | Producer context edits |

## Contract

All routes below are GET routes under `/console/race-engineer`. They require current
Engineer capability and the current profile's opt-in. There is no profile/path/filename
selector. All successful data responses have `version: 1` and a relay `generation`.
Errors use `{error: {code, message}}` with generic messages.

- `snapshot`: measured instruments, receipt and age, current lap summary, best and
  theoretical values from the bounded retained pool. No history or trace payload.
- `history?since=REVISION`: sessions and retained lap summaries. An unchanged revision
  returns `changed: false` without arrays. A changed revision returns a bounded replacement
  list, including explicit detail eviction. Clients discard IDs no longer present.
- `lap?id=ID`: asynchronous detail with `state: ready|pending|unavailable|evicted`.
- `reference?id=GRANT`: asynchronous authorized reference summaries and shared notes.
- `compare?a=LAP_ID&b=LAP_ID`: shared asynchronous A/B result. Delta is B minus A.
  Archived lap IDs are `r:GRANT:SESSION:LAP` and never resolve without a current grant.

The access route retains its existing grant list and adds live analysis availability.
Unknown/duplicate query keys, invalid revisions and malformed IDs return 400. Unknown
routes and POST methods return 404. Other unsupported methods use the HTTP server's 501 response. Revoked reference selectors return 410 with
`reference_unavailable`; role/feature loss returns 403. No route builds a full index
on its HTTP thread. Reference data remain historical and cannot replace live values.

Manual notes and context are explicitly shared by the producer. Automatic projections
omit paths, filenames, recorder counters, source addresses, streams and credentials.
Measured channels remain distinct from declared context and inferred capture quality.


## Preparation limits and verified assurances

Comparison preparation uses one worker, at most sixteen queued pairs and sixteen
cached replies totaling at most 16 MiB. A reply is at most 2 MiB. These limits also
apply to failed work; oversize results are explicitly unavailable. Multiple readers
of the same measured/context/definition pair share one task. Current detail updates
remain provisional and never contribute to confirmed best or theoretical values.

Six focused tests exercise real signed HTTP, context/definition revisions, compact
and incremental publications, comparison math and bounded concurrent preparation.
The actual Control Center delta function is executed in Chromium against the same
inputs, including negative half-millisecond rounding and unequal closing stations.
Twelve assurances were individually falsified and restored: thin snapshots,
incremental history, recursive projection, query allowlist, post-preparation grant
validation, observed-sector coverage, context/track pool rules, cache count/bytes,
entry bytes and definition revision invalidation.


Recorded context selections use the recording session counter, which can differ from
live collection when a recorder starts later or collection is toggled. Definition
annotation maps to that recording counter and restores the live counter afterward;
opaque live session/lap identities do not change. The regression fixture compares
the selected variant and larger sectors with the ordinary recording analysis.
