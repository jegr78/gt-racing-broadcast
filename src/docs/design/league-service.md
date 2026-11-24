# League service

Design agreed 2026-11-22. Contract, configuration, client and the simulated service
(#1253), the result export (#1254), the live state with its widgets and Director control
(#1255) and the widgets on the timing page (#1256) are implemented.

A league runs its own management service (the Community Team Cup runs CTC Manager,
Community League Racing runs CLR). During a race the relay sends the live race state
to that service. The service answers with values it computed from it, for example a
team-versus-team points score or a championship table, and racecast shows them as
widgets in the stream and on the public live timing page. After the race the relay
sends the final classification, so nobody types the result into the league service.

racecast calls this a league service, never a plugin: no league code runs inside the
relay or the binary.

## Principles

- The league service computes every league value. racecast computes none of them and
  holds no scoring rule, points table or penalty logic.
- The relay calls the service. The service never calls the relay, and nothing new is
  funnelled.
- The contract is league-neutral. It carries a two-team score as well as a table of
  twenty drivers.
- Without a configured service nothing changes.
- A slow or dead service never delays the relay, the HUD or the recording.

## Configuration

League values in `profiles/<name>/profile.env`:

| Key | Meaning |
| --- | --- |
| `LEAGUE_SERVICE_URL` | Base URL of the service's racecast endpoints. HTTPS only. |
| `LEAGUE_SERVICE_KEY` | Key the service issued. A secret like `CONSOLE_SECRET`. |

One service per profile. The Control Center refuses to save a URL that is not HTTPS,
and the client refuses one that was edited into the file by hand. In simulation mode
`RACECAST_SIM_LEAGUE_SERVICE_URL` and `_KEY` replace both values, so a rehearsal reaches
the loopback fake (`tools/event-sim/fakes/league_sim.py`) and never the real service.

The league service shows a reference on its own match or event page. The producer
enters it in the Control Center, which then loads the event from the service: its
races in order (the legs of a CTC match, one race for a CLR event) and the widgets.
The relay keeps the reference, the race list and the current race in
`runtime/<profile>/league-event.json`; `event stop` clears it. Every call names the
reference and the current race. The service never guesses the race from date or
drivers.

The current race starts at the first one. It moves to the next when a new race starts
counting after a finished one, the moment the recording re-arms. Until then a resend
still belongs to the finished race. The Director can set the current race by hand.
After the last listed race the relay keeps sending live state but exports no further
result, and the Director Panel says so. `league-event.json` keeps the `finished` mark,
the finish time and the `done` mark next to the current race, so a relay restart between
the finish and the next start still moves on. The first race state a relay sees is its
baseline: a restart inside a finished race sends nothing again. The Director's pick
names the next race to send: it clears the `finished` and `done` marks, so a resend of
the picked race comes from the recording, never from the live race just run.

Whether the widgets appear on the public timing page is a separate profile switch,
`LEAGUE_WIDGETS_TIMING` in `profile.env`, default off, also a checkbox in the Control
Center's League service section. A running relay picks a change up within a tick.

## Contract

The OpenAPI file `league-service.openapi.yaml` next to this document is the source.
racecast owns it. Each body carries `v`. Fields are only ever added and receivers
ignore fields they do not know. A breaking change gets a new version number and a new
path.

Every request carries `Authorization: Bearer <LEAGUE_SERVICE_KEY>` and the racecast
`User-Agent`.

| Call | When | Answer |
| --- | --- | --- |
| `GET /v1/event?ref=` | The producer loads the event in the Control Center. | The event's races and the widgets the service offers. |
| `POST /v1/live` | Every two seconds and at once on a position change, in race and qualifying mode. | The current value of each widget. |
| `POST /v1/result` | When the race state turns `finished`, and on the operator's request. | A status, optionally a link and a graphic. |
| `GET /v1/result?ref=&race=` | The operator refreshes a pending result. | The same status. |

### Live state

The body is an allowlist of its own next to `driver_race.public_view`, built from the
undelayed standings: reference, current race, `mode`, race state, red flag, stint, lap,
remaining distance or time, track name (null until a track is picked; the race state
is null until it is known), the fastest lap, and one row per car with
position, entry, number, driver, team, class and class position, laps, gap and
interval, lapped count, status, pit stops, last and best lap time and the finished
flag. No fuel, no tyre compound, no stream URL, no telemetry channel. The rows are
always `entries`, in qualifying mode too, where the dashboard snapshot itself uses
`qualifying`.

Drivers and teams go out verbatim as the Sheet's Configuration roster names them. The
service matches them to its own records. Risk: CTC Manager matches drivers by PSN id
and the roster holds display names, so a name that differs needs an alias on the
service's side. Each row has an optional `driver_ref` for an id of the service, empty
for now, so a later roster import from the service needs no contract change.

Every roster entry stands in the standings with or without a sender
(`race_distance.set_roster`). Race Control places a car without data relative to
another one (`race_distance.estimate`) and corrects distance and pit stops
(`correct`), as in endurance. The service receives the standings after those
corrections, so it never sees a missing car.

### Widgets

A widget has a stable `id`, a title, one of two forms and sample values:

- `score`: up to eight labelled values, for example two teams and their points.
- `table`: up to 64 rows of rank (1 to 999), name and value, with an optional column title.

The answer to `POST /v1/live` maps widget ids to their current values. racecast
renders both forms itself in the league look and sets every string as text. The
service sends no HTML, no CSS and no image for a widget.

Limits the relay enforces: eight widgets, 64 rows per table, bounded string lengths.
Anything beyond a limit is dropped, and an answer that does not parse leaves the last
good value in place. Dropped means the whole element, never a cut string: a race, widget,
value or row with a field over its limit, of the wrong type, null, or holding a control
character is left out, and that holds for an optional field that is present too. A list
keeps its first allowed items. An answer over 256 KiB, one that takes longer than 10 s,
one that is not JSON, one without `v: 1`, or an event or status whose own title or
message is invalid does not parse.

The widget list arrives with the event. Loading the event (Control Center or `racecast
event league REF`) keeps it in `league-event.json` for the relay and in the profile's
`profiles/<name>/league-widgets.json` for the scene builders, outside `overlay/` like the
audience widgets. The scene builders then offer each widget as a slot
(`league-widget-<id>` in the HUD, Intermission and Split HUD layouts, not the driver
dashboard) and work offline with the sample values. A widget the service no longer lists
stays in the profile file marked unlisted: it stays placed with its style, the builder
shows it dimmed, and on air it stays hidden because the loaded event does not name it.

A placed widget reaches a live page as a hidden element; the page polls
`/league-widgets/data` once a second and fills it from elements and text only. The
league look comes from the profile's overlay CSS: the widget inherits the page font, and
its colours are the variables `--league-widget-bg`, `--league-widget-text` and
`--league-widget-accent` (the border), which a league sets in `:root` of its overlay CSS.
Without them the widget is white on translucent black with a green border. The builder
keeps those colours as variable references on save; position, size, font and a colour
the producer sets by hand are slot props like any HUD slot. A table shows only the whole
rows that fit its box, the last line counting the rest ("+11 more"); a row is never cut.

### Result

The body carries the reference and the race, the final classification (position, driver, team, class, laps, gaps, best
lap, pit stops, status), the fastest lap, and the events a steward needs: red flags,
confirmed disconnects and Race Control corrections with their time. No per-second
samples. The full recording stays on the producer's machine.

The service stores the result as a draft that a person confirms there. A resend for
the same reference and race replaces the draft, so corrections made after the finish still
arrive.

A recording sample holds position, entry, driver, laps, gaps, status and pit stops,
but no team, no lap time and no fastest lap. The recording therefore stores the result
itself: at the finish the relay builds the Result and keeps it in the recording's `meta`
table under `results`, one document per race with its reference, race and finish time.
A resend rewrites that document. The other way, rebuilding the result later from the
roster, the saved race actions and `driver-race-audit.jsonl`, does not work after the
event: a race reset deletes the fastest lap and the corrections, `event start` clears
the actions, each car's counted laps live in memory only, the audit log has no event
boundary, and `event stop` clears the reference and the race list. A recording without
a stored result (one from before this version) cannot be sent; the CLI says so. A race
that finished before the event was loaded is kept without a reference and race; sent
from the recording, it goes out for the loaded event's current race unless the operator
names another (the `RACE` argument and `--ref`).

The relay builds the Result from the race service and the recording:

- The classification is the standings at the finish after Race Control's corrections,
  with the roster's number and team. `best_ms` is a car's fastest lap that counted for
  the fastest lap (GT7 took it as its best lap, #1264), so a voided lap is no car's best
  lap any more. A fastest lap Race Control sets by hand is its car's best lap.
- `fastest` is the race service's confirmed fastest lap with the roster team, never the
  fastest raw lap time.
- `events` are the audit log's `red_flag` actions (kind `red_flag`, once per flag: a
  repeated hold before the release is the same flag; the release is no event, since the
  contract has no kind for it), its
  `correct` actions with metres, pit stops and Race Control's reason text (kind
  `correction`; the reason leaves the machine for the league's own service) and the
  recording's confirmed disconnects, dated by their last packet (kind `disconnect`).
  The window starts at the latest of the recording's start and the last race start,
  reset or new event in the audit log (the relay writes a `race_start` line when a race
  starts counting), so it survives a relay restart and a new track pick. It ends when the
  result is built, so a correction made after the finish is in a resend. Who acted stays
  on the producer's machine. Retirements, estimates, driver names and fastest lap
  changes are not steward events in this version.

The first version exports the race only. The recording writes in race mode only, so a
qualifying export is a later step.

The answer carries `status` (`stored` or `pending`), an optional `message`, an
optional `link` and an optional `graphic_url`. The relay fetches the graphic only from
the service URL's own scheme, host and port, follows no redirect, accepts PNG only (the
`image/png` type and the PNG signature) up to 4 MB, and files it under
`runtime/<profile>/league-graphics/`. It never goes on air by itself. The Director Panel
shows it, and the Director's **Use as Race Results** copies it over the `Race Results`
graphic and has OBS reload that image; the existing RACE RESULTS key then puts it on
air. A `racecast graphics` download brings back the Sheet's Race Results graphic. While
the status is `pending`, a refresh action in the Control Center asks the service again.
Nothing waits for the answer: the race, the next race and the event stop go on without
it.

The relay sends on its own thread. A send that a relay restart interrupted is marked
`failed` (final) with "the relay restarted", never resumed by itself. When the service
took a result but this machine cannot write its answer, the relay does not post again
and shows the answer as sent with the local error. `league-event.json` and
`league-results.json` are written under a lock file, so the relay and the CLI never
lose each other's change. A refusal a retry cannot change (HTTP 400, 401, 403,
404, 409, 422) is final: the result stands `failed` with the service's own reason (its
`error` or `message`, bounded and typed like every answer) in the Control Center and the
Director Panel, and nothing retries it. A timeout, an overload (408, 429), a server error
(5xx) or a network failure is retried after 2 s, doubling up to 60 s, as long as the
relay runs. A manual resend is always possible. A newer result for the same reference
and race replaces a queued one.
Every answer, and every failure with its reason, lands in
`runtime/<profile>/league-results.json` (the newest 64 sends), which the Control Center,
`racecast event result` and the Director Panel show. `event stop` keeps it.

