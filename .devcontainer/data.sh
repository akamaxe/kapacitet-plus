#!/usr/bin/env bash
# Downloads the published data bundle (this repository's release `data`, under ODbL: see
# DATA-LICENSE.md) and unpacks it, so a new codespace opens on the screens rather than on an empty
# copy. It runs once after the codespace is built (postCreateCommand), and again from start.sh while
# the data are missing. A failed download leaves Kapacitet+ empty, and its setup page then takes the
# same file from the browser. It never fails the codespace.
set -uo pipefail
cd "$(dirname "$0")/.."
URL="${KAPACITET_DATA_URL:-https://github.com/akamaxe/kapacitet-plus/releases/download/data/kapacitet-data.zip}"

if [ -f data/processed/results/showcase.json ]; then
  exit 0
fi
if [ ! -x .venv/bin/python ]; then
  echo "Kapacitet+ is not installed yet; the data will come once it is."
  exit 0
fi

echo "Downloading the data of Kapacitet+ (about 250 MB)..."
tmp=$(mktemp -d)
if curl -fsSL --retry 3 --retry-delay 5 -o "$tmp/kapacitet-data.zip" "$URL"; then
  .venv/bin/python -m kapacitet unbundle "$tmp/kapacitet-data.zip" \
    || echo "The data could not be unpacked; the setup page will ask for kapacitet-data.zip."
else
  echo "The data could not be downloaded from $URL; the setup page will ask for kapacitet-data.zip."
fi
rm -rf "$tmp"
exit 0
