# Driver telemetry ingest, contract v1

The HTTP contract between a driver's sender and the relay (#1111). The sender script
`src/relay/racecast-sender.py` implements it; any other client, such as a later native
dashboard app, can implement it from this page. The product design is the spec in
epic #1109.

## Credentials

- **Invite link**: `https://<funnel-host>/console/drive?i=<invite>`. One per event:
  `racecast event start` creates it, `racecast event stop` deletes it, and
  `racecast event invite --new` or the Control Center's **New link** replaces it. The
  invite only opens the registration below.
- **Driver code**: what registration returns, `D1:<payload>.<signature>`. The payload
  names the entry, the stint or qualifying segment (endurance only), the driver's name
  and a random connection id. The relay signs it with a key that lives only in the
  invite file on the producer's machine, never in the link, so the link alone cannot
  mint codes and a new invite revokes every code. The ingest also refuses a code whose
  entry is no longer in the `Configuration` tab. A code never authenticates any other
  `/console` page.

## Registration

`GET /console/drive/data?i=<invite>` returns

```json
{"v": 1, "multi_lobby": false, "entries": ["OVO eSports #111"], "segments": [], "rate_hz": 20}
```

`entries` are the rows of the league's `Configuration` tab; `roster` is false when the
relay runs without the HUD and so has no entry list. `multi_lobby` is true for
endurance; `segments` then lists the stint and qualifying labels of the `Schedule` and
`Qualifying` tabs, and registration needs one of them.

`POST /console/drive/register?i=<invite>` with
`{"entry": "...", "segment": "...", "name": "..."}` returns `{"ok": true, "v": 1,
"pending": true, "code": "D1:..."}`. The registration is a request, and the ingest
refuses the code until Race Control or the director approves it, as Approval below
describes. Behind Funnel the client is the last `X-Forwarded-For` address the local
proxy appends; from any other peer the relay ignores that header. An IPv6 client counts
by its /64. Registration answers 429 beyond 30 registrations a minute per client. At 200
waiting requests a new one drops the oldest waiting request of the client holding the
most (on a tie the oldest of them), so a newcomer always lands. A client keeps at most three waiting requests per entry
and stint and eight in all; a further registration drops that client's own oldest
waiting request, never another client's. The ingest answers that code 410; the relay
remembers the last 500 dropped codes of the invite. Two requests with
the same entry, stint and name from different clients both wait, and the desks mark
them as duplicates. The desks never see the client address. The name takes up to 24 ASCII letters, digits, spaces, `_ . -`,
because it ends up in a command line (PSN Online IDs have at most 16); codes issued
under the earlier 32 character rule stay valid. While the entry already has its live senders,
registration answers 409 and the invite page shows the reason. `GET /console/drive/sender.py?i=<invite>`
serves the sender script of the running relay.

## Ingest

`POST /console/drive/ingest` with

- header `X-Driver-Code: D1:...`
- optional header `X-Sender-Stats`: a JSON object of integer counters the sender
  reports (`received`, `forwarded`, `posted`, `dropped`, `post_failed`,
  `interruptions`, `heartbeats`); other keys are ignored
- body, batch format v1: the four bytes `RCT1`, then for each packet a big-endian
  uint16 length and the packet exactly as the console sent it, still encrypted. At
  most 120 packets of at most 512 bytes each.

A sender posts batches at `rate_hz` (default 20 per second) and keeps every packet
in order. The relay decrypts and filters, so a new field never needs a sender update.
Packets of any GT7 format (`A`, `B`, `~`) are accepted; the sender script requests `A`
so a driver's other receivers keep working.

Reply `200`: `{"ok": true, "v": 1, "rate_hz": 20, "accepted": N}`. A client follows
`rate_hz` from every reply.

