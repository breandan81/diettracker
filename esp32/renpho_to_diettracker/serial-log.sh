#!/usr/bin/env bash
# Live serial monitor + append to a dated log (catch flaky weigh-ins).
#
#   ./serial-log.sh
#   PORT=/dev/ttyUSB0 ./serial-log.sh
#   ./serial-log.sh /dev/ttyACM0
#
# Stop with Ctrl-C. Log keeps growing under ../logs/ (gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$(cd "$ROOT/.." && pwd)/logs"
mkdir -p "$LOG_DIR"

pick_port() {
  if [[ -n "${1:-}" ]]; then
    echo "$1"
    return
  fi
  if [[ -n "${PORT:-}" ]]; then
    echo "$PORT"
    return
  fi
  for p in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0 /dev/ttyUSB1; do
    if [[ -e "$p" ]]; then
      echo "$p"
      return
    fi
  done
  echo "No serial port found (tried ttyACM*/ttyUSB*). Plug in the ESP32-C3." >&2
  exit 1
}

PORT_PATH="$(pick_port "${1:-}")"
if [[ ! -e "$PORT_PATH" ]]; then
  echo "Port not found: $PORT_PATH" >&2
  exit 1
fi

if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "arduino-cli not on PATH" >&2
  exit 1
fi

LOG="$LOG_DIR/serial-$(date +%Y%m%d).log"
{
  echo
  echo "======== session start $(date -Is) port=$PORT_PATH ========"
} | tee -a "$LOG"

echo "Logging to $LOG"
echo "Monitor: 115200 dtr=off rts=off (Ctrl-C to stop)"
echo

# --timestamp: stamp each board line; -q: only serial I/O (no cli chatter)
# stdbuf: line-buffer so tee flushes promptly when piped
exec stdbuf -oL -eL arduino-cli monitor \
  -p "$PORT_PATH" \
  -c baudrate=115200,dtr=off,rts=off \
  --timestamp \
  -q \
  2>&1 | tee -a "$LOG"
