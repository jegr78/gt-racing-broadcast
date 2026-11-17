# Driver telemetry dashboard

Confirmed product scope, 2026-11-10. Implementation has not started. A probe in
real league lobbies comes first (see "Probe"); its results may change this design.

Open blocker for league use, 2026-11-12: most drivers run a dashboard app on the
phone they would also send from, and the sender cannot run next to it (see
"Sender"). The relay side is built anyway. A native client that solves this is
an idea under "Clients".

Drivers send their own GT7 telemetry to the relay through an event invite link.
The relay turns it into a live broadcast dashboard: driver and team names, order,
gaps, track map, fuel, fastest lap and qualifying ranking. Data is live only. The
relay persists corrections, the audit log and the fastest lap, never the telemetry.

The dashboard serves every league race, not only endurance. Its core is a race in
one GT7 lobby, which covers a short league race in a solo commentary profile and
each stint of an endurance race. Endurance adds a layer for several lobbies:
stints, driver changes, carrying distance across lobbies and rejoins. Sections
mark what belongs to that layer.

## Constraints

- GT7 sends UDP only to a device on the driver's LAN that sends heartbeats to the
  console (port 33739, replies on 33740). No browser can send or receive raw UDP,
  so every driver runs a sender program next to the console.
- Tailscale Funnel carries HTTPS and TCP only. Ingest runs under the `/console`
  mount, the only funnelled path.
- Each packet describes only the driver's own car. GT7 sends no gaps, no driver
  name and no car number.
- 0x84 carries the live race position in our offline race recordings (P15 to P1
  over five laps, format `~`). Online lobbies are unverified.
- At most 15 senders race at once: a lobby holds 16 players and one slot is the
  broadcast spectator.

## Event model

A single-lobby race runs in one GT7 lobby, timed or over a lap count. It starts
with or without a formation lap. A solo profile has no `Schedule` tab.

Endurance (multi-lobby layer): races are timed. Each stint runs in its own GT7
lobby on the same track. A driver ending a stint enters the box and passes the GO
to the team mate, who waits in the box of the next lobby and takes over. The next
lobby starts a normal race; every driver drives lap 1 into the box and waits, so
lap 2 is the first race lap. The first stint usually starts with a manual
formation lap. The final stint runs to the last lap of its lobby; the race timer
does not end it.

In endurance, qualifying runs one driver per team per segment (Q1/Q2/Q3,
qualifying and superpole); drivers may change between segments. A single-lobby
league's qualifying session is one segment.

## Entries

An entry is one row of the `Configuration` tab with its name and number, the same
source as the HUD's top 3. In endurance an entry is a team whose drivers take
turns. In a single-lobby race an entry is usually a driver. Team-vs-team leagues
such as the Community Team Cup race drivers as entries for one of two teams; an
optional column on the entry row names that team, and the dashboard shows it with
the team's colour. The column's header and whether the dashboard adds a team
standing are open.

## Access and identity

- One invite link per event. `racecast event start` creates it, `event stop`
  invalidates it. The producer regenerates it, revoking the old one, from the CLI
  and the Control Center. The link permits sending telemetry and nothing else.
- No per-driver tokens and no Discord login: drivers do not reliably have their
  credentials at hand.
- The driver opens the link, picks the entry from the `Configuration` tab, in
  endurance also the stint or qualifying segment from the schedule, and types a
  name. The page returns a self-issued code or command line for the sender, valid
  for the event.
- At most one connection per entry in a single-lobby race, two per team in
  endurance. Race Control and the director see every connection live, correct its
  assignment and disconnect it.

## Sender

- One Python script, standard library only, served by the relay on the invite
  page so its version always matches the relay. It runs on desktops, in Termux
  (Android) and in a-Shell (iOS, foreground with the screen on and the device
  charging). Prebuilt Windows and macOS programs exist for drivers without
  Python; they are unsigned, so the guide covers SmartScreen and Gatekeeper.
- It requests packet format `A`. The first heartbeat decides the format for every
  receiver, so `A` leaves a driver's SimHub or phone dashboard working. Every
  dashboard field is in the base packet.
