#!/usr/bin/env bash
# provision-iptest.sh: MINIMAL test-box provisioning, derived from provision.sh, for the
# #505 YouTube-egress-IP investigation. It stands up JUST enough to answer one question
# faithfully: does THIS box's egress IP get YouTube-bot-blocked the way the AWS event box
# does?  It reuses the SAME layers a real box uses: a dedicated `racecast` user, the
# racecast binary, and `racecast install-tools` (yt-dlp / streamlink / ffmpeg / deno). Plus
# it copies the REAL YT cookies, then resolves the test URL(s) with the exact relay command
# (`ytdlp_resolve_cmd`: yt-dlp -g -f b[height<=1080]/b --cookies … -- URL).
#
# EVERYTHING not needed to resolve/pull a feed is STRIPPED vs provision.sh: no NVIDIA/GPU,
# no xfce desktop, no Firefox, no RustDesk, no OBS/install-apps, no Tailscale join, no
# prepare-event. So it runs on a cheap CPU box (t3.small) in seconds, not a GPU box.
#
# Ubuntu 24.04, run as root:  sudo ./provision-iptest.sh
#
# Env:
#   RACECAST_TAG    racecast release to install (default: latest). Use `preview-main`
#                   only if a stable lacks the streamlink-venv fix and you also test PULLS.
#   RACECAST_USER   event user to create/target (default: racecast, same as provision.sh).
#   IPTEST_COOKIES  path on THIS box to the yt-cookies.txt to copy in (default /tmp/yt-cookies.txt).
#                   The orchestrator scp's the real box cookies there BEFORE running this.
#   IPTEST_URLS     space-separated YouTube URLs to resolve (default: one public live URL).
#                   Pass the real `youtube.com/live/<id>` commentator/VOD URLs to be exact.
#   IPTEST_PULL     1 = after a successful resolve, do a 10 s streamlink byte-pull too
#                   (proves the full feed path, not just the resolve). Default 0.
#   IPTEST_HARNESS  1 = also deploy the #505 concurrency harness (branch source tree, so
#                   tools/multifeed-429-probe.py + src/ are present) as the racecast user,
#                   ready for tools/cloud/iptest-505-run.sh. Default 0.
#   IPTEST_HARNESS_REF   git branch/tag of the source to fetch for the harness
#                        (default: feat/505-multifeed-429-probe).
set -euo pipefail

RACECAST_REPO="jegr78/gt-racing-broadcast"
RACECAST_USER="${RACECAST_USER:-racecast}"
COOKIES_SRC="${IPTEST_COOKIES:-/tmp/yt-cookies.txt}"
URLS="${IPTEST_URLS:-https://www.youtube.com/@SkyNews/live}"
YTDLP_FMT='b[height<=1080]/b'   # mirrors racecast-feeds.py YTDLP_FORMAT exactly

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '  \033[1;33m!!\033[0m  %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = 0 ] || { echo "provision-iptest.sh must run as root (sudo)" >&2; exit 1; }

# --- dedicated event user (verbatim intent from provision.sh: one operational account) ---
if ! getent passwd "$RACECAST_USER" >/dev/null; then
  useradd -m -s /bin/bash "$RACECAST_USER"
  ok "created event user $RACECAST_USER ($(getent passwd "$RACECAST_USER" | cut -d: -f6))"
fi
USER_HOME="$(getent passwd "$RACECAST_USER" | cut -d: -f6)"
USER_GROUP="$(id -gn "$RACECAST_USER")"

# Give racecast the SAME SSH key the box was launched with, so you log in DIRECTLY as
# `racecast` (mirrors the real box, where OS-login/guest-agent does this). No
# `ssh ubuntu@… sudo -u racecast` dance. Copy from the invoking login user's authorized_keys.
SSH_SRC_USER="${SUDO_USER:-ubuntu}"
SRC_AK="$(getent passwd "$SSH_SRC_USER" 2>/dev/null | cut -d: -f6)/.ssh/authorized_keys"
if [ -f "$SRC_AK" ]; then
  install -d -o "$RACECAST_USER" -g "$USER_GROUP" -m 0700 "$USER_HOME/.ssh"
  install -m 0600 -o "$RACECAST_USER" -g "$USER_GROUP" "$SRC_AK" "$USER_HOME/.ssh/authorized_keys"
  ok "racecast SSH access enabled (key from $SSH_SRC_USER). 'ssh racecast@<ip>' works directly"
else
  warn "no authorized_keys at $SRC_AK. Reach racecast via 'ssh $SSH_SRC_USER@<ip> sudo -u racecast …'"
fi

# --- 1/4  APT base (the subset install-tools' streamlink venv needs; no desktop/GPU pkgs) ---
log "1/4  APT base"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y curl python3 python3-venv python3-pip ca-certificates wget gnupg >/dev/null
ok "base packages present"

# --- 2/4  racecast binary (into the racecast user's home, user-owned tree, as provision.sh) ---
log "2/4  racecast binary"
if have racecast; then
  ok "racecast already on PATH ($(sudo -u "$RACECAST_USER" racecast --version 2>/dev/null | head -1))"
