# Multi-feed 429 viability on the cloud box (#505) — design, runbook & results

**Date:** 2026-07-14
**Issue:** #505 (empirical predecessor to the #489 hardening)
**Status:** Harness built + validated on the UTM VM. Awaiting the off-event on-box run.

## 1. Goal

Measure — off-event, on the AWS box's datacenter IP — how many **distinct sustained**
googlevideo (YouTube) / Twitch pulls the IP tolerates before a 429, how long until it hits,
and how it recovers. The output is the concrete result table #489's design decisions depend
on (backoff/cadence thresholds, and the verdict on whether sustained 2-/3-feed splitscreen +
POV is viable on a datacenter IP at all).

This is **measurement + a maintainer harness**, not a shipped feature.

## 2. Scope — factors under test

Everything runs on the box's **fresh account** (the account/cookie the box already uses).

| Factor | Levels | Note |
|---|---|---|
| **Concurrency** (distinct sustained pulls) | 1 (sanity), **2** (observed failure), **3** (POV case) | 1 confirms the account itself pulls |
| **Activation** | **burst** (all at once — current relay) vs **staggered + gentler HLS cadence** (per-pull delay + jitter, higher `--hls-live-edge`) | the primary controllable lever |
| **Platform** | **YouTube** (googlevideo) vs **Twitch** (account-less plugin) | Twitch is a separate small arm |
| **Recovery strategy** | **fixed-retry** (current ~15 s storm) vs **backoff + jitter** | #489 lever 1, quantified |

### Deliberately NOT varied (decided in brainstorming, 2026-07-14)

- **Account age / "auth-cookie strength" lever — DEFERRED.** "Anonymous" is not a real
  baseline: a YouTube live can't even be *resolved* without cookies passing the bot-check,
  and the box's current (fresh) account is already the known-throttling baseline. The only
  open account question — *does a years-old account lift the per-IP ceiling?* — would require
  the operator's **personal cookies on the box**, which is an exposure we will not take. It is
  deferred to a future run with a **dedicated account aged over months** (the personal account
  never touches the box). If that avenue is ever needed, #489 escalates to Twitch / proxy
  egress / home-producer fallback instead.
- **Same-URL double-pull** — the *guaranteed* 429 already handled by #491's single-pull
  invariant. The harness pulls **distinct** URLs only.

## 3. The cells, adaptive ordering & time budget

**Survival window = 20 min** (a cell that holds 20 min clean is "sustained-viable"). **Cool-down
= 10 min** after any YouTube cell that actually threw a 429 (the observed N24 429 self-healed in
~6 min; 10 min ≈ 1.7× — start the next cell from a clean IP). Twitch cells need no YT cool-down
(different infrastructure). Fixed-retry recovery numbers fall out of the baseline burst cells for
free, so only *backoff* recovery needs a dedicated run.

Adaptive order — the headline answers land first:

| # | Cell (`--cell-id`) | Flags | Expected | ~Duration |
|---|---|---|---|---|
| 1 | `yt-1-sanity` | `--n 1` | works | 3 min |
| 2 | `yt-2-burst` | `--n 2 --activation burst` | 429 in ~2 min | ~2 + 10 cooldown |
| 3 | `yt-2-stagger` | `--n 2 --activation staggered --hls-live-edge 6` | ? (does cadence save 2-feed?) | 20 *or* ~12 |
| 4 | `yt-3-burst` | `--n 3 --activation burst` | fast 429 | ~2 + 10 cooldown |
| 5 | `yt-3-stagger` | `--n 3 --activation staggered --hls-live-edge 6` | ? (does cadence save 3-feed?) | 20 *or* ~12 |
| 6 | `tw-2-burst` | `--platform twitch --n 2` | no throttle | 20 min |
| 7 | `tw-3-burst` | `--platform twitch --n 3` | no throttle | 20 min |
| 8 | `yt-2-recovery-backoff` | `--n 2 --retry-mode backoff` | — | ~25 min |

**Total ≈ 2.5 h active, ~3–4 h wall-clock** with setup + cool-downs + margin. One off-event
afternoon. **Stop early** the moment an event looms.

