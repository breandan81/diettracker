#!/usr/bin/env bash
# Finish Linode setup after default Caddy "Hello from Caddy" page.
# Replaces the :80 static site with TLS + basic_auth + reverse_proxy to τrend.
#
# Run interactively (needs your sudo password):
#   cd ~/trend-multi && bash deploy/finish-setup.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$PWD"
DOMAIN="tau.bposhaughnessy.com"
BETA_USER="beta"
# Optional gate password — pass BETA_PASS=... or use ./deploy/caddy-hash-password.sh
BETA_HASH="${BETA_HASH:-}"

echo "==> apt packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip curl ca-certificates

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> install caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

echo "==> python venv + deps"
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
mkdir -p data/photos

if [[ ! -f secrets.env ]]; then
  echo "ERROR: secrets.env missing in $APP_DIR" >&2
  exit 1
fi

echo "==> systemd unit trend-multi"
tmp=$(mktemp)
sed -e "s|/home/breandan/trend-multi|${APP_DIR}|g" \
    -e "s|^User=breandan|User=$(whoami)|" \
    -e "s|^Group=breandan|Group=$(whoami)|" \
    deploy/trend-multi.service > "$tmp"
sudo cp "$tmp" /etc/systemd/system/trend-multi.service
rm -f "$tmp"
sudo systemctl daemon-reload
sudo systemctl enable trend-multi
sudo systemctl restart trend-multi
sleep 2
systemctl is-active trend-multi
curl -sS http://127.0.0.1:8511/api/health
echo

echo "==> Caddyfile for ${DOMAIN} (TLS + basic_auth + reverse_proxy)"
sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy
# Use Python so '$' in the bcrypt hash is never expanded by bash
export DOMAIN BETA_USER BETA_HASH
sudo -E python3 - <<'PY'
from pathlib import Path
import os

domain = os.environ["DOMAIN"]
user = os.environ["BETA_USER"]
h = os.environ["BETA_HASH"]
# No file log — avoids permission fights; use journalctl -u caddy instead
text = f"""{domain} {{
	encode gzip

	basic_auth {{
		{user} {h}
	}}

	reverse_proxy 127.0.0.1:8511

	header {{
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
		-Server
	}}
}}
"""
Path("/etc/caddy/Caddyfile").write_text(text)
print(text)
PY

if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  sudo ufw allow OpenSSH || true
  sudo ufw allow 80/tcp || true
  sudo ufw allow 443/tcp || true
fi

sudo systemctl enable caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy || sudo systemctl restart caddy
sleep 3

echo "==> public health check"
curl -sS "https://${DOMAIN}/api/health" || true
echo
echo "Done."
echo "  Open:  https://${DOMAIN}"
echo "  Gate:  ${BETA_USER} / (from BETA_PASS env)"
echo "  Then use τrend login (signup needs SMTP — see deploy/SMTP.md)"
