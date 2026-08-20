#!/usr/bin/env bash
# harden-ssh-server.sh — run ON the Linode (as root or via sudo)
#
# Prerequisites:
#   - You can already SSH in with a key as a non-root sudo user (e.g. breandan)
#   - Keep a Linode LISH console tab open in case something goes wrong
#
# What it does:
#   - Disables password authentication
#   - Disables root SSH login (root password still works in LISH for recovery)
#   - Ensures PubkeyAuthentication is on
#   - Reloads sshd
#
# Usage:
#   sudo bash harden-ssh-server.sh
#   sudo bash harden-ssh-server.sh --dry-run
#
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

# Safety: refuse if the invoking sudo user has no authorized_keys
SUDO_USER_NAME="${SUDO_USER:-}"
if [[ -n "$SUDO_USER_NAME" && "$SUDO_USER_NAME" != "root" ]]; then
  AUTH="/home/${SUDO_USER_NAME}/.ssh/authorized_keys"
  if [[ ! -s "$AUTH" ]]; then
    echo "Refusing: ${AUTH} is missing or empty." >&2
    echo "Install your laptop's public key first, then re-run." >&2
    exit 1
  fi
  echo "→ Found authorized_keys for ${SUDO_USER_NAME} ($(wc -l < "$AUTH") line(s))"
else
  echo "Warning: not invoked via sudo from a normal user; double-check you have key access." >&2
fi

DROP_IN="/etc/ssh/sshd_config.d/99-hardening.conf"
CONTENT=$(cat <<'EOF'
# Managed by harden-ssh-server.sh — key-only SSH
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
EOF
)

echo "→ Will write ${DROP_IN}:"
echo "$CONTENT"
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry-run — no changes)"
  exit 0
fi

# Backup main config once
if [[ ! -f /etc/ssh/sshd_config.bak.pre-harden ]]; then
  cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.pre-harden
  echo "→ Backed up /etc/ssh/sshd_config → sshd_config.bak.pre-harden"
fi

mkdir -p /etc/ssh/sshd_config.d
printf '%s\n' "$CONTENT" > "$DROP_IN"
chmod 644 "$DROP_IN"

# Validate config before reload
if command -v sshd >/dev/null 2>&1; then
  sshd -t
  echo "→ sshd -t OK"
fi

if systemctl is-active --quiet ssh 2>/dev/null; then
  systemctl reload ssh
  echo "→ Reloaded ssh"
elif systemctl is-active --quiet sshd 2>/dev/null; then
  systemctl reload sshd
  echo "→ Reloaded sshd"
else
  service ssh reload 2>/dev/null || service sshd reload 2>/dev/null || true
  echo "→ Asked ssh service to reload"
fi

echo
echo "Done. From another terminal on your laptop, verify:"
echo "  ssh breandan@YOUR_LINODE_IP"
echo "Root password SSH should now fail; LISH console still works for emergencies."
echo
echo "Current effective settings:"
sshd -T 2>/dev/null | grep -iE '^(pubkeyauthentication|passwordauthentication|kbdinteractiveauthentication|permitrootlogin) ' || true
