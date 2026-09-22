#!/usr/bin/env bash
# iptest-505-run.sh. Laptop orchestrator for the #505 YouTube multi-feed CONCURRENCY
# measurement. Pairs with provision-iptest.sh (the on-box provisioner).
#
# Does the whole run deterministically. No ad-hoc SSH: launch a persistent throwaway box
# (m5.large, non-burstable → no CPU-credit confound), provision it faithfully WITH the
# harness (provision-iptest.sh IPTEST_HARNESS=1: real install-tools toolchain + real
# cookies + racecast SSH key + tools/multifeed-429-probe.py + src/), then run the #505
# cell matrix (sanity → 2/3-feed burst/staggered → recovery), each detached on the box and
# polled to completion, honouring a cool-down after any cell that threw a 429. Finally it
# folds every cell's results.jsonl into the matrix table and (optionally) tears the box down.
#
# Prereq: the YouTube AWS-range bot-check must be OFF (run provision-iptest first / a fresh
# box resolves). If resolves BOT-CHECK, the concurrency question can't be reached. Wait for
# an off window (see iptest-regions.sh).
#
# Config (env):
#   IPTEST_COOKIES   local yt-cookies.txt (REQUIRED; from `racecast cookies`)
#   IPTEST_HOST      racecast@<ip> of an ALREADY-provisioned box. Skips launch+provision+teardown
#   IPTEST_REGION    AWS region for a fresh box (default eu-central-1, the real box's region)
#   IPTEST_TYPE      instance type (default m5.large)
#   IPTEST_KEY       SSH private key (default ~/.ssh/racecast-box.pem)
#   IPTEST_SURVIVAL  per-cell survival window seconds (default 1200 = 20 min)
#   IPTEST_COOLDOWN  cool-down seconds after a 429 cell (default 600 = 10 min)
#   IPTEST_TEARDOWN  1 = terminate the launched box at the end (default 1; ignored if IPTEST_HOST)
#   IPTEST_URLS      space-separated distinct live URLs (default: 3 always-live public channels)
#   IPTEST_CELLS     space-separated cell keys to run, in order (default: the full matrix
#                    "sanity 2-burst 2-stagger 3-burst 3-stagger recovery"). Quick look:
#                    IPTEST_CELLS="sanity 2-burst 2-stagger".
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROVISION="$HERE/provision-iptest.sh"
COOKIES="${IPTEST_COOKIES:-}"
REGION="${IPTEST_REGION:-eu-central-1}"
ITYPE="${IPTEST_TYPE:-m5.large}"
KEY="${IPTEST_KEY:-$HOME/.ssh/racecast-box.pem}"
SURVIVAL="${IPTEST_SURVIVAL:-1200}"
COOLDOWN="${IPTEST_COOLDOWN:-600}"
KN=iptest-505; SGN=iptest-505-sg
URLS="${IPTEST_URLS:-https://www.youtube.com/@SkyNews/live https://www.youtube.com/@NASA/live https://www.youtube.com/@aljazeeraenglish/live}"
SSHOPT=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o BatchMode=yes)

[ -n "$COOKIES" ] && [ -f "$COOKIES" ] || { echo "set IPTEST_COOKIES to a real yt-cookies.txt" >&2; exit 1; }
[ -f "$PROVISION" ] || { echo "missing $PROVISION" >&2; exit 1; }

IID=""; HOST="${IPTEST_HOST:-}"; TEARDOWN="${IPTEST_TEARDOWN:-1}"

launch_and_provision() {
  local pub; pub="$(mktemp)"; ssh-keygen -y -f "$KEY" > "$pub"
  local myip cidr ami sg
  myip="$(curl -s https://api.ipify.org)"; cidr="$myip/32"
  ami="$(aws ec2 describe-images --region "$REGION" --owners 099720109477 \
        --filters "Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*" "Name=state,Values=available" \
        --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text)"
  echo "region=$REGION type=$ITYPE ami=$ami ssh-from=$cidr"
  aws ec2 delete-key-pair --region "$REGION" --key-name "$KN" >/dev/null 2>&1
  aws ec2 import-key-pair --region "$REGION" --key-name "$KN" --public-key-material "fileb://$pub" >/dev/null 2>&1
  sg="$(aws ec2 create-security-group --region "$REGION" --group-name "$SGN" --description iptest-505 --query GroupId --output text 2>/dev/null)"
  [ -n "$sg" ] && [ "$sg" != None ] || sg="$(aws ec2 describe-security-groups --region "$REGION" --group-names "$SGN" --query 'SecurityGroups[0].GroupId' --output text)"
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$sg" --protocol tcp --port 22 --cidr "$cidr" >/dev/null 2>&1
  IID="$(aws ec2 run-instances --region "$REGION" --image-id "$ami" --instance-type "$ITYPE" \
        --key-name "$KN" --security-group-ids "$sg" --associate-public-ip-address \
        --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20}' \
        --tag-specifications 'ResourceType=instance,Tags=[{Key=purpose,Value=iptest-505}]' \
        --query 'Instances[0].InstanceId' --output text)"
  [[ "$IID" == i-* ]] || { echo "launch failed: $IID"; rm -f "$pub"; exit 1; }
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
  local ip; ip="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
  echo "IID=$IID IP=$ip: waiting for SSH"
  for _ in $(seq 1 15); do ssh "${SSHOPT[@]}" ubuntu@"$ip" true 2>/dev/null && break; sleep 10; done
  echo "=== provision (toolchain + cookies + racecast key + harness) ==="
  scp "${SSHOPT[@]}" "$COOKIES" ubuntu@"$ip":/tmp/yt-cookies.txt >/dev/null 2>&1
  scp "${SSHOPT[@]}" "$PROVISION" ubuntu@"$ip":/tmp/provision-iptest.sh >/dev/null 2>&1
  ssh "${SSHOPT[@]}" ubuntu@"$ip" \
    "sudo IPTEST_COOKIES=/tmp/yt-cookies.txt IPTEST_HARNESS=1 IPTEST_URLS='${URLS%% *}' bash /tmp/provision-iptest.sh 2>&1 | grep -E 'OK|RESOLVED|BOT-CHECK|MISSING|harness'"
  rm -f "$pub"
  HOST="racecast@$ip"
}

