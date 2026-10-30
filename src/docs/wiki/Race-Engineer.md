# Race Engineer

Open **Console → Race Engineer** with your existing signed link or Discord login.
The producer enables **Allow Race Engineer** in the Control Center Telemetry controls. Assign the
additive Race Engineer Crew checkbox to each Engineer; Producers inherit access.
Directors, Commentators and Race Control require that explicit checkbox. Redeploy
the current Sheet webhook when adding the Crew field.

![Race Engineer analysis desk](images/console-race-engineer-desktop.png)

## Live values and analysis

The producer can replay a profile recording through the running relay. The desk
then reads **Replay · Recorded session and capture date · speed×** and receives the same source as
the broadcast HUD. It exposes a display label, never the producer's filename.

![Engineer desk receiving a replay](images/console-race-engineer-replay.png)

Every switch starts a separate pool, including stop and end of file. Live laps
driven during replay are absent from the live pool. With recording enabled their
details remain recoverable from the recording through the existing shared-reference
workflow. Without recording, those laps are lost. The desk states this limitation.

Lap and sector times use **m:ss.SSS** throughout the desk and Telemetry workspace
(for example, **2:05.678**). Time differences stay in seconds with a sign
(for example, **+0.125 s**).

Current lap, live delta, best completed time, race position and average fuel use lead
the desk. Under the current lap, **Predicted** is the lap time at the current pace: the
reference lap A's time plus the live delta. It reads **N/A** while there is no live
delta or the reference has no lap time. **Avg fuel · L/lap** averages every completed lap of the live session from
the first lap on, pit laps included; only a lap without an official time (the out lap)
is left out. A lap's use is the fuel it burned, so a refuel during a pit lap does not
cancel it; a lap without a fuel reading is left out, never counted as zero. Below it,
the tile estimates the laps left: the current fuel level divided by that average
(**≈ n laps left**). A second line gives the time left (**≈ m:ss time left**,
**h:mm:ss** from one hour): the laps left times the average lap time of the completed,
comparison-eligible laps (out, pit and partial laps excluded, so a pit stop does not
skew the pace). Either line reads **N/A** when one of its inputs is
missing. Fuel, theoretical best and
tyres follow, then large speed/gear/RPM/throttle/brake/steering values.
Select the comparison before inspecting the charts; channel and distance controls
are attached to the chart. One line below the selection reports the comparison:
the distance the delta covers, the lap time difference, or why none is shown yet.
The delta tile names the distance it covers, the best tile names its lap. A loss
carries a **+** sign, so gain and loss do not depend on color. As in the broadcast
HUD, an arrow next to the delta shows the trend over the last 1.5 s: **▲** the gap
grows (losing time now), **▼** it shrinks (gaining time now). Changes below 0.02 s
show no arrow, and a new reference or a new live lap starts the trend over.

**Shift points · lap in progress** lists each gear's reference RPM for the current car
next to the pre-cut RPM of its last upshift in the running lap, the difference and how
many upshifts were seen. The reference is the same full-throttle power-equality
estimate as in the Telemetry workspace, from the machine-local entry for the car;
ratios decoded from the car win over the stock source. It is not verified for the
event, and an early shift can be intentional (fuel, traction). An upshift whose phase
was not measurable reads **N/A**, never a number. Without a local entry the desk
names no target and points to **Telemetry → Data management → Shift references by
car**; the desk itself never downloads anything.

Beside the live values (below them on narrower screens) the **Track map** shows the
complete reference outline with the live car marker, and the **Program · OBS** tile
below it shows a still of the running OBS
program, refreshed about every 1.5 s, the same still Race Control shows. **Hide**
folds it to a small bar and the browser remembers the choice. The still is only
fetched while the tile is open and the tab is visible, and it stops with the access.

