# Driver change timing: a hint for an early handover

Status: draft, waiting for a measurement on a real driver change (see "Before building").

## Problem

In an endurance event every stint runs in its own GT7 lobby. At the stint switch the
outgoing driver A drives into the box in lobby N, and the incoming driver B, who has
waited in the box of lobby N+1 since the end of the first lap, starts the pit crew's
work there. The team calls the handover by voice.

The league rule: B may start the crew's work only once A's car has come to a standstill.
The league allows a small tolerance (usually 3 to 4 s). Race Control checks this today
after the event, from the replays of both lobbies: A's pit entry or standstill, and the
start of the crew's work for B.

Telemetry can give Race Control both moments during the event, so a suspected early
handover reaches the desk while the race runs and the team can still be told. The
replay check after the event stays the decision; this feature only prioritises it.

## What the league defines (not this design)

- The rule and its tolerance. The tolerance is a league setting
  (`handover_tolerance_s` in `profiles/<name>/driver-race.json`, default 3 s), shown
  next to each hint.
- Whether refuelling or a tyre change is expected. Neither is mandatory; leagues and
  events differ.
- What Race Control does with a hint.

## How the data of A and B arrive together

The relay already accepts two senders per team in endurance (`driver_ingest.Hub`). Each
driver registers for their entry and stint segment through the invite link and gets a
code. During the change both run at once: A's sender from lobby N, B's from lobby N+1.
The relay dates every sample on its own clock (packet id within the batch), so both
moments lie on one time axis although the drivers sit at different machines.

Precondition, part of the driver guide: A keeps the sender running until the car stands
in the box, B starts the sender before the stint switch.

## The two moments

Both are taken from the samples of the change window (`CHANGE_WINDOW_S` after the stint
switch), per team.

- **T_stop (A):** the first moment A's car stands in the box: off the racing line by more
  than `BOX_LATERAL_M` and below 1 km/h for at least 1 s. GT7's auto drive takes the car
  from the pit entry to the box; its length depends on the track, so the pit entry is not
  used.
- **T_service (B):** the first sign of the crew's work on B's parked car, whichever comes
  first:
  - `fuel`: fuel rises by more than `FUEL_RISE_L` (refuelling started),
  - `tyres`: the tyre temperatures jump to fresh tyres (to be confirmed, see below),
  - `departure`: the car leaves the box (the service is over; a lower bound only).

  The kind of signal is stored with the moment.

## The hint

- **Early:** `T_service(B) < T_stop(A) - tolerance`. A `departure` before `T_stop(A)`
  always counts, since the work started even earlier.
- **Unchecked:** one of the two moments is missing (a sender not running, a car without a
  box stop in the window). Race Control sees that the change needs the manual check.
- **In order:** nothing is shown live; the moments stay in the recording.

A hint is a Race Control event like a suspected disconnect: listed on the desk's Events
with team, both drivers, both moments, the difference, the signal kind and the tolerance.
States `suspected`, `confirmed`, `dismissed`; decisions go to `driver-race-audit.jsonl`.
Every change, early or not, is written to the race recording and its CSV, so the
post-event check starts from a list. Nothing reaches OBS, the public timing page or the
teams automatically.

## Accuracy

Each moment carries the sample spacing (about 0.1 s) plus the difference in upload delay
of the two senders, which batch their packets. That difference is not measured yet; the
design assumes it stays below 1 s. The hint shows the tolerance it was judged with, and
the desk says that the replay decides.

## Before building

One measurement on a real driver change with two PS5s and two senders:

1. B's samples during the service: fuel with and without refuelling, tyre temperatures
   with and without a tyre change (does a change show as a step, and how large), and the
   flags.
2. A's samples from pit entry to standstill: is the standstill clear, and does it sit
   beyond `BOX_LATERAL_M` (the box detection is still unconfirmed, #1110).
3. The offset between the two senders' timestamps against a common reference, for
   example both lobbies' replays or a screen recording with both consoles in view.

The thresholds above are placeholders until this measurement replaces them.

## Out of scope

- Measuring whether a team gives the gained time back on track.
- Messages to teams.
- Single-lobby events (no handover between lobbies).
