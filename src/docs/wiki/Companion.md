# Companion

> Technical reference. What the buttons do for a director: [Director guide](Director).

Bitfocus Companion is the director's button board: a grid of buttons served as a web
page that directors open in a browser. The
[director panel](Run-an-event#the-director-panel-remote-control) is the primary
control surface; Companion mirrors the same show controls as a hardware-style button
board (the layout a Stream Deck uses) for directors who prefer physical buttons.
Install it first per [Set up the broadcast PC](Set-up-the-broadcast-PC).

## Import the button board

1. Start Companion: `racecast companion start` (Windows/macOS: the first run just
   launches Companion; native Linux with the companion-pi systemd service: works after a
   one-time `racecast companion enable-control` — `install-apps` runs this automatically;
   other Linux setups: start it manually). In the launcher press **Launch GUI**.
2. In the admin: **Import/Export → Import** → the file `racecast export companion` writes
   (the active profile's `runtime/<profile>/racecast-buttons.companionconfig`). The import dialog offers two paths:
   - **First import on a fresh machine:** confirm **"Replace current
     configuration"**. Afterwards enter the OBS WebSocket password once (next
     section) — the shipped config is password-stripped.
   - **Re-import (button update):** choose **"Import, Resetting only Selected
     Components"** and keep the **default checkboxes** — this preserves
     Companion's settings, **including the stored OBS WebSocket password**;
     nothing needs re-typing.
3. Bind the board to the tailnet: `racecast companion restart` — sets Companion's bind
   address to this machine's Tailscale IP. (Other Linux setups without the companion-pi
   systemd service: set the launcher's **GUI Interface** to the Tailscale IP manually.)

> ⚠️ **"Replace current configuration"** replaces the **entire** Companion
> configuration on this station. Fine for a fresh/dedicated producer station;
> **back up first** if this Companion holds other content.

## Connect to OBS

The **OBS connection** (`127.0.0.1:4455`) comes with the config — **but without the
password** (removed for security). After a **first import**: → **Connections** → open
the OBS entry → **enter your OBS WebSocket password** (the one you set in
[Set up the broadcast PC](Set-up-the-broadcast-PC)) → the connection turns green.
(A re-import via **"Resetting only Selected Components"** keeps the stored
password — nothing to do.)

## The button board

The board has three pages — **show control** (scenes & feeds), **race timer & audio**,
and **flags & graphics**. The full layout, what each
button does, and the screenshots live in the [Director guide](Director#the-companion-web-buttons-board) —
that's the operator's reference for actually using the board.

This page covers only how the board is wired up. The relay buttons (`Feeds Next`,
`Feeds Reload`, `Feed A Reload`, `Feed B Reload`, `POV Reload`, `POV Stop`,
`POV Toggle` → `/pov/toggle`, and the `FEED A/B ROBUST/AUTO` quality switches → `/feed/<A|B>/quality/<tier>`)
use the **Generic HTTP Requests**
connection — see [Relay Mode §4](Relay-Mode#4-control-it-companion--relay). Everything else
uses the OBS connection. Four combos sit on both: `RED FLAG` (Standby-Cover visibility
through OBS, Race Control write through the relay), `SPLIT` (cuts to Splitscreen through
OBS, then asks the relay's `/obs/split` to show both feeds and set the audio for the
on-air feed, and sets Race Control to *Driver Swaps*) and `STINT A` / `STINT B` (clear Race Control). The `Feeds Next` handover
also clears Race Control when it cuts back to the Stint scene, so a swap done purely with
`SPLIT` → `Feeds Next` needs no `STINT A/B` press to wipe the banner. The Race Control
writes go to `/setup/set/racecontrol/…` / `/setup/clear/racecontrol` and need the
[sheet-write webhook](Sheet-Webhook).

## Remote access

### Over the tailnet

`racecast companion start` binds Companion to this machine's Tailscale IP. A director on
the tailnet opens `http://<tailscale-ip>:8000/tablet` in any browser to reach the Web
Buttons page.

### Over the Funnel (no Tailscale account needed)

With Companion ≥ v4.1.0 running and `racecast funnel on` active, a director can open the
web-buttons page at `https://<magicdns-host>/console/buttons` — shown as a card on the
`/console` launcher. The relay reverse-proxies the request (HTTP for the page and assets,
plus a transparent WebSocket passthrough for Companion's realtime channel) behind the
**director token gate**. No Tailscale account or extra configuration is needed on the
director's side.

See [Remote access → Companion web buttons over the Funnel](Remote-access#companion-web-buttons-over-the-funnel-consolebuttons)
for the full security boundary and trade-off note.

## Test

Run `racecast companion open-buttons` (opens the Web Buttons board on Companion's bound address), press
a button → OBS reacts. For remote directors, see [Director (Remote)](Director).

## State feedback (optional)

A button can light up when its scene/source is live: in the button editor → **Add
feedback** → **Source Visible** / **Scene Active** → pick a highlight color. Now the
director always sees what's on air. A button can also hold **multiple stacked actions** —
e.g. one "Go to Interview" that switches scene *and* shows the lower-third *and* unmutes
Discord (this is how the row-0 combos work).
