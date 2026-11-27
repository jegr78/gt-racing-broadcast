# Event walkthrough video

Status: approved by the user on 2026-11-24. Decided: crew onboarding as the
audience, the `full` scenario as the base (extended, not copied), a real OBS for the
program picture, generated title clips as feeds, text captions from the scenario, no
length limit, reproducible by one command outside the normal CI.

## Problem

The slide decks and the wiki show each desk on its own. Nobody sees what a race day looks
like when all desks work at the same time: the commentator submits a link while Race
Control approves drivers, the director takes the splitscreen while the drivers swap, the
public timing follows a decision made at the desk seconds earlier. A new crew member
learns this on their first real event.

The event simulation already plays a whole race day through the real pages. It keeps
only still images, and its program picture is a drawn test pattern.

## Goal

One command records the extended `full` scenario and renders a film that shows every
perspective at once:

- every view runs for the whole film, the active ones large, the others as live previews,
- the program picture is what a real broadcast shows: scenes, transitions, splitscreen,
  the demo profile's HUD, intro, trailer and outro,
- captions say who acts and what they do, chapters mark the phases,
- every action the film is about stays in real time and readable; only waiting is sped up,
- after a UI change the same command produces the film again.

Non-goals: a spoken commentary (a later second track), Companion buttons as a view, a run
in the normal CI, real league data or real race footage.

## The film

### Views

| View | Source | Shape |
|---|---|---|
| Program | the real OBS instance's own recording | 16:9 |
| Director Panel | `director` page | 16:10 |
| Race Control desk | `race_control` page | 16:10 |
| Commentator Cockpits | the cockpit of the commentator on air and, at a handover, the one up next | 16:10 |
| Public live timing | `viewer` page | 16:10 |
| Control Center | `producer` page, at the start and the end | 16:10 |
| Driver | the invite page in a phone viewport, at registration, before each driver swap and at the sender failure | 9:19 |

### Stage

- The canvas is 2560x1440 at 30 fps.
- At most two views are large. The others sit in a rail as live previews with the role
  name and a highlight ring while the step on them acts and holds its result.
- A view takes a place on the stage only once its recording has started. Before that it
  is not there at all, and when its page opens it fades in where it will stand.
- When a step's layout has a single large view, that view comes forward. It stands in the
  centre at 80 % of the canvas height, clear of the chapter bar and the caption line, and
  lies over the rest of the stage, which stays visible behind it at 35 % opacity. The views
  large before stay large behind it. Steps with two views keep the side-by-side layout.
  Consecutive steps of the same role keep the same layout.
- A commentator cockpit is on the stage only around its own stint. It comes on with the
  chapter of the first caption that names it, which is its link submission or the swap that
  puts it on air. At a four-view switch the cockpit on air before it hands over and leaves
  with the next caption. At the first switch that is the cockpit named first. A cockpit
  named again later comes back with that chapter. The one on air at the end stays, and a
  cockpit that no caption names stays on the stage.
- A tab a page opens, such as Race Control's Show standings (`race_control:standings`), is
  a view of its own next to its page. It is on the stage only with the captions that name
  it and only once it opened, so the click that opens it stays visible on its page. Its
  video counts from its own start.
- The one exception is the switch at a driver swap: Director, Program and both
  commentator cockpits share the canvas as four equal quarters, so the change of the
  On Air banner in both cockpits is readable at the moment the program switches.
- The stage follows the scenario: a captioned step without a focus brings its own role
  forward alone. A step can name its own focus, for example Director and Program together
  at a driver swap. A step whose role is not on the stage keeps the previous layout.
- Changes of size, position and opacity are animated (about 600 ms), never cut. A view
  that comes on or leaves fades where it stands.
- A visible pointer and a click mark show what was operated. The simulation clicks
  without a pointer today.
