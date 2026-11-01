"""The model inputs themselves (config/assumptions.toml): no pipeline output needed."""

from __future__ import annotations


def test_every_assumption_is_labelled_and_sourced():
    from kapacitet.config import assumptions

    assert len(assumptions()) > 20
    for key, a in assumptions().items():
        assert a.tag, f"{key} has no tag"
        assert a.source, f"{key} has no source"
        assert a.label, f"{key} has no label"
        if a.tag in ("sourced", "regulation", "library"):
            assert a.url, f"{key} is tagged {a.tag} but has no link"


def test_every_assumption_has_a_unit_field():
    """A unit may be empty for a ratio or a list, but it must be stated, never left out."""
    import tomllib

    from kapacitet.paths import CONFIG

    raw = tomllib.loads((CONFIG / "assumptions.toml").read_text(encoding="utf-8"))
    for key, entry in raw.items():
        assert "unit" in entry, f"{key} has no unit field"
        assert "value" in entry, f"{key} has no value"
