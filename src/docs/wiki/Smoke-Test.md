# Smoke test: verify the event core after a toolchain update

`racecast smoketest` answers one question: **did a toolchain update break the
broadcast?** It runs a real, throwaway event against live streams it finds
itself, drives the director rundown against it, checks every step, and tears the
event down again.

Run it after `pacman -Syu` / `brew upgrade` / `winget upgrade` moved **ffmpeg**,
**yt-dlp**, **streamlink** or **deno**, and never on an event day.

```bash
racecast smoketest                 # ~8 minutes end to end
racecast smoketest --minutes 15    # longer observation window
racecast smoketest --json          # machine-readable, for a scripted check
```

## What it actually proves

The relay pulls streams through four external programs, and each one is a place
an update can break the broadcast:

| Program | What it does on air | How the smoke test exercises it |
|---|---|---|
| `yt-dlp` | resolves a YouTube live stream to an HLS manifest | resolves each candidate in the relay's exact command form |
| `deno` | solves YouTube's JS challenge for yt-dlp | recorded in the fingerprint; a failure surfaces as a failed resolve |
| `streamlink` | serves YouTube HLS and pulls Twitch directly | serves both feeds for the whole run |
| `ffmpeg` | encodes the on-air program audio to MP3 | a real read from `/preview/program-audio` |

This is deliberately **not** what `tools/e2e.py` does. The e2e harness stubs all
four programs out and drives fake URLs, because it has to run in CI on a machine
that has none of them. It proves the relay's HTTP surface. It cannot prove that
your installed toolchain still works.

## What a run does, in order

1. **Refuses** if a relay or static streams are running (`--force` overrides).
   The run pulls real streams; a rate-limit hit mid-broadcast is the most
   expensive possible way to learn that a test was badly timed.
2. Asks you to type **`CLEAR SCHEDULE <profile>`**. There is no `--yes`. The
   phrase names the profile because the accident worth guarding against is the
   right command run against the wrong league, the command **clears the URL
   column** of the Schedule tab.
3. Records a **toolchain fingerprint**: the four version strings, plus yt-dlp's
   own JS-runtime line read off one of the streams discovery just proved live.
   Not a check, a note: when a later run goes red, this is what you diff
   against the last green one.
4. **Discovers three live sources**: two on YouTube (live-filtered search) and
   one on Twitch (category listing). Each candidate must resolve at 720p or
   better and be a sim-racing stream before it is used, and at most three
   candidates per platform are probed.
5. **Writes the URL column** of the Schedule tab's first three stint rows.
   The rows are located the way the relay locates them, never assumed: a `URL`
   header means the data starts at physical row 2, and only rows the relay would
   count as a stint are eligible: a blank spacer is skipped, and without a
   `URL` header only rows that already hold a stream. Streamer and Stint are
   left untouched, so a foreign stream shows up under your usual names.

   > **What it costs you.** The URL cells are **cleared and not restored**,
   > four rows are emptied while three are rewritten, so the fourth row's URL is
   > gone for good. The run prints the previous values and records them in
   > `smoketest-history.jsonl`; that file is the only copy. Use a dedicated
   > testing league, never the one you are about to broadcast.
   >
   > A tab **without** a `URL` header whose streams are not in column A is
   > refused before anything is written: the Sheet webhook can only address
   > column A there, so writing would blank whatever else lives in it.
6. Starts a normal event titled `Smoketest <date>`. Tailscale, Discord, relay,
   OBS, Companion, scene collection, Standby, page refresh. Nothing is skipped.
7. Runs the **director rundown** and reads OBS back after every step:

   ```
   STANDBY -> INTRO -> ARM A -> STINT A -> HUD on -> STANDINGS on/off -> FLAG YELLOW -> CLEAR
     -> TIMER START/PAUSE/RESET
     -> ARM B -> SPLIT   [Feed A on air]
     -> NEXT (handover)
     -> STINT B -> ARM A -> SPLIT   [Feed B on air]
     -> INTERVIEW -> OUTRO -> STANDBY
   ```

