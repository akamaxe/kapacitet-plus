"""grunder_for_berakning.sv.md (study/templates): every value in it comes from the data. Every
placeholder resolves, and the template's own text holds no figure: no digit outside a placeholder,
except the legal citations it may name (`3 kap. 9 §`, `27 a §`). No data needed."""

from __future__ import annotations

import re

import pytest

from kapacitet.study import init_study
from kapacitet.study import collect
from kapacitet.study.disclosure import assign
from kapacitet.study.publish import PLACEHOLDER, TEMPLATE, criteria_values, render, signoff_state
from kapacitet.study.review import corrections, load_rows

CITATIONS = (re.compile(r"\d+ kap\. \d+ §"), re.compile(r"27 [a-c] §"))


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_the_template_has_no_digit_outside_its_placeholders_and_citations():
    text = PLACEHOLDER.sub("", _template())
    for c in CITATIONS:
        text = c.sub("", text)
    found = [(i + 1, line) for i, line in enumerate(text.splitlines()) if re.search(r"\d", line)]
    assert not found, f"figures typed into the template: {found}"


def test_a_placeholder_without_a_value_is_refused():
    with pytest.raises(KeyError, match="nothing_like_it"):
        render("Värde: {{ nothing_like_it }}", {})
    assert render("{{a}} och {{ a }}", {"a": "x"}) == "x och x"


@pytest.mark.parametrize("approval", ["", "Board decision 2026-10"])
def test_every_placeholder_resolves_from_a_study(tmp_path, approval):
    from test_study_publish import STATIONS, put

    study = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    if approval:
        study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
            '# approval_reference = "..."', f'approval_reference = "{approval}"'), encoding="utf-8")
        from kapacitet.study import open_study
        study = open_study(study.root, allow_git=True)
    for sid, v in STATIONS.items():
        put(study, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"])
    collect.collect(study, "2026Q4")
    rows = load_rows(study, "2026Q4")
    fixes, _ = corrections(study, "2026Q4", rows)
    result = assign(rows, study.config.disclosure, study.id, fixes)
    values = criteria_values(study, "2026Q4", rows, result, fixes, signoff_state(study, "2026Q4", rows), "2026-11-05")
    used = set(PLACEHOLDER.findall(_template()))
    assert used == set(values), "a value no placeholder uses, or a placeholder without a value"
    text = render(_template(), values)
    assert "{{" not in text and "}}" not in text
    assert "Testnät AB" in text and "2026Q4" in text and "2024, 2025" in text
    assert (approval in text) if approval else "publiceras inte" in text