- It forwards the encrypted packets unchanged in batched HTTPS POSTs at a rate the
  relay sets when it connects (default 20 Hz). The relay decrypts and filters;
  new fields never need a driver update.
- It finds the console on the LAN and asks for its address only when discovery
  fails. It reports an interruption to the driver.
- GT7 sends to port 33740 on every device that heartbeats, but two programs on one
  device cannot both bind 33740. The sender therefore needs a device of its own
  next to a driver's existing receiver:
  - Dashboard app on the phone, the common setup: the sender runs on a second
    device on the same LAN, such as a PC, laptop, tablet or old phone. The probe
    confirmed that both receive at full rate. It cannot
    share the phone with the app: the port clashes, and iOS runs a-Shell only in
    the foreground.
  - SimHub or GT Race Engineer on the PC: the sender runs on another device, or
    on the same PC in one of two modes. As forwarder it binds 33740 itself and
    passes every packet unchanged to a local port the other app listens on. As
    listener it sends no heartbeat and reads a local port the other app forwards
    to, for apps with such an option like GT Race Engineer's "Telemetry
    Forwarding". The driver's setup then stays unchanged.
  - A phone dashboard app that can forward: it forwards to the sender in listener
    mode on a second device. Without that option the second device heartbeats on
    its own. The iPhone apps Race Dash, ezio dash and Victory offer no forwarding
    (checked 2026-11-12); they only take the console's IP address.
  - No other receiver: the sender runs on the phone or the PC.

## Clients

The ingest is a documented, versioned HTTP contract, so a client other than the
sender script can send: it posts the encrypted GT7 packets with the code from
the invite page and follows the send rate the relay sets.

Idea, not planned: native dashboard apps for phones and tablets that show the
driver's own data and send it to the relay in the same app. The driver keeps a
dashboard on the phone, needs no second device, and the app runs in the
foreground anyway, which also solves the iOS restriction. It replaces the
driver's current dashboard app, so it has to be good enough to switch to. It
means two app store releases, an Apple developer account and a maintenance path
outside this repository.

## Track layout

The producer picks the GT7 layout before the event in the Control Center, from
the same list as the track editor. It is an event setting for every profile kind.
Distance and map need it from the first packet, and recognition needs a full lap.
The line comes from `TrackDB.reference_trace`, the downloaded `signatures.json`
that covers 114 of 121 layouts, reverse variants included. Picking a layout without
a line warns at setup: that event has no map and no distance until a recording
teaches the line. Once a sender completes its first lap, recognition checks the
pick and warns Race Control and the director on a mismatch; both can change the
layout.

The pick also serves the solo POV telemetry without any dashboard. Today
`gt7_telemetry` names the track after the first clean lap. With a pick, map and
corner names show from the first metre, layouts that differ by one chicane are not
confused, and recordings carry the layout, so "Set track" is not needed
afterwards. Recognition stays as the cross-check. This part does not wait for the
probe.

## Race distance

Order and gaps come from each entry's cumulative race distance, measured with the
relay's receive time as the common clock: the projection onto the track's line
through `TrackDB`, plus in endurance the laps of earlier stints. GT7's position is a
cross-check inside one lobby; a disagreement is shown to Race Control and the
director.

- **Start.** With the event setting "formation lap" (default on) a car's distance
  starts when it crosses the line into lap 2. The crossing times form the starting
  gaps. Without it, distance starts at lap 1.
- **Driver change** (multi-lobby layer). The broadcast stint switch opens the
  change window. Inside it, a team changes when its old car stands in the box and
  the new driver's car then leaves the box. The old car's distance becomes an
  anchor; the new car continues from the anchor by its own movement along the
  line. Lap 1 of a follow-up lobby never counts. Race Control and the director
  override the change per team.
