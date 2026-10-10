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
