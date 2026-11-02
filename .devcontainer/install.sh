#!/usr/bin/env bash
# Builds what Kapacitet+ needs inside the codespace: the engine in its own environment (the
# versions constraints.txt pins) and the interface. It runs once when the codespace is created
# (onCreateCommand), and again from start.sh if something is missing. Safe to run twice.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check --quiet -c constraints.txt -e .

cd web
npm ci --no-audit --no-fund
npm run build
