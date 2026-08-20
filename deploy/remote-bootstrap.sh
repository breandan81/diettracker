#!/usr/bin/env bash
# remote-bootstrap.sh — run ON the Ubuntu Linode (once).
#
# Installs: python3-venv, Caddy, app dir layout, systemd unit.
# Does NOT overwrite an existing secrets.env.
#
# Usage:
#   bash remote-bootstrap.sh
#   APP_DIR=~/trend-multi DOMAIN=trend.example.com bash remote-bootstrap.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/trend-multi}"
DOMAIN="${DOMAIN:-}"
SERVICE_USER="${SERVICE_USER:-$(whoami)}"
UNIT_SRC=""

echo "==> App dir: $APP_DIR (user=$SERVICE_USER)"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run as the deploy user (e.g. breandan), not root. Script will sudo when needed." >&2
  exit 1
fi

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip rsync curl ca-certificates debian-keyring debian-archive-keyring apt-transport-https

# Caddy stable from official repo (idempotent-ish)
if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Installing Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

mkdir -p "$APP_DIR" "$APP_DIR/data/photos" "$APP_DIR/deploy"
cd "$APP_DIR"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# requirements may arrive later via rsync; install if present
if [[ -f requirements.txt ]]; then
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

if [[ ! -f secrets.env ]]; then
  if [[ -f secrets.example.env ]]; then
    cp secrets.example.env secrets.env
    # generate a session secret
    SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    if grep -q '^SESSION_SECRET=' secrets.env; then
      sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$SECRET|" secrets.env
    else
      echo "SESSION_SECRET=$SECRET" >> secrets.env
    fi
    echo "Created $APP_DIR/secrets.env — edit SMTP_*, PUBLIC_BASE_URL, ADMIN_USER_IDS, XAI_API_KEY"
  else
    echo "WARNING: no secrets.example.env yet — rsync the app, then copy secrets.example.env → secrets.env"
  fi
fi

# systemd unit
UNIT_DST=/etc/systemd/system/trend-multi.service
if [[ -f "$APP_DIR/deploy/trend-multi.service" ]]; then
  UNIT_SRC="$APP_DIR/deploy/trend-multi.service"
elif [[ -f "$(dirname "$0")/trend-multi.service" ]]; then
  UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/trend-multi.service"
fi

if [[ -n "$UNIT_SRC" ]]; then
  tmp=$(mktemp)
  sed -e "s|/home/breandan/trend-multi|$APP_DIR|g" \
      -e "s|^User=breandan|User=$SERVICE_USER|" \
      -e "s|^Group=breandan|Group=$SERVICE_USER|" \
      "$UNIT_SRC" > "$tmp"
  sudo cp "$tmp" "$UNIT_DST"
  rm -f "$tmp"
  sudo systemctl daemon-reload
  sudo systemctl enable trend-multi
  echo "Installed $UNIT_DST"
else
  echo "WARNING: trend-multi.service not found — deploy code first, then re-run bootstrap"
fi

# Caddyfile
if [[ -n "$DOMAIN" ]]; then
  CADDY_EX="$APP_DIR/deploy/Caddyfile.example"
  [[ -f "$CADDY_EX" ]] || CADDY_EX="$(dirname "$0")/Caddyfile.example"
  if [[ -f "$CADDY_EX" ]]; then
    tmp=$(mktemp)
    sed "s/YOUR_DOMAIN/$DOMAIN/g" "$CADDY_EX" > "$tmp"
    # Optional: BETA_PASS=secret DOMAIN=... bash remote-bootstrap.sh
    if [[ -n "${BETA_PASS:-}" ]]; then
      if command -v caddy >/dev/null 2>&1; then
        HASH=$(caddy hash-password --plaintext "$BETA_PASS")
      elif [[ -x "$APP_DIR/.venv/bin/python" ]]; then
        HASH=$("$APP_DIR/.venv/bin/python" -c "import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode()[:72], bcrypt.gensalt(14)).decode())" "$BETA_PASS")
      else
        echo "WARNING: BETA_PASS set but cannot hash — install caddy or create venv first" >&2
        HASH=""
      fi
      if [[ -n "$HASH" ]]; then
        # Placeholder in Caddyfile.example is __BETA_PASSWORD_HASH__
        sed -i "s|__BETA_PASSWORD_HASH__|$HASH|g" "$tmp"
        echo "Closed-beta basic_auth user: beta  (password from BETA_PASS)"
      fi
    else
      echo "NOTE: Caddyfile still has placeholder __BETA_PASSWORD_HASH__ — set it before relying on the gate."
      echo "  Generate:  ./deploy/caddy-hash-password.sh 'your-password'"
      echo "  Or re-run: BETA_PASS='your-password' DOMAIN=$DOMAIN bash deploy/remote-bootstrap.sh"
    fi
    sudo mkdir -p /var/log/caddy
    sudo cp "$tmp" /etc/caddy/Caddyfile
    rm -f "$tmp"
    sudo systemctl enable caddy
    sudo systemctl reload caddy || sudo systemctl restart caddy
    echo "Caddy configured for https://$DOMAIN → 127.0.0.1:8511 (basic_auth enabled in template)"
  fi
else
  echo "NOTE: set DOMAIN=your.domain when running bootstrap to write /etc/caddy/Caddyfile"
  echo "  DOMAIN=trend.example.com BETA_PASS='shared' bash deploy/remote-bootstrap.sh"
fi

# Open HTTP/HTTPS if ufw is active (SSH assumed already allowed)
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow OpenSSH || true
  sudo ufw allow 80/tcp || true
  sudo ufw allow 443/tcp || true
fi

echo
echo "Next:"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "  1. From laptop:  ./deploy/deploy.sh ${SERVICE_USER}@${IP:-YOUR_LINODE_IP}"
echo "  2. Edit $APP_DIR/secrets.env (SMTP required for signup; PUBLIC_BASE_URL=https://$DOMAIN)"
echo "  3. sudo systemctl restart trend-multi"
echo "  4. curl -sI https://${DOMAIN:-YOUR_DOMAIN}/api/health"
