#!/usr/bin/env bash
# Starts Kapacitet+ when someone opens the codespace (postAttachCommand) and keeps its log in this
# terminal.
#
# The log is not decoration. GitHub stops an idle codespace after 30 minutes, and what counts as
# activity is the editor and its terminals, not the requests to the forwarded port. Every request
# the app makes writes a line here, so the codespace stays up while Kapacitet+ is in use in the
# other tab, and stops by itself, saving the free hours, once it is not.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=/tmp/kapacitet.log
PORT=8000
touch "$LOG"

if [ ! -x .venv/bin/python ] || [ ! -f web/dist/index.html ]; then
  echo "Setting Kapacitet+ up (a few minutes, only this once)..."
  bash .devcontainer/install.sh >> "$LOG" 2>&1 || echo "The setup failed; the lines below say why."
fi

# One server for the whole codespace, however many editor windows attach: the loop holds the lock
# for as long as it runs, and starts the server again if it stops (five quick stops in a row and
# it gives up, saying so in the log).
setsid nohup bash -c '
  exec 9> /tmp/kapacitet.lock
  flock -n 9 || exit 0
  crashes=0
  while [ "$crashes" -lt 5 ]; do
    started=$(date +%s)
    .venv/bin/python -m kapacitet serve --port '"$PORT"' >> '"$LOG"' 2>&1
    code=$?
    [ "$code" -eq 0 ] && exit 0
    if [ $(( $(date +%s) - started )) -lt 60 ]; then crashes=$((crashes + 1)); else crashes=0; fi
    echo "Kapacitet+ stopped (exit code $code); starting it again." >> '"$LOG"'
    sleep 3
  done
  echo "Kapacitet+ keeps stopping; the lines above say why." >> '"$LOG"'
' > /dev/null 2>&1 < /dev/null &

url="http://127.0.0.1:$PORT/"
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  url="https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/"
fi
for _ in $(seq 1 120); do
  if curl -fs "http://127.0.0.1:$PORT/api/v1/health" > /dev/null 2>&1; then
    echo
    echo "  Kapacitet+ is running: $url"
    echo "  It opens in a new browser tab. If it does not, click Ports below, then the globe next to Kapacitet+."
    echo "  Keep this tab open while you use it: the lines below are what the app is doing."
    echo
    break
  fi
  sleep 1
done
exec tail -n 50 -F "$LOG"
