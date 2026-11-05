"""study.toml (study/config.py) and how a study's settings reach the engine (config.py, seals.py).

No pipeline output is needed: every study here is a folder of the test's own."""

from __future__ import annotations

import tomllib

import pytest

from kapacitet import config
from kapacitet.study.config import (BUILDER_ONLY, DOCUMENTED_ONLY, METHOD_VERSION, StudyConfigError, load, parse,
                                    template)


@pytest.fixture()
def study_env(tmp_path, monkeypatch):
    """A study folder named by KAPACITET_STUDY; the settings are read again on entry and exit."""
    def use(text: str):
        (tmp_path / "study.toml").write_text(text, encoding="utf-8")
        monkeypatch.setenv("KAPACITET_STUDY", str(tmp_path))
        config.reload()
        return tmp_path

    yield use
    monkeypatch.delenv("KAPACITET_STUDY", raising=False)
    config.set_overrides(None)
    config.reload()


def test_an_empty_study_toml_holds_the_owners_defaults():
    cfg = parse({})
    assert cfg.method.percentile == 50 and cfg.method.budget == "aggregate"
    assert cfg.method.consumption_device == "ev"
    assert cfg.disclosure.level == "station" and cfg.disclosure.min_customers == 5
    assert cfg.output.csv_dialect == "sv-excel" and cfg.output.retention_days is None
    assert (cfg.mv.band_low_pu, cfg.mv.band_high_pu) == (0.98, 1.06)
    assert cfg.cases.protection == "dppower"
    assert cfg.dso.method_version == METHOD_VERSION
    assert cfg.assumptions == {}


def test_the_template_parses_to_the_defaults():
    text = template("Nätbolaget AB")
    cfg = parse(tomllib.loads(text))
    assert cfg.dso.name == "Nätbolaget AB"
    assert cfg.model_dump(exclude={"dso"}) == parse({}).model_dump(exclude={"dso"})
    for line in ("[time]", "# reference_year = ", "# weather_first_year = ", "# metering_grid_area = ",
                 "# temperature_station = "):
        assert line in text                                          # every key there is, stated
    placed = parse(tomllib.loads(template("X", {"lat": 63.8, "lon": 20.3, "temperature_station": 140480})))
    assert (placed.site.lat, placed.site.temperature_station) == (63.8, 140480) and placed.site.given


@pytest.mark.parametrize("data, where", [
    ({"metod": {}}, "metod"),                                      # a misspelt section
    ({"method": {"percentil": 50}}, "percentil"),                  # a misspelt key
    ({"method": {"budget": "both"}}, "budget"),
    ({"method": {"percentile": 120}}, "percentile"),
    ({"mv": {"band_low_pu": 1.04, "band_high_pu": 1.0}}, "band_low_pu"),
    ({"site": {"lat": 59.3}}, "lat"),                              # lat without lon
    ({"site": {"lat": 59.3, "lon": 18.0}}, "temperature_station"),  # a place without its own station
    ({"site": {"strang_lat": 59.3, "strang_lon": 18.0}}, "strang_lat"),   # a STRÅNG point and no place
    ({"site": {"zone": "SE5"}}, "zone"),
    ({"time": {"weather_first_year": 2020, "weather_last_year": 2018}}, "weather_first_year"),
    ({"disclosure": {"min_customers": 0}}, "min_customers"),
])
def test_a_mistake_is_an_error_that_names_it(data, where):
    with pytest.raises(StudyConfigError) as err:
        parse(data)
    assert where in str(err.value)


def test_an_override_names_a_known_key_its_unit_and_a_source():
    ok = parse({"assumptions": {"household_kwh_per_year": {"value": 6000, "unit": "kWh/yr",
                                                           "source": "the DSO's metering, 2024"}}})
    assert ok.overrides() == {"household_kwh_per_year": 6000}
    bad = {
        "no_such_input": {"value": 1, "unit": "", "source": "x"},
        "household_kwh_per_year": {"value": 6000, "unit": "kWh/yr"},
    }
    for key, entry in bad.items():
        with pytest.raises(StudyConfigError) as err:
            parse({"assumptions": {key: entry}})
        assert key in str(err.value)
    for entry, words in (({"value": 6000, "unit": "kWh/yr", "source": "  "}, ("needs its source",)),
                         ({"value": 6000, "unit": "MWh/yr", "source": "x"}, ("unit", "'MWh/yr'")),
                         ({"value": "a lot", "unit": "kWh/yr", "source": "x"}, ("takes a value like", "'a lot'"))):
        with pytest.raises(StudyConfigError) as err:
            parse({"assumptions": {"household_kwh_per_year": entry}})
        for word in words:
            assert word in str(err.value)


def test_an_override_that_would_change_nothing_in_a_station_is_refused():
    for key, unit, words in (("cabinet_reach_m", "m", "network builder"), ("pv_system_losses_pct", "%", "fixed")):
        with pytest.raises(StudyConfigError) as err:
            parse({"assumptions": {key: {"value": 80.0, "unit": unit, "source": "x"}}})
        assert key in str(err.value) and words in str(err.value) and "change nothing" in str(err.value)


