"""The Swedish a DSO would file, send or publish says that no native speaker has reviewed it
(kapacitet/language.py), and holds no English of ours. Needs no data."""

from __future__ import annotations

from pathlib import Path

import kapacitet
from kapacitet.language import NOT_REVIEWED_SV

SRC = Path(kapacitet.__file__).parent
# the publication's criteria carry the sentence through their status line, and only while a draft
STATUS_LINE = {"grunder_for_berakning.sv.md"}


def _swedish_templates() -> list[Path]:
    found = [p for folder in (SRC / "templates", SRC / "study" / "templates") for p in folder.rglob("*.md")
             if ".sv." in p.name or p.stem.endswith("_sv") or p.parent.name.endswith("_sv")]
    assert len(found) >= 9, [p.name for p in found]
    return found


def test_every_swedish_template_says_its_language_is_not_reviewed():
    for path in _swedish_templates():
        text = " ".join(path.read_text(encoding="utf-8").split())
        if path.name in STATUS_LINE:
            assert NOT_REVIEWED_SV not in text and "{{status}}" in text, path.name
        else:
            assert text.count(NOT_REVIEWED_SV) == 1, path.name


def test_the_texts_built_in_code_append_the_same_sentence():
    """The annex of a case, the redispatch report, the fleet's compliance page and a draft
    publication are built line by line: each names the one constant."""
    for name in ("study/cases.py", "study/publish.py", "engine/redispatch.py", "engine/meter_compliance.py"):
        source = (SRC / name).read_text(encoding="utf-8")
        assert "from ..language import NOT_REVIEWED_SV" in source and source.count("NOT_REVIEWED_SV") >= 2, name
    assert NOT_REVIEWED_SV.endswith(".") and "\n" not in NOT_REVIEWED_SV


def test_the_mv_bands_default_source_is_swedish_in_a_swedish_text():
    from kapacitet.study.config import MV_SOURCE_DEFAULT, MV_SOURCE_DEFAULT_SV, Mv

    assert Mv().source == MV_SOURCE_DEFAULT and Mv().source_sv == MV_SOURCE_DEFAULT_SV == "planeringsband, inte uppmätt"
    assert Mv(source="  planning band, not measured ").source_sv == MV_SOURCE_DEFAULT_SV
    assert Mv(source="mätt i fördelningsstationen 2025").source_sv == "mätt i fördelningsstationen 2025"


def test_no_swedish_template_leaves_an_english_phrase_of_ours_unexplained():
    for path in _swedish_templates():
        text = path.read_text(encoding="utf-8")
        for phrase in ("not entered", "not measured", "no meter readings", "not computed", "planning band"):
            # a code of a CSV column may be named, in backticks and with what it means
            assert phrase not in text.replace(f"`{phrase}` (", ""), (path.name, phrase)