## 4. The harness — `tools/multifeed-429-probe.py`

Maintainer-only (under `tools/`), NOT shipped, NOT run in CI. Precedent: `tools/fanout-soak.py`.

- **Faithful reproduction.** It importlib-loads the real relay module `src/relay/racecast-feeds.py`
  and reuses its resolve + streamlink builders (`ytdlp_resolve_cmd`, `streamlink_fanout_cmd`,
  `streamlink_serve_flags`/`streamlink_twitch_flags`), so the measured pull path is production's,
  not a synthetic approximation. **This means it must run from a full repo checkout** — the relay
  module imports ~25 `src/scripts/*` modules (confirmed on the VM). It is NOT the shipped binary.
- **What it does.** Starts N `streamlink --stdout` pulls of N **distinct** live URLs, drains their
  bytes continuously with no backpressure (same as the relay fan-out ring), scans streamlink
  stderr for 429/throttle markers, re-launches an exited pull per the retry policy, and logs — per
  pull + aggregate — start, first-429 (+elapsed), retries, recovery (bytes resume), and throughput.
- **Output.** Per-pull `pull_N.log` + a machine-readable `results.jsonl` (one record per pull + one
  aggregate `cell` record) under `probe-runs/<cell-id>/`.
- **Serves + logs only.** Provokes nothing beyond the pulls themselves; safe to Ctrl-C (validated:
  0 streamlink orphans).
- **Off-event guard.** Refuses to run (exit 3) if a relay PID is active, unless `--force`.
- **Key flags:** `--urls FILE` / `--url` (distinct live URLs), `--platform`, `--n`, `--activation
  burst|staggered`, `--stagger-s`/`--jitter-s`, `--hls-live-edge` (staggered gentler cadence),
  `--quality full|robust|emergency`, `--cookies`, `--duration-s` (default 1200), `--retry-mode
  fixed|backoff`, `--cell-id`, `--out`, `--dry-run`, `--force`, `--summarize RESULTS.JSONL…`.

Pure helpers (unit-tested in `tests/test_multifeed_probe.py`): `activation_delays`,
`backoff_delay`, `classify_pull_line`, `relay_running`, `read_url_list`, `apply_live_edge`,
`summarize_cells`.

## 5. On-box runbook (off-event only)

### 5.0 Pre-flight — the hard gate
- **No event scheduled** for the next ~4 h. This run deliberately provokes 429s; doing it near a
  broadcast would prolong the very outage we study.
- `racecast relay stop` — the harness refuses while a relay is live, but stop it explicitly.
- The box is otherwise idle (no other producer, no OBS pulling feeds).

### 5.1 Setup on the box (once)
```bash
ssh -i ~/.ssh/racecast-box.pem racecast@racecast-box-aws      # the AWS box
# 1. Get the repo SOURCE as a tarball (public repo → no auth, no git needed; tools/ + src/
#    are NOT in the shipped binary, so a full source tree is required):
cd ~
curl -L -o iro505.tgz \
  "https://codeload.github.com/jegr78/gt-endurance-racing-broadcast/tar.gz/refs/heads/feat/505-multifeed-429-probe"
tar xzf iro505.tgz
cd gt-endurance-racing-broadcast-feat-505-multifeed-429-probe   # the unpacked tree
#    (once #505 is merged to main, swap the branch for refs/heads/main and the dir suffix.)
# 2. Put the box runtime bin on PATH so yt-dlp's JS challenge (deno) works — WITHOUT it
#    yt-dlp shows a misleading "n challenge solving failed" (per #489):
export PATH=$HOME/racecast/runtime/bin:$PATH
which deno yt-dlp streamlink        # all three must resolve
# 3a. YouTube list — `@channel/live` reliably resolves a live public stream (validated:
#     SkyNews, NASA, AlJazeera all live 2026-07-14). The harness takes the first --n.
cat > urls-yt.txt <<'EOF'
https://www.youtube.com/@SkyNews/live
https://www.youtube.com/@NASA/live
https://www.youtube.com/@aljazeeraenglish/live
EOF
# 3b. Twitch list — big variety channels go on/off, so RE-VERIFY these are live at run time
#     (the one-liner below). These three were live 2026-07-14; swap any that went offline.
cat > urls-tw.txt <<'EOF'
https://www.twitch.tv/jynxzi
https://www.twitch.tv/theprimeagen
https://www.twitch.tv/lirik
EOF
# re-verify Twitch liveness right before the Twitch cells:
for c in jynxzi theprimeagen lirik; do \
  streamlink --stream-url "twitch.tv/$c" best >/dev/null 2>&1 && echo "LIVE $c" || echo "offline $c"; done
```

