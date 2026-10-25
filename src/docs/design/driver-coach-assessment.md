# Deterministic coach assessment

Delivery #979 of #977. Assessment consumes authorized frozen live/reference rows;
calibration, ownership, focus retention and provider scheduling remain separate.
It reuses `ai_driver_review.channels`, `gt7_track_definitions.corner_metrics` and
the existing distance/time interpolation. Completed-session analysis is unchanged.

| Requirement | Production path | Positive case | Failure case | Runtime evidence |
| --- | --- | --- | --- | --- |
| Measured pedal transitions | Existing driver channel calculator | Observed brake onset/release and full throttle after minimum speed | Missing samples and transitions outside the observed section | Aligned five-metre synthetic traces |
| Steering, gear, speed and line observations | Bounded per-section metrics | Available channels produce typed evidence | Missing/non-finite channels produce no corresponding diagnosis | Synthetic channel removal and spatial offsets |
| Observation, interpretation, experiment and check | Structured findings | One qualitative experiment with an explicit timing check | No ideal line, hydraulic pressure, apex inference or promised gain | Inspect actual returned prose and exact fact values |
| Following passage and combinations | Entry-to-following-exit comparison | Retain corner and downstream timing separately | Faster corner followed by a greater loss is a tradeoff | Connected two-corner traces |
| Personal references and recurrence | Compatible own cohort | Own laps support variation and demonstrated capability | No external target and a one-off large outlier | Repeated small versus isolated large differences |
| Reviewed passage identity | Current frozen definition fingerprint | Reviewed name/number may be spoken | Provisional sections never become official turn names | Definition review flag and identity mismatch |
| Safe fallback | Deterministic general hint | Usable lap time and personal variation without geometry | Unsuitable lap, sparse trace, incompatible reference | Actual Live Store packets plus incomplete synthetic inputs |

Screening thresholds suppress changes at a single distance station. Pedal station
differences require two five-metre stations, steering five degrees, speed two
km/h and sampled line separation one metre. These are noise screens, never
recommended driving targets. Time changes use at least 0.05 seconds or 0.5 percent
of the measured check window. Repeated support sorts before isolated magnitude;
the focus engine later applies the selected training objective.

A corner experiment includes its measured exit and the following corner or
connected combination. A faster isolated corner is never called improvement when
the following window loses more time. A single measured gain remains provisional;
repeated-attempt progress belongs to #980. Missing downstream evidence prevents a
technique success claim. General personal consistency feedback remains available.

Each finding carries exact measured/interpolated evidence with units and lap
provenance. Brake percentage is pedal input, and the slowest measured station is
not called the apex. Action text contains a qualitative experiment, no fabricated
numeric target. Traffic, weather, tyre wear, setup and aids are not inferred.
Public results omit raw traces and filesystem paths.

Work is bounded to eight personal cohort rows, 20,001 stations per trace,
128 sections and 24 returned findings. Distance indexes slice only the current
section before applying existing metric calculators. On the synthetic 2,001
station, 12-section, nine-lap replay, local calculation fell from 0.24 seconds to
0.09 seconds after indexing. This is measured development evidence, not a device
latency guarantee. Live orchestration must run assessment outside telemetry and
view/control locks.

An over-budget definition or an incomplete connected combination cannot support
a per-section success claim. The evaluator falls back to the general hint rather
than shortening the check window. Unrepresentable coordinate differences make
line evidence unavailable while preserving valid pedal and timing observations.