The desk reads the existing single GT7 source of a solo POV profile. Speed, gear,
RPM, throttle/brake, steering angle (degrees with **L**/**R**), race position, observed lap time, fuel and tyre temperatures refresh roughly
twice per second. Below them, an input trace draws throttle (green) and brake (red)
over the last five seconds, like the HUD trace. Every missing value reads **N/A**, without its unit; explanatory status sentences stay prose. The observed lap clock
and live delta describe received data; neither advances without new packets.
Race position reads **P15/15**, for example, from GT7's own-car position and field
size. Missing or inconsistent readings read **N/A**. Completed lap options
include the position measured at the line when GT7 provided it. No gaps to
other cars are inferred. Receipt time (24-hour) and age of the last telemetry packet remain
visible. During a replay the line shows the recorded time in UTC instead.

Select **Session** and **Lap B** to inspect retained current or prior-session laps.
Sessions show the track, vehicle name and lap count; unknown names fall back to
the car ID. Shared session and reference-lap options include the producer's label.
Lap A is chosen by this precedence, highest first: a producer-shared **Reference
source**; your own pick in **Lap A**; the shared pin; the best lap. For the live lap the
best is the lap the **Best completed** tile shows (same car and track, and no known
compound or settings contradiction); for an older lap B it is the fastest eligible lap
of B's session with the same car and track (or from the same session while the track
is unknown). The line under the selection and the delta tile name which one applies:
**Against best**, **Against pinned**, **Against your pick** or **Against** the
reference source's label, followed by **Lap N** and the distance the delta covers
(**· Lap N · through X m**).

**Pin lap A** shares the lap currently shown as A with every Engineer on the desk. Only
a completed, comparison-eligible lap of the live session can be pinned. Race Engineer
crew and Producers can pin; the last write wins. The pin survives page reloads and ends
with its session, and when a replay starts or stops. Its detail is protected from
eviction; if the lap's detail is no longer kept the note reads **Pinned lap detail no
longer kept**. The note under the button reads **Pinned by name · Lap N**. While A is
the pinned lap the button reads **Unpin lap N**; while A is another pinnable lap (your
own pick) it reads **Pin lap A** and replaces the pin directly. A pin applies to Lap B
in the pinned session.

Your own pick is local to your browser. It is dropped when a new best lap appears, when
the pin changes, when it is no longer usable for lap B, and on page reload; A then
falls back along the precedence above. With **Follow live**, every new live lap becomes
B; A follows the precedence and is no longer set to the previous lap. When no
comparable lap is available the desk shows **No reference**. While lap B is
an out lap, pit lap or first lap of a stint, or while its track is still being
recognised in the first lap of a session, it chooses none and says so; a chosen
reference source stays selected. The charts use the same validated distance stations, sector
and context rules as the local Telemetry workspace. Blue is A, orange is B, delta is
B minus A. The current lap is provisional; future curve sections and incomplete
200 m sectors say **Not yet driven** in muted text. Missing measurements remain
**N/A**, and untimed lap zero reads **Out lap**. Confirmed best and theoretical values exclude it.
While the lap grows, comparison retains the latest completed observed prefix and
refreshes it in the background. Its displayed distance identifies that prefix.
The map uses the complete selected reference lap as its outline, or the latest
confirmed session lap when no reference is available. Its outline and full-distance
chart range stay stable while the live trace grows, including during preparation.
The displayed best uses retained eligible summaries. The theoretical best composes the
best **larger sectors** over the same retained pool, not an unlimited full-session
archive: the layout's selected sector variant, or the automatic provisional thirds
when no reviewed track definition exists. It reads **unconfirmed** until reviewed
sectors and confirmed context back it. The tooltip also shows the 200 m mini-sector
sum; that is a diagnostic only, since picking the best of every 200 m runs below what
a driven lap allows.

Change the chart channel or distance limits to inspect braking, throttle, speed,
steering or gear. **Full distance** resets zoom. The map uses measured world
coordinates. The car marker comes from the latest measured snapshot, independently of
the selected lap endpoint. Missing coordinates and unrelated historical tracks
do not show a current car. Reviewed larger sections show named A/B times and gain or loss in separate groups
when both laps use the same confirmed definition. Expand each group to inspect its
diagnostic mini-sectors, or open the full diagnostic table below. A segment that
spans a section boundary appears in both affected groups; each section gain uses
its exact reviewed boundaries. Each group shows its distance range and the number
of its mini-sectors. On a lap in progress, the group the car is in says **Still driving**
and later groups say **Not reached**. Unconfirmed definitions retain their status.
A short trailing mini-sector is grouped with its predecessor for display; shared
analysis values are unchanged. Curves show their channel units, delta has a zero
line, and an empty comparison has a message, retains known distance and omits artificial axes. Turn labels
sit on the complete reference outline, also ahead of the car, avoid overlaps and have a textual legend. Declared settings and strategy use named
values rather than raw JSON. When the producer's relay has no track recognition data yet, the
desk says so next to the track, which reads **N/A**. Unknown tracks compare only within the same source and session; known
car, compound or strategy contradictions prevent the default comparison.

**Follow live** selects the newest active lap. Manually choosing a session or lap
turns it off. While it is off, new completed-lap notifications continue without
changing your chosen lap or zoom. Following live again or selecting the newest
current/completed lap clears the notice. Each browser has its own selection. Producer context revisions,
shared notes and limitations update without resetting that selection.

## Connection, history and access

Pause, menu, loading, preparation delay, stale telemetry and browser disconnection
have distinct labels. Five seconds without packets dims retained values. A browser
connection interruption keeps selection and zoom while it retries. A relay restart, and a
replay starting or ending, starts a new generation and clears the live selection and zoom.
The notice names which of them happened. A selected shared reference stays selected. Explicitly
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

Desktop and tablet place the charts beside the map. Smartphone stacks them while
keeping comparison feedback, source/lap selection, channel/zoom controls and
reviewed section summaries available. Its main values use two columns. Context,
notes, shared references and Crew chat follow the analysis on every screen.

![Compact phone view](images/console-race-engineer-phone.png)

See [Control Center activation and sharing](Control-Center#race-engineer-activation-and-reference-sharing)
and the [Console access mechanism](Console#race-engineer-access).

Live shift phases use packet timestamps, including accelerated replay. A new GT7
session clears its lap observations even when the car and lap number stay the same.