### 5.2 Run the cells in order (one at a time; honour cool-downs)
```bash
P="python3 tools/multifeed-429-probe.py --out probe-runs"
# 1 sanity
$P --urls urls-yt.txt --n 1 --duration-s 180  --cell-id yt-1-sanity
# 2 baseline 2-feed burst  (fixed-retry recovery falls out of this for free)
$P --urls urls-yt.txt --n 2 --activation burst --cell-id yt-2-burst
#    -> if it threw a 429: WAIT 10 min before the next YT cell.
# 3 does staggered + gentler cadence save 2-feed?
$P --urls urls-yt.txt --n 2 --activation staggered --hls-live-edge 6 --cell-id yt-2-stagger
# 4 POV baseline: 3-feed burst
$P --urls urls-yt.txt --n 3 --activation burst --cell-id yt-3-burst           # 10-min cooldown if 429
# 5 does staggered save 3-feed?
$P --urls urls-yt.txt --n 3 --activation staggered --hls-live-edge 6 --cell-id yt-3-stagger
# 6/7 Twitch escape hatch (no cool-down needed between these)
$P --urls urls-tw.txt --platform twitch --n 2 --cell-id tw-2-burst
$P --urls urls-tw.txt --platform twitch --n 3 --cell-id tw-3-burst
# 8 recovery under exponential backoff (provoke a 429, observe clear time)
$P --urls urls-yt.txt --n 2 --retry-mode backoff --cell-id yt-2-recovery-backoff
```
Each cell prints a one-line verdict and writes `probe-runs/<cell-id>/results.jsonl` + logs.
`Ctrl-C` any cell safely at the first sign of event collision.

### 5.3 Collect + analyse
```bash
python3 tools/multifeed-429-probe.py --summarize probe-runs/*/results.jsonl   # the matrix table
tar czf probe-runs-$(date +%F).tgz probe-runs                                 # keep raw logs
# scp the tarball back to the maintainer machine for the write-up.
```

## 6. Evaluation → the verdict

`--summarize` folds every cell's `results.jsonl` into the matrix table below. Fill §7, then
answer each #489 question with one line drawn from the numbers:

1. **Baseline ceiling** — from `yt-2-burst` / `yt-3-burst`: `time_to_first_429_s` + `sustained_s`.
2. **Activation lever** — compare `*-stagger` vs `*-burst` at the same N: did staggered turn a
   429 into a clean 20-min hold, or just delay it?
3. **Recovery lever** — `yt-2-burst` recovery (fixed) vs `yt-2-recovery-backoff` (backoff):
   `recovery_s`. → the concrete backoff/cadence numbers handed to #489.
