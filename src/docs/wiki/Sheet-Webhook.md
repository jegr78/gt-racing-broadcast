# Sheet-Webhook: the write path back into the Google Sheet

> This is the **write** path. For the tab/column layout the relay **reads** (and a
> ready-to-copy demo Sheet), see [Sheet template](Sheet-Template).

The relay reads the Sheet via CSV export (no key needed). Writing back,
race-timer sync and the director panel's HUD/Schedule/POV controls: goes
through **one** Google Apps Script web app deployed inside the broadcast
Sheet. One URL + key in the active profile's `profile.env` powers all of it:

```
SHEET_PUSH_URL=https://script.google.com/macros/s/…/exec?key=<your secret>
```

Without it everything still works read-only: the timer stays local to one
machine and the panel's HUD row + URLs section are display-only.

## What it writes

| Action (sent by the relay) | Sheet target |
|---|---|
| `timer` | Timer tab (race-timer state: see [Race-Timer](Race-Timer)) |
| `setup` | Setup tab: the cell **below** a header (`Stint`, `Streamer`, `Session`, `Race Control`, `Flag`): found by text, so the tab layout may move. The set of accepted headers is the script's `SETUP_FIELDS` allowlist; a value for a header not in it is rejected (`unknown setup field`), so a new setup field needs the header in the Setup tab **and** in `SETUP_FIELDS` (redeploy) |
| `schedule` | **Schedule** tab (or the **Qualifying** tab when the payload carries `"tab":"Qualifying"`), **physical row N** (the panel sends the CSV line number automatically): URL + Streamer + Stint label, located by the `URL`/`Streamer`/`Stint` headers in row 1 (falls back to fixed cols A/B with no header row); row `last+1` appends. The Stint cell is only written when a `Stint` header exists. The Qualifying tab has the **same structure** as the Schedule tab. **Neither tab may have leading blank rows**, the gviz CSV export maps physical sheet rows to CSV lines 1:1 when the tab starts at row 1. A header row is silently skipped when reading but its physical line number is still used when writing. |
| `pov` | POV tab **row 2**: the `url` and/or `name` cell, located by header text (so the columns may move) |
| `teams` | Setup tab: the cell **below** the `Team <slot>` header (slot 1–3 → `A6`/`B6`/`C6` in the shipped layout): found by text, same as the other Setup fields. The Overlay tab only mirrors them read-only |
| `crew` | **Crew** tab (`Name \| Commentator \| Director \| Producer \| Race Control \| Discord`, header in row 1). `{"action":"crew","row":N,"name":..,"commentator":bool,"director":bool,"producer":bool,"race_control":bool,"discord":".."}` writes **data row N** (sheet row N+1; `row last+1` appends). Columns are located **by header text** (any order; extra columns are ignored), so a pre-existing `Name\|Director\|Producer` tab is auto-extended with the `Commentator`, `Race Control` and `Discord` columns on first write. `commentator`/`director`/`producer`/`race_control` are written as **`TRUE`/`FALSE` booleans** (checkbox-compatible, never the string `X`, which would break a Google Sheets checkbox cell); `discord` is the verbatim username. `{"action":"crew","row":N,"delete":true}` deletes data row N (rows shift up). The tab must start at row 1 with the header and have no interior blank rows (the Control Center editor maintains this). |

The relay only sends Setup values that exist in the Configuration tab's
vocabulary columns: the same lists the sheet's own dropdowns use.

> **Crew-tab coordination:** the `crew` action requires a **Crew** tab in the league's
> Sheet (columns `Name | Commentator | Director | Producer | Race Control | Discord`,
> header in row 1) **and** the redeployed script that handles `crew`. Without it,
> director/producer/race-control roles simply resolve to empty: commentators still
> work from the Schedule: and the Control Center crew editor surfaces an
> *outdated-script* error. Nothing crashes. A script predating the **Race Control**
> column ignores the extra `race_control` field and the column is appended on the next
> write, so older deployments degrade gracefully.

