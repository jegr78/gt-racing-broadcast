#!/usr/bin/env bash
# gcp-box.sh. Start / stop / status control wrapper for the GCP GPU box.
#
# Runs on YOUR laptop (not the box). A thin wrapper around the `gcloud` CLI so you
# never have to remember the instance name or zone. Cost control: STOP the box
# after every event: a running g2-standard-8 (L4) bills by the hour, a stopped
# ("TERMINATED") one only its boot disk. The tailnet IP is stable across
# stop/start (Tailscale), so the box keeps its `100.x` address after a restart.
#
# Usage:
#   tools/cloud/gcp-box.sh status         # cloud state + type + external IP (default)
#   tools/cloud/gcp-box.sh start          # start (gcloud waits until RUNNING)
#   tools/cloud/gcp-box.sh stop           # stop (idle = boot disk only)
#   tools/cloud/gcp-box.sh ip             # print the current external IP (running only)
#   tools/cloud/gcp-box.sh ssh [args…]    # gcloud SSH in as racecast
#
# Config (env overrides, never commit machine-specific values):
#   GCP_BOX_NAME     instance name         (default racecast-box)
#   GCP_BOX_ZONE     zone                  (default europe-west4-b)
#   GCP_BOX_PROJECT  project               (default: your active gcloud project)
#   GCP_BOX_SSH_USER login user            (default racecast)
set -euo pipefail

NAME="${GCP_BOX_NAME:-racecast-box}"
ZONE="${GCP_BOX_ZONE:-europe-west4-b}"
SSH_USER="${GCP_BOX_SSH_USER:-racecast}"
PROJECT_ARG=()
[ -n "${GCP_BOX_PROJECT:-}" ] && PROJECT_ARG=(--project "$GCP_BOX_PROJECT")

die() { echo "gcp-box: $*" >&2; exit 1; }
command -v gcloud >/dev/null 2>&1 || die "the 'gcloud' CLI is not installed (see cloud SDK)"

describe() { gcloud compute instances describe "$NAME" --zone "$ZONE" ${PROJECT_ARG[@]+"${PROJECT_ARG[@]}"} \
               --format="value($1)" 2>/dev/null; }

state()       { describe status; }
external_ip() { describe 'networkInterfaces[0].accessConfigs[0].natIP'; }

status() {
  local st ty ip
  st="$(state)" || die "cannot reach GCP (check 'gcloud auth login' / project)"
  [ -z "$st" ] && die "instance $NAME not found in $ZONE"
  ty="$(describe 'machineType.basename()')"
  echo "GCP box $NAME ($ty, $ZONE)"
  echo "  state:       $st"          # RUNNING | TERMINATED (= stopped) | STOPPING | …
  if [ "$st" = "RUNNING" ]; then
    ip="$(external_ip)"
    echo "  external IP: ${ip:-<none>}"
  fi
  echo "  ssh:         gcloud compute ssh $SSH_USER@$NAME --zone $ZONE   (tailnet 100.x is stable)"
}

start() {
  local st; st="$(state)"
  if [ "$st" = "RUNNING" ]; then echo "already running."; status; return 0; fi
  echo "starting $NAME …"          # gcloud start is synchronous (waits for RUNNING)
  gcloud compute instances start "$NAME" --zone "$ZONE" ${PROJECT_ARG[@]+"${PROJECT_ARG[@]}"} >/dev/null
  status
  echo "note: the desktop session + Tailscale take ~1 more minute after RUNNING."
}

stop() {
  local st; st="$(state)"
  if [ "$st" = "TERMINATED" ]; then echo "already stopped (TERMINATED)."; return 0; fi
  echo "stopping $NAME …"
  gcloud compute instances stop "$NAME" --zone "$ZONE" ${PROJECT_ARG[@]+"${PROJECT_ARG[@]}"} >/dev/null
  echo "stopped: billing drops to the boot disk only."
}

case "${1:-status}" in
  status|"")  status ;;
  start)      start ;;
  stop)       stop ;;
  ip)         ip="$(external_ip)"; [ -n "$ip" ] && echo "$ip" || die "no external IP (box not running?)" ;;
  ssh)        shift; exec gcloud compute ssh "$SSH_USER@$NAME" --zone "$ZONE" ${PROJECT_ARG[@]+"${PROJECT_ARG[@]}"} "$@" ;;
  -h|--help|help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)          die "unknown action '${1}'. Try: status | start | stop | ip | ssh | help" ;;
esac