- **Red flag.** Race Control or the director triggers `RED FLAG` (see Roles). It
  anchors every entry's last known distance. In the new lobby each car continues
  from its anchor when it leaves the box; lap 1 does not count. A profile setting
  decides whether a restart keeps the gaps or neutralises them (order and laps
  kept, cars in the same lap anchored level); Race Control and the director
  override it per restart. "Restart released" ends the mode.
- **Safety car.** A leader-driven safety car needs no rule: the field closes up on
  track and the distances show it.
- **Rejoin** (multi-lobby layer). A team that disconnected rejoins in the next
  stint at a fixed time behind the leader at the swap. Race Control marks it; the
  team gets the leader's start distance and the later departure produces its
  deficit.
- **Corrections.** Race Control and the director add a ± distance per entry with a
  reason, for missing laps, disconnects or counting errors. Every correction is
  logged with identity and time and survives a relay restart.
- **Finish.** A single-lobby race ends by lap count or by time; the event setting
  says which, and Race Control or the director can change it.
  - By lap count, the leader finishes on completing the lobby's lap count, counted
    by GT7's lap counter including lap 1. Race Control or the director enters the
    count, also at event setup.
  - By time, Race Control or the director triggers `Final lap` once GT7 shows the
    leader on the last lap; the leader finishes at its next line crossing. GT7
    sends no remaining time and the race timer cannot be synchronised with the
    lobby's timer, so the timer never ends a race.
  - `Finish` ends the race at once by hand, for both modes, and freezes the order.
  - Every other entry finishes at its next line crossing after the leader.
  - In endurance the race timer does not end the race: the final stint lobby runs
    to its lap count, which an optional `Laps` column in the `Schedule` tab seeds.
  - Without a count or `Final lap` the dashboard keeps running until `Finish`.

## Display

- Order, gap to the leader and interval; a deficit of a full lap or more reads
  `+N L`. Status `Box` while a car stands in the box, `No data` while its sender is
  missing. An entry without data keeps the position implied by the other positions.
- Remaining time comes from the existing race timer, including its red flag
  adjustments.
- A track map draws the picked layout's line before any car has driven. Each car
  is a dot labelled with its entry number, placed at its own GT7 position, which
  uses the line's coordinates. When dots overlap, they are offset and the car
  ahead is drawn on top.
- The fastest race lap, in endurance across all stints, with entry and driver.
  Laps outside the race do not count: the formation lap, lap 1 of a follow-up
lobby and the lap containing a change or red flag wait. GT7 decides whether a lap
is valid; Race Control and the director correct the time.
- Qualifying shows the ranking of the running segment by GT7 best lap with gaps
  between best laps. Elimination and grid stay with the league and the Sheet.
- A readiness list shows per entry whether a sender is connected before the start;
  in endurance per team and stint, also for the next stint during the race.
- A delay setting holds telemetry, and in endurance the dashboard's stint switch,
  back to match the on-air commentator feed.
- Each entry row shows entry name, number, driver name, tyre compound, fuel and the
  time of the last completed lap. Two global switches show or hide the compound
  and the fuel column for every entry at once; names and number always show.
- Each entry row shows its race laps; in endurance also the race laps in the
  current stint lobby. Race laps exclude the formation lap and lap 1 of a
  follow-up lobby.
- Each entry row shows the number of pit stops: box stops that are not part of a
  driver change, the lap-1 wait of a follow-up lobby or a red flag restart. Race
  Control and the director correct the count.
- GT7 telemetry carries no tyre compound. Race Control and the director set it per
  entry from a dropdown, reading it from the broadcast spectator's view in the
  lobby: Racing Soft `RS` (red), Racing Medium `RM` (yellow), Racing Hard `RH`
  (white), Intermediate `I` (green), Wet `W` (blue). An entry without a compound
  shows none. The compound stays through a driver change until someone changes
  it. It applies to the race and to qualifying.

Outputs: a relay overlay page for OBS styled through the league's overlay builder,
a commentator view in the Cockpit and a public live timing page.