4. **Twitch escape hatch** — did `tw-2-burst` / `tw-3-burst` hold 20 min clean (429=no)?
5. **Verdict for sustained multi-feed** — with the best of {activation, Twitch}, is sustained
   2-feed / 3-feed viable on the datacenter IP, or is proxy/home egress (#489 lever 5) required?

Also confirm the **public-live ≈ unlisted-live** assumption from the baseline (§2 of the issue):
if `@channel/live` public pulls throttle the same way the N24 unlisted feeds did, the reproduction
is faithful; if a 1-feed public pull already misbehaves, fall back to arranged distinct test streams.

## 7. Results (2026-07-14 run, box eu-central-1a)

### FINAL MODEL (most accurate — supersedes everything below) — the AWS block is INTERMITTENT

A faithful multi-region re-test (`tools/cloud/provision-iptest.py` + `iptest-regions.py`: a fresh
Ubuntu 24.04 box per region, real `racecast install-tools` toolchain, the real `racecast cookies`
jar, the exact relay resolve command) resolved YouTube **cleanly in ALL three AWS regions — including
eu-central-1, the same region as the "blocked" box** (`18.197.x`, `108.131.x`, `44.222.x` all
RESOLVED, ~22:00). Yet ~2-3 h earlier the box's IP **and 4 fresh EIPs** all bot-checked. Same region,
different time → the YouTube AWS-range bot-check is **intermittent / rolling (toggles over hours), NOT
a permanent block.** This fits every observation: the weekend event worked (block mostly off, one
transient 429 blip the retries healed); early-evening testing hit a multi-hour "on" window (box + EIPs
+ public + VOD + live all bot-checked, while residential + GCP — not subject to the AWS-range check —
resolved); late evening the block was off again (fresh AWS boxes resolve).

**Event implication:** AWS is **usable**, not dead. YouTube pulls work when the block is off (apparently
most of the time). The risk is **intermittent feed failures during "on" windows** — a *reliability*
problem, exactly what **#489** (429/timeout backoff + a standby fallback egress that engages only during
a block window) is meant to harden. No cloud move, no residential/home egress, no paid proxy needed.

**Residual uncertainty (honest):** most likely time-based; a small chance the freshly-exported cookies
helped vs. the earlier jar. Resolved by *monitoring the resolve status over the next days* to
characterise the on/off duty cycle before the event. The GCP / po_token / residential analysis below is
**superseded** — it was a snapshot taken during one "on" window and wrongly generalised to "AWS is
permanently blocked."

### RESOLUTION (earlier, partially superseded) — during the "on" window it looked AWS-specific; GCP resolved

The block is **AWS-specific, not datacenter-generic.** A throwaway GCP e2-small in europe-west4
(`34.90.124.118`) resolved the **same currently-live unlisted commentator stream** (`0NY_4irYQu0`)
**and** a public stream **cleanly, without cookies, without POT, without a proxy** — while AWS
eu-central-1 (4 IPs) bot-checks everything. This matches the #395 finding that GCP passed the
YouTube bot-check, and pins the cause: the **GCP→AWS migration** put the box in YouTube's
AWS-range bot-blocklist. The gold-standard proof chain (all on the box's AWS IP vs a residential
Mac vs GCP, same tooling): live unlisted → AWS ❌ / Mac ✅ / GCP ✅; and the box's own cookie file
resolves fine from the Mac → **100% the egress IP, not cookies/yt-dlp/POT.**

**Two clean fixes (both keep the cloud model — no home-egress, no residential proxy):**
1. **Move the box back to GCP** — GCP IPs resolve YT directly, no proxy needed. Caveat: GCP GPU
   (T4/L4) capacity was the likely reason for the AWS move (L4 stockout, see the capacity memo);
   verify GPU stock for the event window.
2. **Keep the AWS GPU box + a cheap GCP micro as the YouTube-egress SOCKS proxy** — the AWS box
   routes its YT feed pulls through the GCP micro (clean IP); Twitch stays direct. Keeps the GPU on
   AWS (no stockout risk), no home-upload bottleneck (GCP→AWS is datacenter bandwidth). Cost: GCP
   egress ~$0.09-0.12/GB → a 24 h 2-feed event ≈ 1.3 TB ≈ **~$110-150** (half at 720p-robust).
   Relay wiring is minimal (proxy env / per-feed `--proxy`); needs a sustained-pull validation.

**DECISION: chosen fix TBD (Jens).** The earlier "residential egress" analysis below is superseded
by this — residential/home was only needed while we thought ALL datacenter IPs were blocked; GCP
shows they are not.

### HEADLINE — YouTube is blocked at the IP level, *upstream* of the concurrency question

On the AWS box, the YouTube resolve fails with **"Sign in to confirm you're not a bot"** on
**every** datacenter IP tried, regardless of cookies or yt-dlp version:

- **4 distinct AWS eu-central-1 IPs** tested (1 auto-assigned `18.185.47.10` + 3 fresh Elastic
  IPs `63.186.219.163`, `63.178.181.38`, `18.199.242.224`): **all bot-blocked**.
