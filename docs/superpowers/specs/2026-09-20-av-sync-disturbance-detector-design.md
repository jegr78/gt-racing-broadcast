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

## Design (built; live-verified on the producer host 2026-09-20)

### Signals
The relay resolves OBS's log directory itself with `logsetup.obs_log_dir`, which it
already imports. An earlier draft of this spec claimed the relay may not import shared
modules and therefore needed an `--obs-log-dir` flag; it imports `logsetup` at line 157,
so the flag was dropped before it was ever written. `AvSyncWatcher` tails the newest log
and matches three patterns:

| Pattern | Meaning | Carries a source? |
|---|---|---|
| `Source <name> audio is lagging (over by N ms)` | OBS repaired an audio timing break | yes |
| `DTS <a> < <b> out of order` / `DTS discontinuity` | demuxer saw a backward timestamp | no |
| `Packet corrupt (stream = N, ...)` | a spliced packet did not parse | no |

Only the first names a source, so only it can be attributed to a feed. The other two are
counted as unattributed context rather than guessed onto one.

Only lines appended AFTER the watcher starts are read, which is what lets every event be
stamped with the relay's own clock: OBS writes `HH:MM:SS.mmm` with no date, so parsing
its timestamp would need the file's date plus midnight-rollover handling for a value the
tail lag already gives to within a second. Both readers of that state take their own
`time.monotonic()` rather than accepting the wall-clock `now` that /status and the
heartbeat pass around; mixing the two would produce ages of about 1.8 billion seconds.

Pure parsing, classification and aggregation: `src/scripts/av_sync.py`, tested in
`tests/test_av_sync.py` against lines copied verbatim from a real OBS 32.2.2 log.

### Classification, and how its window was set
A repair within `RESTART_WINDOW_S` of the feed entering `serving` is EXPECTED: the relay
caused it by splicing a new stream. Anything else is UNEXPLAINED, and only that earns a
health reason.

The window is derived, not chosen. Three legs stand between a feed serving and OBS being
able to log a repair at all, and this repo pins every one:

| Leg | Bound | Source |
|---|---|---|
| relay waits out the HLS prefetch burst | up to 7 s | `SEGMENT_FETCH_BUDGET_S` 1.0 x `--hls-live-edge 4`, plus the 3 s reserve |
| OBS reconnects to the rebuilt input | up to 10 s | `reconnect_delay_sec: 10` in `GT_Racing_Endurance.json` |
| OBS fills its buffer before divergence shows | about 9 s | `buffering_mb: 8` at the measured 7.2 Mbps |
| | **26 s floor** | |

Measured against that floor: repairs landed 6-14 s after serving in three cases and 32 s
in a fourth. A first attempt at 30 s called that fourth one unexplained and turned the
panel yellow for a repair a restart had almost certainly caused — two seconds decided it.
The value is therefore rounded up to two heartbeats (60 s), and the rounding is a
deliberate asymmetry: too narrow cries wolf on every handover, and a detector the
director learns to ignore is worth nothing; too wide misses an unexplained repair in the
first minute after a restart, when the director already knows the stream was disturbed.

### Publication
- `/status` gains its own `av` block (`{feeds: {...}, context: {...}}`), absent until
  something happens. Deliberately NOT part of `desync`: that is the ping-pong stint
  mismatch of #494, and sharing a name would render one state under the other's label.
- A yellow health reason for a recent unexplained repair, held for 5 minutes so one blip
  does not paint the panel yellow all event. It is excluded from the Discord notify level
  exactly like the #535 inbound stall and the #583 backlog — an `@here` for something OBS
  has already repaired trains the crew to ignore the pings that matter.
- The reason names no fix, because there is none left to apply, and asks for the only
  check that can confirm lip sync:
  `Feed A audio timing broke by 987 ms with no restart to explain it — OBS re-synced
  itself; check the program picture and sound`
- `health-history.db` v11 adds `av_repairs_total` / `av_unexplained_total` (running
  totals, additive migration), and the post-event report states how often OBS re-synced a
  feed's audio and how many of those nothing explained.
- **No new UI surface.** The health reason already reaches the Director Panel through the
  existing health block, so nothing under `src/ui/` or `src/director/` changed and no wiki
  screenshot went stale.

### Deliberately not done
- **Rebuilding the input on the message.** OBS has already repaired it; a rebuild would
  add a second disturbance to a finished repair.
- **`SetInputAudioSyncOffset`.** Verified available (obs-websocket 5.7.4, currently 0 ms)
  and still the wrong tool: a scalpel for a constant offset, not a timestamp break.
- **Lowering OBS's audio buffering.** Raised and withdrawn during design. There is no
  millisecond threshold to lower: the condition is `audio_buffering_maxed()`, and the
  logged value is only how far past the mix clock the source was. Measured the same
  evening: 103, 170-190, 999-1009, 4717 and 5415 ms, all the same event.

## What the live run found

Running the relay from source against real OBS on the Windows producer host produced
three defects that no local check had shown, which is the argument for doing it:

1. `'Relay' object has no attribute 'log'` — the relay logs through a module-level `LOG`.
   `_start_av_watcher()` runs only from `Relay.start()`, which no unit test called, so
   the whole suite passed and the relay died on its first real start. Covered now.
2. `UnicodeDecodeError` reading `streamlink --help` on a German Windows — pre-existing,
   not part of this work, fixed alongside it. `subprocess` reads pipes in a THREAD, so
   the surrounding `except` never saw it: the relay printed a traceback and silently lost
   the help text and with it the queue-deadline flag. A test now keeps all seven
   `subprocess.run` calls in the file decoding leniently.
3. The health reason named the magnitude of the WRONG event: it read `997 ms` while the
   unexplained repair had been `987 ms` and the 997 belonged to a later, explained one.
   That is the same mis-attribution this detector exists to stop, one layer up. The
   unexplained magnitude is tracked separately now.

Verified on that host: the watcher opens OBS's **currently active** log while OBS holds
it (a Windows file-sharing lock would have sunk the whole approach), a clean start
publishes no `av` block and stays green, and a real `/reload/A` produced repairs the
detector attributed to Feed A with the right counts.

## Open

- The active-output backlog failure (an active OBS output stops the backlog recovering
  after a restart, 2/2 fail vs 2/2 pass) is unrelated to A/V sync and belongs to the
  #619 chain review.
- The `/status` backlog health reason still reads "OBS reads slower than real time" while
  OBS was measured at exactly 1.000x for 60 s in that state. The attribution is wrong and
  sends a director after the wrong component. Recorded in #619.