The public live timing page lets viewers and team mates follow the race in a
browser without the stream. It is read-only and shows the dashboard's data with
the same delay, so a viewer with the stream open sees nothing early. Each event
gets its own public link, separate from the drivers' invite link, which needs no
login, may be posted in the stream description or on Discord and stops working
with `event stop`. It is the first unauthenticated path through Funnel, a
deliberate exception to "everything under `/console` is authenticated". It
serves timing data only, never stream URLs, chat or controls.

The layout of the overlay and the live timing page follows a survey of common
timing graphics in sim and real-world racing broadcasts, done before the design.
Issue
#1115 lists the starting references: the GT World Series tower (compound letter,
pit stop column, pit row with time, compound change and fuel), GT7's Timing
Monitor, Le Mans Ultimate endurance and qualifying towers with track map and focus
card, and PXLCast's live timing with switchable columns and an event feed.

## Roles

Every new control exists in both the Race Control desk (`/console/race-control`,
role `race_control` and producers) and the Director Panel, except showing and
hiding the dashboard on air, which stays with the director. This covers
connections and assignments, readiness, change overrides and rejoin (endurance),
corrections, the restart mode and release, the track layout, the finish mode and
lap count, `Final lap` and `Finish`, the pit stop count, fastest lap and
qualifying corrections, the delay setting, the tyre compound per entry, the
compound and fuel display switches and showing the invite link.

Race Control triggers the full `RED FLAG`: the Race Control banner, the Standby
cover and the dashboard's red flag mode. This is the first program change reachable
through Funnel, a deliberate exception to "program control stays on the tailnet".
It requires the `race_control` role or a producer, no step-up, and enters the
audit log with identity. The last of two concurrent changes wins; both desks show
it with its author at once.

## Probe

Before the dashboard is built, a sender script and an ingest that only counts and
samples answer the open questions in two stages.

Stage 1 runs on the maintainer's own PS5 and devices, without the league. A
maintainer tool acts as a simulated PS5 that answers heartbeats and replays a
`.gt7rec` recording re-encrypted, so the tests repeat without the console. It
answers:

- Do the desktop script and a-Shell receive 60 Hz reliably for a full stint?
- Does Termux run the script in the Android emulator, with the host forwarding
  UDP 33740, and survive forced Doze?
- Does a second, format-`A` heartbeat on another device leave a phone dashboard
  app and SimHub working, so the console really sends to every heartbeating device?
  Answered for a phone app on 2026-11-12 on a PS5: with ezio dash connected on an
  iPhone, a Linux PC heartbeating `~` received 59 Hz, all format `~` (3544 packets
  in 60 s standing, 5283 in 90 s over two driven laps). ezio dash showed live
  values throughout without a dropout. The console sent from UDP port 62236, not
  33739, so a host firewall drops the packets as unsolicited until UDP 33740 is
  allowed. Open: SimHub, and the format ezio dash requests.
- With the sender and SimHub on the same PC: does SimHub's GT7 reader accept the
  packets the sender forwards to another local port, with SimHub's own heartbeat
  on or off?
- In listener mode: does the sender take the packets GT Race Engineer forwards to
  a local port, with no heartbeat of its own?
- Does GT7's best lap (0x78) exclude laps invalidated for track limits?
- Do packets continue during a long box standstill, and does the projection hold
  in the pit lane?
- How does Funnel carry 15 simulated senders at 20 Hz?
- How many concurrent viewers of the public live timing page do Funnel and the
  producer's upload carry next to the senders and the stream?

Stage 2 needs a coordinated league lobby with two or three drivers and, for the
endurance questions, a second lobby. Independent of stage 1, the leagues first ask
their drivers which telemetry receiver they use (SimHub, which dashboard app, on
which device, and whether that app can forward telemetry to another address) and
which second device they could run the sender on (PC, laptop, tablet, old Android
phone, Raspberry Pi). The answers show how many drivers the
dashboard can reach. Stage 2 answers:

- Does 0x84 carry the live position in online and Sport lobbies?
- Does the change detection fire in a real driver change, and does the anchor
  carry the distance over?
- Does the spectator slot receive any telemetry?
- Does a real Android device hold 60 Hz on real Wi-Fi for a full stint?