## In the relay

A new stdlib-only module under `src/scripts` holds the client, following
`timing_publish.py`: its own thread, bounded socket calls, backoff after a failure.
The tick hands it the standings and never waits for it.

- The relay starts the sender on its own thread once it owns its port. The dashboard tick
  hands it the newest undelayed snapshot (`driver_race.Service.live`, which adds each
  car's best counted lap) and only wakes it: at once when the running order or the mode
  changed, but never sooner than one second after the last send, and with a settings
  reload when `profile.env` changed. Without a loaded event nothing is sent.
- The HUD shows a widget value as soon as it arrives.
- The public timing page shows it with the same `delay_s` as the timing itself, so the
  score never gives away an overtake before the picture does. The relay delays the
  value; the service knows nothing about the delay.
- A value is held for 30 seconds after the last good answer. After that the widget
  hides, and the Director Panel names the reason. Each value goes to the page with the
  seconds of hold it has left, so a page also hides it when the relay stops answering.

## Control

- Placement: a slot per widget in the scene builders, positioned and styled per scene
  like the audience counters.
- On air: the Director shows and hides each widget. Default hidden. The switch lives in
  the relay's memory, so a relay restart hides every widget again, and so does loading an
  event (another reference or a reload of the same one), which also drops the values the
  old event's answers left; a change of the current race keeps both, and so does a
  `league-event.json` that cannot be read for a moment. `event stop` removes the file and
  hides everything. A widget is on air
  only when it is shown, placed in the scene and has a value inside the hold. The routes
  are `GET /league/widgets` (each widget with its switch, the scenes it is placed in and
  the reason it cannot show) and `GET /league/widget/<id or place>/<show|hide|toggle>`,
  director-only under `/console`. The place (1 to 8) is the widget's 1-based position in the
  event's list, so the Companion buttons **LEAGUE WIDGET 1** and **LEAGUE WIDGET 2** on
  page 3 work for any league. A service that reorders its widget list moves what a
  button switches.
- Timing page: the profile switch decides, independent of the on-air state. The block
  sits above the table. The league timing host stores and broadcasts the relay's state
  body unparsed, so the block reaches hosted viewers without a Worker change; the
  hosted viewer page gets the same rendering. The block lists every widget of the loaded event
  that held a value `delay_s` ago, each as id, form, title and value, nothing else, a
  table cut to its first 20 rows to keep the page's upload small. A
  value newer than the delay, or one whose hold ended before it, stays off the page.
- Result: sent automatically at `finished` and retried after a failure. "Send result"
  is also an action in the Control Center and a `racecast` verb (`racecast event result
  send [RACE]`), for the current event or for a recording file chosen after the event
  (`racecast event result send RACE --recording FILE [--index N] [--ref REF]`; the
  Control Center takes only a recording file name in the profile's runtime folder). For the current event a running relay
  rebuilds the result from the live race while it still stands finished, otherwise it
  sends the copy in the recording. The Control Center shows the service's status,
  message and link, and a refresh while the status is `pending`
  (`racecast event result refresh`). The Director Panel shows the current race, lets the
  Director set it, send again and take the graphic, and says when the event's races are
  over. Its routes (`/league/status`, `/league/race`, `/league/result/send`,
  `/league/graphic`, `/league/graphic/take`) are director-only under `/console`.

## Security

- This is a second outbound path next to the league timing host, and the first that
  carries the undelayed standings off the machine. It goes only to the league's own
  service, and the root `CLAUDE.md` security section changes with the first PR.
- The key is a profile secret: never logged, never in a status response, excluded from
  anything that reaches the tailnet-facing status.
- The service URL must be HTTPS. The client follows no redirect at all, so the key
  never reaches another host.
- Outgoing data is an allowlist. Incoming data is bounded, typed and rendered as text.
- The graphic fetch accepts only the service URL's own scheme, host and port, PNG only,
  up to 4 MB, no redirect. The key goes with it, to that origin only.
- The driver names leave the producer's machine for the league's own service. The
  league decides that by configuring the service.

## Testing

- A simulated league service in `tools/event-sim` answers all calls, so the suite and
  the e2e harness run without a real service.
- A maintainer tool plays an existing race recording against a service URL. CTC Manager
  and CLR build their side against real race histories without a race running.

## Order

One epic, one PR per issue:

1. Contract, configuration and the client module with the simulated service.
2. Result export with the Control Center action and the CLI verb.
3. Live state, widget list, builder slots and Director control.
4. Widgets on the public timing page and the timing host.

The endpoints in CTC Manager and CLR are issues in their own repositories.

## Later

- Roster import from the league service (`driver_ref`).
- Qualifying result export.
