# Sheet-Template: the Google Sheet that drives a league

Every league is driven by **one Google Sheet**. The relay reads it over the public
CSV export (no API key, no login): you only need the long `SHEET_ID` from the URL
in your profile's `profile.env` (see [League profiles](Profiles)). This page is the
**read contract**: every tab, its columns, and a sample row. The **write** path (the
optional Apps Script that lets the Director Panel and race timer write back) is the
separate [Sheet-Webhook](Sheet-Webhook) page.

> **Just want to try it?** The shipped `demo` profile already points at a public,
> read-only demo Sheet built to this spec: `racecast profile use demo` and you are
> running. To build your own, copy that Sheet (**File → Make a copy**) or recreate the
> tabs below, then put your copy's ID in `profile.env`.

CSV stubs for every tab live next to this page under
[`src/docs/sheet-template/`](https://github.com/jegr78/gt-racing-broadcast/tree/main/src/docs/sheet-template),
import them as a starting point.

## How the relay reads it

Each tab is fetched as CSV from:

```
https://docs.google.com/spreadsheets/d/<SHEET_ID>/gviz/tq?tqx=out:csv&sheet=<TabName>
```

The **gviz CSV export maps physical sheet rows 1:1**, so a tab must **start at row 1**
with **no leading blank rows**. Most tabs locate their columns **by header text**
(case-insensitive), so you may reorder columns; the exceptions are called out below.

| Tab | Default name | CLI override | Read by | Purpose |
|---|---|---|---|---|
| [Overlay](#overlay-tab) | `Overlay` | `--overlay-tab` | relay (`/hud`) | Live HUD values (stint, streamer, teams, race control) |
| [Configuration](#configuration-tab) | `Configuration` | `--config-tab` | relay | Team roster, brand/flag keys, panel dropdown vocabulary, cue presets |
| [Schedule](#schedule-tab) | `Schedule` | `--sheet-tab` | relay | One row per stint: stream URL + commentator |
| [Qualifying](#qualifying-tab) | `Qualifying` | `--qualifying-tab` | relay | Same shape as Schedule, used in qualifying mode |
| [POV](#pov-tab) | `POV` | `--pov-tab` | relay | The optional driver picture-in-picture stream |
| [Setup](#setup-tab) | `Setup` | *(fixed)* | webhook | Director-Panel write target (mirrored read-only into Overlay) |
| [Timer](#timer-tab) | `Timer` | `--timer-tab` | relay | Race-countdown state (see [Race Timer](Race-Timer)) |
| [Crew](#crew-tab) | `Crew` | `--crew-tab` | relay | Director / producer / commentator / race-control roster |
| [Producer](#producer-tab) | `Producer` | — | Control Center | Producer-handover schedule with one-click Funnel takeover |
| [Assets](#assets-tab) | `Assets` | `--assets-tab` | `racecast graphics` / `media` | Links to the broadcast graphics + intro/outro/trailer clips |
| [Brands](#brands-tab) | `Brands` | — | `racecast brands` | Per-league brand-logo overrides for the HUD (optional) |
| [Event Notes](#event-notes-tab) | `Event Notes` | `--event-notes-tab` | relay | League-owner notes shown as a modal in the console pages (optional) |

---

## Overlay tab

The live lower-third values. Read **by label**: column **A** holds the label, and the
value is the **first non-empty cell from column C onward** (column B is a spacer). In a
league with the write webhook the Director Panel writes the Setup tab and the Overlay
tab mirrors it with a formula: but the relay only ever **reads** Overlay.

| Label (col A) | Meaning |
|---|---|
| `Stint` | Current stint number/label shown on the HUD |
| `Streamer` | Current commentator name |
| `Session` | Session label (e.g. `Race: Hour 3`) |
| `Round Top` | Event / round title (top line) |
| `Round Bottom` | Country name (bottom line; normalised to look up the flag) |
| `Race Control` | Text for the race-control banner |
| `Flag` | Current race-condition flag (Green / Yellow / Safety Car / FCY / Red / …); blank hides it |
| `Teams P1` / `Teams P2` / `Teams P3` | The three podium-slot team names |

Sample:

```
              | (B) |
Stint         |     | 5
Streamer      |     | Sample Commentator
Session       |     | Race: Hour 3
Round Top     |     | Demo Series: Round 1
Round Bottom  |     | Germany
Race Control  |     | GREEN
Flag          |     | Green Flag
Teams P1      |     | Sample Team Alpha #11
Teams P2      |     | Sample Team Bravo #22
Teams P3      |     | Sample Team Charlie #33
```

The team name and country resolve to a bundled logo/flag by a normalised key
(lowercase, spaces → `-`): `Germany` → `germany.png`, `Sample Team Alpha` →
`sample-team-alpha`: matched against the team's **brand key** (next tab) and the
bundled `flags/`/`brands/` images. See [HUD overlays](HUD-Overlays).

---

## Configuration tab

A header row (row 1) plus one row per team. Columns are located **by header text**
(case-insensitive); extra columns are ignored.

| Column header | Required? | Meaning |
|---|---|---|
| `Teams` *(or `Team Name`)* | yes | Team label; a trailing `#NNN` is stripped to a car number |
| `Number` | optional | Car number (wins over an embedded `#NNN`) |
| `Brand Key` *(or `Brand Name` / `Brand`)* | optional | Manufacturer key → brand logo on the HUD; its text is also the team's **Brand Name** HUD element unless overridden below |
| `Brand Name Override` | optional | Text shown as the team's **Brand Name** HUD element instead of the `Brand Key`/`Brand Name`/`Brand` value. Does **not** change which brand **logo** is used. Leave blank to show the brand text verbatim |
| `Stints` | optional | Dropdown options for the panel's **Stint** field |
| `Streamers` | optional | Dropdown options for the panel's **Streamer** field |
| `Session` | optional | Dropdown options for the panel's **Session** field |
| `Race Control` | optional | Dropdown options for the panel's **Race Control** field |
| `Flag` | optional | Dropdown options for the panel/Companion **race-condition flag** (Green/Yellow/Safety Car/Full Course Yellow/Red/…). Shown color-coded in the HUD; hidden when unset. Distinct from the country flag (which derives from `Round Bottom`/Country) |
| `Cue Preset` | optional | Quick-cue presets for the director text-cue channel |
| `BG Color` *(or `BG Colour`/`Background Color`/`Background Colour`)* | optional | Per-team tile background colour: any plain CSS colour (`#C00000`, `rgb(0,80,160)`, `white`); implausible values are ignored |
| `Text Color` *(or `Text Colour`/`FG Color`/`FG Colour`)* | optional | Per-team tile text colour, same rules as `BG Color` |

Sample:

```
Teams                  | Number | Brand Key | Stints  | Streamers          | Session       | Race Control | Cue Preset     | Flag
Sample Team Alpha #11  | 11     | porsche   | Stint 1 | Sample Commentator | Practice      | GREEN        | Stand by       | Green Flag
Sample Team Bravo #22  | 22     | bmw       | Stint 2 | Second Commentator | Qualifying    | YELLOW       | Wrap in 30s    | Yellow Flag
Sample Team Charlie #33| 33     | ferrari   | Stint 3 |                    | Race          | SAFETY CAR   | Throw to break | Safety Car
```

The vocabulary columns (`Stints`/`Streamers`/`Session`/`Race Control`/`Flag`/`Cue Preset`)
are independent lists: blanks are skipped and duplicates dropped. See
[Configuration](Configuration#google-sheet-configuration-tab-columns).

The canonical flag states ship default HUD colors: `Green Flag`, `Yellow Flag`,
`Double Yellow`, `Safety Car`, `Full Course Yellow`, `Code 60`, `Red Flag`,
`Checkered Flag`. The abbreviations `FCY`/`VSC`/`SC` map onto those. Any other value
renders in a neutral default style and can be colored per-league via the overlay
`customCss`.

`BG Color`/`Text Color` publish as the `--team-bg`/`--team-fg` custom properties on the
HUD's team tiles: a per-league overlay decides what to do with them. See
[HUD overlays](HUD-Overlays#team-colours-and-qualifying-lap).

---

## Schedule tab

One row per stint, in stint order. A header row is **optional**: with headers, columns
are found by name; without one, fixed positions apply (**A** = URL, **B** = Streamer,
**C** = Stint).

| Column header | Fallback col | Meaning |
|---|---|---|
| `URL` | A | The stint's live stream: a `youtube.com`/`twitch.tv` URL, or a bare YouTube channel id (`UC…`, public `/live` only), or `local:` for a stint read from the producer machine's capture card ([local capture stint](Relay-Mode#local-capture-stint)) |
| `Streamer` *(or `Name`)* | B | Commentator name (matched to the Crew/Configuration roster) |
| `Stint` | C | Stint label shown on the HUD (optional) |

Sample:

```
URL                                          | Streamer           | Stint
https://www.youtube.com/watch?v=SAMPLE00001  | Sample Commentator | Stint 1
https://www.twitch.tv/sample_channel         | Second Commentator | Stint 2
UCSAMPLECHANNELID0000000  | Third Commentator | Stint 3
local:                                       | Fourth Commentator | Stint 4
```

Feed A serves the odd stints, Feed B the even ones; at each handover the off-air feed
advances to the next row. See [Relay: how the feeds work](Relay-Mode). Edits apply on
the next `/next` or `/reload`: a running stint is never cut mid-feed.

---

## Qualifying tab

**Identical structure** to the Schedule tab (`URL` / `Streamer` / `Stint`). Qualifying
is a single stream, so it lands on Feed A (Feed B idles). Switch with
`racecast event start --qualifying` or live via the panel's Qualifying section. Keep at
least one sample row so the tab parses.

```
URL                                          | Streamer           | Stint
https://www.youtube.com/watch?v=SAMPLE0QUAL  | Sample Commentator | Qualifying
```

---

## Quali Times tab

Optional. One row per car, giving that car's qualifying best lap for display on the race
tiles: **not** the `Qualifying` tab above, which is the qualifying *schedule*
(URL/Streamer/Stint). Maintained **once** between qualifying and the race; no live typing
during the show.

| Column header | Required? | Meaning |
|---|---|---|
| `Team` *(or `Teams` / `Team Name`)* | yes | Matched to a car by the **exact** label first, then by the team name with a trailing `#NNN` stripped. So `Tavernello Racing #6` and `Tavernello Racing` both match that car, and a team fielding **two** cars gives each its own lap by writing both rows with their numbers (`… #14`, `… #54`) |
| `Best Lap` *(or `Best-Lap` / `Bestlap` / `Quali Time`)* | yes | The car's qualifying best lap |

**Format the `Best Lap` column as plain text** (`Format → Number → Plain text`) before
typing lap times, otherwise Google Sheets parses `1:38.973` as a duration (38.973
*minutes*), and the CSV export the relay reads already carries the mangled value. A
single already-mangled cell can be rescued with a leading apostrophe: `'1:38.973`.

| Sheet cell | HUD shows |
|---|---|
| `1:38.973` | `1:38.973` |
| `1:38,973` | `1:38.973` |
| `0:01:38,973` | `1:38.973` |
| `1:01:38.973` *(a non-zero hour)* | `1:01:38.973`: passed through unchanged; a >1h lap is nonsense, but showing the cell beats guessing |
| *(empty, or team not found)* | *(lap slot stays hidden)* |

Sample:

```
Team                   | Best Lap
Tavernello Racing #6   | 1:38.973
```

Because the laps are entered once and never change during the show, the relay reads this
tab on **its own slow cycle** (once at startup, then about once a minute): completely
separate from the HUD's own refresh. Nothing on air ever waits for it: whether the tab is
missing, present, or unreachable, the lower third and the panel's write-confirmations
carry on at full speed.

This tab can fail to show a lap in three different ways, and the relay handles each
differently:

- **The tab was never created**: every lap slot stays empty; nothing was ever fetched.
- **The tab exists, but the `Team` or `Best Lap` header is gone** (e.g. renamed or
  deleted): the whole map is replaced with empty, so every lap slot blanks.
- **The fetch itself fails** (a transient network blip, or a tab that existed and was
  removed mid-event): the **last successfully fetched** lap times keep showing; the
  relay logs a warning once and keeps retrying on the next cycle.

---

## POV tab

The optional driver picture-in-picture. Only **row 2** is read. A header row is
optional; with one, columns are found by name (`URL`, `Name`), otherwise A = URL,
B = Name.

```
URL                                          | Name
https://www.youtube.com/watch?v=SAMPLE00POV  | Driver Cam
```

---

## Setup tab

The Director Panel's **write** target (it needs the webhook: see
[Sheet-Webhook](Sheet-Webhook)). The relay does not read it; the Overlay tab mirrors it
read-only. Header row in row 1, values written in the cell **below** each header
(located by text):

| Header | Value below |
|---|---|
| `Stint` | Current stint label |
| `Streamer` | Current commentator |
| `Session` | Session label |
| `Race Control` | Race-control banner text |
| `Flag` | Race-condition flag (must be in the script's `SETUP_FIELDS`; the Overlay tab mirrors it) |
| `Team 1` / `Team 2` / `Team 3` | The three podium team names |

```
Stint | Streamer           | Session | Race Control | Flag       | Team 1                | Team 2                 | Team 3
5     | Sample Commentator | Race    | GREEN        | Green Flag | Sample Team Alpha #11 | Sample Team Bravo #22  | Sample Team Charlie #33
```

---

## Timer tab

Two columns: a label in **A**, the value in **B**. Drives the on-screen race countdown;
fully documented on the [Race Timer](Race-Timer) page.

| Label (col A) | Value (col B) | Meaning |
|---|---|---|
| `Race End (UTC)` | ISO-8601 UTC (`2026-06-21T18:00:00Z`) | Absolute end-time anchor (wins over `Remaining`) |
| `Duration` | `H:MM:SS` | Total race length |
| `Remaining` | `M:SS` | Paused remainder (mutually exclusive with `Race End`) |
| `Visible` | `TRUE` / `FALSE` | Show/hide the HUD timer |
| `Updated (UTC)` | ISO-8601 UTC | Last-write timestamp (newest write wins across producers) |

```
Race End (UTC) | 2026-06-21T18:00:00Z
Duration       | 6:00:00
Remaining      |
Visible        | TRUE
Updated (UTC)  | 2026-06-21T16:30:00Z
```

---

## Crew tab

The roster behind the `/console` crew roles. Header row in row 1, located **by text**
(any order, extra columns ignored). Boolean cells accept `TRUE`/`FALSE` (also `x`,
`yes`, `1`, `✓`).

| Column header | Meaning |
|---|---|
| `Name` *(or `Crew` / `Person`)* | Person's name (required) |
| `Commentator` | Can use the Commentator Cockpit (also implied by a Schedule entry) |
| `Director` | Reaches the Director Panel via `/console` |
| `Producer` | Can take over / produce |
| `Race Control` *(or `RC`)* | Reaches the read-only race-control desk |
| `Discord` | Discord username for the `/console` login match |

```
Name               | Commentator | Director | Producer | Race Control | Discord
Sample Commentator | TRUE        | FALSE    | FALSE    | FALSE        | sample_caster
Sample Director    | FALSE       | TRUE     | TRUE     | FALSE        | sample_director
Sample Marshal     | FALSE       | FALSE    | FALSE    | TRUE         | sample_marshal
```

Roles are additive (a person can hold several). Without a Crew tab, commentators still
work from the Schedule and the other roles resolve empty. The `crew` write action (the
Control Center crew editor) is detailed in [Sheet-Webhook](Sheet-Webhook).

---

## Producer tab

The producer-handover schedule shown on the **Control Center Home** view. Each row
represents one production segment and identifies the producer responsible for it by their
machine's Tailscale MagicDNS name. The Control Center renders each row as a one-click
**Funnel takeover** button: except your own machine's row, which is shown but
disabled (the Control Center matches the row's MagicDNS against this machine's own full
FQDN, displayed as "Your MagicDNS: …" so you know exactly what to enter). There is no
CLI flag; this tab is read directly by the Control Center on demand using the active
profile's `SHEET_ID`.

The tab is **read-only / admin-owned**: the league owner maintains it per event directly
in the Sheet: no write-back from the app.

| Column header | Meaning |
|---|---|
| `Part` | Human label for the segment (e.g. `1`, `2`, `Night 1`) |
| `Producer` | The producer's name |
| `MagicDNS` | That producer's machine's **full Tailscale MagicDNS FQDN** (e.g. `producer-a.tailXXXX.ts.net`). Must be the full `*.ts.net` name, a bare hostname will not match the self-guard |
| `Stream Key` | **Optional.** A short reference label (e.g. `key1`) for the OBS stream key to use during this Part: the real key is stored as a Script Property, never in a cell. See [Sheet-Webhook, Stream keys](Sheet-Webhook#stream-keys-per-producer-part). |

Duplicate rows are allowed and meaningful: a producer covering two consecutive segments
→ repeat the row with the same Producer and MagicDNS.

```
Part    | Producer          | MagicDNS
1       | Sample Producer A | producer-a.tailnet-demo.ts.net
2       | Sample Producer B | producer-b.tailnet-demo.ts.net
3       | Sample Producer A | producer-a.tailnet-demo.ts.net
```

---

## Assets tab

Where the broadcast **graphics** and **intro/outro/trailer clips** are linked: read by
`racecast graphics` and `racecast media`, **not** the relay. Each row: a **label** in
column A and a link in the first non-empty cell to its right.

- A **Google-Drive** share link is downloaded by `racecast graphics` as
  `runtime/<profile>/graphics/<Label>.png`: **the label is the filename**, so keep it
  filesystem-clean and matching the OBS scene's image name.
- The rows labelled **`Intro Video`** / **`Outro Video`** / **`Trailer Video`** hold a
  **YouTube** URL and are downloaded by `racecast media` into `runtime/<profile>/media/`
  (the relay skips them for graphics).
- The row labelled **`Intermission Music`** holds a Google-Drive MP3 link **or** a
  YouTube/URL and is downloaded by `racecast media` into
  `runtime/<profile>/media/intermission.mp3`. A synthetic ambient-loop placeholder
  plays in the OBS scene if the row is absent or the file has not been downloaded yet.

The OBS scene collection expects these graphic labels:

```
Overlay              | <Drive link>
Standings            | <Drive link>
Schedule             | <Drive link>
Standby              | <Drive link>
Standby Cover        | <Drive link>
Race Results         | <Drive link>
Quali Results        | <Drive link>
Race Weather 1       | <Drive link>
Race Weather 2       | <Drive link>
Quali Weather        | <Drive link>
Post Race Interviews | <Drive link>
Intermission         | <Drive link>
Flag Green           | <Drive link>
Flag Yellow          | <Drive link>
Flag Red             | <Drive link>
Flag Safety Car      | <Drive link>
Flag Virtual Safety Car | <Drive link>
Intro Video          | https://www.youtube.com/watch?v=SAMPLE0INTRO
Outro Video          | https://www.youtube.com/watch?v=SAMPLE0OUTRO
Trailer Video        | https://www.youtube.com/watch?v=SAMPLE0TRAILER
Intermission Music   | <Drive link or YouTube URL>
```

The five **`Flag …`** rows are **optional**. Each is a full-screen transparent 1080p PNG
placed as an image source in the **Stint** and **Splitscreen** OBS scenes. They are the
*graphic* alternative to the flag-status **text** chip in the HUD (`Flag` field in the
Setup tab): the text chip and the graphic overlay are independent controls: you can use
one, both, or neither depending on your broadcast design. Exactly one flag graphic is
active at a time (or none); switching to a new one hides the previous. The director
controls them from the panel's **Flag Gfx** row or the Companion **FLAGS** page's
graphic row (see [Director](Director#the-companion-web-buttons-board)). A missing
graphic file is non-fatal: OBS shows a transparent placeholder until you fetch it.

### Hiding OBS-only assets from the crew Graphics browser (optional)

The **cockpit**, **Director Panel**, and **Race Control** pages each show a read-only
**Graphics** browser, a click-to-open list of the downloaded still-graphics so the crew
can reference them on air. Some assets are **OBS-internal** (the `Overlay` HUD frame, the
weather overlays, backgrounds, the flag graphics) and only clutter that list. Mark them
with an **`Internal`** column so they are hidden from the browser but **still downloaded
for OBS**.

To use it, give the Assets tab a **header row** and add an `Internal` column, a
Google-Sheets **checkbox** (`Insert → Checkbox`), the same style as the Crew-tab role
boxes:

```
Name                 | Link          | Internal
Standings            | <Drive link>  | ☐
Overlay              | <Drive link>  | ☑
Flag Green           | <Drive link>  | ☑
```

- A **ticked** box = internal → the asset downloads to OBS as before but is **omitted**
  from the crew Graphics browser. Unticked/empty = shown to the crew.
- The `Internal` flag is read **independently of the link**, so you can tick a row that
  has **no link** (e.g. a placeholder-seeded weather overlay) purely to hide it.
- Accepted header names: `Internal`, `OBS only`, `OBS-only`. Column A stays the label
  (`Name`/`Label`).
- Fully optional and backward compatible: an Assets tab with **no header row / no
  `Internal` column** shows **every** graphic in the browser (the previous behaviour).
- Re-run `racecast graphics` after changing the column, the flag is captured into
  `runtime/<profile>/graphics/manifest.json` at download time (the same cadence as the
  graphics themselves).

A missing graphic is non-fatal: `racecast setup` warns and OBS shows black until you
run `racecast graphics`. The graphics/clips are **never committed**; they always come
from the Sheet. See [Configuration](Configuration#sheet-driven-graphics).

---

## Brands tab

**Optional.** Overrides or adds manufacturer (brand) logos shown in the HUD. Without this tab the committed base logos in `src/assets/brands/` serve for all leagues. Add rows only for manufacturers you want to override or that the base set does not include.

Header row in row 1, two columns:

| Column header | Meaning |
|---|---|
| `Brand` | Manufacturer name; normalized the same way as the Configuration tab's `Brand Key`/`Brand Name`/`Brand` column: lowercased, runs of whitespace replaced by a single hyphen, and any character outside `a–z 0–9 -` removed. So `BMW` → `bmw`, `Aston Martin` → `aston-martin`, `Mercedes-AMG` → `mercedes-amg`; `BMW` overrides the built-in `bmw` logo and a new `Cupra` adds `cupra` |
| `Logo` | A Google-Drive **share link** to the logo image (`File → Share → Copy link`), the same format as the Assets-tab graphics |

```
Brand   | Logo
BMW     | https://drive.google.com/file/d/<ID>/view?usp=sharing
Cupra   | https://drive.google.com/file/d/<ID>/view?usp=sharing
```

`racecast brands` downloads each logo into `runtime/<profile>/brands/<key>.png`. The relay serves `/hud/assets/brands/<key>` from that directory first; if a key is not found there it falls back to the committed `src/assets/brands/` base set. Run `racecast brands` (or the Control Center **Profile** view → **Brands → Download**) before an event whenever logos change.

---

## Event Notes tab

**Optional.** League-owner notes shown as a toggleable `📋 Notes` modal in the Director Panel, Commentator Cockpit, and Race Control desk. The tab is read-only / admin-owned, the league owner maintains it directly in the Sheet per event, and it is never written from the consoles. If the tab is absent or contains no notes, the modal button self-hides.

Header row in row 1, three columns:

| Column header | Required? | Meaning |
|---|---|---|
| `Heading` | optional | A bold label line shown above its note |
| `Note` | required | The note text; rows with an empty Note are skipped |
| `Priority` | optional | `Important` highlights the note with accent styling and a left border; empty / `Info` / anything else = normal styling |

```
Heading        | Note                                    | Priority
Safety Brief   | Check brake temps before first stint   | Important
Pit Strategy   | Box on even hours for driver swap      |
Weather        | Rain expected after hour 2             | Info
```

The relay reads this tab via `--event-notes-tab` (default `Event Notes`). Disable the modal with `--no-event-notes`, or a custom `--sheet-csv-url` also disables it (same as the Channel and Crew tabs).

---

## See also

- [League profiles](Profiles), where `SHEET_ID` lives and how leagues switch
- [Configuration & secrets](Configuration): the full `profile.env` key reference
- [Sheet-Webhook](Sheet-Webhook): the optional Apps Script **write** path
- [HUD overlays](HUD-Overlays): how Overlay/Configuration values become the lower-third
- [Race Timer](Race-Timer): the Timer tab in depth
