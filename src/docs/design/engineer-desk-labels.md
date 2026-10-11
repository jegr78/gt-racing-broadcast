# Engineer desk labels and live position

Requirements and verification for #852 under #850.

| Requirement | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Session car name, including first provisional lap | Live session metadata and API projection | Actual CarDB/FakeCars name appears with track/lap count | Unknown car falls back to ID; no extra private fields | Signed HTTP and browser option |
| Granted reference labels | Desk reference/session/lap options | Shared label appears on session and A lap | HTML labels stay text; revocation clears protected sources | Existing granted reference browser flow |
| Manual-only new-lap notice | History revision and Follow live/selection handlers | Manual view accumulates newly closed lap IDs | No count while following; follow/latest selection clears | Real packet replay and independent browsers |
| Current measured car marker | Snapshot instruments and map transform | Car moves while inspecting a confirmed lap | Missing/nonfinite/menu coordinates and different track do not draw a car | Synthetic position packets and browser circle coordinates |

Marker coordinates come from the current measured snapshot, independent of selected
trace endpoints and comparison preparation. A known matching layout or the same
live session supplies the coordinate cohort; an unrelated historical map does not
claim a current car. Paused/stale values retain the desk's existing dimmed state.

Snapshot-only marker updates preserve the last calculated live delta while a
manual comparison request is pending. A browser regression holds that response
and observes a newer snapshot before checking the unchanged delta.