- Fresh `racecast cookies firefox` export ("logged-in session detected"): **no change**.
- Latest **yt-dlp 2026.07.04** (vs the pinned 2026.06.09), **with AND without** cookies: no change.
- **Control:** the *same* yt-dlp 2026.07.04 on a **residential IP** (maintainer Mac) resolves the
  same stream **cleanly, without cookies**, at the same moment.

→ This is the signature of YouTube's **datacenter-IP `po_token` enforcement**: residential IPs are
exempt; datacenter IPs require a proof-of-origin token that yt-dlp cannot mint on its own (cookies
do not substitute). The weekend 24 h event worked because that IP/session was not yet enforced;
enforcement has since tightened. It is **not** a per-IP lottery (4 IPs, one region, all fail) and
**not** fixable by cookies, yt-dlp version, or rolling IPs.

**Consequence:** the YouTube **concurrency arm** (2-/3-feed 429 — the original #505 question) could
**not run** — we cannot resolve even ONE YouTube feed from an AWS IP. The bottleneck moved *upstream*
from "per-IP concurrency throttle" to "per-IP bot-block." **Event-critical:** with the next event in
3 days, YouTube commentator feeds will **not** resolve on the box as-is.

### Twitch arm — completed, clean

| cell | platform | N | activation | 429? | sustained | agg Mbps | per-pull |
|---|---|---|---|---|---|---|---|
| `tw-2-burst` | twitch | 2 | burst | **no** | 1200 s (full) | 15.07 | 7.40 / 7.66 |
| `tw-3-burst` | twitch | 3 | burst | **no** | 1200 s (full) | 21.24 | 7.40 / 7.45 / 6.39 |

Both cells: 1 attempt per pull, zero retries, zero 429, held the full 20-min window. Twitch needs
no cookies and no yt-dlp (Streamlink Twitch plugin resolves in-process), so it sidesteps **both** the
bot-block and the per-IP googlevideo throttle.

### Verdicts (mapped to #489)

1. **Baseline YT ceiling:** UNMEASURABLE on AWS — the IP is bot-blocked before concurrency is reached.
2. **Activation lever (staggered/cadence):** not reached (YT blocked).
3. **Recovery lever (fixed vs backoff):** not reached (YT blocked).
4. **Twitch escape hatch:** **CONFIRMED** — sustained 2- and 3-feed hold 20 min clean.
5. **Sustained multi-feed on a datacenter IP:** **YouTube NOT viable as-is** (IP bot-block, deeper
   than the 429 this issue set out to measure); **Twitch viable**. YouTube from the box needs a
   `po_token` provider, a residential/proxy egress (#489 lever 5), or the home-producer fallback.
6. **Public-live ≈ unlisted-live assumption:** not reached for YT.

### po_token provider — TESTED on the box, does NOT overcome the IP block

The standard datacenter-YouTube fix was set up and tested live (2026-07-14):
`bgutil-ytdlp-pot-provider` v1.3.1 — Node 22 (the server needs Node ≥20; box apt ships 18) running
`build/main.js` on `127.0.0.1:4416`, plus the pip POT plugin in `~/.config/yt-dlp/plugins/`. With
`--extractor-args "youtube:player_client=web;fetch_pot=always"` the provider **correctly mints a POT**
(server logs `Generated IntegrityToken` / `Generating POT`; yt-dlp logs `Retrieved a player PO Token`).
**Yet the resolve still returns "Sign in to confirm you're not a bot."** A sweep of **8 player clients**
(`tv, ios, android, mweb, tv_embedded, web_embedded, android_vr, web_creator`), with and without
cookies, with and without POT: **every one bot-checks.**

→ **Conclusion: this AWS IP is bot-blocked *below* the POT layer.** A valid POT does not help; the
block is on the IP itself. No yt-dlp-side lever (cookies, yt-dlp version, POT, player-client, IP
rolling across 4 IPs) resolves it. The POT infrastructure is correct and would likely work behind a
clean IP — but it cannot rescue a blocked one.

### Residential egress — PROVEN to fully unblock YouTube (2026-07-14)

Tested by routing the box's YouTube traffic through the maintainer's **home Mac** (residential IP
`77.181.212.231`) over the tailnet via a reverse SSH SOCKS tunnel (`ssh -N -R 1080 box`, box uses
`--proxy socks5://127.0.0.1:1080`). Result: **the resolve succeeds cleanly — no POT, no cookies, no
client override needed.** Residential egress removes the block entirely, and residential IPs are not
per-IP-throttled either, so **both** #505 barriers (bot-block AND concurrency 429) vanish behind a
residential IP.

**Operational caveat:** the resolved googlevideo URL is **IP-bound**, so the actual video *pull* must
also exit through the residential IP. Routing through home therefore carries the **full YouTube video
bandwidth** through the home connection (box ← home ← googlevideo) — i.e. it is functionally
**producing the YouTube feeds from home**, with the box only hosting OBS. A commercial **residential
proxy** keeps the box autonomous but is paid **per-GB** — expensive for sustained multi-feed video.

### Recommended next steps (→ #489 + event prep, urgent)

- **YouTube from the cloud box requires residential egress** (#489 lever 5) — PROVEN above. Two forms:
  route through home (free, but that is ~home-producing the YouTube feeds), or a paid residential
  proxy (autonomous box, but per-GB cost is steep for video). POT is **not** needed once egress is
  residential.
- **OR the home-producer fallback** for **YouTube-heavy** events. **Twitch-heavy** events run fine on
  the box today (Twitch needs neither cookies, yt-dlp, nor a clean IP).
- **Before the next event (3 days):** decide the YouTube egress (proxy vs. home). The box as-is cannot
  pull YouTube feeds regardless of tooling.
- The **YT concurrency arm** (the original #505 numbers) is still open — re-runnable via the ready
  harness once the box has a resolving egress (the harness needs `--cookies` + the POT provider +
  `--extractor-args`). It could not be reached because the block sits upstream of concurrency.

### po_token provider — reusable setup (on the box, for the residential-proxy path)

Left installed on the box for a future clean-egress run: Node 22 at `~/node22/bin/node`, provider at
`~/bgutil-ytdlp-pot-provider-1.3.1/server` (`node build/main.js` → `:4416`), plugin at
`~/.config/yt-dlp/plugins/bgutil`. Relay integration (when egress is clean) = run the provider +
add `--extractor-args "youtube:player_client=web;fetch_pot=always"` to the resolve.

### Operational note — no Elastic IP

The box has **no Elastic IP**; every stop/start draws a new auto-assigned public IP. That is a
separate liability (a fresh IP could be worse for reputation/geo), but it is **not** the cause here —
all four AWS IPs failed identically. An EIP would only give a *stable* IP, not an *un-blocked* one.

## 8. Validation already done (2026-07-14, UTM arm64 Ubuntu VM + local Mac)

Before any box run, the whole setup/run/analyse pipeline was validated on the UTM Ubuntu VM
(Python 3.14) — the "cheapest validation first" gate, no cloud box touched:
- Unit tests (`tests/test_multifeed_probe.py`) pass on the VM.
- Dry-run loads the real relay module + prints production flags (needs the full `src/` checkout —
  the `chat_admin` import proved the relay is not standalone; that is now a documented setup step).
- Real concurrent 2-puller run: both `@channel/live` streams resolved (deno on PATH → no "n
  challenge" error), both flowed bytes with independent per-pull accounting, no 429 (residential
  IP), clean dual teardown, correct `results.jsonl` + `--summarize`.
- **Ctrl-C safety:** SIGINT → clean exit, **0 streamlink orphans**, results still written.
- **Off-event guard:** a live relay PID → refuses with exit 3.

The box run is therefore purely the *measurement* — the scripts, commands, logs, and analysis are
already known-good.

## 9. Concurrency arm REACHED (2026-07-15, AWS m5.large eu-central-1, block "off")

The intermittent AWS bot-check was **off** this run, so YouTube resolved and the original #505
concurrency question was finally measured with **faithful GT7 bitrate** (verified live 1080p /
5552 kbps streams, not low-bitrate 24/7 filler). Two harnesses ran back-to-back on the same IP.

### 9a. Static concurrency matrix (`multifeed-429-probe.py`)

| cell | N | activation | t→first-429 | recovery | agg Mbps | valid? |
|---|---|---|---|---|---|---|
| yt-2-burst | 2 | burst | **144s** | 48s | 4.84 | ✅ |
| yt-2-stagger | 2 | staggered | **109s** | 1s | 3.72 | ✅ |
| yt-3-burst | 3 | burst | **93s** | 225s | 2.86 | ✅ |
| yt-2-recovery-backoff | 2 | burst/backoff | — | — | 277.90 | ❌ VOD-race |
| yt-3-stagger | 3 | staggered | — | — | 110.95 | ❌ VOD-race |

Two "recovery" cells used a long-DVR "24/7" URL that streamlink pulled at >1× (agg >100 Mbps) →
never reached live-edge bitrate → invalid, discarded. The three valid cells stand: **2 and 3
concurrent GT7 feeds throttle within 1.5–2.5 min; more feeds = faster; staggering does not help.**

### 9b. Driver-swap scenario (`multifeed-scenario-probe.py`, the operational timeline)

Simulates the real broadcast: single 1080p → 2×720p splitscreen for the driver swap → back to
single 1080p → single 1080p + 720p POV. Three fresh live GT7 streams (A/B/POV).

| phase | feeds | agg Mbps | 429? | first-429 into phase | note |
|---|---|---|---|---|---|
| 1 | A 1080p (single) | 4.33 | yes | 147s | 2 events, feed **survived** (kept ~5.4 Mbps) |
| 2 | A+B **2×720p** | **1.69** | yes | **49s** | **worst phase — both feeds 0 bytes for ~65s**, then trickle ~1 Mbps each |
| 3 | B 1080p (single) | 4.62 | **no** | — | **clean 180s** |
| 4 | B 1080p + POV 720p | 4.96 | yes | 123s | clean ~120s, then both decay (B 4.5→2.3, POV 5.6→2.6) |

### 9c. Model (supersedes the "bandwidth-driven" single-factor read)

1. **Single feed (1080p ~5.5 Mbps) is viable.** May catch an occasional transient 429 that
   streamlink retries through **without losing the feed** (phase 1), or run fully clean (phase 3).
   Matches production reality: normal single-commentator broadcasts work.
2. **Any TWO concurrent feeds throttle within ~1–2 min, hard — and dropping the splitscreen to
   2×720p does NOT prevent it.** Phase 2 (2×720p) was the *worst* phase of all: zero bytes on both
   feeds for ~65s. The dominant factor is the **number of concurrent googlevideo connections from
   the IP**, not per-feed resolution (720p vs 1080p barely moved the threshold).
3. **The throttle is stateful/cumulative per-IP with a recovery time.** Phase 2 got **zero grace**
   because it followed an already-429'd phase 1; phase 4 got **~120s of grace** because it followed
   a clean single-feed phase 3. A "fresh" IP can survive 2×720p for a few minutes (cf. the aborted
   `yt-2-robust` cell that held 2×720p ~5 min clean from a rested IP) — but that is fragile and
   cannot be relied on live.

### 9d. Operational verdict for the driver-swap

**The "reduce both feeds to 720p during the 5–10 min splitscreen" mitigation is INSUFFICIENT on the
datacenter IP by itself** — a 5–10 min 2-feed splitscreen will stutter/black out, worse if the IP is
already throttled. What actually helps, in order:
- **Minimise concurrent-feed time** — keep the A+B overlap as short as mechanically possible and
  deactivate the outgoing feed immediately (directly motivates #491/#492 two-stage scheduling +
  fast deactivate, and #489 backoff).
- **Never enter a two-feed phase right after the IP is already throttled** (stateful budget).
- **Durable fix = never have two concurrent googlevideo pulls**: prefer continuation handovers
  (`/next`, label-only on the on-air feed) over concurrent A/B, OR residential/proxy egress for the
  multi-feed moments (§7). On a **residential IP none of this throttle appears** — the whole arm is
  a datacenter-IP property.

Artifacts: `summary.json` + `timeline.jsonl` per cell/scenario on the box (`~/iro505/{probe,scenario}-runs/`),
copied to the session scratchpad. Box **stopped** (not terminated) 2026-07-15 for a fast re-run.