else
  case "$(uname -m)" in
    aarch64|arm64) asset="racecast-linux-arm64.tar.gz" ;;
    *)             asset="racecast-linux.tar.gz" ;;
  esac
  tag="${RACECAST_TAG:-latest}"
  if [ "$tag" = "latest" ]; then
    url="https://github.com/${RACECAST_REPO}/releases/latest/download/${asset}"
  else
    url="https://github.com/${RACECAST_REPO}/releases/download/${tag}/${asset}"
  fi
  curl -fsSL "$url" -o /tmp/racecast.tar.gz
  tar -xzf /tmp/racecast.tar.gz -C "$USER_HOME"
  chown -R "$RACECAST_USER:$USER_GROUP" "$USER_HOME"
  ln -sf "$USER_HOME/racecast" /usr/local/bin/racecast
  ok "racecast installed ($(sudo -u "$RACECAST_USER" racecast --version 2>/dev/null | head -1))"
fi

# --- 3/4  racecast install-tools: yt-dlp / streamlink / ffmpeg / deno (the feed toolchain) ---
log "3/4  racecast install-tools (yt-dlp / streamlink / ffmpeg / deno). As $RACECAST_USER"
sudo -u "$RACECAST_USER" -H racecast install-tools \
  || warn "install-tools reported issues — see the resolve step below"
RTBIN="$USER_HOME/runtime/bin"
for t in yt-dlp streamlink deno; do
  [ -x "$RTBIN/$t" ] && ok "$t present ($("$RTBIN/$t" --version 2>/dev/null | head -1))" \
                     || warn "$t MISSING at $RTBIN — feed path will fail"
done
# streamlink MUST be the install-tools venv build (>=8.2.0 for --http-cookies-file). apt's
# 6.6.2 is too old and every cookie'd YouTube feed would 403: install-tools prints an
# "apt too old" note WHILE it builds the venv; THIS is the definitive post-check.
if "$RTBIN/streamlink" --help 2>/dev/null | grep -q -- '--http-cookies-file'; then
  ok "streamlink supports --http-cookies-file (venv build OK, the apt-too-old note above is expected)"
else
  warn "streamlink lacks --http-cookies-file: YouTube pulls will 403 (venv build failed; re-run install-tools)"
fi

# --- copy the REAL YT cookies (this is the whole point: mirror the relay's auth context) ---
log "copy YT cookies"
if [ -f "$COOKIES_SRC" ]; then
  install -d -o "$RACECAST_USER" -g "$USER_GROUP" -m 0755 "$USER_HOME/runtime"
  install -m 0600 -o "$RACECAST_USER" -g "$USER_GROUP" "$COOKIES_SRC" "$USER_HOME/runtime/yt-cookies.txt"
  ok "cookies copied -> $USER_HOME/runtime/yt-cookies.txt ($(wc -l <"$COOKIES_SRC") lines)"
else
  warn "no cookies at $COOKIES_SRC. Resolve will run WITHOUT --cookies (still a valid IP test,"
  warn "  but not identical to the relay; stage the box's yt-cookies.txt there to be exact)"
fi

# --- 4/4  the actual test: resolve each URL from THIS box's IP, exactly as the relay does ---
log "4/4  resolve test from this box's egress IP"
COOKIE_ARG=""
[ -f "$USER_HOME/runtime/yt-cookies.txt" ] && COOKIE_ARG="--cookies $USER_HOME/runtime/yt-cookies.txt"
echo -n "  egress IP (as the internet sees it): "; curl -s https://api.ipify.org; echo
for U in $URLS; do
  echo "  --- $U ---"
  out="$(sudo -u "$RACECAST_USER" -H env PATH="$RTBIN:$PATH" \
        "$RTBIN/yt-dlp" -g -f "$YTDLP_FMT" --no-warnings --no-playlist $COOKIE_ARG -- "$U" 2>&1 | head -1)"
  case "$out" in
    http*)          echo "  RESOLVED  -> ${out:0:80}…" ;;
    *"not a bot"*)  echo "  BOT-CHECK -> IP is YouTube-bot-blocked" ;;
    *)              echo "  OTHER     -> ${out:0:110}" ;;
  esac
  if [ "${IPTEST_PULL:-0}" = "1" ] && [ "${out:0:4}" = "http" ]; then
    echo -n "  pull 10s: "
    bytes="$(sudo -u "$RACECAST_USER" -H env PATH="$RTBIN:$PATH" timeout 10 \
            "$RTBIN/streamlink" --stdout --http-header "User-Agent=Mozilla/5.0" -- "$out" best 2>/dev/null | wc -c)"
    echo "$bytes bytes"
  fi
done
# --- optional: deploy the #505 concurrency harness source (for tools/cloud/iptest-505-run.sh) ---
if [ "${IPTEST_HARNESS:-0}" = "1" ]; then
  log "deploy #505 harness source (as $RACECAST_USER)"
  ref="${IPTEST_HARNESS_REF:-feat/505-multifeed-429-probe}"
  if sudo -u "$RACECAST_USER" -H bash -lc "
        cd ~ && curl -fsSL -o iro505.tgz 'https://codeload.github.com/${RACECAST_REPO}/tar.gz/refs/heads/${ref}' \
        && tar xzf iro505.tgz \
        && ln -sfn \"\$(tar tzf iro505.tgz | head -1 | cut -d/ -f1)\" iro505 \
        && test -f iro505/tools/multifeed-429-probe.py"; then
    ok "harness at $USER_HOME/iro505 (ref $ref). Src/ + tools/ present"
  else
    warn "harness deploy failed (ref $ref)"
  fi
fi

log "provision-iptest.sh complete. Resolve verdict(s) above are the #505 answer for this IP"