| Status | Meaning | Client action |
|---|---|---|
| 400 | malformed batch | drop that batch |
| 403 | invalid or revoked code, or a code without a request | register again (the sender script retries once a minute and tells the driver) |
| 409 | the entry already has its live senders | retry rarely; the producer can disconnect the other one |
| 410 | Race Control rejected this registration, the producer disconnected this sender, or a cap dropped this waiting registration | register again and use the new code; the sender script says so once and stops |
| 423 | the registration waits for approval, the body carries `"pending": true` | keep the code, tell the driver it waits, retry every few seconds, the sender script every 10 s |
| 413 | body too large | send smaller batches |
| 429 | too many failed attempts or senders | retry after a minute |

## Approval

Every registration lands as a pending request in `runtime/<profile>/driver-requests.json`,
bound to the invite. A new invite starts with none, a relay restart keeps them and
`racecast event stop` deletes the file. The
ingest answers 423 for a pending request, 410 for a rejected one (so the sender stops),
403 for a code without a request, and only an approved request reaches the hub, so nothing of an unapproved code
reaches the race, the recording, the dashboard or the public timing. An approval may
correct the entry, the stint and the name. The relay then uses the corrected values in
place of the ones signed into the code. These routes are on the tailnet, for directors
also under `/console/drivers/*` and for Race Control under
`/console/race-control/drivers/*`. `GET /drivers/requests` returns `{multi_lobby, entries, segments, requests: [{conn,
entry, segment, name, status, ts, registered {entry, segment, name}, who, decided}]}`;
`POST /drivers/requests/approve` and `/correct` take `{conn, entry?, segment?, name?}`,
where a missing field keeps the request's value, and `/reject` takes `{conn}`. They answer
`{ok, request}`, 400 for a value outside the roster, the stint labels or the name rule,
404 for an unknown request and 409 when the status does not allow the decision. Only a
pending request is approved, only an approved one corrected, and a rejection is final.
`POST /drivers/requests/reject-pending` rejects every waiting request and answers `{ok,
rejected}` with their count; if requests keep coming, renew the invite. A rejection
stops new data and leaves what the code sent before in the race and the recording;
`/drivers/reassign` and `/drivers/race/change` answer 409 for a rejected connection.
A decision or a Move (`/drivers/reassign`) answers 404 when the invite was renewed
meanwhile and 500 when the file could not be written, which leaves the request, and for
a Move the sender's entry, as it was. Each decision appends `driver_approve`, `driver_correct`,
`driver_reject` or `driver_reject_pending` with `who` to `driver-race-audit.jsonl`, the
single decisions with the new values and `before`.

## Limits

- One live sender per entry in a single-lobby race, two per team in endurance (the
  driver on track and the one waiting in the next lobby). A connection is live while
  its batches arrive; after 20 s without one its slot is free.
- A second registration for a full entry is refused (409 at `register`, and at
  `ingest` for a code issued before the entry filled up), not swapped in. A sender that
  restarts with its own code is the same connection. While the console sends nothing, a
  client keeps its slot with an empty batch (`RCT1` alone) at least every 20 s; the
  sender script sends one every 5 s.
- A league without stint labels registers by entry alone, in endurance too.
- The producer sees every connection on the tailnet (`GET /drivers/data`) and
  disconnects one with `POST /drivers/disconnect {"conn": "..."}`. The ingest answers
  that code 410 from then on, also after a relay restart; the relay remembers the last
  500 disconnected codes of the invite. A connection quiet
  for 10 minutes leaves that list, the readiness rows and the dashboard; it comes back
  as a new sender if it posts again, still under the entry the producer reassigned it to. A new invite drops every connection.

## Race

