#!/usr/bin/env bash
# Fix Caddy after "permission denied" on /var/log/caddy/trend-multi.log
#   bash ~/trend-multi/deploy/fix-caddy.sh
#
# Rewrites Caddyfile without a file log. Keeps basic_auth only if already present
# (does not embed any password). Prefer deploy/open-site.sh to remove the gate.
set -euo pipefail

DOMAIN="${DOMAIN:-tau.bposhaughnessy.com}"

sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy

sudo python3 - <<PY
from pathlib import Path
import re
import os

domain = os.environ.get("DOMAIN", "tau.bposhaughnessy.com")
path = Path("/etc/caddy/Caddyfile")
t = path.read_text() if path.is_file() else ""
m = re.search(r"basic_auth\s*\{\s*(\S+)\s+(\S+)", t, re.S)
auth = ""
if m:
    user, h = m.group(1), m.group(2)
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
path.write_text(text)
print(text)
PY

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
sleep 2
systemctl is-active caddy
curl -sS "https://${DOMAIN}/api/health" || true
echo
echo "If you see JSON above, you're good: https://${DOMAIN}"