- A caption line names the role and the action ("Race Control confirms the disconnect of
  #33"). A chapter title appears at each phase. A clock shows the race time, and a
  fast-forward mark shows while time is sped up.

### Pace

- A step with a caption plays its action in real time, from `start` to `acted` in the
  timeline, with a lead-in of 2.5 s and a hold of 3 s after it, so the result is readable
  on every view it changes. From `acted` to `end` the step only waits for its checks, and that
  part is waiting time like any other.
- Where the next step follows at once, the source has no time for the lead-in or the hold.
  The picture then stands still for the missing part, so every caption is on screen for at
  least 6 s and its layout has settled before the click.
- An action longer than 8 s plays its first 8 s in real time and the rest sped up like a
  wait, for example Race Control approving one request after another. A step in a
  chapter with a four-view switch is exempt and always plays its action in real time.
  Of a run of captioned steps with the same role and action, the first three play as usual.
  The later ones are sped up under one caption, the first of them, which stays at least
  6 s. A run in a chapter with a four-view switch plays in full.
- In a chapter with a four-view switch, a driver swap, every captioned step holds 6 s. The
  switch itself also plays its wait for the checks in real time, since its checks wait for
  the On Air change in the cockpits, and holds 3 s after it.
- Stretches where the scenario only waits (for a lap, a pit exit, a lobby) are sped up
  by a factor chosen so that the timing still moves continuously. No stretch is cut out:
  positions never jump.
- The film starts once the views of the first caption have opened, so it opens on them.
- The film has no length limit.

### Sound

The feeds are silent. A quiet music bed runs under the film, as in the trailer. Where
sound matters in operation (program audio in the cockpit, the handover), a caption says
so.

## Storyboard

The order is the order of `full`. New steps are marked **new**.

| # | Chapter | What happens | Large |
|---|---|---|---|
| 1 | Qualifying (the day before) | Producer picks the track and starts the qualifying event (Start event with the Qualifying option). **new:** Comms 1 submits the stream link, the director approves it and feed A runs warm (auto-arm) | Control Center, Cockpit, Director |
| 2 | On air: qualifying (the day before) | Program with Comms 1, the cockpit on air. Drivers register for qualifying (one on the invite page), Race Control approves them. The director sets a 10 minute timer and starts it as the session starts; the cars drive their laps and Race Control adds laps, the public page ranks them. The timer runs out, the cars come in, the producer stops the event | Program, Cockpit, Driver, Race Control, Timing |
| 3 | Race day: start the event | The next day the producer starts the race event and sets the race | Control Center |
| 4 | Drivers sign up | **new:** a driver registers on the invite page; the others follow. Race Control approves, **new:** corrects one registration, **new:** rejects one | Driver, Race Control |
| 5 | Race start | **new:** intro, **new:** trailer, program with Comms 1, the qualifying timer reset, the race timer and the part start. Formation lap, start | Program, Director, Timing |
| 6 | Stint 1 at the desk | Pit stop and **new:** tyre compound confirmed. A disconnect: suspected, confirmed, marked for rejoin. A sender stops: estimate behind another car. A retirement | Race Control, Timing |
| 7 | Next commentator | Comms 2 submits late, the director approves, the incoming feed runs warm | Cockpit, Director |
| 8 | Driver swap 1 | **new:** the next driver requests telemetry for stint 2 on the invite page and Race Control approves it. Cars box, splitscreen once the leader stands in the box, switch to stint 2 once every car stands there (5 minutes at most), Race Control enters the compounds, new lobby, release | Driver, Race Control; at the switch the four quarters: Director, Program, Cockpit Comms 1, Cockpit Comms 2 |
| 9 | Stint 2 at the desk | The disconnected car rejoins a lap down. Red flag, cars to the box, restart with kept gaps. **new:** pit stop with a compound change | Race Control, Program |
| 10 | Driver swap 2 | As chapter 8, to stint 3, with Comms 2 handing over to Comms 3 | as chapter 8 |
| 11 | The finish | Final lap called, chequered flag, result, fastest lap | Race Control, Timing |
| 12 | Off air | **new:** outro, end of the part, the producer stops the event | Director, Program, Control Center |

The retirement stays in stint 1, as in `full`: the deck's images depend on it.

## Design

Three stages, each usable on its own.

### 1. Record: `event_sim.py --scenario full --record DIR`

- **Pages.** Every page role records its page for the whole run. Which mechanism
  is Playwright's video recording (gate G2).
- **Pointer.** In record mode an init script draws the pointer and the click mark. It is
  not injected in any other mode, so tests and deck screenshots stay as they are.
- **Driver page.** A new `driver` page role opens `/console/drive` with the invite link in
  a phone viewport and registers through the form. The other drivers keep registering
  over HTTP. Before each driver swap the page registers one seat of the next stint, which
  the HTTP registration leaves out (`drivers register` with `skip`). A seat the desk
  corrects is not one that drives afterwards: the page's code stays under the seat the
  form sent.
- **Program.** `--obs real` starts OBS as a separate instance with a Sim profile, the
  localized demo scene collection, its WebSocket on a free port with a per-run password,
  and a loopback RTMP sink of the run as its stream target. On Linux it reads its own
  configuration directory inside the run's work dir, and the user's configuration is never
  read or written. On macOS OBS ignores that, so the run refuses while any OBS runs, backs
  up the user's configuration folder (verified, then marked complete), writes the Sim files
  into it, and after the run restores the folder and verifies every file; a backup left by
  a killed run is restored with `event_sim.py --restore-obs`. The run starts and stops
  OBS's recording over the WebSocket. Without `--obs real` the stand-in stays, as today.
- **Feeds.** The stream stub gets a `card` look: dark background in the demo colours,
  the title ("Stint 1"), the commentator's name, a running clock and slow motion in the
  background. The texts and the clock face are rendered once as PNGs with Playwright's
  Chromium and ffmpeg only overlays them, so no `drawtext` is needed. `--record` selects it.
- **Intro, trailer, outro.** Generated the same way as short clips (8 to 10 s) into the
  run's media directory, where the demo collection's media sources expect them. Nothing
  is committed and nothing is downloaded.
- **Timeline.** The run writes `timeline.json`: per step its id, role, action, start
  time, `acted` (when the action returned, before the step's checks were awaited), end
  time, caption, chapter and focus, plus each recording's start offset.

### 2. Scenario: `full`, extended

New step keys, all optional and ignored outside record mode:

- `caption`: the line shown while the step runs. A step without one is waiting time.
- `chapter`: starts a chapter with this title.
- `focus`: the views to enlarge, when it is not the step's own role. Four views lay out
  as quarters (the swap switch only).

New actions:

| Role | Action | Does |
|---|---|---|
| `race_control` | `reject_driver` | rejects one waiting request |
| `race_control` | `correct_driver` | changes a registration's entry or stint before approving |
| `race_control` | `compound` | sets an entry's tyre compound |
| `director` | `play` | takes the Intro, Trailer or Outro scene and waits for its media to end |
| `driver` | `register` | registers through the invite page |
| `commentator` | `submit_link` | exists; used for Comms 1 before the event goes on air |

The deck's 50 image names stay, and each still shows the same situation. Numbers and lists
inside an image may follow the extended run: 40 requests waiting instead of 42, Reserve 44
and Driver 10-1 in the approved list, a tyre mark in the tower. A test pins each name to
its step, role and element, not to its content, and the new steps carry no deck shot
names. The deck images are regenerated only at the next deck update.

### 3. Render: `tools/build-event-video.py DIR`

- A stage page (HTML and CSS, as `tools/trailer/trailer.html` does) holds one element per
  view and lays them out from the timeline: sizes, positions, rings, captions, chapter
  titles, the clock.
- The film is captured frame by frame: for each output frame the stage page shows every
  recording at the source time that frame stands for, then the frames are encoded with
  ffmpeg and the music bed is added. Speeding up waiting time is therefore exact and
  needs no second pass.
- Outputs under `runtime/event-video/<run>/`: `event-walkthrough.mp4`, `chapters.txt`
  (time and title per chapter, in the form video platforms read) and `captions.vtt`.
- Rendering reads only the recording directory. A recording can be rendered again with a
  changed stage without a new simulation run.

### Limits and safety

- Recording needs a machine with a display and OBS, and more memory than the local E2E
  unit's 2 GiB (`full` already exceeds it). It runs on the maintainer's Mac (gate G4). `--record` states its own limit, checked
  before the stack starts, and fails closed like the other runs. An OOM kill is a hard
  failure.
- The demo profile only. Ports 8088 and 8089 stay untouched, as in every simulation.
- The recording shows console links and tokens of the demo run. A CSS mask over the same
  selectors the screenshots mask paints them black before a frame is written.

## Gates before the build

Each gate is a short experiment with a written result. A failed gate goes back to the
user before anything else is built.

| Gate | Question | Passes when |
|---|---|---|
| G1 | Does a second OBS instance with its own configuration directory run next to the user's OBS on the workstation, without touching it? | it starts without a dialog, records the demo collection with the HUD browser source, and the user's OBS files are byte-identical afterwards |
| G2 | Which page capture is sharp enough? | text of a desk in a rail preview and in a large slot is readable in a 1440p test frame |
| G3 | How long does frame-by-frame rendering take? | a 60 s test film with six views renders in a time that makes the whole film practical (target: under two hours for the film) |
| G4 | Does the run fit the machine? | six recordings plus OBS stay under the stated memory limit for the whole run |

### Results

- **G1 passed.** A second OBS starts next to the user's with `XDG_CONFIG_HOME` pointing
  into the run's work dir and `--multi --minimize-to-tray --disable-missing-files-check
  --profile Sim --collection Sim`, plus its own `plugin_config/obs-websocket/config.json`.
  It records the demo collection with the HUD browser source, and none of the user's OBS
  configuration files changed. The only changed files under the user's configuration
  directory were browser caches (`plugin_config/obs-browser/**/GPUCache`,
  `DawnWebGPUCache`, `GraphiteDawnCache`), written by the user's own running OBS, so the
  byte comparison excludes them. Two requirements follow for #1310: the run stops the
  recording and quits OBS over the WebSocket, because a SIGTERM kills the browser's GPU
  process and can leave the recording unfinished; and the run never kills the user's OBS.
- **G2 passed with Playwright's video recording** (`record_video_dir`, recorded at the
  page's viewport size). Text in a large slot is readable. In a rail preview the views
  are recognizable, not readable, so the rail serves orientation and the readable text
  always sits in one of the two large views.
- **G3 passed.** Seeking a VP8 recording with keyframes far apart dominates the frame
  time. Transcoding each recording once to all-keyframe VP8 (`-c:v libvpx -g 1 -b:v 4M
  -deadline realtime -cpu-used 8`) and rendering with four parallel workers, each on its
  own slice of frames, brings a 30-minute film to about 80 minutes on the 6-core
  workstation.
- **G4 passed on the Mac, failed on the Linux box.** On the box (6 cores, 15 GB), `full`
  with page videos broke every limit of the local E2E unit: the 200% CPU quota throttled
  it into timeouts, the per-page ffmpeg encoders exhausted the 512 tasks, and memory grew
  about 100 MB per minute to an OOM kill at 5 GiB after 40 minutes. On the Mac (Apple M3,
  8 cores, 16 GB) the same run passed: 121 steps, no hard finding, 37 minutes; the whole
  process tree of the simulation peaked at 2.75 GB and stayed flat, the second OBS at
  0.74 GB, and the system kept about 35% of its memory free. The page videos took
  1.14 GB. Recording therefore runs on the Mac. macOS has no cgroups, so the run needs its
  own watchdog over its whole process tree (Playwright, the browsers and their encoders
  run in process groups of their own).
- **G1 on macOS.** OBS there ignores `HOME` and `XDG_CONFIG_HOME` and always uses
  `~/Library/Application Support/obs-studio`. The run refuses while any OBS runs, copies
  that folder, runs OBS with its own Sim profile, collection and WebSocket port, and
  restores the folder afterwards; all 5507 files compared equal after both test runs.
  OBS 32.2.2 crashes at launch on macOS 27.2 (a Qt bug fixed in OBS 33.0), so recording
  needs OBS 33.

## Tests

- **Scenario:** the new actions get role tests as the existing ones have. `full --check`
  stays valid. The deck shot names are unchanged.
- **Timeline:** unit tests for the pace rules (a short captioned action is never sped up,
  waiting is, no source time is skipped, the picture stands only in a still, lead-in and
  minimum caption time, long actions and runs, swap holds) and for the focus rules (own
  role, override, at most two large views, four quarters only for a step that names four,
  a single view in front, cockpits only around their stint).
- **Stage:** a rendering test lays out a fixed timeline and checks that no view overlaps
  a caption and that every view is inside the canvas; the view in front may cover the
  dimmed views behind it and lies on top.
- **End to end:** a short scenario (`smoke`) records and renders a few seconds in the
  manual workflow, with the stand-in instead of a real OBS, to keep the pipeline from
  rotting. The full film is a maintainer run.

## Delivery

An epic with these sub-issues, in this order:

1. Gates G1 to G4, with their results written into this spec.
2. The missing simulation actions and the driver page role.
3. Record mode: page recordings, pointer, timeline, the `card` feed look, generated
   intro, trailer and outro, the real OBS instance.
4. `full` extended: new steps, captions, chapters, focus.
5. The renderer and its stage.
6. Maintainer docs (`tools/CLAUDE.md`) and a first full film for review.

The finished film is uploaded by the user. The repository holds the scenario and the
tools, never the clips or the film.
