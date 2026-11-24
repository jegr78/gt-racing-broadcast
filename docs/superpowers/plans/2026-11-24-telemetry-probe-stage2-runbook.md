# Driver telemetry probe, stage 2: league lobby runbook (#1116)

What only a real league lobby can answer (epic #1109, spec
`src/docs/design/driver-telemetry-dashboard.md`, section "Probe"). Stage 1 (#1110,
`2026-11-22-telemetry-probe-stage1-runbook.md`) measured the transport on the
maintainer's own PS5; this run needs other drivers, an online lobby, a Sport race, a
second lobby and the broadcast's spectator console.

Every block names the question it answers, the setup, the steps, what to record and a
pass mark. Pass marks are proposals for the report, not league rules: change them if a
result shows they are wrong. Who drives when, in which lobby, is the league's decision;
this runbook only says what each block needs.

The driver guide is `src/docs/wiki/Driver-Telemetry.md` (Driver Telemetry in the wiki).
#1265 rewrites it after stage 1; until then the setup sheets of section 3 are what the
test drivers follow.

## 1. What the report must answer

From the issue's "Done when" and the open points of #1263 and #1264:

| # | Question | Block |
|---|---|---|
| Q1 | Does 0x84 carry the live race position in an online lobby? | B |
| Q2 | Does 0x84 carry it in a Sport race? | C |
| Q3 | A real driver change between two lobbies: does the change detection fire (old car stands in the box, new car leaves the box), and does the anchor carry the distance over? | D |
| Q4 | What does the broadcast spectator slot receive, if anything? | B, D |
| Q5 | Does a real Android device with Termux hold 60 Hz for a full stint on real Wi-Fi, screen on, charging, and what does the vendor's battery management do? | B to D, E |
| Q6 | Does a lobby carry the qualifying best (0x78 `best_ms`) into the race? Does a restart lobby keep the race best? (#1264) | F |
| Q7 | Spa 24h Layout (`d60ebc`), 4 laps: lap 1 counted, no lap count stepping back, no distance jump, finish taken, with formation off and on (#1263, fixed in #1286) | G |

## 2. Roles

| Role | Who | Needs |
|---|---|---|
| Producer | maintainer | the producer PC with the repo checkout, the league profile, OBS optional |
| Race Control | one person of the league | the Race Control desk (`/console/race-control`) with a console link |
| Test drivers | two or three league drivers, at least one with an Android phone or tablet as the sender device | PS5 with GT7, a second device on the same network, the setup sheet of their platform |
| Second-stint drivers | for block D: one per entry that changes, may be the same people on a second PS5 or other league drivers | as above, registered for stint 2 |
| Spectator | whoever runs the league's broadcast PS5 | that PS5 in the lobby as spectator, a device next to it for the sender |
| Lobby host | one of the drivers, by the league's choice | creates the lobbies of blocks B, D and F |

The producer also notes the observations (marks) during the run. If that is too much at
once, a second person can type them with `probe-stage2.py mark` on the producer PC.

## 3. Before the session

### 3.1 League coordination (maintainer, a week before)

1. Send the driver poll (section 8) and collect the answers for the report.
2. Pick the test group from the answers: two or three drivers, at least one Android
   sender device, one driver who can host lobbies.
3. Agree with the league on: a date and two hours, the lobby settings of blocks B, D and
   F (track, car class, laps or time, qualifying on for block F), who hosts, who joins
   the broadcast PS5 as spectator, and which entries and stints the test drivers register
   for. The relay needs a roster entry per test car and, for block D, stint labels in
   the Schedule; a test Sheet tab set of the league or the league's own profile both
   work.
4. Ask each driver for their sender device and platform, so they get the right sheet.

### 3.2 Producer PC (day before)

The installed `racecast` binary predates the probe tooling, so everything runs from the
repo checkout. The invite page's Windows and macOS programs come from the latest GitHub
release and have no `--check` until a release ships it: the setup sheets therefore use
the Python script on every platform.

1. `git pull` on `main`.
2. Machine `.env` in the repo root: `RACECAST_TELEMETRY_PROBE=1` (block B's spectator
   sender uses the probe ingest).
3. Dry run, the dress rehearsal on simulated parties (section 7). It needs the E2E unit
   and Playwright's Chromium:
   ```
   python3 tools/event-sim/event_sim.py --scenario probe-stage2 --out <dir> --no-skips
   ```
   It must end with `0 hard findings`. Then `<dir>/probe-stage2/report.md` shows the
   tables this run produces.
4. Profile: the league profile, or a copy of it for the test (`racecast profile use
   <name>`). It must not be a solo POV profile: that one binds UDP 33740 itself.
5. Track: `racecast event track --list`, then pick the layout of block B. Blocks F and G
   change it later.

### 3.3 Driver devices (day before)

Each test driver gets the sheet of their platform from `tools/probe-stage2.py kit`
(written in step 4.1 once the event runs) and does the "Before the session" part. The
sheets cover the stage 1 findings:

- iPhone and iPad: a-Shell cannot find the console by broadcast ("No route to host"), so
  the command carries `--console <PlayStation IP>` (#1265).
- Android: `pkg install python`, `termux-wake-lock`, battery use unrestricted, charging.
- Windows and macOS: no firewall rule; Windows asks once on the first start. Linux with an
  active firewall: `sudo ufw allow 33740/udp`.
- A dashboard app on the same phone blocks the sender: the sender needs another device.
- SimHub on the same PC: `--listen 33741` with SimHub's "Forward UDP packets to"
  127.0.0.1:33741, or `--forward 127.0.0.1:PORT` with SimHub's "Game telemetry UDP port"
  on PORT (needs a SimHub restart). GT Race Engineer: `--forward 127.0.0.1:PORT` and the
  same address in its "Telemetry Forwarding" (#1265).

### 3.4 Open items that change the reading

- #1262: the sender opens a new TLS connection per post, which caps Funnel at about five
  to six posts per second in total. Two or three drivers and the spectator still fit,
  with post latencies of a second or more. If #1262 has not landed, post rate and
  latency are recorded but are no pass criterion.
- #1264: the fastest race lap can take a lap GT7 marked invalid. Block F records the
  0x78 data the fix needs; the desk's fastest lap is not judged.

## 4. Session timeline

Times are relative to the agreed start. Every block starts with a mark, so the report
splits by block:

```
python3 tools/probe-stage2.py mark --out <results> "block B online lobby"
```

### Block A: bring-up (T-45 to T-15)

Answers nothing by itself; every later block depends on it.

1. Producer:
   ```
   py src/racecast.py relay start
   py src/racecast.py funnel on
   py src/racecast.py event start
   py src/racecast.py event race --formation on --laps 10
   py src/racecast.py event invite
   python3 tools/probe-stage2.py kit --out <kit>
   python3 tools/probe-stage2.py watch --out <results>
   ```
   `event start` creates the invite; `event invite --new` only renews it while an event
   runs (#1265), and a renewed invite needs new sheets and new registrations. The lap
   count is the league's choice for block B. `kit` writes `windows.md`, `macos.md`,
   `linux.md`, `iphone.md` and `android.md`; they carry the invite link, so they go to
   the test drivers only. `watch` runs in its own terminal until the end of the session.
2. Producer: send each driver their sheet. Race Control opens the desk.
3. Drivers: register on the invite page, start a GT7 session, run the self-check
   (`... --check`) and post the whole output. Race Control approves the registrations.
4. Producer: in the watch table every driver shows up with `dev/s` near 60 once their
   sender runs.

Pass (proposal): every driver's check prints `RESULT: OK`. A driver whose check fails
follows the sheet's "If the check fails" or drops out of the blocks that need them.

Record: each check output (into `<results>/checks/<name>.txt`), platform, device model,
OS version and, for Android, the battery settings the driver changed.

Note from stage 1: a sender on a device of the producer's tailnet resolves the Funnel
host to its tailnet address and does not test Funnel (#1265). The drivers at home take
the public path, which is the path under test.

### Block B: online lobby race (T+0 to T+25)

Answers Q1, Q4 (first look), starts Q5.

1. Mark `block B online lobby`. The lobby host creates the lobby with the agreed
   settings; the test drivers join; the broadcast PS5 joins as spectator.
2. Spectator sender, on a device next to the broadcast PS5 (the script downloaded as in
   the setup sheets):
   ```
   python3 racecast-sender.py --url https://<host>/console/probe/ingest --key KEY --name Spectator
   ```
   `KEY` from `curl -s http://127.0.0.1:8088/telemetry/probe` on the producer PC. The
   probe ingest keeps the spectator out of the roster and the race.
3. Optional raw capture of the spectator's packets for later analysis, on another device
   of that network:
   `python3 tools/gt7-telemetry-probe.py --ps-ip <broadcast PS5 IP> --capture spectator.hex -n 3600`
   (one minute of packets as hex lines; it asks for format `~`).
4. The race runs to its end. Each driver notes their GT7 position at two moments the
   producer calls out (mark `position check 1` and `position check 2`).
5. At the end: `python3 tools/probe-stage2.py collect --out <results> --runtime runtime/<profile>`.

Pass (proposal) for Q1: in the report's "0x84 per sender" table every driver's samples
carry a position, the car counts match the lobby, and the "0x84 against the relay's
order" table agrees in most polls (a difference right after an overtake is expected,
since the relay polls once a second). The noted positions match the samples at the marks.

Q4: the spectator's row in "Senders" and "0x84 per sender". Possible results: no packets
at all, packets of a car the spectator follows (lap and position change with the camera),
or packets with no car (`on_track` false). Whatever it is, it is the answer.

### Block C: Sport race (T+25 to T+45)

Answers Q2. A Sport race cannot be coordinated: each driver enters a Daily Race of their
choice, so the cars are in different races and the relay order means nothing.

1. Mark `block C sport`. Each test driver enters a Sport Daily Race with their sender
   running.
2. Each driver notes their GT7 position at the start, after lap 1 and at the finish,
   with the time.

Pass (proposal) for Q2: the "0x84 per sender" table shows positions for every driver
and they match the noted positions at those times. The relay's order and its mismatch
flag are not read in this block.

### Block D: driver change between two lobbies (T+45 to T+80)

Answers Q3, continues Q5, Q4 (second look).

The relay's change detection runs in endurance: the broadcast stint switch opens the
change window; inside it a team changes when its old car stands in the box and the new
driver's car then leaves the box (spec, "Race distance", "Driver change").

1. Mark `block D lobby 1`. Stint 1 lobby with the stint 1 drivers, race settings as
   agreed. The stint 2 drivers registered for stint 2 and were approved.
2. The race runs at least five laps.
3. Mark `block D change`. The stint 1 drivers drive into the box and stand there. The
   director switches to the next stint (Director Panel or Companion "Next"), which opens
   the change window.
4. The lobby host opens lobby 2. The stint 2 drivers join it, start their senders, and
   leave the box one after the other.
5. Mark `block D lobby 2`. The race runs at least three more laps. The stint 1 drivers
   stop their senders after their car stood in the box for a minute.
6. Collect as in block B.

Pass (proposal) for Q3: in "Driver changes" every entry has one `change` inside the
window, `old status` is `box`, and `anchor carried` is true. "Lap count and distance"
lists no step for those entries. Lap 1 of lobby 2 must not add a lap.

Record: the order in which the cars left the box, any car that left before the window
opened, what the desk showed (a screenshot per change).

### Block E: Android over the stint (runs through blocks B to D)

Answers Q5. The Android sender runs from block B's start to block D's end, screen on,
charging, on the home Wi-Fi.

1. The driver notes the battery settings, the Wi-Fi band and anything the phone shows
   (battery warnings, Termux notifications).
2. After block D, optional: ten minutes with the screen off and the wake lock held, then
   ten minutes without the wake lock (`termux-wake-unlock`), mark both.

Pass (proposal): in "Hz per minute" the Android sender has no low minute (below 59 Hz on
the device), interruptions 0, `max idle s` below 2. The screen-off minutes are reported,
not judged.

### Block F: qualifying best and restart lobby (T+80 to T+100)

Answers Q6.

1. Mark `block F qualifying`. The lobby host sets qualifying on. The drivers set times.
2. Mark `block F race`. The race starts from that qualifying. Watch the report's "Best
   lap (0x78)": a best that appears `before a lap` in the race stream came from
   qualifying.
3. After two laps Race Control triggers `RED FLAG` on the desk. Mark `block F restart`.
   The lobby host opens a restart lobby; the drivers join, Race Control releases the
   restart. Two more laps.

Pass: none, both are measurements. Report per driver: the best at race start (none, or
the qualifying time), the best right after the restart (none, the race best, or the
qualifying best).

### Block G: Spa 24h Layout, 4 laps (T+100 to T+120)

Answers Q7. One driver is enough; it repeats stage 1's run that found #1263.

1. Producer: `py src/racecast.py event track d60ebc`, then
   `py src/racecast.py event race --formation off --laps 4`. Mark `block G formation off`.
2. A 4-lap race on Spa 24h Layout with one pit stop. Collect.
3. `py src/racecast.py event race --formation on --laps 4`. Mark `block G formation on`.
   Same race once more. Collect.

Pass (proposal): in both runs the entry counts lap 1, the lap count only rises, "Lap
count and distance" lists nothing for it, and the race state reaches `finished` after
lap 4.

### Wrap-up

1. Stop `watch` (Ctrl+C), then:
   ```
   python3 tools/probe-stage2.py collect --out <results> --runtime runtime/<profile>
   python3 tools/probe-stage2.py report <results>
   ```
2. Ask each driver for the end of their sender output (the `stopped:` line and the last
   status lines).
3. `py src/racecast.py event stop`.

## 5. What the results folder holds

| File | From | Content |
|---|---|---|
| `poll.jsonl` | `watch` | one record per second: every driver connection (packets, loss, device counters, post latency, the latest sample with 0x84 position, car count, lap, 0x78 best), the race (state, change window, every entry's distance, laps, status, GT7 position) and every probe sender |
| `marks.jsonl` | `mark` | the block marks and observations |
| `probe/probe-*.json` | `watch`, `collect` | full probe snapshots every minute (600 samples per sender) |
| `race-recording.csv` | `collect` | the relay's race recording |
| `race-recording-*.sqlite`, `driver-race-audit.jsonl` | `collect --runtime` | the recording file and every desk action |
| `checks/*.txt` | drivers | the self-check outputs |
| `report.md`, `analysis.json` | `report` | the tables below |

No file carries a driver code, the probe key or the invite id; connection ids are short
hashes. Driver names stay as typed. The kit folder is not part of the results: its sheets
carry the invite link.

`.gt7rec` captures: the relay writes them only from its own GT7 listener, which an
endurance profile does not start, and neither the hub nor the sender keeps packets. The
race recording, the probe snapshots and `poll.jsonl` are the record of driver data; the
spectator's raw packets come from the optional capture in block B.

## 6. Report template

The comment on #1116. `report.md` provides the tables; this frame maps them to the
questions and the spec.

```
## Probe stage 2, <date>

Lobby test with <n> drivers (<platforms>), league <name>. Results folder attached.

### Driver poll
<n> answers. Receivers today: ... Second devices: ... Drivers the dashboard reaches
without new hardware: ...

### Q1 0x84 in an online lobby (block B)
<"0x84 per sender" and "0x84 against the relay's order" rows of block B>
Answer: yes / no / partly, because ...

### Q2 0x84 in a Sport race (block C)
<rows, noted positions against samples>

### Q3 Driver change (block D)
<"Driver changes" rows>
Change detection fired: ... Anchor carried: ... Lap 1 of lobby 2 counted: ...

### Q4 Spectator slot (blocks B, D)
<Spectator row of "Senders" and "0x84 per sender">

### Q5 Android over the stint (block E)
<"Hz per minute" of the Android sender, device, battery settings, observations>

### Q6 Best lap carry-over (block F)
<"Best lap (0x78)" rows at race start and after the restart>

### Q7 Spa 24h Layout (block G)
<"Lap count and distance" of both runs, laps per GT7 against the relay>

### Transport
<"Senders" table: device and relay Hz, loss, post latency (with #1262's state)>

### Spec changes
<each result that changes the design, with the section of
src/docs/design/driver-telemetry-dashboard.md it changes>
```

Where a result changes the design, the same change updates the spec's "Probe" section
and the affected section, and the driver guide after #1265.

## 7. Dress rehearsal and what it cannot cover

`tools/event-sim/scenarios/probe-stage2.toml` plays the producer's side of blocks A, B
and D against simulated parties: the real relay and Control Center on a demo profile,
three teams with three drivers each in three lobbies (two changes per team) whose packets
carry a 0x84 position, the real `racecast-sender.py` as the spectator through the probe
ingest against a simulated PS5, and the sender's `--check` through the real invite. The
producer's watch records the run into `OUT/probe-stage2/`, and the scenario asserts the
report: six changes, all inside the change window with the anchor carried, one probe
sender, 0x84 seen, no lap count stepping back, the race recording saved. One commentator
link on all three Schedule rows makes each stint switch a continuation, so the run needs
no feed and no ffmpeg; the director's Next on a real handover opens the same change
window.

What it cannot cover, so the lobby test must:

- Whether GT7 fills 0x84 in an online lobby or a Sport race: the simulated cars set it
  because the scenario says so.
- What the broadcast spectator slot receives: the simulated spectator is one car lapping
  at a steady pace.
- A real Android device: vendor battery management, Wi-Fi power saving, Termux in the
  background, thermal limits. Stage 1 ran an emulator with forced Doze for minutes only.
- GT7's own lobby behaviour: lap counters in the pit lane, lap 1 of a follow-up lobby,
  where a restart lobby puts the cars, qualifying bests.
- Funnel from other networks and other providers: the rehearsal posts on loopback.
- People: a driver who joins late, leaves the box before the window, or types a wrong
  entry.

The real recordings of `~/Documents/Racecast-Analysen` (Grand Valley) can drive the
stage 1 tools (`tools/gt7-sim-ps5.py REC --loop` with a sender) for a by-hand check of
the sender on a real packet stream; the committed scenario uses synthetic data so it
runs on any machine.

## 8. Driver poll (ready to send)

```
Driver telemetry: two quick questions

We are testing a way to show live race data from your GT7 telemetry on the broadcast.
A small program (the sender) runs on a device on your home network and sends your car's
telemetry to the broadcast. Before we test it in a lobby, we would like to know what
you use today. Answers are voluntary and only used to plan the test.

1. Telemetry today: do you use a telemetry app or dashboard with GT7?
   - SimHub (on which device?)
   - GT Race Engineer (on which device?)
   - A dashboard app on a phone or tablet (which app, which device?)
   - Something else (what?)
   - None
   If you use one: can it forward the telemetry to another IP address and port?
   (yes / no / don't know)

2. A second device: which device on the same network as your PlayStation could run the
   sender during a race? It runs next to the PlayStation for the whole race.
   - Windows PC or laptop
   - Mac
   - Linux PC or Raspberry Pi
   - Android phone or tablet
   - iPhone or iPad
   - None

A dashboard app on your phone and the sender cannot share that phone, so if your phone
runs a dashboard during races, please name another device for question 2.
```
