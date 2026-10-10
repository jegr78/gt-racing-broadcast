# AI analysis packages

Requirement matrix for #818. Sources remain profile-scoped; packages contain
one completed GT7 session and explicitly selected compatible references.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Stable recording/session/lap selection | source loading, builder | repeated lap numbers across sessions | live/unfinished/foreign-profile selection | finalized synthetic recording |
| Numerical facts and provenance | metrics and comparisons | known lap/section deltas | bad timing, unusable lap, contradictory context | deterministic package assertions |
| Immutable context and definitions | snapshots | later context edits preserve package | stale index/source identity | snapshot fingerprints |
| Explicit references only | suggestions and selection | compatible own/external reference | unknown-track external reference, unselected suggestions | preview reference list |
| Templates, language and preview | prompt and manifest | all three templates, language override | invalid template/provider, size limits | readable summary/detail |
| One invocation and bounded package | writer | compact summary/local detail | oversize selection, no usable laps | serialized byte counts |

Manual report examples inspected locally and verified byte-for-byte against the
user's Mac copies on 2026-10-10 informed these methods: separate measured evidence
from causes, match tyre/stint contexts, inspect broader combinations through the
exit, retain quality limitations, and propose concrete counterchecks. Personal
recordings, report prose and race-strategy examples are not bundled.

## Contract and limits

`load_source` freezes the current index and selected context of a finalized,
profile-local recording. Completion requires a subsequent indexed GT7 session or
recorded lap progression beyond the race's total. A recorder stop alone does not
establish session completion. Unlimited practice requires a subsequent session
boundary. Live files, stale indexes and changed captures are refused.

IDs combine persistent recording identity, GT7 session and lap number. Good
capture quality, complete monotonic traces starting at zero and ending at the
supplied lap time are mandatory. Excluded laps retain diagnostic reasons and
valid captured GT7 times. Selected GT7-time totals include such trace exclusions;
they are sums of selected completed laps, not official result-screen times.
Statistics over usable laps do not imply an isolated pace or causal effect.

Context snapshots retain revision and the selected effective values, contributing
stints, selected lap roles and notes. They exclude later/overwritten strategy
notes, other sessions and unused track/shift snapshots. Explicit references carry
their own source hashes, context and version provenance. Snapshot fingerprints
allow #820 to check relevant changes without rewriting historical inputs.

Comparisons require usable, context-compatible laps with matching car/layout.
Unknown layouts only allow comparisons within the same recording/session. Mini
intervals remain diagnostics; reviewed larger-sector definitions are preferred.
Without an external recording reference, prompts prohibit ideal-line and optimal
braking claims, including when the driver chooses their own session best.
Numerical facts and comparison deltas are computed here; structured results cite
fact IDs and exact values. The result schema requires three exercises; semantic
validation and persistence belong to #820.

The three versioned templates ask for measured evidence, labelled interpretations,
context confounders and concrete exercises with counterchecks. Driving technique
follows the exit and adjoining combinations. Consistency separates phase/compound
and objective. Session overview distinguishes data completeness from pace
eligibility. Strategy recommendations remain outside v1.

Packages contain `detail.json`, `summary.md`, `manifest.json` and
`result-schema.json`. They are immutable exports, created in fresh directories.
The preview includes selected IDs, references, all included primary/reference
context and notes, goals/questions, limitations, byte counts and an explicit
subscription-cloud transmission statement. It never starts an agent.

Conservative limits are 48 KB summary/1 MB detail/40 laps for Codex and
40 KB summary/800 KB detail/30 laps for Claude, including references. These are
application limits, not guarantees about provider context windows or entitlement.
Oversize inputs require a smaller selection; no splitting or repair calls occur.
The selected report language defaults to the UI language and can be overridden.

## Review and runtime evidence

Independent security review identified unselected later-stint notes in snapshots
and omitted reference/stint notes in preview. Red/green tests reproduce both;
selected effective snapshots and a complete context/notes preview correct them.
The follow-up independent pass found no additional confirmed issues.

A read-only check on a copied real recording rebuilt 21 index rows, recognized
one completed session and packaged three usable laps in 349,791 detail bytes
and 4,805 summary bytes. Index and package preparation took 4.75 seconds locally.
The original recording and analysis examples were not modified or bundled.

Guard mutations separately removed partial-file, index-stamp, selection,
completion, changed-source, selected-lap, usable-data, reference-profile,
reference-compatibility, provider and package-limit checks. Each failed at the
intended assertion, including semantic error form. Separate mutations restoring
all stints, omitting reference notes and accepting all trace qualities failed
privacy/preview/quality assertions. The committed implementation was restored.
