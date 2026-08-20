#!/usr/bin/env bash
# Generate a bcrypt hash for Caddy basic_auth.
#
# Usage:
#   ./deploy/caddy-hash-password.sh
#   ./deploy/caddy-hash-password.sh 'my-shared-password'
#   caddy hash-password --plaintext '...'   # if caddy is installed locally
#
set -euo pipefail

PASS="${1:-}"
if [[ -z "$PASS" ]]; then
  read -r -s -p "Password: " PASS
  echo
  [[ -n "$PASS" ]] || { echo "Empty password" >&2; exit 1; }
fi

if command -v caddy >/dev/null 2>&1; then
  HASH=$(caddy hash-password --plaintext "$PASS")
elif command -v docker >/dev/null 2>&1; then
  HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$PASS")
else
  # Fallback: Python bcrypt (same cost Caddy often uses is fine for basic_auth)
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY=python3
  fi
  HASH=$("$PY" - <<'PY' "$PASS"
import sys
try:
    import bcrypt
except ImportError:
    sys.stderr.write("Install caddy, or: pip install bcrypt\n")
    sys.exit(1)
pw = sys.argv[1].encode("utf-8")[:72]
print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=14)).decode("ascii"))
PY
)
fi

echo
echo "Paste into /etc/caddy/Caddyfile inside basic_auth { }:"
echo
echo "	beta $HASH"
echo
echo "Then:  sudo systemctl reload caddy"
echo "Browser will prompt for user \"beta\" and your password."
echo "Remove the basic_auth block entirely when you want the site open."
