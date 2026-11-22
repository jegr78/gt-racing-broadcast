# Driver telemetry probe, stage 1: PS5 runbook (#1110)

The hardware measurements stage 1 of the driver telemetry probe still owes (epic #1109,
spec `src/docs/design/driver-telemetry-dashboard.md`, section "Probe"). Every block
names the question it answers, the setup, the steps and what to write into the report.
Stage 2 (#1116) needs a league lobby and is not part of this runbook.

Pass marks below are proposals for the report, not fixed rules: change them if a
result shows they are wrong.

## 0. Preparation (once)

The installed `racecast.exe` predates the probe (#1134), so everything runs from the
repo checkout on `main`.

1. `git pull` on every machine that runs the sender (Windows PC, Mac, Linux box).
2. Machine `.env` in the repo root on the producer PC:
   ```
   RACECAST_TELEMETRY_PROBE=1
   ```
3. Use a profile whose relay does not bind UDP 33740 itself. Only a solo POV profile
   starts the relay's own GT7 listener, so use `demo` (endurance, public demo Sheet):
   ```
   py src/racecast.py profile use demo
   py src/racecast.py relay start
   py src/racecast.py funnel on
   ```
   The relay log prints `GET /telemetry/probe on the tailnet shows the key`.
4. Read the probe key and check the endpoint:
   ```
   curl -s http://127.0.0.1:8088/telemetry/probe
   ```
   Note `key`. Ingest URLs: `https://<magicdns-host>/console/probe/ingest` (Funnel) and
   `http://<tailnet-ip>:8088/console/probe/ingest` (tailnet). Use the Funnel URL for
   every sender: that is the path drivers take.
5. Firewall, inbound UDP 33740, on every sender machine:
   - Windows (admin PowerShell):
     `New-NetFirewallRule -DisplayName "racecast sender UDP 33740" -Direction Inbound -Protocol UDP -LocalPort 33740 -Action Allow -Profile Private`
   - Linux: `sudo ufw allow 33740/udp`
   - macOS: allow incoming connections for Python when asked.
6. PS5: note its IP address (Settings, Network). Keep it awake; rest mode stops
   telemetry.
7. A results folder outside the repo, one JSON per test:
   ```
   curl -s http://127.0.0.1:8088/telemetry/probe > <results>/<block>-<name>.json
   curl -s -X POST http://127.0.0.1:8088/telemetry/probe/reset
   ```
   The probe keeps the last 600 samples per sender (60 s at 10 Hz). Save the JSON
   within a minute of the moment you want to look at.
8. Optional dry run without the console: on a second machine
   `python3 tools/gt7-sim-ps5.py <recording>.gt7rec --loop`, then a sender with
   `--console <that machine>`. It proves the chain before the PS5 time starts.

Per sender, the probe JSON holds `packets_per_s`, `packets.lost`, `packets.reordered`,
`packets.decrypt_failed`, `kinds` (packet format), `max_batch_gap_s`, `idle_s`, and the
sender's own `sender_stats` (`received`, `posted`, `dropped`, `post_failed`,
`interruptions`). These are the numbers for the report.

## 1. Several receivers on one console

Question: does a format `A` heartbeat leave a phone dashboard app and SimHub working,
and which format does each receiver get?

1. iPhone: start ezio dash, check live values.
2. Windows PC: SimHub with its GT7 reader and its own heartbeat, check live values.
3. Mac: `python3 src/relay/racecast-sender.py --url <funnel ingest> --key KEY --name Mac --duration 300`
4. Drive for five minutes.
5. Save the probe JSON.

Report: `kinds` of the Mac sender (all `A`, or the format another receiver asked
for?), `packets_per_s`, whether ezio dash and SimHub ever dropped out. If `kinds`
shows `A` while ezio dash runs, the console keeps one format per receiver.

## 2. Sender and SimHub on the same PC

Question: does SimHub's GT7 reader take the packets the sender forwards to a local
port, with SimHub's own heartbeat on and off?

1. Close SimHub. On the Windows PC:
   `py src/relay/racecast-sender.py --url <funnel ingest> --key KEY --name Win-fwd --forward 127.0.0.1:<SIMHUB_PORT>`
2. Point SimHub's GT7 input at `<SIMHUB_PORT>`, heartbeat off. Check live values.
3. Same with SimHub's heartbeat on.

Report: SimHub's settings that worked (port field, heartbeat option, if there is one),
live values yes or no in both variants, `sender_stats.forwarded` against `received`.
If SimHub has no port setting, note that: the guide's SimHub line then needs a change.

## 3. Listener mode behind GT Race Engineer

Question: does the sender take the packets GT Race Engineer forwards, with no heartbeat
of its own? Skip this block if GT Race Engineer is not at hand.

1. GT Race Engineer connected to the PS5, "Telemetry Forwarding" to
   `<PC IP>:<LISTEN_PORT>`.
2. On that PC: `py src/relay/racecast-sender.py --url <funnel ingest> --key KEY --name GRE --listen <LISTEN_PORT> --bind 0.0.0.0`
   (`--listen` binds 127.0.0.1 by default; forwarding from another device needs
   `--bind`). Allow inbound UDP `<LISTEN_PORT>` in the firewall.
3. Drive five minutes, save the JSON.

Report: `packets_per_s`, `sender_stats.heartbeats` (expected 0), whether GT Race
Engineer forwards every packet or a lower rate.

## 4. Best lap and track limits

Question: does GT7's best lap (0x78, `best_ms` in the samples) leave out a lap that
GT7 invalidated for track limits?

1. One sender running (any machine).
2. Time Trial or Custom Race on a track with an obvious cut. Lap 1 clean, lap 2 with a
   cut that GT7 marks as invalid and that is faster than lap 1, lap 3 clean.
3. Within a minute after each line crossing, save the JSON.

Report: per lap `last_ms`, `best_ms` and what GT7 showed on screen. If `best_ms` takes
the invalid lap, the fastest lap needs a correction path (the desks already offer one).

## 5. Box standstill and the pit lane on the dashboard

Question: do packets continue during a long box standstill, and does the projection
onto the racing line hold in the pit lane?

This block uses the real driver path, not the probe ingest.

1. `py src/racecast.py event track --list`, then `event track <ID>` for the GT7 layout
   you drive. Pick one with a racing line (setup warns otherwise).
2. `py src/racecast.py event invite --new`, open the link on a phone or the PC, pick an
   entry, type a name, copy the command.
3. Run the command on the Mac (or another machine), approve the request in the Director
   Panel (`py src/racecast.py relay open-panel`), section Driver requests.
4. Custom Race with a pit stop. Drive in, stand in the box for five minutes, drive out.
5. Watch the Director Panel and the timing tower: distance, status `Box`, no
   `No data` during the standstill.

Report: packet rate during the standstill (sender status lines), whether `Box` showed
and cleared, whether distance stayed plausible through pit entry and exit (no jump,
no lap gained or lost).

Bonus, not owed by stage 1: in a private online lobby alone, note whether
`race_position` is set (stage 2 asks for online and Sport lobbies with several cars).

## 6. 60 Hz over time, every platform at once

Question: do the desktop script and a-Shell receive 60 Hz reliably for a full stint?
Running all four together also answers "four heartbeating devices on one console".

Length: 15 minutes. A league's stint length does not matter here: the run only has to
outlast what shows up late (iOS auto-lock, a paused a-Shell, Wi-Fi power saving), and 15
minutes is three times the longest iPhone auto-lock.

1. Senders, each with its own name, all against the Funnel ingest:
   - Windows PC: `py src/relay/racecast-sender.py ... --name Win`
   - Mac: `python3 src/relay/racecast-sender.py ... --name Mac`
   - Linux box: `python3 src/relay/racecast-sender.py ... --name Linux`
   - iPhone, a-Shell: get the script with
     `curl -o racecast-sender.py "<script link from the invite page>"` (an invite from
     block 5 serves it), then `python3 racecast-sender.py ... --name iPhone`. Screen
     on, a-Shell in front, charging.
2. Reset the counters, drive 15 minutes with a pit stop, save the JSON every 5 minutes
   and at the end.

Pass mark (proposal): `packets_per_s` at least 59, `packets.lost` below 0.1 % of
`received`, no `interruptions`, `max_batch_gap_s` below 2 s.

Report: the numbers per platform, every interruption with its time, what iOS did
(screen lock, a-Shell paused, temperature).

## 7. Android emulator with Termux and forced Doze

Question: does Termux run the script in the Android emulator with the host forwarding
UDP 33740, and does it survive forced Doze? Needs Android Studio with an emulator
(not on the producer PC today; the Mac works).

1. Start an emulator, install Termux (F-Droid APK, `adb install`).
2. In Termux: `pkg install python`, `termux-wake-lock`, get the script
   (`curl -o` from the invite page link, or `adb push` and `termux-setup-storage`).
3. Host forwarding: `adb emu redir add udp:33740:33740`, inbound UDP 33740 allowed on
   the host.
4. `python racecast-sender.py ... --name Android --console <PS5 IP>`. Check the rate.
5. Screen off (`adb shell input keyevent 26`), then
   `adb shell dumpsys deviceidle force-idle`; ten minutes in Doze. Back with
   `adb shell dumpsys deviceidle unforce`.

Report: rate before and during Doze, `interruptions`, whether the wake lock held,
whether discovery worked through the emulator NAT or `--console` was needed.

## 8. Funnel with 15 simulated senders

Question: how does Funnel carry 15 senders at 20 Hz? Needs no PS5.

Run it from a machine outside the home network (mobile hotspot), so the posts really
travel through Funnel:

```
python3 tools/gt7-probe-load.py --url https://<magicdns-host>/console/probe/ingest --key KEY \
    --senders 15 --duration 600 --recording <recording>.gt7rec \
    --status http://<tailnet-ip>:8088/telemetry/probe --reset
```

If a private test stream can run from OBS at the same time, run it once with and once
without. Watch the relay's CPU in the Task Manager.

Report: the tool's JSON report, relay CPU, upload use, whether the stream dropped
frames.

## 9. Live timing viewers next to the senders

Question: how many concurrent viewers of `/console/timing` do Funnel and the upload
carry next to the senders and the stream? The byte budget (`RACECAST_TIMING_BUDGET_KBPS`,
default 1000) stretches the poll interval instead of refusing viewers, so the result
sets that default.

1. `py src/racecast.py event timing --new --serve funnel` prints the timing link. Keep the
   senders of block 8 (or `tools/driver-replay.py --profile demo`) running, so the page
   has changing data.
2. From a machine outside the home network, step the crowd up:
   ```
   python3 tools/timing-viewer-load.py "<timing link>" --viewers 50 --duration 180
   ```
   then 100, 200, 400 and 800 viewers. Stop when `errors` or 429 answers appear or the
   stream drops frames.
3. Once more at the largest clean step while block 8's senders and a private test
   stream run.

Report per step: `upload_kbps`, `status`, `errors`, `latency_s`, `poll_after_s`,
`refresh_s` and `data_age_s`, plus relay CPU and dropped stream frames. The step at
which `refresh_s` grows past what a viewer accepts decides the budget default.

## Afterwards

The results go into a comment on #1110 and, where a result changes the design, into
`src/docs/design/driver-telemetry-dashboard.md` (section "Probe") and the driver guide
`src/docs/wiki/Driver-Telemetry.md`.