> **Race-condition flag coordination:** the panel/Companion **Flag** control writes
> through the `setup` action like the other Setup fields. It needs three things in
> place together: a `Flag` header in the **Setup** tab, a `Flag` row in the
> **Overlay** tab that mirrors it (the relay only ever reads Overlay), and `'Flag'`
> in the script's `SETUP_FIELDS` allowlist with the script **redeployed** (new
> version under the same `/exec` URL: not a new deployment, which changes the URL).
> A script predating `Flag` rejects the write with `unknown setup field: Flag`; the
> HUD then shows the flag only for the ~30 s optimistic-override window and drops it.

> **Not written to the Sheet:** the free-text **event title**
> ([Director](Director#event-title)) is producer-side runtime state
> (`runtime/<profile>/event.json`), set from `EVENT_TITLE` / `event start
> --title` / the panel's inline editor. It changes per event and is producer-
> chosen, so it is deliberately kept out of the shared Sheet.

A `teams` write sends `{"action":"teams","slot":1|2|3,"name":"<team>"}`. The
relay validates the panel's choice against the Configuration tab's roster and
sends the **verbatim** Configuration team label (e.g. `Example Team #111`), so the
Setup cell matches the tab's team dropdown exactly: just like Streamer/Session.
The script locates the `Team <slot>` header in the **Setup** tab (case-insensitive)
and writes the name into the cell **below** it (`A6`/`B6`/`C6` in the shipped
layout). The v2 script responds `{"ok":true,"action":"teams","v":2}`. **Never
write the Overlay tab**, it mirrors the Setup teams read-only, and writing there
overwrites the mirror formula (the bug this corrects).

### Configuration tab: team-name and Number columns

The team-name column in the Configuration tab may be headed **`Teams`** or
**`Team Name`**, the relay accepts either. An optional **`Number`** column
holds the car number. The relay strips a trailing `#NNN` token from the team
name field and treats the `Number` column as the canonical car number if
present (it takes precedence over the embedded `#NNN`). This means a row like
`Example Team #111` with a `Number` value of `111` yields team name
`Example Team` and car number `111` without duplication. The panel's P1/P2/P3
podium dropdowns offer the bare team name, but write the **verbatim**
Configuration label (with the `#NNN` if that is how the column reads) into the
Setup tab's `Team 1`/`Team 2`/`Team 3` cells, so the value matches the tab's
own dropdown. The Overlay tab's `Teams P1/P2/P3` rows mirror those Setup cells
read-only.

## Crew

The `crew` action maintains the **Crew** tab, the per-person roster used by the relay
to resolve `/console` roles (commentator/director/producer/**race control**) and to match
a Discord login to a crew member. The Control Center's crew editor reads the tab via the
relay (`/crew/data`) and writes changes back through the `crew` webhook action
(`/api/crew`, `/api/crew/delete` in the Control Center API, which POST to the relay, which
forwards to the webhook). The script responds `{"ok":true,"action":"crew","v":7}`,
accepted by the relay's `check_webhook_response`.

The **Race Control** column flags a person for the read-only [Race Control](Console)
monitoring desk (`/console/race-control`: program preview, redacted schedule, timer,
chat). It is unrelated to the director-only HUD **Race Control** banner (the Setup-tab
`Race Control` field, see [Director](Director)); the role string is `race_control`, the
banner is `racecontrol`, and they never collide.

**Tab structure:** one header row
(`Name | Commentator | Director | Producer | Race Control | Discord`) at row 1; data rows
below it; no interior blank rows. Both the read and write paths are **header-aware**,
they locate each column by its header text, so the columns may sit in any order and extra
columns are ignored. The Control Center editor maintains the no-blank-rows invariant.

**Write a row:** `{"action":"crew","row":N,"name":"Alice","commentator":false,"director":true,"producer":false,"race_control":false,"discord":"alice_d"}`
writes or overwrites data row N (sheet row N+1, skipping the header). `row last+1`
appends a new person. `commentator`/`director`/`producer`/`race_control` are written as
`TRUE`/`FALSE` booleans (checkbox-compatible, never the string `X`, which would break a
Google Sheets checkbox cell); `discord` writes the verbatim username (empty clears it). A
pre-existing `Name | Director | Producer` tab is auto-extended with the `Commentator`,
`Race Control` and `Discord` columns on the first write: existing data is untouched.

**Delete a row:** `{"action":"crew","row":N,"delete":true}` deletes data row N
(sheet row N+1); all rows below shift up. The Control Center re-numbers its in-memory
roster after the delete.

> **Graceful degradation:** without a Crew tab or an up-to-date script, director,
> producer and race-control roles resolve to empty: commentators still reach
> `/console/cockpit` from the Schedule: and the editor surfaces an *outdated-script*
> banner. A script predating the **Race Control** column ignores the extra field and
> appends the column on the next write. Nothing crashes.

## Stream keys (per Producer Part)

> **This is a read action, not a write.** `get_stream_key` fetches a stream key
> from the Apps Script's **Script Properties** on demand, the key never lands in
> any Sheet cell or CSV export. The existing `SHEET_PUSH_URL` credential is reused;
> no new secret is needed.

For events where different producers use different OBS stream keys (e.g. a 12 h /
24 h event split across multiple YouTube streams), the relay can set OBS's stream
service and key automatically when the producer selects their Part on the Control
Center Home view.

### Producer tab: optional `Stream Key` column

Add an optional **`Stream Key`** column to the **Producer** tab
(see [Sheet template: Producer tab](Sheet-Template#producer-tab)). Each cell holds
a short **reference label** (`key1`, `key2`, …), **never the real key**. The relay
reads only the reference; the real key stays in Script Properties where viewers
cannot see it.

| Column header | Meaning |
|---|---|
| `Stream Key` | **Optional.** Short reference label (e.g. `key1`) identifying the OBS stream key for this Part. Leave blank if this Part shares the key already in OBS. |

Example:

```
Part  | Producer          | MagicDNS                        | Stream Key
1     | Sample Producer A | producer-a.tailnet-demo.ts.net  | key1
2     | Sample Producer B | producer-b.tailnet-demo.ts.net  | key2
3     | Sample Producer A | producer-a.tailnet-demo.ts.net  | key1
Q     | Sample Producer A | producer-a.tailnet-demo.ts.net  | key1
```

A Part whose label starts with **`Q`** (e.g. `Q`) is the **qualifying** broadcast. Its
`Stream Key` cell may reference **any existing key**: commonly the **same** ref as race
Part 1 (`key1` above), since qualifying runs on a separate day and can reuse the same
ingest target; no new Script Property is needed in that case. Reference a distinct ref
(and add a matching property below) only if qualifying streams to a different broadcast.
The Director-Panel Parts control shows the numeric parts in race mode and the single `Q`
part in qualifying mode (`racecast event start --qualifying`, or the Control Center
**Qualifying** toggle). Qualifying is a single part, so ending it stops the OBS stream and
auto-generates the post-event report: a clean, separate session from the race.

### Store the real keys in Script Properties

In the Apps Script editor: **Project Settings → Script Properties**. Add one
property per reference:

| Name (property key) | Value |
|---|---|
| `key1` | the real OBS stream key for Producer A |
| `key2` | the real OBS stream key for Producer B |

Add one more property (e.g. `keyQ`) **only** if the qualifying `Q` row references a
distinct key; when the `Q` row reuses `key1` (the common case) there is nothing extra to add.

Script Properties are visible only to Sheet **editors** (the league owner): viewers
cannot see them, and they never appear in any CSV export or gviz fetch. The relay
retrieves the key at switch time over HTTPS and passes it straight to OBS; it is
never logged or written back to any cell.

### Apps Script handler

`get_stream_key` is one clause in the main `doPost` dispatcher, it already ships in
the [One-time setup](#one-time-setup-per-sheet) script below (alongside `timer`,
`schedule`, `crew`, …). If you paste that script fresh, there is nothing extra to do.

If you are **upgrading an existing sheet**, add this one `else if` to your `doPost`
dispatcher (it uses the same parsed `p` payload and `out()` helper as every other
action), then **redeploy** (see
[Updating the script later](#updating-the-script-later)):

```javascript
else if (action === 'get_stream_key') {
  const key = PropertiesService.getScriptProperties().getProperty(String(p.ref || ''));
  return key
    ? out({ok: true, action: 'get_stream_key', key: key})
    : out({ok: false, action: 'get_stream_key', error: "no key for ref '" + String(p.ref || '') + "'"});
}
```

The response always echoes `action`, and the key is returned only over HTTPS, it is
never written back to any cell.

**Request / response contract:**

| | JSON |
|---|---|
| Request | `{"action": "get_stream_key", "ref": "<reference>"}` |
| Success | `{"ok": true, "action": "get_stream_key", "key": "<key>"}` |
| Unknown ref | `{"ok": false, "action": "get_stream_key", "error": "no key for ref '<ref>'"}` |

### Set the stream target

Once the Producer tab has a `Stream Key` column and the Script Properties are
populated, the producer sets OBS's stream target before going live:

- **Control Center Home:** each Producer Part row with a `Stream Key` reference
  shows a **Set target** button. Click it to fetch the key for that Part and apply
  it to OBS's stream settings.
- **CLI:** `racecast obs stream-target <part>` (e.g.
  `racecast obs stream-target 1`).

> **OBS must not be streaming.** `set target` only works while OBS is not live,
> it writes the service and key to OBS's stream settings. To switch between two
> back-to-back Parts: **stop the broadcast** → set the target for the next Part →
> go live again.

A Part with no `Stream Key` reference (blank cell) reports "no reference for
this Part": OBS's current stream settings are left unchanged. This is normal for
events where all Parts share one key already configured in OBS.

> The relay also calls `get_stream_key` server-side when the Director Panel starts a
> broadcast Part (#395): the key is applied to OBS over localhost and never reaches
> the browser.

## One-time setup (per sheet)

1. Open the broadcast Google Sheet → **Extensions → Apps Script**.
2. Replace the editor contents with this script (set `KEY` to a random secret):

   ```javascript
   const KEY = 'change-me';            // must match the key=... in the URL below
   const TABS = {setup: 'Setup', schedule: 'Schedule', qualifying: 'Qualifying',
                 pov: 'POV', timer: 'Timer', crew: 'Crew'};
   const SETUP_FIELDS = ['Stint', 'Streamer', 'Session', 'Race Control', 'Flag'];
   const TIMER_ROWS = {'Race End (UTC)': 1, 'Duration': 2, 'Visible': 3,
                       'Updated (UTC)': 4, 'Remaining': 5};

   function doPost(e) {
     const out = (o) => ContentService.createTextOutput(JSON.stringify(o))
         .setMimeType(ContentService.MimeType.JSON);
     if (((e.parameter && e.parameter.key) || '') !== KEY) return out({error: 'bad key'});
     try {
       const p = JSON.parse(e.postData.contents);
       const ss = SpreadsheetApp.getActiveSpreadsheet();
       const action = p.action || 'timer';
       if (action === 'timer') writeTimer(ss, p);
       else if (action === 'setup') writeSetup(ss, p.fields || {});
       else if (action === 'schedule') writeSchedule(ss, p);
       else if (action === 'pov') writePov(ss, p);
       else if (action === 'teams') writeTeams(ss, p);
       else if (action === 'crew') writeCrew(ss, p);
       else if (action === 'get_stream_key') {
         const key = PropertiesService.getScriptProperties().getProperty(String(p.ref || ''));
         return key
           ? out({ok: true, action: 'get_stream_key', key: key})
           : out({ok: false, action: 'get_stream_key', error: "no key for ref '" + String(p.ref || '') + "'"});
       }
       else return out({error: 'unknown action: ' + action});
       return out({ok: true, action: action, v: 7});
     } catch (err) { return out({error: String(err)}); }
   }

   function tab(ss, name) {
     const sheet = ss.getSheetByName(name);
     if (!sheet) throw 'tab not found: ' + name;
     return sheet;
   }

   function writeTimer(ss, p) {
     const sheet = ss.getSheetByName(TABS.timer) || ss.insertSheet(TABS.timer);
     const write = (label, value) => {
       sheet.getRange(TIMER_ROWS[label], 1).setValue(label);
       sheet.getRange(TIMER_ROWS[label], 2).setNumberFormat('@').setValue(value);
     };
     if ('end' in p) write('Race End (UTC)', p.end);
     if ('duration' in p) write('Duration', p.duration);
     if ('visible' in p) write('Visible', p.visible);
     if ('remaining' in p) write('Remaining', p.remaining);
     write('Updated (UTC)', new Date().toISOString());
   }

   function writeSetup(ss, fields) {
     // Locate every header first, then write: an unknown/renamed header
     // aborts the whole write with a clear error.
     const sheet = tab(ss, TABS.setup);
     const grid = sheet.getDataRange().getValues();
     const targets = {};
     Object.keys(fields).forEach((name) => {
       if (SETUP_FIELDS.indexOf(name) === -1) throw 'unknown setup field: ' + name;
       let hit = null;
       for (let r = 0; r < grid.length && !hit; r++)
         for (let c = 0; c < grid[r].length && !hit; c++)
           if (String(grid[r][c]).trim().toLowerCase() === name.toLowerCase())
             hit = [r + 2, c + 1];          // the value cell sits BELOW the header
       if (!hit) throw 'header not found in Setup tab: ' + name;
       targets[name] = hit;
     });
     Object.keys(targets).forEach((name) => {
       sheet.getRange(targets[name][0], targets[name][1])
            .setNumberFormat('@').setValue(fields[name]);
     });
   }

   function writeSchedule(ss, p) {
     // Columns located by header text in row 1; falls back to A/B with no header
     // row. Stint written only when that header exists. p.tab is restricted to the
     // two known tabs so a stray value can never address an arbitrary sheet.
     const target = p.tab === TABS.qualifying ? TABS.qualifying : TABS.schedule;
     const sheet = tab(ss, target);
     const row = Number(p.row);
     const last = sheet.getLastRow();
     if (!row || row < 1 || row > last + 1) throw 'row out of range: ' + p.row;
     const lastCol = sheet.getLastColumn();
     const header = lastCol >= 1 ? sheet.getRange(1, 1, 1, lastCol).getValues()[0] : [];
     const colOf = (name) => {
       for (let c = 0; c < header.length; c++)
         if (String(header[c]).trim().toLowerCase() === name) return c + 1;
       return 0;
     };
     const urlCol = colOf('url') || 1;
     const nameCol = colOf('streamer') || 2;
     const stintCol = colOf('stint');
     if ('url' in p) sheet.getRange(row, urlCol).setNumberFormat('@').setValue(p.url);
     if ('name' in p) sheet.getRange(row, nameCol).setNumberFormat('@').setValue(p.name);
     if ('stint' in p && stintCol) sheet.getRange(row, stintCol).setNumberFormat('@').setValue(p.stint);
   }

   function writePov(ss, p) {
     const sheet = tab(ss, TABS.pov);
     const lastCol = Math.max(1, sheet.getLastColumn());
     const header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
     const colOf = (name) => {
       for (let c = 0; c < header.length; c++)
         if (String(header[c]).trim().toLowerCase() === name) return c + 1;
       return 0;
     };
     if ('url' in p)  sheet.getRange(2, colOf('url')  || 1).setNumberFormat('@').setValue(p.url  || '');
     if ('name' in p) sheet.getRange(2, colOf('name') || 2).setNumberFormat('@').setValue(p.name || '');
   }

   function writeTeams(ss, p) {
     // Each podium slot is the cell below its header, located by text so the
     // layout may move. Write only the Setup tab, the mirror elsewhere is
     // read-only and must not be overwritten.
     const slot = Number(p.slot);
     if (!(slot >= 1 && slot <= 3)) throw 'slot out of range: ' + p.slot;
     const sheet = tab(ss, TABS.setup);
     const grid = sheet.getDataRange().getValues();
     const header = 'team ' + slot;
     for (let r = 0; r < grid.length; r++)
       for (let c = 0; c < grid[r].length; c++)
         if (String(grid[r][c]).trim().toLowerCase() === header) {
           sheet.getRange(r + 2, c + 1).setNumberFormat('@').setValue(p.name || '');
           return;                                  // the value cell sits BELOW the header
         }
     throw 'header not found in Setup tab: Team ' + slot;
   }

   function writeCrew(ss, p) {
     const sheet = ss.getSheetByName(TABS.crew) || ss.insertSheet(TABS.crew);
     const HEADERS = ['Name', 'Commentator', 'Director', 'Producer', 'Race Control', 'Discord'];
     if (sheet.getLastRow() < 1) {
       sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
     }
     const row = parseInt(p.row, 10);                 // 1-based data row (header is row 1)
     if (!(row >= 1)) throw 'crew: row must be >= 1';
     const target = row + 1;                          // sheet row (skip the header)
     if (p['delete'] === true) {
       if (target <= sheet.getLastRow()) sheet.deleteRow(target);
       return;
     }
     const name = (p.name || '').toString().trim();
     if (!name) throw 'crew: name is required';
     // Columns located by header text; a missing header is appended, so an older
     // tab is auto-extended without disturbing its data.
     let header = sheet.getRange(1, 1, 1, Math.max(1, sheet.getLastColumn())).getValues()[0];
     const colOf = (label) => {
       for (let c = 0; c < header.length; c++)
         if (String(header[c]).trim().toLowerCase() === label.toLowerCase()) return c + 1;
       const col = header.length + 1;                 // append a new header column
       sheet.getRange(1, col).setValue(label);
       header = header.concat([label]);
       return col;
     };
     // Text columns are pinned as text so numeric-looking handles aren't coerced.
     // Role columns are written as booleans (not "X") to stay compatible with
     // checkbox cells, whose validation only accepts TRUE/FALSE.
     const setText = (label, value) =>
       sheet.getRange(target, colOf(label)).setNumberFormat('@').setValue(value);
     const setFlag = (label, value) =>
       sheet.getRange(target, colOf(label)).setValue(!!value);
     setText('Name', name);
     setFlag('Commentator', p.commentator);
     setFlag('Director', p.director);
     setFlag('Producer', p.producer);
     setFlag('Race Control', p.race_control);
     setText('Discord', (p.discord || '').toString().trim());
   }
   ```

3. **Choose one random secret** and use the **same value** in two places: the script's
   `KEY = '…'` (step 2) **and** the `?key=…` in the URL (step 4). They must match exactly,
   the `key` is the *only* thing protecting the webhook (see [Security](#security) below),
   because **access: Anyone** means anyone with the URL could otherwise write to your Sheet.
4. **Deploy → New deployment → Web app**, execute as **Me**, access:
   **Anyone**. Copy the `/exec` URL.
5. In the league's `profiles/<name>/profile.env` on every producer machine: append your
   secret as `?key=…` so it matches the script's `KEY`:

   ```
   SHEET_PUSH_URL=https://script.google.com/macros/s/…/exec?key=<your secret>
   ```

6. Restart the relay. `racecast event status` shows the profile check as PASS; the
   panel's HUD line reports `sheet sync OK` after the first action.

## Updating the script later

**Manage deployments → ✎ Edit → Version: New version → Deploy.** This keeps
the `/exec` URL: no `profile.env` change on any machine. (A *New deployment*
instead creates a NEW URL and every `profile.env` must be updated.)

The relay detects an outdated (v1, timer-only) script: panel writes then
report *"webhook script outdated: redeploy"* instead of failing silently.

The current script is **v7** (v3 added the Schedule `Stint` column; v4 lets the
`schedule` action target the **Qualifying** tab via `"tab":"Qualifying"`; v5 added
`teams`; v6 added the `crew` action and the `Crew` tab; v7 makes `crew` header-aware
and adds the `Commentator` + `Discord` columns). The relay does not enforce the
version, so an older script degrades gracefully: a v2 script ignores Stint
write-back; v2/v3 scripts ignore the `tab` field and write the **Schedule** tab,
qualifying-row edits would land on the race Schedule until you redeploy; a v5 script
ignores the `crew` action, so the Control Center crew editor surfaces an
*outdated-script* error and leaves the Sheet unchanged. A **v6** script writes only
the `Name | Director | Producer` columns positionally: on a 5-column tab it would
write the Director flag into the `Commentator` column, so **redeploy v7 before using
the crew editor against a tab that has the `Commentator`/`Discord` columns** (v7 is
header-aware and writes each column by its header). The relay still reads the Crew tab
directly for role resolution, so roles work if the tab is populated by hand. The
handover HUD update (Setup tab via the `setup` action) and serving the qualifying
stream read-only both keep working regardless.
Add a **Qualifying** tab (same columns as Schedule) and redeploy to enable it.
Add a **Crew** tab (`Name | Commentator | Director | Producer | Race Control | Discord`,
header in row 1) and redeploy to enable the crew editor's write-back.

## Security

The URL+key is a write credential for the Sheet: treat it like the other
`profile.env` secrets (never commit it). The endpoints the panel uses sit on the
relay's unauthenticated control server: the tailnet is the trust boundary,
same as all other `/panel` controls.
