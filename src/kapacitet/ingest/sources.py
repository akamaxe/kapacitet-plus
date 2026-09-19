"""Catalogue of external data sources (shown in the UI's provenance table)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    provider: str
    homepage: str
    license: str
    attribution: str
    tag: str  # real | reference | synthetic
    used_for: str
    health_url: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


SOURCES: dict[str, Source] = {
    s.key: s
    for s in [
        Source(
            key="smhi_strang",
            name="STRÅNG solar radiation model",
            provider="SMHI",
            homepage="https://opendata.smhi.se/apidocs/strang/",
            license="CC BY 4.0",
            attribution="Solar radiation: SMHI STRÅNG (CC BY 4.0)",
            tag="real",
            used_for="Hourly global, direct and diffuse irradiance at the neighbourhood → PV production",
            health_url="https://opendata-download-metanalys.smhi.se/api/category/strang1g/version/1.json",
        ),
        Source(
            key="smhi_metobs",
            name="Meteorological observations (air temperature)",
            provider="SMHI",
            homepage="https://opendata.smhi.se/apidocs/metobs/",
            license="CC BY 4.0",
            attribution="Temperature: SMHI station Stockholm-Bromma Flygplats (CC BY 4.0)",
            tag="real",
            used_for="Hourly air temperature → heat-pump demand, PV cell temperature",
            health_url="https://opendata-download-metobs.smhi.se/api/version/1.0.json",
        ),
        Source(
            key="elprisetjustnu",
            name="Day-ahead spot prices SE1–SE4",
            provider="Elpriset just nu.se (ENTSO-E data)",
            homepage="https://www.elprisetjustnu.se/elpris-api",
            license="Free API; attribution requested",
            attribution="Elpriser tillhandahålls av Elpriset just nu.se",
            tag="real",
            used_for="Hourly SE3 price → economic value of curtailment; SE1–SE4 prices on the national view",
            health_url="https://www.elprisetjustnu.se/api/v1/prices/2025/01-01_SE3.json",
        ),
        Source(
            key="entsoe",
            name="ENTSO-E Transparency Platform (day-ahead prices)",
            provider="ENTSO-E",
            homepage="https://transparency.entsoe.eu/",
            license="ENTSO-E Transparency terms (token required)",
            attribution="Prices: ENTSO-E Transparency Platform",
            tag="real",
            used_for="Optional: prices before Nov 2022 (needs ENTSOE_API_TOKEN)",
        ),
        Source(
            key="esett",
            name="eSett Open Data",
            provider="eSett Oy (Nordic imbalance settlement)",
            homepage="https://opendata.esett.com/",
            license="eSett Open Data terms",
            attribution="Production and consumption: eSett Open Data",
            tag="real",
            used_for="Production by type and consumption per bidding zone (national view); Stockholm load profile (validation)",
            health_url="https://api.opendata.esett.com/EXP16/MBAOptions",
        ),
        Source(
            key="osm",
            name="OpenStreetMap",
            provider="OpenStreetMap contributors (Overpass API)",
            homepage="https://www.openstreetmap.org/copyright",
            license="ODbL 1.0",
            attribution="© OpenStreetMap contributors (ODbL)",
            tag="real",
            used_for="Buildings, streets and mapped secondary substations of the neighbourhood",
            health_url="https://overpass-api.de/api/status",
        ),
        Source(
            key="scb",
            name="Households by size and tenure (HushallT26)",
            provider="Statistics Sweden (SCB)",
            homepage="https://www.statistikdatabasen.scb.se/goto/sv/ssd/HushallT26",
            license="CC0 (SCB open data)",
            attribution="Household sizes: Statistics Sweden (SCB)",
            tag="real",
            used_for="Number of occupants of the simulated households (Stockholm, owner-occupied småhus)",
            health_url="https://api.scb.se/OV0104/v1/doris/sv/ssd/START/HE/HE0111/HE0111A/HushallT26",
        ),
        Source(
            key="zones",
            name="Bidding-zone geometries SE1–SE4",
            provider="entsoe-py (EnergieID)",
            homepage="https://github.com/EnergieID/entsoe-py",
            license="MIT",
            attribution="Zone geometries: entsoe-py (MIT)",
            tag="reference",
            used_for="Map of the four Swedish price zones",
        ),
        Source(
            key="capacity_maps",
            name="Capacity maps of Svenska kraftnät, Vattenfall Eldistribution and Ellevio",
            provider="Svk / Vattenfall Eldistribution / Ellevio",
            homepage="https://www.vattenfalleldistribution.se/var-verksamhet/om-elnatet/kapacitet/kapacitetskarta/",
            license="Linked and cited only — never scraped",
            attribution="Linked, not reproduced",
            tag="reference",
            used_for="Context on regional grid congestion",
        ),
        Source(
            key="synthetic_grid",
            name="Synthetic LV network",
            provider="Kapacitet+ (this project)",
            homepage="",
            license="Derived from OSM (ODbL share-alike applies if published)",
            attribution="Synthetic topology on real OSM geometry",
            tag="synthetic",
            used_for="Cables, cabinets, transformer and fuses (typical parameters, pandapower standard types)",
        ),
    ]
}
