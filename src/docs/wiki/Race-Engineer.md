# Race Engineer

Open **Console → Race Engineer** with your existing signed link or Discord login.
The producer enables **Allow Race Engineer** in the local Telemetry view. Assign the
additive Race Engineer Crew checkbox to each Engineer; Producers inherit access.
Directors, Commentators and Race Control require that explicit checkbox. Redeploy
the current Sheet webhook when adding the Crew field.

![Race Engineer analysis desk](images/console-race-engineer-desktop.png)

## Live values and analysis

The desk reads the existing single GT7 source of a solo POV profile. Speed, gear,
RPM, throttle/brake, observed lap time, fuel and tyre temperatures refresh roughly
twice per second. Missing measurements say **Unavailable**. The observed lap clock
and live delta describe received data; neither advances without new packets.
Receipt time, data age and packet age remain visible.

Select **Session** and **Lap B** to inspect retained current or prior-session laps.
Select an eligible **Lap A** from the selected session, or choose a producer-shared
**Reference source**. The charts use the same validated distance stations, sector
and context rules as the local Control Center. Blue is A, orange is B, delta is
B minus A. The current lap is provisional; future curve sections and incomplete
200 m sectors remain empty. Confirmed best and theoretical values exclude it.
The displayed best uses retained eligible summaries. The theoretical value uses
the retained detailed pool, not an unlimited full-session archive.

Change the chart channel or distance limits to inspect braking, throttle, speed,
steering or gear. **Full distance** resets zoom. The map uses measured world
coordinates. Larger sectors and corner markers keep their reviewed/unconfirmed
status. Unknown tracks compare only within the same source and session; known
car, compound or strategy contradictions prevent the default comparison.

**Follow live** selects the newest active lap. Manually choosing a session or lap
turns it off. New completed-lap notifications continue without changing your chosen
lap or zoom. Each browser has its own selection. Producer context revisions,
shared notes and limitations update without resetting that selection.

## Connection, history and access

Pause, menu, loading, preparation delay, stale telemetry and browser disconnection
have distinct labels. Five seconds without packets dims retained values. A browser
connection interruption keeps selection and zoom while it retries. A relay restart
shows a new generation and clears the old live selection and zoom. Explicitly
shared recordings remain selectable historical sources and never replace live values.

Collection starts with the profile opt-in and incoming packets, without waiting for
an Engineer browser and without starting permanent recording. Without recording,
history is volatile and disappears on relay restart. Memory retains at most eight
sessions, 512 summaries and sixteen detailed completed laps totaling at most
16 MiB, plus the protected current lap. Older session details go first, then the
oldest current-session details. **Evicted**, **pending** and **unavailable** detail
states are visible. Matching recorded detail is recovered locally in the background
once for all readers; discarded summaries no longer supply selectable live IDs.

The producer shares finalized reference recordings and context explicitly from
**Telemetry → Race Engineer access**, using a readable label. Engineers cannot
browse the archive. Revocation, deletion or source replacement clears a selected
reference with a notice. Role removal or disabling the profile opt-in clears the
protected desk and stops further polling. Already transmitted browser data cannot
be recalled. Reload after the producer restores access.

The desk cannot start recording, edit context, assign tracks, delete, export or
download full recordings, invoke AI or change broadcast controls. Use existing
**Crew chat** and your team's voice channel for feedback. No driver-message channel,
shared Engineer notes, video monitor or extra driver ingestion is added.

## Smaller screens

Tablet supports the full analysis desk. Smartphone shows compact instruments,
status, session/lap selection, context and Crew chat. Extensive charts, comparison
controls and map are designed for larger screens.

![Compact phone view](images/console-race-engineer-phone.png)

See [Control Center activation and sharing](Control-Center#race-engineer-activation-and-reference-sharing)
and the [Console access mechanism](Console#race-engineer-access).
