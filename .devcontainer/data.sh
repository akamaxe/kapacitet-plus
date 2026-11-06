#!/usr/bin/env bash
# Downloads the published data bundle (this repository's release `data`, under ODbL: see
# DATA-LICENSE.md) and unpacks it, so a new codespace opens on the screens rather than on an empty
# copy. It runs once after the codespace is built (postCreateCommand), and again from start.sh while
# the data are missing. A failed download leaves Kapacitet+ empty, and its setup page then takes the
# same file from the browser. It never fails the codespace.
#
# The release's file can be replaced by anyone who may publish on the repository, so the address
# and the sha256 come from config/data-release.json, committed with the code (`kapacitet bundle
# --pin` writes it). A download whose digest differs is not unpacked. KAPACITET_DATA_URL points it
# at another file; that file's digest then has to come with it, in KAPACITET_DATA_SHA256.
# When gh is available (it is in a codespace), the download's build-provenance attestation
# (.github/workflows/attest-data.yml) is checked as well and the result is reported; the sha256
# stays the check that decides, because a file uploaded before its attestation has none yet.
set -uo pipefail
cd "$(dirname "$0")/.."
PIN=config/data-release.json

if [ -f data/processed/results/showcase.json ]; then
  exit 0
fi
if [ ! -x .venv/bin/python ]; then
  echo "Kapacitet+ is not installed yet; the data will come once it is."
  exit 0
fi

pinned() { .venv/bin/python -I -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' "$PIN" "$1" 2>/dev/null; }
URL="${KAPACITET_DATA_URL:-$(pinned url)}"
SHA="${KAPACITET_DATA_SHA256:-$(pinned sha256)}"
if [ -z "$URL" ] || [ -z "$SHA" ]; then
  echo "$PIN does not say which data to download; the setup page will ask for kapacitet-data.zip."
  exit 0
fi

# reports whether the file carries a provenance attestation of the repository that publishes it;
# never fails: the sha256 above is the required check
attestation() {
  local repo
  repo=$(printf '%s' "$URL" | sed -nE 's#^https://github\.com/([^/]+/[^/]+)/releases/.*#\1#p')
  if [ -z "$repo" ] || ! command -v gh >/dev/null 2>&1; then
    return 0
  fi
  if gh attestation verify "$1" -R "$repo" >/dev/null 2>&1; then
    echo "The data carry a build-provenance attestation of $repo."
  else
    echo "The data have no attestation of $repo that gh could verify; their sha256 matches the pin."
  fi
}

echo "Downloading the data of Kapacitet+ (about 250 MB)..."
tmp=$(mktemp -d)
if ! curl -fsSL --retry 3 --retry-delay 5 -o "$tmp/kapacitet-data.zip" "$URL"; then
  echo "The data could not be downloaded from $URL; the setup page will ask for kapacitet-data.zip."
elif ! echo "$SHA  $tmp/kapacitet-data.zip" | sha256sum -c --status -; then
  echo "The downloaded file is not the data release this version expects (its sha256 differs from"
  echo "$PIN), so it was not unpacked; the setup page will ask for kapacitet-data.zip."
  if [ -n "${KAPACITET_DATA_URL:-}" ] && [ -z "${KAPACITET_DATA_SHA256:-}" ]; then
    echo "KAPACITET_DATA_URL names another file: give its sha256 in KAPACITET_DATA_SHA256 as well."
  fi
else
  attestation "$tmp/kapacitet-data.zip"
  .venv/bin/python -m kapacitet unbundle "$tmp/kapacitet-data.zip" --expect-sha256 "$SHA" \
    || echo "The data could not be unpacked; the setup page will ask for kapacitet-data.zip."
fi
rm -rf "$tmp"
exit 0
