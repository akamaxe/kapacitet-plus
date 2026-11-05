"""The model inputs themselves (config/assumptions.toml): no pipeline output needed."""

from __future__ import annotations


def test_every_assumption_is_labelled_and_sourced():
    from kapacitet.config import assumptions

    assert len(assumptions()) > 20
    for key, a in assumptions().items():
        assert a.tag, f"{key} has no tag"
        assert a.source, f"{key} has no source"
        assert a.label, f"{key} has no label"
        assert a.label_sv, f"{key} has no Swedish label (label_sv)"
        assert bool(a.note) == bool(a.note_sv), f"{key}: a note needs its Swedish note_sv, and only then"
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


def test_no_input_carries_a_spanish_label():
    """The interface speaks English and Swedish only. A `label_es` or `note_es` left in either
    file would never reach the API (config.assumptions() and the Ei pack copy named fields only):
    it would sit there unused, kept up by nobody and at odds with the two-language rule."""
    import tomllib

    from kapacitet.paths import CONFIG

    for name in ("assumptions.toml", "ei_conditions.toml"):
        raw = tomllib.loads((CONFIG / name).read_text(encoding="utf-8"))
        entries = raw.get("condition", []) if name == "ei_conditions.toml" else raw.values()
        for entry in entries:
            stray = [k for k in entry if k.endswith("_es")]
            assert not stray, f"{name}: {entry.get('key', entry.get('label'))} still has {stray}"
