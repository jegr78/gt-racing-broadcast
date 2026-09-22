#!/usr/bin/env bash
# iptest-regions.sh. Orchestrator for the #505 YouTube-egress-IP test. Runs on YOUR laptop.
#
# For each AWS region it launches a THROWAWAY Ubuntu 24.04 t3.small (no GPU), uploads the
# real yt-cookies.txt + provision-iptest.sh, runs the minimal provision (racecast install-tools
# + cookie copy + the exact relay resolve command), prints the RESOLVED/BOT-CHECK verdict for
# that region's egress IP, then tears the box down (instance + security group + key). Cheap
# (t3.small ≈ $0.02/h, up for a few minutes) and self-cleaning.
#
# Answers: is the YouTube block AWS-wide, or specific to the eu-central-1 range the event box
# used (possibly reputation-burned by the 24 h event)? A region that RESOLVES = relocate there.
#
# Config (env overrides):
#   IPTEST_REGIONS   space-separated regions (default: eu-central-1 eu-west-1 us-east-1)
#   IPTEST_COOKIES   local yt-cookies.txt to upload (REQUIRED; from `racecast cookies`)
#   IPTEST_URLS      space-separated YouTube URLs to resolve (passed to provision-iptest.sh)
#   IPTEST_KEY       local SSH private key to use/derive a pubkey from (default ~/.ssh/racecast-box.pem)
#   IPTEST_SSH_CIDR  CIDR allowed to SSH the throwaway boxes (default: this laptop's /32)
#   IPTEST_TYPE      instance type (default t3.small)
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROVISION="$HERE/provision-iptest.sh"
REGIONS="${IPTEST_REGIONS:-eu-central-1 eu-west-1 us-east-1}"
COOKIES="${IPTEST_COOKIES:-}"
URLS="${IPTEST_URLS:-https://www.youtube.com/@SkyNews/live}"
KEY="${IPTEST_KEY:-$HOME/.ssh/racecast-box.pem}"
ITYPE="${IPTEST_TYPE:-t3.small}"
KN="iptest-key"
SGN="iptest-sg"

[ -f "$PROVISION" ] || { echo "missing $PROVISION" >&2; exit 1; }
[ -n "$COOKIES" ] && [ -f "$COOKIES" ] || { echo "set IPTEST_COOKIES to a real yt-cookies.txt" >&2; exit 1; }
[ -f "$KEY" ] || { echo "missing SSH key $KEY" >&2; exit 1; }

PUBKEY="$(mktemp)"; ssh-keygen -y -f "$KEY" > "$PUBKEY" 2>/dev/null || { echo "cannot derive pubkey from $KEY" >&2; exit 1; }
MYIP="$(curl -s https://api.ipify.org)"; CIDR="${IPTEST_SSH_CIDR:-${MYIP}/32}"
SSHOPT=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o BatchMode=yes)

echo "regions: $REGIONS | type: $ITYPE | ssh-from: $CIDR | urls: $URLS"

for R in $REGIONS; do
  echo "======================== REGION $R ========================"
  AMI="$(aws ec2 describe-images --region "$R" --owners 099720109477 \
        --filters "Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*" "Name=state,Values=available" \
        --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text 2>/dev/null)"
  [ -n "$AMI" ] && [ "$AMI" != "None" ] || { echo "no Ubuntu 24.04 AMI in $R. Skip"; continue; }

  aws ec2 delete-key-pair --region "$R" --key-name "$KN" >/dev/null 2>&1
  aws ec2 import-key-pair --region "$R" --key-name "$KN" --public-key-material "fileb://$PUBKEY" >/dev/null 2>&1
  SG="$(aws ec2 create-security-group --region "$R" --group-name "$SGN" --description 'iptest throwaway' --query GroupId --output text 2>/dev/null)"
  [ -n "$SG" ] && [ "$SG" != "None" ] || SG="$(aws ec2 describe-security-groups --region "$R" --group-names "$SGN" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)"
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" --protocol tcp --port 22 --cidr "$CIDR" >/dev/null 2>&1

  IID="$(aws ec2 run-instances --region "$R" --image-id "$AMI" --instance-type "$ITYPE" \
        --key-name "$KN" --security-group-ids "$SG" --associate-public-ip-address \
        --query 'Instances[0].InstanceId' --output text 2>&1)"
  if [[ "$IID" != i-* ]]; then
    echo "launch failed in $R: $IID"
    aws ec2 delete-security-group --region "$R" --group-id "$SG" >/dev/null 2>&1
    aws ec2 delete-key-pair --region "$R" --key-name "$KN" >/dev/null 2>&1
    continue
  fi
  aws ec2 wait instance-running --region "$R" --instance-ids "$IID" 2>/dev/null
  IP="$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
  echo "instance $IID @ $IP. Waiting for SSH"

  up=0
  for _ in $(seq 1 12); do
    ssh "${SSHOPT[@]}" ubuntu@"$IP" true 2>/dev/null && { up=1; break; }
    sleep 10
  done
  if [ "$up" = 1 ]; then
    scp "${SSHOPT[@]}" "$COOKIES" ubuntu@"$IP":/tmp/yt-cookies.txt >/dev/null 2>&1
    scp "${SSHOPT[@]}" "$PROVISION" ubuntu@"$IP":/tmp/provision-iptest.sh >/dev/null 2>&1
    ssh "${SSHOPT[@]}" ubuntu@"$IP" \
      "sudo IPTEST_URLS='$URLS' IPTEST_COOKIES=/tmp/yt-cookies.txt bash /tmp/provision-iptest.sh 2>&1 | grep -E 'egress IP|RESOLVED|BOT-CHECK|OTHER|---|install-tools|MISSING|cookies copied'"
  else
    echo "SSH never came up in $R"
  fi

  echo "--- teardown $R ---"
  aws ec2 terminate-instances --region "$R" --instance-ids "$IID" >/dev/null 2>&1
  aws ec2 wait instance-terminated --region "$R" --instance-ids "$IID" 2>/dev/null
  aws ec2 delete-security-group --region "$R" --group-id "$SG" >/dev/null 2>&1
  aws ec2 delete-key-pair --region "$R" --key-name "$KN" >/dev/null 2>&1
  echo "$R done + cleaned"
done
rm -f "$PUBKEY"
echo "===== ALL REGIONS DONE ====="