# run one cell: launch detached on the box, poll to completion, print + return the cell record
run_cell() {
  local cell="$1"; shift
  local extra="$*"
  echo ">>> cell $cell  ($extra)  survival=${SURVIVAL}s"
  ssh "${SSHOPT[@]}" "$HOST" "cd ~/iro505 && export PATH=\$HOME/runtime/bin:\$PATH && mkdir -p probe-runs && \
    nohup python3 tools/multifeed-429-probe.py --urls urls-yt.txt --cookies \$HOME/runtime/yt-cookies.txt \
      --out probe-runs --duration-s $SURVIVAL --cell-id $cell $extra > probe-runs/$cell.console 2>&1 & echo launched pid=\$!"
  # poll until the harness prints its '# done' line
  local waited=0
  while ! ssh "${SSHOPT[@]}" "$HOST" "grep -q '# done' ~/iro505/probe-runs/$cell.console 2>/dev/null"; do
    sleep 20; waited=$((waited+20))
    [ $waited -gt $((SURVIVAL+600)) ] && { echo "  timeout waiting for $cell"; break; }
  done
  local rec
  rec="$(ssh "${SSHOPT[@]}" "$HOST" "grep '\"kind\": \"cell\"' ~/iro505/probe-runs/$cell/results.jsonl 2>/dev/null")"
  echo "  $rec"
  case "$rec" in *'"threw_429": true'*)
    echo "  -> 429: cooling down ${COOLDOWN}s before next cell"; sleep "$COOLDOWN" ;;
  esac
}

trap 'echo "interrupted: box $IID left running for inspection"; exit 130' INT

[ -z "$HOST" ] && launch_and_provision || echo "using existing host $HOST"

# (re)write the URL list on the box from IPTEST_URLS. Covers both the fresh-launch and the
# existing-IPTEST_HOST paths, so a re-run with new streams always uses the current list.
ssh "${SSHOPT[@]}" "$HOST" "cd ~/iro505 && printf '%s\n' $URLS > urls-yt.txt && echo urls: \$(wc -l < urls-yt.txt)"

echo "===================== #505 YT CONCURRENCY MATRIX ($HOST) ====================="
CELLS="${IPTEST_CELLS:-sanity 2-burst 2-stagger 3-burst 3-stagger recovery}"
for c in $CELLS; do
  case "$c" in
    sanity)    run_cell yt-1-sanity           --n 1 --duration-s 180 ;;
    2-burst)   run_cell yt-2-burst            --n 2 --activation burst ;;
    2-stagger) run_cell yt-2-stagger          --n 2 --activation staggered --hls-live-edge 6 ;;
    3-burst)   run_cell yt-3-burst            --n 3 --activation burst ;;
    3-stagger) run_cell yt-3-stagger          --n 3 --activation staggered --hls-live-edge 6 ;;
    recovery)  run_cell yt-2-recovery-backoff --n 2 --activation burst --retry-mode backoff ;;
    *)         echo "  unknown cell key: $c (skipped)" ;;
  esac
done

echo "===================== RESULTS MATRIX ====================="
ssh "${SSHOPT[@]}" "$HOST" "cd ~/iro505 && python3 tools/multifeed-429-probe.py --summarize probe-runs/*/results.jsonl"
echo "--- raw results tarball on box: ~/iro505/probe-runs (scp back if needed) ---"

if [ -n "$IID" ] && [ "$TEARDOWN" = 1 ]; then
  echo "=== teardown $IID ==="
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" >/dev/null 2>&1
  aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$IID" 2>/dev/null
  aws ec2 delete-security-group --region "$REGION" --group-name "$SGN" >/dev/null 2>&1
  aws ec2 delete-key-pair --region "$REGION" --key-name "$KN" >/dev/null 2>&1
  echo "torn down + cleaned"
elif [ -n "$IID" ]; then
  echo "box $IID left RUNNING (IPTEST_TEARDOWN=0). Stop it: aws ec2 terminate-instances --region $REGION --instance-ids $IID"
fi
echo "===== #505 run done ====="
