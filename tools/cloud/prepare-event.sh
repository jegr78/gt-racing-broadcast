#!/usr/bin/env bash
# prepare-event.sh: on-box, per-event racecast preparation for the cloud GPU box.
# Runs the recurring event-prep sequence to "ready" (no go-live) and reports which
# one-time manual setup is still missing. Companion to tools/cloud/provision.sh.
# Run as the `racecast` user on the box:  ./prepare-event.sh <league> [flags]
set -uo pipefail

RACECAST_USER="${RACECAST_USER:-racecast}"

LEAGUE=""
NO_TWITCH=0
NO_SPEEDTEST=0
NO_UPDATE=0

usage() {
  cat <<'EOF'
Usage: ./prepare-event.sh <league> [--no-twitch] [--no-speedtest] [--no-update]

  <league>        racecast profile name for this event (required; must be imported)
  --no-twitch     skip the Twitch cookie/auth refresh (default: run it alongside YouTube)
  --no-speedtest  skip the bandwidth test (default: run it)
  --no-update     skip the racecast binary self-update (default: run it, with preview guard)

Prepares the box to "ready"; it never goes live (no `racecast event start`).
EOF
}

log()  { printf '\033[1;34m[prepare]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; SOFT_WARNINGS=$((SOFT_WARNINGS + 1)); }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

SOFT_WARNINGS=0

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --no-twitch)    NO_TWITCH=1 ;;
      --no-speedtest) NO_SPEEDTEST=1 ;;
      --no-update)    NO_UPDATE=1 ;;
      -h|--help)      usage; exit 0 ;;
      -*)             usage; die "unknown flag: $1" ;;
      *)              if [ -z "$LEAGUE" ]; then LEAGUE="$1"; else die "unexpected argument: $1"; fi ;;
    esac
    shift
  done
}

is_league_imported() {  # $1 = profile name
  racecast profile list 2>/dev/null | awk '{print $NF}' | grep -qxF "$1"
}

is_preview_version() {  # $1 = version string
  case "$1" in *preview*) return 0 ;; *) return 1 ;; esac
}

have_tty() { [ -t 0 ]; }

do_update() {
  if [ "$NO_UPDATE" = 1 ]; then log "update: skipped (--no-update)"; return 0; fi
  local cur; cur="$(racecast --version 2>/dev/null)"
  if is_preview_version "$cur"; then
    if have_tty; then
      printf '\033[1;33m[prepare]\033[0m Preview build '\''%s'\'' installed (kept for the Linux fixes).\n' "$cur"
      read -r -p "         Update to latest STABLE (loses the preview fixes)? [y/N] " ans
      case "$ans" in
        [yY]|[yY][eE][sS]) racecast update || die "racecast update failed" ;;
        *) log "update: keeping preview build '$cur'" ;;
      esac
    else
      log "update: preview build '$cur' kept (no TTY to confirm). Run interactively, or 'racecast update' to move to stable."
    fi
  else
    log "update: stable build '$cur'. Checking for a newer stable"
    racecast update || die "racecast update failed"
  fi
}

resolve_root() {
  local bin; bin="$(command -v racecast)" || die "racecast not on PATH. Is this the racecast user on a provisioned box?"
  ROOT="$(dirname "$(readlink -f "$bin")")"
  RUNTIME="$ROOT/runtime"
  PROFILES="$ROOT/profiles"
}

PREFLIGHT_RC=0

