"""The Swedish manual (docs/manual_sv.md) names only commands the command line has.

Every `kapacitet ...` in a code span or a fenced block is walked against the CLI's own help: each
subcommand has to be one of the choices its parent's help lists, and each `--flag` has to appear
in the help of the command it is given to. A command renamed or a flag dropped fails here, with the
manual's line, before a planner types it. The help is printed by `pipeline.main(... --help)`, so
nothing runs; it needs neither data nor Node.

It also holds the manual to what it promises: README links it, and it says that no native speaker
has reviewed it.
"""

from __future__ import annotations

import re
import shlex
from functools import cache
from pathlib import Path

import pytest

from kapacitet import pipeline

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "manual_sv.md"

CHOICES = re.compile(r"positional arguments:\s*\n\s*\{([a-z0-9,-]+)\}")
CALL = re.compile(r"(?:python -m )?\bkapacitet((?: +[^\s`]+)+)")


def _code(text: str) -> list[tuple[int, str]]:
    """(line number, code) for every code span and every line of a fenced block."""
    out: list[tuple[int, str]] = []
    fenced = False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            out.append((n, line.split(" #", 1)[0]))
        else:
            out += [(n, span) for span in re.findall(r"`([^`]+)`", line)]
    return out


def _commands() -> list[tuple[int, list[str]]]:
    """Every command the manual names, as (line, its words after `kapacitet`). A pattern such as
    `kapacitet <kommando> --help` or `kapacitet …` names no command and is left out."""
    found = []
    for n, code in _code(MANUAL.read_text(encoding="utf-8")):
        m = CALL.match(code.strip())
        if m:
            words = shlex.split(m.group(1))
            if words and not words[0].startswith("<") and words[0] != "…":
                found.append((n, words))
    return found


@cache
def _help(path: tuple[str, ...]) -> str:
    """The help text of `kapacitet <path>`, printed by the parser itself."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as end:
        pipeline.main([*path, "--help"])
    assert end.value.code == 0, f"kapacitet {' '.join(path)} --help failed"
    return buf.getvalue()


def test_the_manual_names_commands():
    names = {" ".join(w for w in words if w[:1].isalpha())[:20] for _, words in _commands()}
    assert len(_commands()) >= 20, "the manual should show the commands it describes"
    assert any(n.startswith("study publish") for n in names)
    assert any(n.startswith("seals verify") for n in names)
    assert any(n.startswith("instance backup") for n in names)


@pytest.mark.parametrize("line,words", _commands(), ids=lambda v: str(v) if isinstance(v, int) else " ".join(v))
def test_every_command_in_the_manual_exists_in_the_cli_help(line, words):
    path: list[str] = []
    descending = True
    for word in words:
        if word.startswith("--"):
            flag = word.split("=", 1)[0]
            assert re.search(rf"(?<![\w-]){re.escape(flag)}\b", _help(tuple(path))), (
                f"docs/manual_sv.md:{line}: kapacitet {' '.join(path)} has no {flag}")
            continue
        if word.startswith("<"):
            descending = False   # a placeholder: what follows are its arguments
        if not descending:
            continue
        choices = CHOICES.search(_help(tuple(path)))
        if choices:
            assert word in choices.group(1).split(","), (
                f"docs/manual_sv.md:{line}: kapacitet {' '.join(path)} has no command {word!r}")
            path.append(word)
        else:
            descending = False   # a positional value: the rest are its arguments
    assert path, f"docs/manual_sv.md:{line}: no command named"
    _help(tuple(path))


def test_the_readme_links_the_manual_and_the_manual_says_it_is_unreviewed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/manual_sv.md" in readme
    text = MANUAL.read_text(encoding="utf-8")
    assert "Ingen med svenska som modersmål har granskat" in text
    assert "glossary.md" in text
    assert not re.search(r"\bkr\b", text), "money is SEK, never kr"