8. **Observes** for the window (5 minutes by default), then stops the event,
   including the post-event report and its Discord message.

## Reading the result

```
Checks
  [PASS] step13_split: …
  [FAIL] step17_split. Feed B expected live, measured True
  [WARN] companion: not reachable

Summary: 1 FAIL, 1 WARN, 0 SKIP, 24 PASS  ->  FAIL
History: runtime/testing/smoketest-history.jsonl
```

- **FAIL** is reserved for what the toolchain can break: a feed that never
  delivers, no resolution, no MP3, a step whose effect did not land. Exit code 1.
- **WARN** covers the environment. OBS not on Standby, Companion unreachable.
  A headless session can cause those and they say nothing about ffmpeg. Exit 0.
- **SKIP** means the run could not prove anything, almost always "nothing
  suitable was streaming". Exit 0 on purpose: a red light for *nobody is racing
  right now* teaches you to ignore red lights.

Every run appends one line to `runtime/<profile>/smoketest-history.jsonl`, the
same shape as the speedtest history. Comparing a red run to the last green one is
`tail -2` on that file, not a memory exercise.

## Why SPLIT runs twice

The rundown fires **SPLIT on both sides of the handover**: once with Feed A on
air, once with Feed B. That is not symmetry for its own sake: the SPLIT macro
once muted the on-air commentator on an even→odd handover, live, during an
eight-hour race. The audio is resolved server-side now, and reading the mute
state back after SPLIT at both parities is what keeps it that way.

## Which profile to run it against

Any profile with a `SHEET_PUSH_URL`, but pick a **dedicated testing league**,
because the command clears that league's schedule URLs. A testing profile wants
its own Sheet, its own Discord webhook pointed at a private server, and a
Configuration tab with real stints and streamers so the HUD has something to
show.

## Optional: your own search vocabulary

The built-in queries and Twitch categories work out of the box. To steer them
without waiting for a release, add a **`Smoke`** tab to the league sheet:

| Platform | Query |
|---|---|
| YouTube | `le mans ultimate live` |
| YouTube | `gt7 endurance` |
| Twitch | `Le Mans Ultimate` |
| Twitch | `Gran Turismo 7` |

A missing tab is normal; the defaults are used silently.

## Running it without changing your installed version

To test a build newer than the one on your broadcast machine **without** moving
that machine off its release, install the preview build **beside** it instead of
over it:

```bash
mkdir ~/racecast-smoke && cd ~/racecast-smoke
# unpack the preview racecast + racecast-ui here, then:
cp -r ~/racecast/profiles/<league> profiles/<league>
mkdir -p runtime/<league>
cp ~/racecast/runtime/yt-cookies.txt runtime/
cp -r ~/racecast/runtime/<league>/graphics runtime/<league>/
```

A racecast binary uses its **own directory** as its home (there is no path
override), which is exactly what keeps the two installs apart. Graphics are a
hard precondition of the event gate, so they have to be there; media is only a
warning. OBS needs nothing new: the event start switches to the already-imported
scene collection by name.

Both installs can never run at once: the relay binds port 8088 as a machine
singleton, and the smoke test refuses to start while a relay is up.

## What the run leaves behind

The discovered URLs stay in the Schedule tab: clearing them again is a manual
step, by design, so you can look at what ran. Everything else is put back: the
relay and Companion are stopped, and a Tailscale Funnel that **this run** opened
is closed again (one that was already open before is left alone). A failing
teardown is a `FAIL` check, not a footnote, so a run can never report `PASS`
while the relay is still pulling.

Each run also posts the usual post-event report to the league's Discord, with
the session logs attached. Pass `--no-report` to keep smoke runs off that
channel.

## See also

- [Run an event](Run-an-event): the real thing this rehearses
- [Relay, how the feeds work](Relay-Mode), the pull pipeline being tested
- [Arch Linux (CachyOS)](Arch-Linux), where toolchain updates arrive in bulk
- [Build & maintenance](Build-and-maintenance), the e2e harness and the CI gates
