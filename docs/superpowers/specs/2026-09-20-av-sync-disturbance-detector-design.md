# A/V sync disturbance detector — design

**Status:** design. 2026-09-20. Measured live on the Windows producer host
(`PC-JeGR-Streaming`, OBS 32.2.2, obs-websocket 5.7.4, relay preview
`1.11.0-preview.main.289dfb2`) against a live YouTube 1080p60 source with a facecam.

## Problem

The producer reported picture and sound drifting apart. Nothing in racecast records
that. `/status` already has a `desync` block, but it means something else entirely (the
ping-pong stint mismatch of #494) and must not be overloaded — this one is `av`.

The only thing that knows is OBS, and it writes it to its own log file where nobody
looks:

```
22:52:21  Source Feed A audio is lagging (over by 5415.66 ms) at max audio buffering. Restarting source audio.
```

Tonight that line was the only reason the cause could be pinned down at all. It reaches
no health reason, no panel, no history, no post-event report.

## What OBS actually does (read, not assumed)

`libobs/obs-audio.c`, the call site of `ignore_audio()`:

```c
/* if a source has gone backward in time and we can no
 * longer buffer, drop some or all of its audio */
if (audio_buffering_maxed(audio) && source->audio_ts != 0 && source->audio_ts < ts.start) {
        bool rerender = ignore_audio(source, channels, sample_rate, ts.start);
```

`ignore_audio()` discards audio samples until the source's audio timestamp is back on
the mix clock. If discarding everything buffered is not enough, it logs the line above,
sets `audio_pending`, zeroes `audio_ts` and clears `timing_set` so the timestamp
adjustment restarts from scratch.

Three consequences that shape the whole design:

1. **The audio is pulled forward; the video is never touched.** An intuition that "the
   picture has to catch up" is inverted — OBS throws audio away.
2. **By the time the line exists, OBS has already applied its remedy.** Reacting to it
   with a relay-side input rebuild would be a *second* disturbance on top of a repair
   that already happened. The detector therefore records and reports; it does not act.
3. **The trigger is a timestamp that went backward**, which is exactly what a feed
   restart hands OBS: the new stream carries its own time base. The `DTS ... out of
   order` and `Packet corrupt` warnings are the same event one stage earlier, in the
   demuxer.

There is no fixed tolerance to tune. The condition is `audio_buffering_maxed()`, not a
millisecond threshold, and the logged magnitude is only *how far past* the mix clock the
source was when the buffer ran out. Measured tonight: 103, 170–190 (six within 170 ms),
999–1009, 4717 and 5415 ms. An earlier idea in this session to "lower OBS's max audio
buffering" was based on a remembered 960 ms default and is withdrawn.

## Measured: a restart is the trigger, an active output is a separate effect

Four runs, same source, same minute-scale window, one restart each, order reversed
between the pairs to rule out ordering. `tools/relay-restart-soak.py --hours 0.06
--every 2`.

| Run | OBS output | Backlog peak | Recovered ≤60 s | OBS audio repair after the restart |
|---|---|---|---|---|
| 1 | idle | 6.2 s | yes (10.1 s) | none (only `Packet corrupt` + DTS) |
| 2 | recording | 18.0 s | **no** | 5415 ms |
| 3 | recording | 29.5 s | **no** | 170–190 ms, six times |
| 4 | idle | 3.1 s | yes | 4717 ms |

Two independent findings, and they must not be conflated:

- **The audio repair follows the restart, not the output state.** Three of four restarts
  produced one, in both states. Every relay restart risks a brief audible audio gap.
- **An active output stops the backlog from recovering** (2/2 fail vs 2/2 pass). That is
  a latency-to-live problem, not an A/V problem, and belongs in its own issue.

The producer confirmed by eye and ear: the short audio gaps were audible, and picture and
sound stayed in sync afterwards. Their ranking is explicit — a standing A/V offset is far
worse than a brief gap that restores sync. So OBS's repair is the *desired* behaviour and
the design must not suppress it.

## What is detectable, and what is not

**Detectable, exactly, from OBS:** every disturbance that OBS repaired, with its source
name, timestamp and magnitude.

**Detectable, earlier, from the relay:** `inbound_max_gap_s` above
`RACECAST_FEED_PREBUFFER_S`, and the restarts the relay itself causes. Tonight both
restart gaps (3.94 s and 4.33 s) exceeded the 3.0 s reserve.

**Not detectable by anything here:** a true lip-sync error whose timestamps are
internally consistent. OBS aligns by timestamp; if the timestamps themselves are wrong
relative to each other, nothing in OBS or the relay can know. Proving lip sync needs
content analysis or a person. The design must not imply otherwise, and must never print
an all-clear it cannot support.

## Design

### Signals
The CLI resolves OBS's log directory today (`logsetup.obs_log_dir`, used by `racecast obs
logs`). The relay must not import shared modules, so the path is passed in as
`--obs-log-dir`, the same way `--overlay-dir` already is. The relay tails the newest OBS
log and matches three patterns, attributing each to a feed by the source name OBS prints
(`Feed A`, `Feed B`, `Feed POV`):

| Pattern | Meaning |
|---|---|
| `Source <name> audio is lagging (over by N ms)` | OBS repaired an audio timing break |
| `DTS <a> < <b> out of order` | demuxer saw a backward timestamp |
| `Packet corrupt (stream = N, ...)` | a spliced packet did not parse |

Pure parsing (line → `{ts, source, kind, ms}`) belongs in `src/scripts/` with unit tests,
like every other parser in this repo; the tail thread stays in the relay.

### Classification
A repair **within a restart window** (the relay knows when it restarted a feed) is
expected: count it, record it, do not page. A repair with **no restart nearby** is the
interesting one — something disturbed the stream that the relay did not cause. That is
the case that earns a yellow health reason.

### Publication
- `/status`, per feed: `av: {repairs, last_ms, last_ts, unexplained}`.
- A yellow health reason for an unexplained repair, worded so it does not blame a
  component the way the current backlog reason wrongly blames OBS.
- A `health-history.db` column, so the post-event report can state how many sync
  disturbances an event had and whether any were unexplained.
- Director Panel: a marker plus a prompt to **look at the program**, because only a
  person can confirm lip sync. A prompt to check, never an automatic all-clear.

### Deliberately not doing
- **Rebuilding the input on the message.** OBS already repaired; a rebuild would add a
  second disturbance. Revisit only if measurements show repairs that leave a standing
  offset.
- **`SetInputAudioSyncOffset`.** It is a scalpel for a constant offset and the wrong tool
  for a timestamp break. Available (verified: 5.7.4, currently 0 ms) if a constant
  per-league offset ever turns out to be needed.
- **Lowering OBS's audio buffering.** Withdrawn above — there is no millisecond threshold
  to lower.

## Open

- The active-output backlog failure (runs 2 and 3) needs its own issue and its own
  measurement. It is unrelated to A/V sync.
- The `/status` backlog health reason currently reads "OBS reads slower than real time".
  Measured against this host, OBS played at exactly 1.000× for 60 s while that reason was
  live. The attribution is wrong and sends a director after the wrong component.
