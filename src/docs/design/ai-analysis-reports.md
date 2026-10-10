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


The result schema is version 1. Findings have priorities 1–3, interpretation,
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
