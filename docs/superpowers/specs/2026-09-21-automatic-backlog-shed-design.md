# Automatic backlog shed — design

**Status:** design. 2026-09-21. Part of the #581 chain (#619's open end).

## Why this overrides an epic decision

#581 recorded the opposite decision, under *Explicitly out of scope*:

> **Touching the byte stream to shed a backlog.** [...] one second of backlog healed
> with one second of black is not a trade. That is what the 26 rebuilds were doing by
> accident.
>
> **A director may resolve a backlog deliberately.** The painful remedy stays available,
> but only a human chooses it, with the cost shown before the click.

That came out of the Catalunya 12 h qualifying post-mortem, where an unguarded automation
produced 26 black dropouts in 56 minutes.

The producer overrode it on 2026-09-21, as a standing preference rather than a one-off:

> *„Ich will die Automatik. Wie schon häufiger erwähnt soll das manuelle Eingreifen nur
> die Notlösung sein."*

Two things changed since Catalunya, and both are why the override is buildable rather
than a repeat:

1. **`RebuildGuard` exists** (#582). It judges whether a rebuild actually helped and
   stands down after three ineffective ones. Catalunya's automation had no such notion;
   this is the single change the epic itself named as the fix that "would have turned 26
   black dropouts into three plus an honest warning".
2. **The backlog is measured** (#583). Catalunya acted on ring overflow, i.e. only once
   the backlog had already destroyed itself. The trigger here is the measured floor, long
   before an overflow.

The director's manual `/obs/feed-reset` (#587) stays exactly as it is. It becomes the
fallback rather than the primary path, which is the producer's stated model.

## One signal added to one control, not a second automation

The epic's other warning is kept verbatim, because it is about correctness rather than
policy:

> Two automations turning the same control is a race someone ends up debugging
> mid-broadcast, and the director should keep seeing exactly one state per feed.

So this is **not** a new detector loop. The freeze path (`_freeze_tick`) already owns the
OBS-rebuild control, and the backlog becomes a second reason to pull that same control.

**It does not live inside `_freeze_tick` though**, and that is deliberate. The freeze
detector runs on its own sampler thread at `_freeze_interval_s`, while the backlog
classification is produced once per heartbeat by `_sample_consumer_backlogs`. Putting the
shed inside the sampler would read a signal from another thread's cadence and would also
inherit the sampler's cursor-window debounce, which has nothing to do with a backlog. The
shed therefore runs on the **heartbeat**, immediately after the classification it reads,
as `_backlog_shed_tick(now)`. `self._rebuild_lock` already serialises the guard against
the sampler thread and the director's re-arm; the heartbeat becomes its third user.

Concretely all three of these are shared:

| Shared | Consequence |
|---|---|
| the control (`f._obs_reconnect()`) | the two reasons can never rebuild concurrently |
| `self._rebuild_guard` | three ineffective rebuilds stand down, whatever mix of reasons fired them |
| `self._last_freeze_ts` cooldown | a freeze rebuild silences the backlog trigger and vice versa |

## Trigger

The signal is the one `_sample_consumer_backlogs` already computes each heartbeat:
`self._backlogged_feeds`, i.e. `feed_backlog_degraded(floor, prebuffer_s, warn_s)`.

**The floor is its own debounce, so one interval is enough.** `take_backlog_floor` returns
the *minimum* backlog seen across the whole heartbeat interval. A transient spike raises
the peak, never the floor, so a degraded floor already means the feed was behind for the
entire 30 s. Requiring a streak of two would only delay the remedy to a full minute of a
visibly late picture. `RACECAST_FEED_BACKLOG_SHED_TICKS` can raise it; the default is 1.

**Only the on-air feed.** Same rule as the freeze path. With fan-out, OBS drops an off-air
feed (`close_when_inactive`), so an idle feed has no consumer and no backlog to shed.
POV is deliberately out: it is a picture-in-picture, the program picture is what this
protects, and giving the PiP its own rebuild reason would put a second actor on the same
control after all.

## Effectiveness accounting

`RebuildGuard.on_window()` judges the previous rebuild by the *freeze* metric. A
backlog-triggered rebuild judged that way would read as "effective" whenever the picture
is not stuttering, even if it is still a minute late.

Worse, `pending` is a single bool consumed by whoever calls first, and the two judges run
on different threads at different cadences. A shed's rebuild would routinely be consumed
and cleared by the sampler's next full cursor window, so the shed's own effectiveness
would never be judged and its three-strike budget would never count down.

So `pending` becomes a **reason tag** (`None`, `"freeze"` or `"backlog"`), the guard's core
becomes `judge(still_bad, reason)`, and each judge only consumes its own:

- freeze fired → the next full cursor window decides
- backlog fired → the next heartbeat's `feed_backlog_degraded` decides

`on_window` stays as the freeze-flavoured wrapper so #582's tests keep their shape.

A judge whose signal is unmeasurable this round consumes nothing, mirroring `on_window`'s
existing `frac is None` rule. That matters more here than for freeze: right after a
rebuild OBS is detached for a stretch of the interval, so `consumer_backlog` answers None
and the floor can be None for a whole heartbeat.

### The A/V detector must not flag our own remedy

`av_sync.record()` calls a repair EXPECTED only when the feed's `serving_age_s` is inside
`RESTART_WINDOW_S`. A shed rebuild splices a new stream into OBS without restarting the
relay feed, so the serving age does not reset and the audio repair that follows would be
classified UNEXPLAINED, turning the panel yellow for something the relay did on purpose.

The relay therefore passes the age since it last **spliced this feed's stream into OBS**,
which is the minimum of the serving age and the age of the last relay-caused rebuild
(`_note_obs_splice`). `av_sync` stays pure and unchanged apart from its docstring, which
described the narrower case. The freeze rebuild has the same latent gap today and is
fixed by the same change.

## What this does NOT do

- **It does not address a backlog that keeps growing.** A host that cannot render in real
  time rebuilds the backlog within a minute, so this would fire, help briefly, fire again
  and stand down after three: the honest warning the epic asked for, not a fix. The fix
  for that cause is #585 (step every feed down to ROBUST), which costs no black at all and
  stays the right remedy for it. This change and #585 are complementary, not alternatives.
- **It does not replace the manual reset.** #587 stays, cost preview included.
- **It does not touch the ring, the prebuffer or the rejoin landing.** If a rebuild right
  after a restart reliably fixes the backlog, that is evidence #630's predicted landing is
  off, and that belongs in #630 rather than here.

## Live result, 2026-09-21 on the producer host

Run: one live YouTube 1080p60 feed, `/reload/A` with an OBS recording active, on
`PC-JeGR-Streaming`.

**The automation behaves exactly as designed.** It fired by itself three times, 60 s
apart (the shared cooldown), and then gave up and said so:

```
08:59:06 WARNING backlog shed A — output 25.3 s behind live — rebuilding OBS input
09:00:09 WARNING backlog shed A — output 26.3 s behind live — rebuilding OBS input
09:01:11 WARNING backlog shed A — output 31.2 s behind live — rebuilding OBS input
09:01:43 WARNING Feed A backlog shed stood down — 3 OBS rebuilds did not bring the
                 output back to the live edge
```

The A/V detector attributed all of it: 9 repairs on Feed A, **0 unexplained**, so
`_note_obs_splice` does keep the relay's own remedy from turning the panel yellow.

**But the remedy does not work in this condition, and the reason is not what the code
said.** The stand-down line originally blamed a host too slow to render in real time.
Measured in that exact state, with the recording running and the output 25-31 s behind:

| | |
|---|---|
| `activeFps` | 60.0000024 |
| `averageFrameRenderTime` | 0.87 ms |
| `renderSkippedFrames` | 13 of 185848 (0.007 %) |
| `outputSkippedFrames` | 9 of 21809 (0.04 %) |
| `cpuUsage` | 2.0 % |

The host is not the bottleneck. The wording is corrected to state the fact and stop
there.

What the samples show instead: 15 s after a rebuild the backlog was already back at
24.7 s. A host losing ground could not build 21 s of backlog in 15 s, so the rebuild is
**landing late** rather than resetting to the prebuffer and then slipping. The same
`/reload/A` with the output IDLE landed cleanly every time (1.4-3.1 s, measured in the
same session an hour earlier). So an active OBS output changes where the rejoin lands —
which is the #614/#630 landing calculation, not this change.

## Resolved: the backlog was a measurement of a dead socket

The section above is kept as written because it shows how the wrong conclusion was
reached. It is superseded by this.

Asked why an automatic reset could not work when the director's manual one does, the
answer turned out to be that **neither did — and neither needed to.**

`Feed._obs_reconnect_now()` calls `release_feed_inputs()`, which is exactly what
`POST /obs/feed-reset` calls. There is no rejoin wait in that path (the prefetch wait
lives in the re-serve hook), so the claim above that the shed inherits #614/#630's
landing was simply wrong. Measured directly: the manual reset reported success, OBS's own
log confirmed it rebuilt the source, and the backlog did not move (26.1 -> 26.3 -> 24.7 ->
27.5 -> 28.0 over a minute).

Then `netstat -ano` on the producer host, one feed port, one OBS media source:

```
TCP 127.0.0.1:53001  127.0.0.1:61220  ESTABLISHED  4120   <- relay
TCP 127.0.0.1:53001  127.0.0.1:61258  ESTABLISHED  4120
TCP 127.0.0.1:61220  127.0.0.1:53001  ESTABLISHED  12616  <- obs64
TCP 127.0.0.1:61258  127.0.0.1:53001  ESTABLISHED  12616
```

**OBS holds two connections for one media source.** On an input rebuild it opens the new
one and never closes the old, and because its process still owns that socket the peer
never resets, so TCP cannot see the abandonment. The relay's handler sat in `sendall`
with a frozen cursor, and `consumer_backlog` takes `max()` over all consumers — so
`/status` reported the abandoned connection's backlog, permanently.

Every loose end of the day follows from that single reading:

| Symptom | Cause |
|---|---|
| the manual reset "does not work" | it works; the number reported was the dead socket's |
| the shed stands down after three tries | it judges itself on that number, which never improves |
| the health reason promises RESET, then shows it failed | same number |
| "an active output stops the backlog recovering" | the recording made the rebuild happen; the artifact did the rest |
| the backlog cleared when the recording stopped | the abandoned socket finally died |

macOS OBS does not do this: on the Mac the same reset leaves one connection and the
backlog goes to 1.9 s. It is a Windows behaviour, i.e. exactly the producer host.

### Fix

Being superseded does not condemn a consumer — this port is built to serve several at
once and a test pins that. What identifies the abandoned one is **superseded AND not
having accepted a byte since**, past a grace. Such a consumer is excluded from
`consumer_backlog` and `take_backlog_floor` at once, and its socket is closed on the next
heartbeat. Which call does the waking is per platform, measured with
`tools/shutdown-wake-probe.py`: on macOS `shutdown` alone wakes a handler blocked in
`sendall`, on Windows only `close` does. The reaper therefore always shuts down and
closes only where that is not enough (`CLOSE_TO_WAKE`).

### Verified live, same host, same recording, same `/reload/A`

| | before | after |
|---|---|---|
| double connection | 4 netstat lines | 4 (unchanged — it is OBS's doing) |
| reported backlog | **26 s, permanently** | **1.1-2.8 s** |
| abandoned socket | never released | reaped after ~40 s (4 lines -> 3) |

With the shed re-enabled, a rebuild now produces `backlogged=False`, the guard stays
armed, and **no shed fires at all** — the automation no longer triggers on a phantom.
The relay logs `feed A: closed 1 abandoned consumer connection(s) OBS left open after an
input rebuild`.

**The program picture was at the live edge the whole time.** Nothing was ever behind.

## Measured against a real OBS backlog

`tools/obs-backlog-shed-probe.py` puts the running OBS behind the live edge by
suspending the process: it keeps the feed socket open and stops draining it, the
relay's `sendall` blocks and the accepted position freezes. On the Windows producer
host, three runs against a live YouTube source, 100 s suspended each:

| | measured |
|---|---|
| backlog while suspended | 0.4 s -> 102.3 s, 1:1 with the clock |
| classification | `backlogged=True` on the real backlog |
| shed fires | yes, logging the real number (47.2 / 56.2 / 46.1 s behind live) |
| rebuild against a suspended OBS | a no-op, no crash, no stand-down |
| shed's rebuild landing | 46.1 s -> 3.7 s within 2 s |
| stand-down | never, across all three runs |

**And the finding that matters more than the proof: OBS recovers on its own.** Once it
runs again it reads the socket greedily rather than at playback rate and sprints back
to the trailing mark — 96.6 s -> 1.8 s in about ten seconds, on one unchanged socket
(macOS behaves the same). In every run the picture would have returned without the
shed. A transient OBS stall is therefore not what this automation is for.

What it is for is a consumer that stays **below** real time, and no healthy host produces
one. An apparent slow drift on macOS was the source's own sawtooth, named by
`inbound_max_gap_s` at 5.1 s and not a slow consumer at all.

## The case it is for, produced and measured

`jegr-linux-cachyos` is the host the field reports came from, kept as an analysis machine.
Running OBS inside a transient scope with `CPUQuota=60%` starves it the way a broadcast
does, and unlike suspending the process it stays responsive to obs-websocket throughout:

| | measured |
|---|---|
| OBS under load | **10 fps instead of 60**, 100 ms frame render instead of 1.4 ms |
| backlog | climbs **monotonically**, 2.9 s to 38.8 s over 45 s, no bursts |
| shed fires | 14:54:57, at 21.5 s behind live |
| OBS rebuilds | `[Media Source 'Feed A']: settings:` at 14:54:57.634, in OBS's own log |
| the consumer socket | peer port **58904 to 50380** at 14:54:58: replaced, not resumed |
| backlog after | **4.0 s**, and it holds |

The peer port is the discriminator and the reason to trust this one. Correlating a drop
with a log line is not enough: in an earlier run on this host the drop began three seconds
**before** the shed fired, which is OBS bursting, and a shed landing mid-burst is credited
for it. Here the climb is monotonic to the last sample before the rebuild, OBS logs the
rebuild, and the socket is a different one afterwards.

### What it costs on such a host

The backlog regrows at the same rate after every shed. Counted over one 11-minute run on
this host: **nine sheds, one every 75 seconds, every one effective, no stand-down.** Each
rebuild is a visible cut. `RebuildGuard` does not stop this and should not: it judges each
rebuild on its own, and each one **does** return the output to the reserve. The comparable
field number is Catalunya's 26 rebuilds in 56 minutes, one every 129 seconds.

So the automation keeps the picture near the live edge on a host that cannot keep up, and
it pays for that in dropouts.

### A run of effective sheds must NEVER stand the automation down

This is a requirement, not a tuning knob. A host that needs a shed every 75 seconds cannot
carry a production at all, and capping the remedy there would punish the automation for
the host's fault while taking away the only thing keeping the picture near live. The
budget is spent by **ineffective** rebuilds, which mean the remedy is not working; an
effective one resets the streak however often it is needed. Pinned by
`t_a_long_run_of_effective_sheds_never_stands_the_automation_down`.

The answer to a host at 10 fps is #585 and different hardware, never a quieter relay.
