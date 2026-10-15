# AI result and persistence requirements

Requirement matrix for #820. Narrative interpretations remain unproven; only
supplied factual values and existing telemetry identities are deterministic.

| Criterion | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Schema, three priorities, facts | result validator | exact package values | wrong schema/value/priority | controlled structured output |
| Existing lap/location links | reference resolver | selected lap and location | nonexistent lap/out-of-trace distance | report-to-telemetry navigation |
| Immutable provenance/history | profile store | completed snapshot | foreign/corrupt/incomplete artifact | persistent run and frozen package |
| Relevant staleness | package reconstruction | unchanged/unrelated note | selected context/source/reference change | immutable historical bytes |
| HTML/Markdown/input ZIP | safe renderer/allowlist | provenance and limitations | untrusted HTML/Markdown, noncompleted report | rendered export and ZIP contents |
| Recording lifecycle | guarded deletion | associated analyses removed | deletion during job, foreign paths | existing delete flow and confirmation |


The original result schema is version 1. Findings have priorities 1–3, interpretation,
possible causes, a selected lap ID, optional trace-bounded metre position and
supplied evidence. Exactly three exercises have distinct priorities 1, 2 and 3.
Schema/type/unknown-field checks reject unsupported measurement fields. Evidence
IDs and exact finite values must match the frozen package and relate to the cited
lap/comparison or selection statistic. Display retains measured/calculated/external
provenance; narrative truth is never claimed by deterministic validation.

Every input file has a persisted SHA256 identity, including the summary/prompt,
manifest and result schema. Completed reports are revalidated on read, and changed
input/prompt bytes are refused. Raw output and validation diagnostics remain local
on failure; incomplete runs can export their input but never a normal report.
History is profile-bound. Relevant source/context/definition/calculation/reference
changes derive a stale marker; an unrelated note/revision does not. Neither stale
nor interrupted status rewrites the historical run or input snapshots.

GET `/api/ai/history?rec=...`, `/job?id=...` and `/export?id=...&format=html|markdown|package`
are same-origin callbacks. Exports use base64 JSON downloads and fixed generated
filenames. HTML has escaped text and a restrictive CSP; Markdown escapes untrusted
link/image syntax. Input ZIP contains exactly the four package files. Validated
links target an existing lap/location, bind its profile/recording identity and
never change the active profile implicitly. Export links require the Control
Center to be running at the recorded local origin and the intended profile selected.
POST `/api/ai/package-export` exports a reviewed package without running a provider.

Deletion is serialized with machine-wide admission. Primary and explicit reference
sources must still exist with the same identity/hash under that lease before a new
job directory is created. Deleting a recording removes its own analyses, including
older source identities associated with the same immutable recording name. Shared
machine agent configuration remains separate. A reference snapshot already frozen
in a different recording's report stays with its owner and derives stale status.

Independent security review found a preview/admission race for deleted reference
captures. Deleted/changed reference tests reproduced it before the lease-bound check
was extended to every explicit source. Re-review has no remaining findings.
Controlled tests cover invalid formats/values/laps/positions, three priorities,
foreign profile access, source and reference deletion, stale context/track/calculation
versions, prompt tampering, escaped HTML/Markdown and exact ZIP contents.
Browser verification opened an exported URL at the exact selected lap and 500 m,
rejected a foreign profile and captured the deletion confirmation. The visual
harness checked 20 views without findings. All fixtures use synthetic telemetry.


Self-review reproduced conflicting factual snapshots sharing a stable lap ID
(e.g. a copied capture with a different index/reference annotation). Preparation
now refuses that ambiguity before inference, and validation independently refuses
it as an invalid report. Identical copies retain the selected recording as their
canonical telemetry view. Both failure paths have behavioral regression tests.


A controlled persistence failure after validation exposed a completed report body
on a failed run. Failure handlers now clear normal report bodies, and history
independently hides any retained body unless the outcome is completed. Raw output
and immutable inputs remain available for diagnosis/manual retry.


Twenty-one guards were individually falsified from the committed implementation:
strict integer types, unknown fields, existing lap, trace-bound location, exact
facts, three priorities, artifact-profile binding, prompt hashes, relevant stale
changes, unrelated revision exclusion, HTML and Markdown escaping, incomplete
bodies in history, persistence-failure bodies, reference admission, source hashes,
conflicting lap identities, deletion admission, visible foreign-profile guidance
and recording-identity navigation. Each failed the intended assertion and was
restored from the commit. The last two ran in the real local browser against
an isolated dev-build fixture. No native credentials or private capture was used.

