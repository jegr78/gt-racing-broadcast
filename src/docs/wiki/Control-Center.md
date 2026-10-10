# The Control Center

The **Control Center** is a local web app that runs the whole producer station,
first-time setup, event-day bring-up, service control, logs: from your browser,
no terminal needed. It is the recommended way to drive the station. Everything it
does is also available as a `racecast …` command (shown as a *CLI alternative*
throughout the operator guides), so the terminal stays a first-class option for
Linux, scripting, and remote sessions.

It is shipped in the same release archive as the CLI: alongside the `racecast` binary
you get **`racecast-ui`**, the Control Center launcher.

## Launch it

| Platform | How |
|---|---|
| **Windows** | Double-click **`racecast-ui.exe`**. |
| **macOS** | Double-click **`racecast-ui.app`**. |
| **Linux** | Run `./racecast-ui` (or `./racecast ui`): most Linux desktops don't launch a plain binary on double-click. |

It opens your default browser at `http://127.0.0.1:8089/`. Keep `racecast-ui` in the
same folder as `racecast`, your `.env` and `profiles/`, the two binaries sit side by
side and share the `runtime/` folder next to them.

- **Already running?** Launching again just reopens the browser: there is only
  ever one Control Center per machine.
- **Closing the tab** leaves it running in the background. Use the **Quit**
  button (bottom-left) to stop the server. Stopping the Control Center does *not*
  stop the relay, Companion, or streams: those are independent and keep running.
- **Port busy?** Set `RACECAST_UI_PORT` in `.env` to a free port and relaunch.

> **CLI alternative:** `racecast ui` runs the same server in a terminal (add
> `--no-browser` to skip opening a tab). `racecast ui` and `racecast-ui` are interchangeable.

## Security: keep it local

The Control Center listens on `127.0.0.1` only (this machine). Its API is
unauthenticated and can start installs and stop services, so it is deliberately
**not** reachable from the LAN or the tailnet. Don't put it behind a proxy or
forward its port. (The director's panel and Companion Web Buttons *are* reached over
Tailscale: those are separate, see [Director setup](Director-Setup).)

## A tour of the views

The left sidebar holds every view. The docked **Console** at the bottom streams
the live output of any action you run.

### Home

![Control Center: Home dashboard](images/cc-home.png)

The dashboard. At a glance: how many core services are up, the state of Tailscale,
Discord, OBS, the relay, Companion and the static streams, the race-timer state,
and tiles for Preflight / Assets / Cookies readiness. **Start event** brings the
station up (optional stint number for a mid-event takeover); **Stop event** winds
the `racecast` services down. The sidebar also shows the **active league profile**
(click it to jump to the Profile view). From here you also open the Director panel
and copy the director / Web Buttons links.

#### System

A live machine-resource card shows the producer machine's **CPU %**, **RAM** (used /
total and a percentage), **Network ↑/↓**, and **Disk free**. Readings are color-coded:
CPU turns yellow above 75 % and red at 90 % or above; RAM turns yellow above 80 % and
red at 92 % or above; Disk free turns yellow below 5 GB and red below 2 GB. Network
throughput is informational and carries no color. The card updates whenever the Control
Center is open, whether or not the relay is running, the machine-resource history charts
in the [Health Monitor](Health-Monitor), by contrast, are recorded by the relay.

> **CLI alternative:** `racecast status`, `racecast event start [--stint N]`, `racecast event stop`.

### Profile

![Control Center: Profile](images/cc-profile.png)

Everything that belongs to a **league**, gathered in one view (the model behind it is in
[League profiles](Profiles)):

- **Active profile**, a switcher to change the active league (every other view then
  acts on it), and a **New profile** dialog. Pick the **Kind**: an *Endurance* profile
  copies an existing one (e.g. `example`) into a new league; a *Solo* profile (single-race
  commentary or driver POV: local capture + webcam, no feeds) generates a fresh profile
  from a **Template** (Commentary or POV) instead of copying. Selecting *Solo* swaps the
  *Copy from* control for the *Template* picker.
