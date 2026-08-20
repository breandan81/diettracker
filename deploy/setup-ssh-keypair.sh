#!/usr/bin/env bash
# setup-ssh-keypair.sh — run on each of YOUR machines (laptop, desktop, etc.)
#
# What it does:
#   1. Creates an Ed25519 SSH key if you don't already have one
#   2. Optionally installs the public key on a remote host (ssh-copy-id)
#   3. Prints next steps for hardening the server (disable password/root SSH)
#
# Usage:
#   ./setup-ssh-keypair.sh
#   ./setup-ssh-keypair.sh breandan@YOUR_LINODE_IP
#   ./setup-ssh-keypair.sh --key-name id_ed25519_linode breandan@YOUR_LINODE_IP
#   KEY_COMMENT="me@laptop" ./setup-ssh-keypair.sh breandan@host
#
set -euo pipefail

KEY_NAME="${KEY_NAME:-id_ed25519}"
KEY_COMMENT="${KEY_COMMENT:-$(whoami)@$(hostname -s 2>/dev/null || hostname)}"
SSH_DIR="${HOME}/.ssh"
KEY_PATH="${SSH_DIR}/${KEY_NAME}"
PUB_PATH="${KEY_PATH}.pub"

REMOTE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --key-name)
      KEY_NAME="$2"
      KEY_PATH="${SSH_DIR}/${KEY_NAME}"
      PUB_PATH="${KEY_PATH}.pub"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      REMOTE="$1"
      shift
      ;;
  esac
done

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

if [[ -f "$KEY_PATH" ]]; then
  echo "→ Existing private key: $KEY_PATH"
else
  echo "→ Generating Ed25519 key: $KEY_PATH"
  ssh-keygen -t ed25519 -a 100 -f "$KEY_PATH" -C "$KEY_COMMENT"
fi

chmod 600 "$KEY_PATH" 2>/dev/null || true
chmod 644 "$PUB_PATH"

echo
echo "Public key ($PUB_PATH):"
echo "----------------------------------------"
cat "$PUB_PATH"
echo "----------------------------------------"
echo

if [[ -n "$REMOTE" ]]; then
  if ! command -v ssh-copy-id >/dev/null 2>&1; then
    echo "ssh-copy-id not found; installing key with a manual one-liner…"
    # Still works if password login is temporarily enabled
    ssh "$REMOTE" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    cat "$PUB_PATH" | ssh "$REMOTE" "cat >> ~/.ssh/authorized_keys"
  else
    echo "→ Installing public key on ${REMOTE} (you may be prompted for the remote password once)…"
    ssh-copy-id -i "$PUB_PATH" "$REMOTE"
  fi

  echo
  echo "→ Testing key login (BatchMode — should NOT ask for a password)…"
  if ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY_PATH" "$REMOTE" "echo OK: key auth works as \$(whoami)@\$(hostname)"; then
    echo
    echo "Success. From this machine you can use:"
    echo "  ssh -i $KEY_PATH $REMOTE"
    echo
    echo "Optional ~/.ssh/config snippet:"
    echo "  Host linode"
    echo "    HostName ${REMOTE#*@}"
    echo "    User ${REMOTE%%@*}"
    echo "    IdentityFile $KEY_PATH"
    echo "    IdentitiesOnly yes"
  else
    echo "Key test failed. Check username/IP and that password login still works, then retry." >&2
    exit 1
  fi
else
  echo "No remote given — key is ready locally only."
  echo "Install on the server with:"
  echo "  $0 breandan@YOUR_LINODE_IP"
  echo "Or paste the public key above into the server's ~/.ssh/authorized_keys"
fi

echo
echo "After EVERY machine you use can key-login as a sudo user, harden the server:"
echo "  (copy harden-ssh-server.sh to the Linode and run with sudo)"
echo "  sudo bash harden-ssh-server.sh"
echo
echo "Do NOT disable passwords until key login works from all your machines."