`on_sample` of the hub hands every decoded sample to `driver_race.Service`
(`race_distance.Race`), dated on the relay's clock by its packet id. Tailnet only:
`GET /drivers/race` returns `{state, track, settings, line_length_m, entries}`;
`POST /drivers/race/settings` takes `formation`, `finish_mode` (`laps`/`time`) and
`lap_count`; `POST /drivers/race/final-lap`, `/finish` and `/reset` act on the race.
Race Control actions (#1113), each answering `{ok, race}` or 400 with `error`:
`/red-flag {mode?}` (default: the profile's `restart`), `/restart-mode {mode}`,
`/release`, `/rejoin {entry, on}`, `/correct {entry, metres, pit_stops, reason}` and
`/change {entry, conn}`. The race adds `red_flag`, `restart_mode`, `league` and
`last_action {ts, who, action}`; entries add `correction_m` and `rejoin`, and the
status `red_flag` while an entry waits for its restart.

The viewer side (#1115): `GET /drivers/dashboard` (also `/console/drivers/dashboard` for
every crew role) returns the delayed allowlist payload `{v, mode, stint, remaining,
remaining_running, track, track_name, visible, delay_s, show, state, red_flag, lap
{leader, count}, lobby_lap, fastest {entry, driver, team, ms}, classes [{name, color,
lap, laps, fastest}], entries [{pos, entry, number, driver, team, color, text_color,
brand, brand_name, laps, stint_laps, gap_s, interval_s, lapped, status, pit_stops,
last_lap_ms, fuel_l, finished, compound, x, z}]}`. `classes` is empty without two named
classes; with them every row adds `class, class_pos, class_gap_s, class_interval_s,
class_lapped, class_color`. A row's `status` is one of the race statuses below, plus `dc`
for a quiet car whose disconnect Race Control confirmed. In qualifying the payload
carries `qualifying [{pos, entry, number, driver, team, color, text_color, brand,
brand_name, best_ms, gap_ms, interval_ms, last_lap_ms, compound}]` and `classes [{name,
color}]` instead of the race fields; with classes a row adds `class, class_pos,
class_gap_ms, class_interval_ms, class_color`. `GET /drivers/line` returns the picked
layout's line `{track, length_m, points [[x, z]]}`. `lap.leader` is the leader's running
race lap (the formation lap is none) and `count` the lap count in race laps; `leader`
never passes `count`. A timed race (`finish_mode` `time`) has no `count`, it stays null.
In endurance `count` is the final lobby's count, so it stays null until the leading car
runs in the final lobby. `lobby_lap` is GT7's own counter of the
lobby on air, null until a car there counts. The public live timing page (#1128) gets
this payload cut by `driver_race.public_view`: `v, mode, stint, remaining,
remaining_running, delay_s, track_name, state, red_flag, lap, fastest, classes` and the
rows without `x` and `z`; a row names a `brand` only when the relay holds its logo. `visible`,
`delay_s` and `show` are always the current settings, never delayed. The settings take
`delay_s` (0 to 120). `POST /drivers/race/fastest` with no `entry`
clears the fastest lap, with `{entry, driver, ms}` sets it. `POST /drivers/race/quali
{entry, ms}` sets a roster entry's time in the running qualifying segment, `ms: null` deletes it, `clear: true` gives
GT7's time back; `GET /drivers/race` lists them as `quali_overrides`. `POST
/drivers/race/compound {entry, code}` sets a roster entry's tyre compound (`RS`, `RM`,
`RH`, `I`, `W`, or null to clear), listed as `compounds {entry: {code, who, ts}}`; the
dashboard rows carry `compound`, and `show {compound, fuel, last_lap_s}` follows the
settings `show_compound` and `show_fuel` and the profile's `last_lap_s`.
`state` is `no_line`, `waiting`, `racing`, `final_lap`, `finishing` or `finished`;
each entry carries `distance_m`, `laps`, `gap_s`, `interval_s`, `lapped`, `status`
(`waiting`, `racing`, `box`, `red_flag`, `no_data`, `no_sender`, `est`, `retired`),
`finished`, `pit_stops`, `gt7_position`
and `position_mismatch` (single lobby: GT7's position contradicts the distance order).

## Versioning

`v` in every reply is the contract version. A change that an existing client cannot
follow raises it, and the relay then states which versions it accepts. The approval
stayed in v1. A client that does not know 423 treats it as a failed post and keeps
retrying, which is what a waiting client has to do. A rejection answering 410 instead
of 403 stayed in v1 too: both tell the client to register again, and 410 already stops it.
