# Telemetry recording selection

Issue #829 distinguishes a recording file from a GT7 session inside that file.
Selection lasts for this page visit and defaults to the selected recording.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Restrict comparison facts to included recordings | telemetry_laps_data, laps HTTP route | Selected file owns all returned laps and best values | Empty selection returns no laps or best; invalid names rejected | Actual cached recording fixtures and HTTP callback |
| Offer readable comparable recordings | Pool metadata and tmRecordingLabel | Weekday/date/time/count, newest first | Undated name safely falls back to stem | Real browser with two recordings |
| Group A/B by recording in driving order | tmFillPickers | optgroups, local best and overall best labels | No fallback when selected recording has no other pace lap | Browser DOM and rendered screenshot |
| Keep all comparison surfaces consistent | tmSelectB and recording menu | Checkbox/all/only-this updates pool, charts and Best | Excluded B cleared; old asynchronous reply discarded | Held-response browser test |
| Preserve explicit saved report comparisons | ai-analysis.openComparison | Confirm identity then include requested recording | Replaced identity never expands selection | Cross-recording report browser callback |
| Default and reset selection correctly | tmSelectRec and tmReset | Only selected recording by default | Profile change clears previous selection | Browser callbacks |

The HTTP `recs` query is a comma-separated list of recording stems. An omitted
query preserves the existing API pool; an explicit empty list selects nothing.
Names resolve only within the active profile. Available recording metadata is
returned independently of the chosen subset so the menu can add excluded files.
