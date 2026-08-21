#!/usr/bin/env bash
# Finish Linode setup after default Caddy "Hello from Caddy" page.
# Replaces the :80 static site with TLS + reverse_proxy to τrend.
# Optional closed-beta gate: set BETA_PASS in the environment (never commit it).
#
#   cd ~/trend-multi && bash deploy/finish-setup.sh
#   BETA_PASS='choose-a-gate-password' bash deploy/finish-setup.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$PWD"
DOMAIN="${DOMAIN:-tau.bposhaughnessy.com}"
BETA_USER="${BETA_USER:-beta}"

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

BETA_HASH=""
if [[ -n "${BETA_PASS:-}" ]]; then
  if command -v caddy >/dev/null 2>&1; then
    BETA_HASH=$(caddy hash-password --plaintext "$BETA_PASS")
  else
    BETA_HASH=$(.venv/bin/python -c "import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode()[:72], bcrypt.gensalt(14)).decode())" "$BETA_PASS")
  fi
  echo "==> Caddyfile for ${DOMAIN} (TLS + basic_auth + reverse_proxy)"
else
  echo "==> Caddyfile for ${DOMAIN} (TLS + reverse_proxy, no basic_auth)"
  echo "    (set BETA_PASS=... to enable a closed-beta gate)"
fi

sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy
export DOMAIN BETA_USER BETA_HASH
sudo -E python3 - <<'PY'
from pathlib import Path
import os

domain = os.environ["DOMAIN"]
user = os.environ["BETA_USER"]
h = (os.environ.get("BETA_HASH") or "").strip()
auth = ""
if h:
    auth = f"""
	basic_auth {{
		{user} {h}
	}}
"""
text = f"""{domain} {{
	encode gzip
{auth}
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
if [[ -n "${BETA_PASS:-}" ]]; then
  echo "  Gate user: ${BETA_USER}  (password from BETA_PASS env — not printed)"
fi
echo "  App login: email signup (SMTP) — see deploy/SMTP.md"
