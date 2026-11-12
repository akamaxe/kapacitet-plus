# Changelog

What changed for someone who runs Kapacitet+ or reads its figures. The version is still 0.1.0 and
nothing here is a release: `main` is the supported version, and the commit history has the rest.
Changes before 12 November 2026 are only in that history.

## Unreleased

### Changed

- **The server refuses to listen beyond this machine without sign-in.** `kapacitet serve` with a
  host other than a loopback address now ends with exit code 2 until `KAPACITET_AUTH` is set to
  `oidc` or, on purpose, to `off`. The container listens on 0.0.0.0 inside, so `docker run` needs
  `-e KAPACITET_AUTH=off` (or the sign-in settings). On this machine it starts as before and says
  in its log that sign-in is off.
- **A network that is not radial is refused.** A ring, or two rows between the same two buses, was
  a warning and is now an error of the network check; the model refuses such tables too. Give
  cables laid in parallel as one row with `parallel`.
- **A study's publication.** `capacity.csv` and the GeoJSON gained
  `generation_shared_by_stations`, `consumption_shared_by_stations` (how many stations share the
  upstream headroom that caps a figure) and `rounded_to_kw`. The figures of a cell with fewer than
  20 customers are rounded down to 10 kW by default (`[disclosure] round_kw`,
  `round_below_customers`; `round_kw = 0` turns it off).
- **Validation is the DSO's verdict.** `[validation]` in `study.toml` holds the DSO's own
  tolerances. Without them no station is called validated; with them, a station outside a
  tolerance is not published. The validation report no longer counts a station without readings
  as validated.
- **The front page and the annual report lead with what can be issued.** The display figure of
  Static vs envelopes is the issuable range, not the perfect-foresight bound, and the annual
  report opens on the day-ahead basis. A link to the perfect-foresight filing now carries
  `basis=foresight`.
- **Sign-in misconfigured.** The API's replies say that sign-in cannot run and no longer which
  variable is missing; the server's log, the start-up and `kapacitet instance check` say it.
- **Results files** written from now on carry a `computed` block: when, the commit of the code and
  the library versions.

### Fixed

- `kapacitet study options` ended with a `KeyError` and exit code 1 after writing its files.
- The first evaluation after a start waited on eight years of ENTSO-E prices read point by point;
  a year is now read in arrays, with identical results.
- Identifiers that name a folder or a file (a network of the app, a station, a run, a case) no
  longer accept a final newline.
- The README said the API had no authentication, that re-issuing during the day was outside the
  prototype, that an application was stored with the address, and that 1 146 hours were limited
  on the day-ahead basis (1 240). It now also says that the intraday replica fails its scorecard.

### Added

- `tools/envelope_networks.py` and `docs/envelopes-by-network.md`: the capacity stages and the
  intraday scorecard on three benchmark networks at two MV voltages, with the decision rule
  committed before any case was computed.
- `SECURITY.md`, and the paragraphs of elmarknadslagen (2026:1281) that replace ellagen's on
  1 January 2027 beside the conditions of Ei2025:01.
