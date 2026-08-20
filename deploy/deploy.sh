#!/usr/bin/env bash
# deploy.sh — run on your LAPTOP to rsync τrend-multi to the Linode and restart.
#
# Usage:
#   ./deploy/deploy.sh breandan@YOUR_LINODE_IP
#   REMOTE_DIR=~/trend-multi ./deploy/deploy.sh breandan@host
#   SKIP_RESTART=1 ./deploy/deploy.sh breandan@host
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${1:-}"
REMOTE_DIR="${REMOTE_DIR:-~/trend-multi}"
SKIP_RESTART="${SKIP_RESTART:-0}"
SSH_OPTS=(-o ConnectTimeout=25 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)

if [[ -z "$REMOTE" ]]; then
  echo "Usage: $0 user@host" >&2
  echo "  REMOTE_DIR=~/trend-multi SKIP_RESTART=1 $0 user@host" >&2
  exit 1
fi

ssh_retry() {
  local tries="${1:-6}"
  shift
  local i
  for i in $(seq 1 "$tries"); do
    if ssh "${SSH_OPTS[@]}" "$@"; then
      return 0
    fi
    echo "SSH failed (attempt $i/$tries) — retrying in 4s…" >&2
    sleep 4
  done
  return 1
}

echo "==> rsync → $REMOTE:$REMOTE_DIR"
rsync_ok=0
for i in 1 2 3 4 5 6; do
  if rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'data/' \
    --exclude 'secrets.env' \
    --exclude 'esp32/**/config.h' \
    --exclude 'esp32/**/test_scale_session' \
    --exclude '.pytest_cache/' \
    --exclude 'node_modules/' \
    --exclude '*.db' \
    --exclude '*.db-journal' \
    "$ROOT/" "$REMOTE:$REMOTE_DIR/"; then
    rsync_ok=1
    break
  fi
  echo "rsync failed (attempt $i) — retrying in 4s…" >&2
  sleep 4
done
[[ "$rsync_ok" -eq 1 ]] || { echo "rsync failed after retries" >&2; exit 1; }

echo "==> remote pip install"
ssh_retry 6 "$REMOTE" bash -s <<EOF
set -euo pipefail
cd $REMOTE_DIR
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data/photos
if [[ ! -f secrets.env && -f secrets.example.env ]]; then
  echo "WARNING: secrets.env missing on server — copy from secrets.example.env and edit"
fi
EOF

if [[ "$SKIP_RESTART" != "1" ]]; then
  echo "==> restart trend-multi (enter your Linode sudo password if prompted)"
  # -t allocates a TTY so sudo can ask for a password
  ssh_retry 6 -t "$REMOTE" \
    "cd $REMOTE_DIR && sudo systemctl restart trend-multi && sleep 1 && systemctl is-active trend-multi && curl -sS http://127.0.0.1:8511/api/health && echo"
fi

echo "==> done"
echo "  Site: https://tau.bposhaughnessy.com"
echo "  Or:   ssh $REMOTE 'curl -sS http://127.0.0.1:8511/api/health'"
