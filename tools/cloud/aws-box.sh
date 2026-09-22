#!/usr/bin/env bash
# aws-box.sh. Start / stop / status control wrapper for the AWS GPU box.
#
# Runs on YOUR laptop (not the box). A thin wrapper around the `aws` CLI so you
# never have to remember the instance id or region. Cost control: STOP the box
# after every event: a running g4dn.xlarge bills ~$0.60/h, a stopped one only
# its EBS boot disk. The tailnet IP is stable across stop/start (Tailscale), so
# `racecast-box-aws` keeps working after a restart.
#
# Usage:
#   tools/cloud/aws-box.sh status          # cloud state + type + public IP (default)
#   tools/cloud/aws-box.sh start [--no-wait]   # start; wait until running, print SSH line
#   tools/cloud/aws-box.sh stop            # stop (idle = boot disk only)
#   tools/cloud/aws-box.sh ip              # print the current public IP (running only)
#   tools/cloud/aws-box.sh ssh [args…]     # SSH in as racecast over the tailnet
#
# Config (env overrides, never commit machine-specific values):
#   AWS_BOX_ID        instance id           (default i-04d70428c19a484ef)
#   AWS_BOX_REGION    region                (default eu-central-1)
#   AWS_BOX_SSH_HOST  tailnet host/IP       (default racecast-box-aws)
#   AWS_BOX_SSH_USER  login user            (default racecast)
#   AWS_BOX_SSH_KEY   private key           (default ~/.ssh/racecast-box.pem)
set -euo pipefail

BOX_ID="${AWS_BOX_ID:-i-04d70428c19a484ef}"
REGION="${AWS_BOX_REGION:-eu-central-1}"
SSH_HOST="${AWS_BOX_SSH_HOST:-racecast-box-aws}"
SSH_USER="${AWS_BOX_SSH_USER:-racecast}"
SSH_KEY="${AWS_BOX_SSH_KEY:-$HOME/.ssh/racecast-box.pem}"

die() { echo "aws-box: $*" >&2; exit 1; }
command -v aws >/dev/null 2>&1 || die "the 'aws' CLI is not installed (brew install awscli)"

q() { aws ec2 describe-instances --instance-ids "$BOX_ID" --region "$REGION" \
        --query "$1" --output text 2>/dev/null; }

state()      { q 'Reservations[0].Instances[0].State.Name'; }
public_ip()  { local ip; ip="$(q 'Reservations[0].Instances[0].PublicIpAddress')"
               [ "$ip" = "None" ] && ip=""; printf '%s' "$ip"; }

status() {
  local st ty ip
  st="$(state)" || die "cannot reach AWS (check 'aws configure' / SSO login for region $REGION)"
  [ -z "$st" ] && die "instance $BOX_ID not found in $REGION"
  ty="$(q 'Reservations[0].Instances[0].InstanceType')"
  echo "AWS box $BOX_ID ($ty, $REGION)"
  echo "  state:     $st"
  if [ "$st" = "running" ]; then
    ip="$(public_ip)"
    echo "  public IP: ${ip:-<none yet>}"
  fi
  echo "  tailnet:   ssh -i $SSH_KEY $SSH_USER@$SSH_HOST   (stable across stop/start)"
}

start() {
  local wait=1
  [ "${1:-}" = "--no-wait" ] && wait=0
  local st; st="$(state)"
  if [ "$st" = "running" ]; then echo "already running."; status; return 0; fi
  echo "starting $BOX_ID …"
  aws ec2 start-instances --instance-ids "$BOX_ID" --region "$REGION" >/dev/null
  if [ "$wait" = 1 ]; then
    echo "waiting for it to reach 'running' …"
    aws ec2 wait instance-running --instance-ids "$BOX_ID" --region "$REGION"
  fi
  status
  echo "note: the desktop session + Tailscale take ~1 more minute after 'running'."
}

stop() {
  local st; st="$(state)"
  if [ "$st" = "stopped" ]; then echo "already stopped."; return 0; fi
  echo "stopping $BOX_ID …"
  aws ec2 stop-instances --instance-ids "$BOX_ID" --region "$REGION" >/dev/null
  echo "stop requested: billing drops to the EBS boot disk once it reaches 'stopped'."
}

case "${1:-status}" in
  status|"")  status ;;
  start)      shift; start "${1:-}" ;;
  stop)       stop ;;
  ip)         ip="$(public_ip)"; [ -n "$ip" ] && echo "$ip" || die "no public IP (box not running?)" ;;
  ssh)        shift; [ -f "$SSH_KEY" ] || die "SSH key not found: $SSH_KEY (set AWS_BOX_SSH_KEY)"
              exec ssh -i "$SSH_KEY" "$SSH_USER@$SSH_HOST" "$@" ;;
  -h|--help|help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)          die "unknown action '${1}'. Try: status | start | stop | ip | ssh | help" ;;
esac