def test_the_keys_refused_in_a_study_are_those_no_station_step_reads():
    """BUILDER_ONLY is every assumption named only by grid/builder.build and what it calls, the
    OSM build a station never runs; DOCUMENTED_ONLY every one no code names. A key named anywhere
    else (the importer, the profiles, the engine, the builder's add_zero_sequence that every
    network is loaded with) takes effect in a station's build."""
    import ast
    from pathlib import Path

    src = Path(config.__file__).parent
    keys = set(config.assumptions())

    def named(*nodes) -> set[str]:
        return {n.value for node in nodes for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in keys}

    elsewhere: set[str] = set()
    for f in src.rglob("*.py"):
        rel = f.relative_to(src).as_posix()
        if rel not in ("study/config.py", "grid/builder.py"):
            elsewhere |= named(ast.parse(f.read_text(encoding="utf-8")))
    tree = ast.parse((src / "grid" / "builder.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    closure, todo = {"build"}, ["build"]                         # build() and what it calls, there
    while todo:
        for c in ast.walk(funcs[todo.pop()]):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in set(funcs) - closure:
                closure.add(c.func.id)
                todo.append(c.func.id)
    in_build = named(*(funcs[name] for name in closure))
    rest_of_builder = named(*(n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name in closure)))
    assert set(BUILDER_ONLY) == in_build - rest_of_builder - elsewhere
    assert set(DOCUMENTED_ONLY) == keys - in_build - rest_of_builder - elsewhere


def test_a_missing_or_broken_file_says_so(tmp_path):
    with pytest.raises(StudyConfigError, match="no study.toml"):
        load(tmp_path / "study.toml")
    (tmp_path / "study.toml").write_text("[dso\nname = 1", encoding="utf-8")
    with pytest.raises(StudyConfigError, match="not valid TOML"):
        load(tmp_path / "study.toml")


def test_the_settings_take_the_studys_site_and_time_only_when_it_is_set(study_env):
    base = config.settings()
    assert base.study == "" and base.site.key == "default"
    study_env("""
[dso]
name = "Nätbolaget AB"
[site]
lat = 63.8
lon = 20.3
temperature_station = 140480
strang_lat = 63.9
strang_lon = 20.2
zone = "SE1"
[time]
reference_year = 2024
weather_first_year = 2020
weather_last_year = 2024
""")
    s = config.settings()
    assert s.study and s.site.key == "study" and s.site.name == "Nätbolaget AB"
    assert (s.site.center_lat, s.site.center_lon) == (63.8, 20.3)
    assert s.site.strang_point == (63.9, 20.2) and s.site.bidding_zone == "SE1"
    assert s.temperature_station == 140480
    assert s.reference_year == 2024 and s.weather_years == (2020, 2021, 2022, 2023, 2024)
    assert s.strang == base.strang and s.price_years == base.price_years    # what the study does not set


def test_a_reference_year_outside_the_weather_years_is_refused(study_env):
    study_env("[time]\nreference_year = 2014\n")
    with pytest.raises(StudyConfigError, match="reference_year"):
        config.settings()


def test_the_studys_overrides_sit_under_the_sensitivity_layer(study_env):
    file_value = config.assumptions()["household_kwh_per_year"].value
    assert config.A("household_kwh_per_year") == file_value
    study_env('[assumptions]\nhousehold_kwh_per_year = { value = 6000.0, unit = "kWh/yr", source = "metering" }\n')
    assert config.A("household_kwh_per_year") == 6000.0
    config.set_overrides({"household_kwh_per_year": 7000.0})          # a tornado case
    assert config.A("household_kwh_per_year") == 7000.0
    config.set_overrides(None)                                          # ...and its end
    assert config.A("household_kwh_per_year") == 6000.0
    config.set_overrides({"z_star_pct": 5.0}, layer="study")
    assert config.A("z_star_pct") == 5.0 and config.A("household_kwh_per_year") == file_value
    with pytest.raises(KeyError):
        config.set_overrides({"no_such_input": 1}, layer="study")
    with pytest.raises(ValueError):
        config.set_overrides({}, layer="tornado")


def test_a_bad_override_fails_the_engine_too(study_env):
    study_env('[assumptions]\nno_such_input = { value = 1, unit = "", source = "x" }\n')
    with pytest.raises(StudyConfigError, match="no_such_input"):
        config.A("z_star_pct")


def test_the_fingerprint_covers_the_study(study_env):
    from kapacitet import seals

    before = seals.fingerprint()
    assert not any(k.startswith("study/") for k in before["files"])
    root = study_env("[method]\npercentile = 50\n")
    fp = seals.fingerprint()
    assert "study/study.toml" in fp["files"]
    # another length too: the hashes are cached by size and mtime, and an mtime can be coarse
    (root / "study.toml").write_text("[method]\npercentile = 40.0\n", encoding="utf-8")
    assert seals.fingerprint()["digest"] != fp["digest"]


def test_a_station_process_sees_the_settings_its_parent_digests(study_env, tmp_path):
    from kapacitet.study.config import load

    root = study_env('[site]\nlat = 63.8\nlon = 20.3\ntemperature_station = 140480\nzone = "SE1"\n'
                     '[time]\nreference_year = 2024\nweather_first_year = 2020\nweather_last_year = 2024\n')
    here = config.study_settings(load(root / "study.toml"), root)
    assert here == config.settings()