Explicit comparison paths allow evidence from directly comparable laps or laps
sharing the selected reference. Unconnected lap evidence remains rejected. This
guard was independently falsified from the committed implementation. A real
subscription-authenticated Linux Codex 0.162.1 run with gpt-6.1-sol completed the
full package, strict validation, history and HTML/input-ZIP export pipeline.
Separate native runs verified unavailable-model failure and cancellation without
a completed report or a retained machine lease. All telemetry was synthetic.

On 2026-10-15, local macOS subscription runs also exercised an actual 20-lap
Grand Valley recording with saved race/stint context. Both Claude Code (`sonnet`,
reported as `claude-sonnet-5-5`) and Codex (`gpt-6.1-sol`, no reported model
metadata) completed with 18 usable selected laps. Both reports passed evidence
validation after reopening, and HTML, Markdown and input-package exports succeeded.
These runs exposed unreadable single-line detail JSON and an insufficiently
specified evidence/lap association in the prompt. Detail serialization and the
summary evidence catalogue now support bounded reads and explicit evidence IDs;
the validator still rejects missing, changed and unrelated facts.


## Driver review version 2

New packages request report schema 2 and template version 2. Saved schema-1 output
is still validated with its original contract, before any display enrichment.
A calculation-version change marks an older snapshot stale without rewriting it.

| Requirement | Production | Positive test | Negative test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Driver-first findings with action/check | versioned result schema + templates + validators | v2 concrete observation/action/check and declared reference | missing action, mismatched reference, invalid technical topic rejected; v1 retained | real Claude/Codex session reviewed against usefulness rubric |
| Frozen channel insights | package TRACE_KEYS + driver channels/focus | steering degrees, measured pedal transitions and matched same-stint pair | missing channels/partial transition never become zero or optimal targets; contrary stint/goal excluded | three raw recordings contain steering samples |
| Visual report | SVG chart renderer + report HTML | trend, equal-scale line, speed/pedal/steering overlays | gaps/unavailable channels and no compatible pair explicit; injection escaped | desktop/mobile/print browser inspection |
| Direct browser HTML | profile-scoped local route + app link | completed report HTML inline, stable reloadable URL | invalid ID/profile/origin/incomplete run semantic errors; CSP/nosniff | real saved and new reports opened without download |
| Provenance and historical reports | frozen store/render dispatch | v1 historical reports and exports remain readable | tampered snapshot still rejected | old report515 reused with original facts, new reports usev2 |
| Full de/en report presentation | language admission + localized report text and UI | German/English HTML/Markdown, labels, charts and feedback | unsupported language rejected, no English system chrome in German report | real de/en provider reports and browser review |

A finding names its driving topic, observation, interpretation, one next attempt
and a measurable check. Technique findings require a position. Reports allow zero
to five findings and zero to three distinct exercises; insufficient evidence must
not produce filler. A declared reference must be a direct, eligible comparison,
with matching car, track, compound, known stint and objective. The validator rejects
transitive references for schema 2. Historical schema-1 evidence rules are retained.
Only cited numeric facts are mechanically verified. Narrative correctness and
usefulness still require review; provider hypotheses do not become measured causes.

The package preserves `steer_deg` without a second conversion. Per-corner channel
metrics describe measured samples: input thresholds of 5% brake and 95% throttle
are diagnostics, not optimal targets. Incomplete channels do not become zero.
The bounded driver-focus catalogue combines compatible frozen corner and pedal/
steering facts. Internal reference selection stays within a known stint.

HTML contains script-free SVG lap-time trends, equal-scale position overlays,
speed, throttle, brake input and steering over distance for up to three locations.
Missing channels and sparse samples are explicit. Position overlays show measured
lines, never an ideal line. Full-session summary statistics may mix driving phases.
The charts use immutable input values, not model-generated coordinates. Desktop,
mobile and print layouts keep findings, actions and checks ahead of technical data.
Technical notes, limitations and full provenance are collapsed but inspectable.

New reports support `de` and `en`; other language requests fail admission. Report
headings, fact labels, charts, automatic limitations and report feedback follow the
selected language. Common racing terms and original technical identifiers in
provenance remain unchanged. Both HTML and Markdown escape provider text, including
the summary, and HTML escapes manual context captions.

GET `/api/ai/report?id=...&profile=...` reuses the completed-report export validation
and returns HTML inline. It uses the selected analysis-profile scope and the same
origin/Host checks as other analysis routes. The response is not cached, cannot be
framed and carries CSP and nosniff headers. The app links to this stable URL in a
new tab; HTML/Markdown/input-package downloads remain explicit secondary actions.
