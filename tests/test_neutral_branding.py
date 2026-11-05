"""Kapacitet+ speaks for itself, not for the place it was first written in.

Standard library only, so it runs on a fresh clone without Node or the pipeline output. Every
tracked text file (`git ls-files`, or a walk of the tree where there is no git) is read line by
line, and neither a line nor a file's path may match FORBIDDEN: the origin and personal
references the product must not carry, including the presenter's talk script that once sat in a
drawer. A word that comes back fails here, with the file and the line, before it reaches the
public repository.

Words with an ordinary meaning are caught only in the phrases that carry the origin ("our
colleagues", "the seminar"), so a product string such as "a link sent to a colleague" passes.
ALLOWED lists the idioms that contain a forbidden word ("of course", "over the course of",
pandas' error "assignment destination is read-only", which constraints.txt quotes, an A-series
paper size). Only the idiom is cut out of the line before it is searched, never the whole line.

Skipped: binary files, the npm lockfile (package names and hashes, nobody's prose) and this file,
which has to spell the pattern out.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

FORBIDDEN = re.compile(
    "(?i)" + "|".join([
        # where it was written
        r"\bKTH\b", r"EI2600", r"innovation_course", r"\bcourse\b", r"\bcurso\b", r"kursprototyp",
        r"prototipo de curso", r"\blectures?\b", r"\bstudents?\b", r"\b(?:my|our|the) university\b",
        r"examiner",
        # what it was written for
        r"Assignment ?[12]\b", r"Assignment[12]", r"\bA2\b", r"Lean Canvas",
        r"\b(?:the|our|this) seminar\b", r"\bseminar (?:slides?|presentation|deck|talk)\b",
        r"\bseminario\b",
        # who wrote it
        r"teammates?", r"compañer[oa]s?", r"\b(?:my|our) colleagues?\b",
        r"\b(?:min|mina|vår|våra) kolleg(?:a|or|orna)?\b", r"chiharum", r"ebbarandi", r"Hilber",
        # the presenter's drawer
        r"\bpresenter(?:'s)? notes?\b", r"\bspeaker notes?\b", r"\btalking points?\b",
    ])
)
ALLOWED = re.compile(
    r"(?i)assignment destination|\bof course\b|\bcourse of\b|\bin due course\b|\bStudent's t\b"
    r"|\bA\d (?:paper|sheet|page|format|size)s?\b"
)


def _offending(text: str) -> re.Match[str] | None:
    """The first forbidden phrase in `text` once its allowed idioms are cut out."""
    return FORBIDDEN.search(ALLOWED.sub(" ", text))

# what the fallback walk never enters: environments, installed packages, data and build output
SKIP_DIRS = {".git", ".venv", "node_modules", "data", "__pycache__", ".pytest_cache", "test-results", "playwright-report"}
SKIP_PATHS = {"web/dist", "web/package-lock.json"}


def _rules(path: Path) -> list[str]:
    """The patterns of an ignore file, without comments, blanks and negations."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [s for s in (line.strip() for line in lines) if s and not s.startswith(("#", "!"))]


def _ignored() -> list[tuple[str, bool, bool]]:
    """The root .gitignore and .git/info/exclude as (pattern, anchored, directories only), for
    the walk without git. A simplification of git's rules, enough to keep local tool output out."""
    out = []
    for line in _rules(ROOT / ".gitignore") + _rules(ROOT / ".git" / "info" / "exclude"):
        pattern = line.rstrip("/").lstrip("/")
        out.append((pattern, "/" in pattern, line.endswith("/")))
    return out


def _tracked() -> list[Path]:
    """The files git tracks; without git, every file under the root that is neither in SKIP_DIRS
    nor matched by the root .gitignore."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=60).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
        if names:
            return [ROOT / n for n in names]
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        pass
    rules = _ignored()

    def skip(rel: str, is_dir: bool) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(rel if anchored else name, pattern)
                   for pattern, anchored, dirs_only in rules if is_dir or not dirs_only)

    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        base = Path(dirpath).relative_to(ROOT).as_posix()
        rel = (lambda n: n) if base == "." else (lambda n: f"{base}/{n}")
        # a folder whose own .gitignore is "*" (tool state that ignores itself) is never committed
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and rel(d) not in SKIP_PATHS and not skip(rel(d), True)
                       and _rules(Path(dirpath) / d / ".gitignore") != ["*"]]
        files += [Path(dirpath) / f for f in filenames if not skip(rel(f), False)]
    return files


def _text(path: Path) -> str | None:
    """The file as text, or None for a binary file (or one that vanished since it was listed)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _checked() -> list[tuple[str, str]]:
    out = []
    for path in _tracked():
        rel = path.relative_to(ROOT).as_posix()
        if path.resolve() == SELF or rel in SKIP_PATHS:
            continue
        text = _text(path)
        if text is not None:
            out.append((rel, text))
    return out


def test_the_files_are_found():
    """A guard that reads nothing would pass on any repository."""
    files = dict(_checked())
    assert len(files) > 50, f"only {len(files)} text files found under {ROOT}"
    assert "README.md" in files and "src/kapacitet/__init__.py" in files


@pytest.mark.parametrize("line", [
    "Built for the KTH course EI2600.",
    "See Assignment2/reference for the brief.",
    "The A2 figures come from results/.",
    "Shown at the seminar on Friday.",
    "Written with my colleagues over the autumn.",
    "Skickas till mina kollegor.",
    "A student project, not a product.",
    "The presenter notes open with N.",
    "Talking points for the first screen.",
    "Of course this is a course project.",
    "assignment destination is read-only, from the KTH lab",
])
def test_the_pattern_catches_origin_text(line):
    assert _offending(line), line


@pytest.mark.parametrize("line", [
    "Of course the envelope is verified.",
    "Over the course of a day the loading moves.",
    "A link sent to a colleague opens the same view.",
    "Skicka länken till en kollega.",
    "Print on A2 paper.",
    "Ei håller ett seminarium om anslutningar.",
    "pandas raises: assignment destination is read-only",
    "IEC 60364-4-41:2005+A1:2017, 411.3.2.3",
])
def test_the_pattern_spares_ordinary_prose(line):
    assert not _offending(line), line


def test_no_path_names_a_course_or_its_people():
    """File names too, binary files included: an Assignment2_figure.png says it as loudly."""
    hits = []
    for path in _tracked():
        rel = path.relative_to(ROOT).as_posix()
        if m := _offending(rel):
            hits.append(f"{rel}: {m.group(0)!r}")
    assert not hits, "course or personal references in file names:\n" + "\n".join(hits[:40])


def test_no_file_names_a_course_or_its_people():
    hits = []
    for rel, text in _checked():
        for i, line in enumerate(text.splitlines(), 1):
            if m := _offending(line):
                hits.append(f"{rel}:{i}: {m.group(0)!r} in {line.strip()[:120]!r}")
    assert not hits, "course or personal references in the repository:\n" + "\n".join(hits[:40])
