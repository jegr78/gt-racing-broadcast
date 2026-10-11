# Race position from GT7 telemetry

Requirements and verification for #854 under #850.

| Requirement | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Parse own race position and field size | GT7 packet parser at 0x84/0x86 | P15/15, P1/2 and P2/2, packet A/B/~ | Sentinel/zero/negative count and position beyond field rejected | Synthetic packet decode |
| Publish measured instruments | Live Store snapshot and signed API | Valid integer position/count | Menu/loading and inconsistent values unavailable | Real HTTP/browser |
| Preserve position at the lap line | Engine closed lap, shared offline index, public summary | Boundary packet position survives live/index paths | Open/session-abandoned capture cannot claim a finish position | Recording with position changing at line |
| Show position prominently and in lap options | Engineer desk | Pn/count tile and completed lap line position | Missing values remain unavailable; no invented competitor gaps | Three viewports and refreshed wiki screenshots |

The protocol uses signed 16-bit values. A positive field size and a position from
one through that size form a usable pair; no unverified game-specific maximum
is imposed. The packet which advances the lap counter supplies the line position.
An unfinished capture has no line position. Old recording indexes are rebuilt
when their schema changes, while old resume packets remain compatible.
