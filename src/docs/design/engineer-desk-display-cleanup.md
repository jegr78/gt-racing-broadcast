# Engineer desk display cleanup

Requirements and verification for all eight items in #853 under #850. Nothing is deferred.

| Item | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Readable declared context | Desk context formatter | Named settings and strategy values | Empty objects omitted; arbitrary text stays text | Signed desk and browser assertions |
| Operator-facing history text | Desk history/reference copy | Recovery and availability explained | Internal counts and data-recall warning absent from desk | Browser text |
| Missing versus not driven | Tiles, lap labels and sector cells | Muted not-driven states and out-lap label | Missing confirmed data stays Unavailable | Actual provisional prefix plus boundary fixtures |
| Delta axes and zero | SVG graph | Seconds and zero line with known full distance | Empty graph keeps known distance only; no invented range | Browser SVG assertions |
| Tiny trailing segment | Mini-sector presentation | Trailing remainder grouped with preceding segment | Missing/incomplete values cannot produce a summed time | Browser 1003 m fixture; shared analysis unchanged |
| Turn label collisions | Map label placement | Distinct bounded labels for adjacent turns | Unplaceable label has a readable legend entry | Browser overlapping-apex fixture |
| Reviewed larger sections | Section table | Names, A/B times and gain/loss for the same reviewed definition | Unreviewed/mismatched definitions never claim a matched delta | Real synthetic reviewed definition and browser |
| Plain terminology | Tiles/source/receipt/status | Current lap, best lap, theoretical best and last packet age | No duplicate data-age label or retained-detail jargon | Three viewport screenshots |

The presentation groups a trailing incomplete 200 m block with its preceding
block. It sums only known times and preserves all shared numerical analysis and
raw distance stations. A pending/future block never supplies a complete time.
Larger-sector comparisons require matching reviewed definition and variant.
