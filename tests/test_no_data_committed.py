"""No data in the repository: what git tracks (the index, so a `git add` is caught before its commit).

The repository is public and MIT; the data derive from OpenStreetMap (ODbL) and are published
apart, and an imported network is an operator's own. `.gitignore` keeps them out, but a
`git add -A` once committed an imported network all the same. The only data files git may hold
are the small test fixtures, under tests/fixtures/.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = "tests/fixtures/"
# what the pipeline, the API and the bundle write, in any folder: tables and arrays, the map
# binaries (state_*.bin), the bidding zones (OSM-derived GeoJSON), archives and logs of records,
# and the formats a later source or an operator's export would arrive in
DATA_SUFFIXES = (".csv", ".tsv", ".parquet", ".npz", ".npy", ".bin", ".geojson", ".zip", ".gz", ".jsonl",
                 ".feather", ".arrow", ".h5", ".hdf5", ".pkl", ".pickle", ".xlsx", ".pbf", ".osm",
                 ".db", ".sqlite")
# files of a built network or of the app's state, whatever folder they land in (json and txt are
# also the configuration's, so these go by name)
DATA_NAMES = {"net.json", "active.json", "dataset.json", "seals.jsonl", "applications.jsonl", "build.log",
              "compliance.txt", "state_doe.json", "state_static.json", "state_uncontrolled.json"}


def _tracked() -> list[str]:
    """The paths in git's index under the project, relative to it, or a skip outside a git checkout.

    The project may be a folder of a larger checkout (vendored, or in a monorepo): `ls-files` run
    in it lists what is under it, relative to it."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    try:
        subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        pytest.skip("not a git checkout (an unpacked archive, a container)")
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_nothing_under_data_is_tracked():
    assert [p for p in _tracked() if p.split("/")[0] == "data"] == []


def test_no_data_file_is_tracked_outside_the_test_fixtures():
    found = [p for p in _tracked() if not p.startswith(FIXTURES)
             and (PurePosixPath(p).name in DATA_NAMES or p.lower().endswith(DATA_SUFFIXES))]
    assert found == [], f"data files in git (they belong in data/ or a release, not in the repository): {found}"


def test_git_ignores_everything_under_data():
    """Not a list of its folders: whatever a later stage or a crash leaves in data/ is ignored too."""
    _tracked()
    probes = ["data/active.json", "data/.active.json.1a2b.tmp", "data/study/meters.csv", "data/anything"]
    out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", *probes],
                         capture_output=True, text=True)
    assert sorted(out.stdout.split()) == sorted(probes)