- **`profile.env` editor**, the active league's config (Sheet ID, push URL,
  intro/outro, logo, and the OBS scene-collection name `OBS_COLLECTION`). Secret values
  are **masked**: click the eye to reveal one. **Open Sheet ↗** opens this league's Google
  Sheet (built from `SHEET_ID`) in a new tab, no need to keep the raw link around. Changes
  apply the next time you (re)start the relay.
- **Overlay CSS**: per-profile CSS for the relay-served **HUD** and **Timer** pages
  (`profiles/<active>/overlay/`; see [HUD overlays](HUD-Overlays)). **Save** writes the file;
  **Apply in OBS** reloads the browser sources (same as `obs refresh`). The first override on
  a profile that had no `overlay/` yet needs one `racecast relay restart` to activate; later
  edits apply live.
- **Crew editor**: reads the league Sheet's `Crew` tab
  (`Name | Commentator | Director | Producer | Race Control | Discord`) via the relay and
  lets the producer set the per-person role flags; changes are written back to the Sheet
  via the `crew` webhook action. **Race Control** flags a person for the read-only
  [Race Control](Console) monitoring desk. The Crew tab and the redeployed Apps Script
  `crew` action are a league Sheet-side coordination item (see [Sheet-Webhook](Sheet-Webhook));
  without them roles degrade gracefully.

![Control Center. Crew editor: per-person Commentator/Director/Producer/Race Control roster for /console access](images/cc-crew-editor.png)

- **Assets**, the active profile's broadcast graphics and intro/outro media. Thumbnails
  show which graphics are present; **Download** fetches them from the Sheet's Assets tab;
  **Check vs sheet** compares what's on disk against what the Sheet lists.

> **CLI alternative:** `racecast profile list|show|use|new`, `racecast graphics`,
> `racecast media`, `racecast sheet open` (or `sheet url`). Edit
> `profiles/<name>/profile.env` and `profiles/<name>/overlay/{hud,timer}.css` in any
> text editor.

### Setup

![Control Center: Setup wizard](images/cc-setup.png)

