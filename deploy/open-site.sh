#!/usr/bin/env bash
# Remove whole-site Caddy basic_auth so friends can reach τrend with only
# normal account signup/login.
#
# Run ON the Linode (needs sudo):
#   bash ~/trend-multi/deploy/open-site.sh
#
set -euo pipefail

DOMAIN="${DOMAIN:-tau.bposhaughnessy.com}"

sudo python3 - <<PY
from pathlib import Path

domain = "${DOMAIN}"
text = f"""{domain} {{
	encode gzip

	reverse_proxy 127.0.0.1:8511

	header {{
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
		-Server
	}}
}}
"""
path = Path("/etc/caddy/Caddyfile")
path.write_text(text)
print(text)
PY

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy || sudo systemctl restart caddy
sleep 1
systemctl is-active caddy

echo
echo "Site is open (no basic_auth gate)."
echo "  https://${DOMAIN}"
echo "Friends still need to create/verify an account (SMTP) to use the app."
echo "Photos stay invite-only until you allow them in Admin."
