# GT Racing Broadcast: Setup Package

This package sets up a complete producer station for the GT Racing
broadcast: OBS scenes + HUD, the Companion button board, the director panel,
and the relay that pulls each commentator's stream into OBS.

**Quickest start:** double-click **`racecast-ui`** to open the **Control
Center**, a local web dashboard that runs setup and event day from your browser,
no terminal needed. The `racecast …` commands below are the CLI alternative.

**The documentation lives in the project wiki**, always current, written for
first-time producers:

- **The Control Center:**
  <https://github.com/jegr78/gt-racing-broadcast/wiki/Control-Center>
- **First-time setup** (one time, ~30 min):
  <https://github.com/jegr78/gt-racing-broadcast/wiki/Set-up-the-broadcast-PC>
- **Event day:**
  <https://github.com/jegr78/gt-racing-broadcast/wiki/Run-an-event>
- **Start page** (all roles):
  <https://github.com/jegr78/gt-racing-broadcast/wiki>

## Quickstart

First-time setup: one guided command. It creates or selects a **league
profile** (and fills in that league's Google Sheet ID), then installs everything
and skips whatever is already done:

    racecast init

Each league lives in its own profile (`profiles/<name>/`). Switch leagues with
`racecast profile use <name>`, or create a new one with
`racecast profile new <name> --from example`.

On event day:

    racecast cookies firefox          # refresh YouTube cookies (log into YouTube in Firefox first)
    racecast cookies twitch firefox   # refresh Twitch cookies (only if any stint uses a gated Twitch feed)
    racecast event start              # bring everything up; prints the director URLs
    racecast event stop               # after the broadcast

`racecast preflight` checks this machine any time and names the exact fix for
anything missing.

The visual **onboarding decks** (one short walkthrough per role) and the printable
**role cheat sheet** are the central reference, one place for every role:
<https://jegr78.github.io/gt-racing-broadcast/>. A local copy of the cheat
sheet also ships at `docs/slides/cheat_sheets.html` (open it in a browser and print).
In the Control Center, **Help & Docs → Onboarding decks** opens the same hub.