The first-time-setup **wizard**. It detects what is already done and marks each
step `done` or `pending`, with a running "*X of N complete*" summary. Run the
pending steps in order: creating/selecting a league profile, installs, YouTube
cookies, broadcast graphics, intro/outro media, the OBS scene collection, the
Companion config: each streams its
output in the Console. **Re-check all** re-reads the state (e.g. after you fill in
the active profile's `SHEET_ID`). The closing **Manual next steps** list covers the
imports no script can do (importing the OBS collection and Companion config, signing in
to Tailscale).

> **CLI alternative:** `racecast init` runs the same steps in the terminal.

### Preflight

![Control Center: Preflight checks](images/cc-preflight.png)

A read-only hardware and tooling check: RAM, CPU, disk, swap; the four command-
line tools (`yt-dlp`, `streamlink`, `ffmpeg`, `deno`) with versions; and the four
apps (OBS, Companion, Tailscale, Discord). Each line is `PASS` / `WARN` / `INFO` /
`FAIL`. **Run** re-checks.

> **CLI alternative:** `racecast preflight`.

### Relay & Static Streams

![Control Center: Relay](images/cc-relay.png)

**Relay** starts / stops / restarts the commentator-feed relay (the normal mode)
and shows its live status. It reads its schedule and HUD data from the **active
profile's** Google Sheet, and applies that profile's overlay CSS; there are no local
relay knobs: ports and bind mode use safe defaults.

![Control Center: Static Streams](images/cc-streams.png)

**Static Streams** is the **fallback** for plain public channels when the relay
mode can't be used. Edit the channel/port list here and start/stop the set.

> **CLI alternative:** `racecast relay start|stop|restart|status|logs`,
> `racecast streams start|stop`.

### Apps & Tools

![Control Center: Apps](images/cc-apps.png)

**Apps** installs, launches, quits and shows the status of OBS, Companion,
Tailscale and Discord. **Tools** does the same for the command-line tools
(`yt-dlp`, `streamlink`, `ffmpeg`, `deno`), with **Install all** / **Update all**.

![Control Center: Tools](images/cc-tools.png)

> **CLI alternative:** `racecast install-apps`, `racecast install-tools` (add `--update` to
> upgrade), `racecast app launch|quit obs|discord|tailscale`.

### General Settings

![Control Center: General Settings (.env)](images/cc-settings.png)

Machine-wide (not league) configuration:

- A safe editor for the local `.env` (this machine only. OBS-WebSocket password,
  Control Center port, the Windows Companion path). Secret values are **masked**: click
  the eye to reveal one. Comments in the file are preserved. Changes apply the next time
  you (re)start the affected service.
- **YouTube cookies**: freshness status, re-exported with **Refresh** (pick the browser).
- **Devices**: **Reload** lists the video and audio devices OBS sees; pick the **capture
  card** (what a [local capture stint](Relay-Mode#local-capture-stint) puts on air) and the
  **commentary mic**, then **Save** (writes `RACECAST_CAPTURE`, `RACECAST_MIC` and
  `RACECAST_MIC_NAME`). A solo profile adds the webcam; a solo commentary profile also
  the tyres/fuel capture, a solo POV profile the GT7 PlayStation IP.
- **GT7 data** shows when the car and track data were last checked and how many cars and
  layouts it knows. **Update now** fetches the latest copy in the background (the row says
  "updating…" until it ends, a failed file opens a message); a running relay uses it
  within a minute.

> **CLI alternative:** edit `.env` in any text editor; `racecast cookies <browser>`;
> `racecast device-scan`.

### Crew Console

![Control Center. Crew Console: per-person /console links and the public-access (Funnel) switch](images/cc-crew-console.png)

Where you hand out crew access. The relay serves a **role-adaptive `/console`** launcher;
crew **sign in with Discord** (the standard) or via a per-person link (the fallback), and
land on the surface their roles allow (commentator cockpit, director panel, Race Control
desk). The per-league secret behind it is generated automatically on the first relay start,
so this view is **zero-config**.

- **Public access (Tailscale Funnel)**: `/console` is reachable over the tailnet by default;
  flip the **Funnel ON** to also expose **only** `/console` publicly (so crew off the tailnet
  can open their link). **Auto-enable on event start** turns it on with every event, and is
  **checked by default** (opt-out), since the Funnel is the preferred produce path; uncheck it
  to keep a machine tailnet/loopback-only. **Copy Link** / **Post to Discord** distribute the
  shared launcher link in one click.
- **Crew links**, one row per person (the Crew tab ∪ the live schedule), for the per-person
  fallback (leagues without Discord login). **Copy funnel link** copies their public HTTPS
  link, **Copy internal link** the tailnet/loopback one, and **Revoke** rotates a single
  person's link (bumps their version), which also invalidates their Discord session, without
  touching anyone else.

Roles per person come from the [Crew editor](#profile) (and the Schedule). Start the relay
first: links and the secret only exist once it is running.

> **CLI alternative:** `racecast links` (add `--post` to drop them into crew chat),
> `racecast funnel on|off`, `racecast console token revoke <streamer>`. See [Console](Console)
> and [Commentator Cockpit](Commentator-Cockpit) for what the crew sees.

### Logs

![Control Center: Logs](images/cc-logs.png)

Live tail of the relay, Companion and static-stream logs: pick a source from the
dropdown. The example above shows a healthy relay startup: the schedule loaded from
the Google Sheet and both feeds bound, with the HUD and Director-panel URLs listed.

> **CLI alternative:** `racecast relay logs -f` (and `companion` / `streams`).

### Post-Event Report

![Control Center: Post-Event Report](images/cc-report.png)

A summary of the last broadcast session: commentators per stint, Feed A / Feed B
activity, incidents and quality metrics: generated from the relay's health history.
The artifact is a **self-contained HTML file** (all assets inline) that opens in any
browser without a server.

Click **Generate** to build the report. It runs `racecast report` as a background job;
for a solo POV profile that also indexes the GT7 recordings in the report window, about
16 s per 3 h of recording not yet indexed. The preview panel then shows the report.
**Download .html** saves the file to your machine; **Send to Discord** posts the HTML
file as an attachment to the league's Discord webhook channel. Discord shows it as a
downloadable attachment that recipients open in a browser.

> **CLI alternative:** `racecast report` (generate into `runtime/<profile>/reports/`),
> `racecast report send [FILE]` (send the newest or a given file to Discord).

### Telemetry

![Control Center: Telemetry lap comparison](images/cc-telemetry.png)

![Telemetry context editor](images/cc-context.png)

Solo POV profiles only. The view reads the profile's GT7 telemetry recordings
(`runtime/<profile>/telemetry-recordings/`, see [Relay mode](Relay-Mode)) and needs no
running relay.

- **Summary.** Above the comparison, the figures of the post-event report for the picked
  recording, one block per track and car: best lap, theoretical best, consistency
  (standard deviation of the counted lap times), fuel per lap, counted laps, the average
  tyre temperatures and the lap-time trend in driving order (the best lap green, laps that
  were not counted grey). Click a dot in the trend to make that lap lap B.
- **Recordings and laps.** Pick a recording, newest first, to list its laps with time.
  `ref` marks a lap that was the fastest so far when it was driven (the live HUD delta ran
  against it), `not counted` a rejected lap with the reason below it. The fastest counted
  lap is purple, lap A carries an orange bar and lap B a blue one. A lap names its car or
  track only when it differs from the rest of the recording. The first
  open shows "Indexing…" while the view runs the index job, the same as `racecast
  telemetry index`: it replays the recording once and caches the result next to it as
  `<stem>.laps.json`. Later opens are instant until the recording or the GT7 track data
  changes. A recording the job cannot read shows the job's reason instead of its laps.
  The recording the relay is still writing shows as `(recording)` and cannot be picked:
  it stays out of every comparison, and the view analyses it only after the recording
  stops.
- **Delete a recording.** **Delete** on the Recordings card removes the selected
  recording, its context and context history, CSV export and lap index after a confirmation, the same as
  `racecast telemetry delete <name>`. It cannot be undone. The recording the relay is
  still writing cannot be deleted: stop the recording first.
- **Context & notes.** Add race settings, per-session confirmations, stints, fuel maps,
  intentional shortshifting and notes before, during or after capture. The context editor
  can select an active recording even while its lap analysis is unavailable. Recording
  notes apply to the whole file; session and lap notes keep their own scope. Hover a chart
  before opening the editor to attach that position to a note for the selected lap.
  Valid edits save automatically; unfinished numeric inputs remain drafts. Concurrent
  edits report a conflict and retain your local draft. Earlier whole-context revisions
  can be restored as a new revision. Measurements and raw capture bytes remain unchanged.
- **Prepare next recording and templates.** Preparation attaches once to the next capture.
  Templates copy settings into the selected session as an unconfirmed proposal; changing
  a template does not change existing recordings. Copying another session also requires
  fresh confirmation and does not carry over its stint boundaries. Missing values remain
  unknown. Templates and preparation belong to the profile.
- **Stint service and comparison.** Enter the lap and optional recording time at which
  service occurred. Temperature resets, refuelling and standstill produce proposed times,
  never confirmed tyre compounds. A pit lap can contain both RM and RH. Each confirmed
  tyre change has a configurable number of complete warmup laps (including zero).
  Each lap shows its measured starting fuel load and the number of lap boundaries since
  the declared initial stint or last confirmed tyre change. This tyre-age proxy includes
  a partial service lap and does not establish prior wear at capture start or a wear percentage.
  It does not assume equal conditions. Default
  comparisons exclude contextual first, pit and warmup laps, mixed known fuel strategies within a lap, different confirmed
  compounds and known differences in race settings or fuel strategy. **Compare other
  conditions / roles** explicitly broadens the pool; numerical quality checks still apply.
  Unknown context remains inspectable and is labelled unconfirmed. Reports group known
  compounds, conditions and strategies separately.
- **Capture and pace.** Every recorded segment remains selectable, including the
  partial segment before the first lap and the unfinished segment after the last lap.
  An unfinished segment shows captured seconds, not an invented completed lap time.
  Capture completeness, distance-trace coverage and pace eligibility are separate.
  Both `reference` and `counted` admit a regular lap; **best so far** marks a historical
  live reference, not a stronger data-quality verdict. Pit laps show all detected
  reasons. **After inferred service; tyres unconfirmed** identifies the next lap without
  claiming a confirmed tyre change or warmup period.
- **Lap clock.** Complete pace traces include the closing boundary sample. Their entire
  receiver-clock interval is normalized to the matched GT7 lap duration, beginning at
  zero and ending at that duration. This prevents a backwards final sector when receiver
  timing differs from GT7. Raw packet times and GT7 lap times are preserved; this is a
  reconciled comparison clock, not a claim that every network arrival was a simulation tick.
- **Lap A and lap B.** Lap B is the lap you click. Lap A starts as the fastest counted
  lap other than B with the same track and car across all recordings of the profile.
  Both pickers list every comparable counted lap; B also shows the clicked lap when it
  is not counted. When B has no such partner, lap A
  reads "no other lap to compare" and the charts show B alone. Laps on an unknown track
  compare only within their GT7 session. When other recordings have no cached laps yet,
  for example after a Set track, the view runs the index job first ("Indexing
  N recordings…"). No click in the view replays a recording itself: loading a lap or
  setting a track on a recording without cached laps also waits for that job.
- **Charts.** Speed, throttle, brake, steering, gear and the delta of B against A over
  lap distance, each with its value range on the left. Below zero B is ahead, above zero
  behind; the delta panel ends with the final gap. Hover to read both laps at one point;
  the map shows where that point is.
- **Track map and mini-sectors.** Lap A is the thin line; lap B is split into 200 m
  mini-sectors, green where B is faster and red where it is slower (the legend under the
  map). The table lists every mini-sector with its distance, the times of A and B, the gap
  and the best time of any comparable lap (purple where B set it); its last row
  adds the theoretical best, the sum of the best mini-sectors. When the comparable laps
  end at different lengths, as on an unknown track, the last mini-sector's best and the
  theoretical best show `—`, the same rule as the post-event report.
- **Set track.** When racecast does not recognise the layout of a recording, or two
  layouts fit, pick it from the list. racecast assigns the recording to that layout. For
  a layout with a downloaded racing line, or the reverse of one, that is all. For any
  other layout racecast also learns the line from the recording's longest counted lap,
  so later recordings on it are recognised. A downloaded racing line always takes
  precedence over a learned one. The note under the button says which of the two
  happened.

The racing lines are not part of the package. Until racecast has downloaded them
(`racecast gt7-data update`, or the first relay start with telemetry), it recognises
only layouts learned with Set track, and every other recording starts as track unknown.

> **CLI alternative:** `racecast telemetry export <name>` writes the same laps as CSV with a current `context.json` snapshot (excluding drafts and revision history);
> `racecast telemetry index` builds the missing lap caches ahead of time.

### Help & Docs

![Control Center: Help & Docs](images/cc-help.png)

**Start here** opens the visual [onboarding decks](https://jegr78.github.io/gt-racing-broadcast/),
one short walkthrough per role plus the printable cheat sheet, all in one central place.
**On this machine** lists the bundled setup guides (rendered offline, no internet needed),
and the guide links open the always-current pages on this wiki.

## Where to go next

- **Setting up a machine?** → [Set up the broadcast PC](Set-up-the-broadcast-PC)
- **Running a show today?** → [Run an event](Run-an-event)
- **The remote director?** → [Director setup](Director-Setup), then the
  [Director guide](Director)

---

> This page is generated from `src/docs/wiki/` in the
> [main repository](https://github.com/jegr78/gt-racing-broadcast): don't edit it
> here by hand. See [Build & maintenance](Build-and-maintenance).
