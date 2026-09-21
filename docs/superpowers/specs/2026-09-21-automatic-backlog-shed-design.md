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

## Open

- **Why the rejoin lands late under an active output.** That is now the blocking
  question for this feature: the shed calls the same `f._obs_reconnect()` primitive, so
  until the landing is right the shed cannot work no matter how it is triggered. It
  belongs to #630.
- Whether the post-restart backlog step is the same phenomenon as a growing one is still
  unmeasured. Both trip the same threshold; the run above produced the step, never a
  growing one.
- Note that this run **supports** #581's original reasoning in one respect: a rebuild did
  not buy the picture back, which is what the epic predicted when it said the backlog must
  never be allowed to build in the first place. The override stands (the guard makes the
  attempt cheap and bounded), but #585 looks more load-bearing than it did this morning.
