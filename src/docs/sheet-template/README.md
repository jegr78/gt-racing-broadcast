# Sheet template: per-tab CSV stubs

These CSV files are a starting point for the Google Sheet that drives a league. One
file per tab; the column/row contract each one follows is documented in the wiki page
[Sheet-Template](../wiki/Sheet-Template.md). All data is **generic placeholder** content.
Replace it with your league's real teams, schedule and graphics links.

To use them: create a Google Sheet, add one tab per file (named exactly as the file,
without `.csv`), and **File → Import → Upload** each CSV into its tab (import location:
*Replace current sheet*). Then put the Sheet's ID in your profile's `profile.env`
(`SHEET_ID=`). The optional write-back Apps Script is on the
[Sheet-Webhook](../wiki/Sheet-Webhook.md) page.

| File | Tab | Read by |
|---|---|---|
| `Overlay.csv` | Overlay | relay (`/hud`) |
| `Configuration.csv` | Configuration | relay |
| `Schedule.csv` | Schedule | relay |
| `Qualifying.csv` | Qualifying | relay |
| `POV.csv` | POV | relay |
| `Setup.csv` | Setup | Director Panel (write) |
| `Timer.csv` | Timer | relay |
| `Crew.csv` | Crew | relay |
| `Producer.csv` | Producer | Control Center |
| `Assets.csv` | Assets | `racecast graphics` / `media` |

The Crew template includes an additive **Race Engineer** checkbox. Redeploy the
current Sheet webhook before editing it. A Producer inherits Engineer access;
other roles require explicit assignment plus the producer's solo POV profile
opt-in. Existing columns keep their header-based meaning.

The Assets sample has optional header-based Internal and Alias columns. Leave an
alias blank to keep the current labels. Column A remains the canonical asset
identifier. Refresh labels with `racecast graphics --metadata-only` and
`racecast media --metadata-only`, or the Profile view's Refresh labels buttons.
See [the Sheet template guide](../wiki/Sheet-Template.md#optional-display-aliases)
for visibility, portability and the Companion upgrade.