run_prep_sequence() {
  log "activating profile '$LEAGUE'"
  racecast profile use "$LEAGUE" || die "racecast profile use '$LEAGUE' failed"

  log "refreshing YouTube cookies"
  racecast cookies firefox || warn "YouTube cookie refresh failed. Check the box's Firefox is signed in to YouTube"
  if [ "$NO_TWITCH" = 1 ]; then
    log "Twitch cookies: skipped (--no-twitch)"
  else
    log "refreshing Twitch cookies"
    racecast cookies twitch firefox || warn "Twitch cookie refresh failed. Sign in to Twitch in the box's Firefox, or pass --no-twitch"
  fi

  log "refreshing broadcast graphics"; racecast graphics || warn "graphics refresh failed (OBS shows black for missing files)"
  log "refreshing intro/outro media"; racecast media || warn "media refresh failed"
  log "refreshing brand logos";       racecast brands || warn "brands refresh failed"

  if [ "$NO_SPEEDTEST" = 1 ]; then
    log "speedtest: skipped (--no-speedtest)"
  else
    log "running bandwidth speedtest"; racecast speedtest || warn "speedtest failed (network); preflight bandwidth check may be stale"
  fi

  log "forcing a clean relay state (stop + free feed ports)"
  racecast relay stop >/dev/null 2>&1 || true
  racecast freeport --force >/dev/null 2>&1 || true

  log "running preflight"
  racecast preflight
  PREFLIGHT_RC=$?
}

sanity_guard() {
  [ "$(id -un)" = "$RACECAST_USER" ] || die "run as the '$RACECAST_USER' user (current: '$(id -un)'). Try: sudo -iu $RACECAST_USER ./prepare-event.sh $*"
  command -v racecast >/dev/null 2>&1 || die "racecast not on PATH"
  [ -n "$LEAGUE" ] || { usage; die "missing <league>"; }
  is_league_imported "$LEAGUE" || die "profile '$LEAGUE' is not imported. Onboard it first (see tools/cloud/README.md §4): racecast profile import <bundle>.zip"
}

# green/red readiness markers
_ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
_bad()  { printf '  \033[1;31mMISS\033[0m %s\n' "$*"; }
_note() { printf '  \033[1;33m--\033[0m   %s\n' "$*"; }

tailnet_joined() { tailscale ip -4 2>/dev/null | grep -qE '^100\.'; }

league_uses_discord() { grep -q '^DISCORD_CLIENT_ID=' "$PROFILES/$LEAGUE/profile.env" 2>/dev/null; }

readiness_report() {
  local fail=0
  log "readiness: one-time setup that neither provision.sh nor this script can do:"

  # go-live prerequisites (block the exit code)
  if tailnet_joined; then _ok "tailnet joined ($(tailscale ip -4 2>/dev/null | grep -E '^100\.' | head -1))"
  else _bad "tailnet NOT joined. Run:  sudo tailscale up --ssh --hostname racecast-box"; fail=1; fi

  if [ -s "$RUNTIME/$LEAGUE/GT_Racing_Endurance.import.json" ]; then _ok "OBS scene collection localized for '$LEAGUE'"
  else _bad "OBS collection not localized. Run 'racecast setup', then import it into OBS over RustDesk (once per league)"; fail=1; fi

  # advisory (surfaced, do NOT block the exit code)
  if [ -s "$RUNTIME/yt-cookies.txt" ]; then _ok "YouTube cookies present"
  else _note "no YouTube cookies yet. Sign in to YouTube in the box's Firefox, then re-run"; fi

  if league_uses_discord; then
    if [ -s "$RUNTIME/discord-rpc-token.json" ] || find "$RUNTIME" -name discord-rpc-token.json -type f 2>/dev/null | grep -q .; then
      _ok "Discord voice token cached"
    else
      _note "league uses Discord but no voice token. Run 'racecast discord join' once over RustDesk"
    fi
  fi

  if [ "$PREFLIGHT_RC" -eq 0 ]; then _ok "preflight passed"
  else _bad "preflight reported issues (exit $PREFLIGHT_RC). See the preflight output above"; fail=1; fi

  echo
  if [ "$fail" -eq 0 ]; then
    log "READY: go live via Control Center 'Start event' or:  racecast event start"
    [ "$SOFT_WARNINGS" -gt 0 ] && warn "$SOFT_WARNINGS soft warning(s) above. Review before going live"
    exit 0
  else
    die "NOT ready: fix the MISS lines above, then re-run:  ./prepare-event.sh $LEAGUE"
  fi
}

main() {
  parse_args "$@"
  resolve_root
  sanity_guard "$@"
  log "profile '$LEAGUE' found; install root $ROOT"
  do_update
  run_prep_sequence
  readiness_report
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
