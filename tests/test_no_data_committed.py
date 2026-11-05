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
# what the pipeline, the API and the bundle write, in any folder
DATA_SUFFIXES = (".csv", ".parquet", ".npz", ".npy", ".zip", ".gz", ".jsonl", ".feather", ".arrow",
                 ".h5", ".hdf5", ".pkl", ".pickle", ".xlsx")
# files of a built network or of the app's state, whatever folder they land in
DATA_NAMES = {"net.json", "active.json", "dataset.json", "seals.jsonl", "applications.jsonl"}


def _tracked() -> list[str]:
    """The paths in git's index, relative to the project, or a skip outside a git checkout."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    try:
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        pytest.skip("not a git checkout (an unpacked archive, a container)")
    if Path(top).resolve() != ROOT:
        pytest.skip("the project is not the root of this git checkout")
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_nothing_under_data_is_tracked():
    assert [p for p in _tracked() if p.split("/")[0] == "data"] == []


def test_no_data_file_is_tracked_outside_the_test_fixtures():
    found = [p for p in _tracked() if not p.startswith(FIXTURES)
             and (PurePosixPath(p).name in DATA_NAMES or p.lower().endswith(DATA_SUFFIXES))]
    assert found == [], f"data files in git (they belong in data/ or a release, not in the repository): {found}"
