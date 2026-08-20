#!/usr/bin/env bash
# Fix Caddy after "permission denied" on /var/log/caddy/trend-multi.log
#   bash ~/trend-multi/deploy/fix-caddy.sh
set -euo pipefail

sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy

sudo python3 - <<'PY'
from pathlib import Path
import re

path = Path("/etc/caddy/Caddyfile")
t = path.read_text()
m = re.search(r"basic_auth\s*\{\s*beta\s+(\S+)", t, re.S)
if not m:
    raise SystemExit("Could not find beta hash in existing Caddyfile")
h = m.group(1)
text = f"""tau.bposhaughnessy.com {{
	encode gzip

	basic_auth {{
		beta {h}
	}}

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
curl -sS https://tau.bposhaughnessy.com/api/health || true
echo
echo "If you see JSON above, you're good: https://tau.bposhaughnessy.com"
