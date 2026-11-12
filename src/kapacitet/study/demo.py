"""A synthetic study to try the tools on: three stations from pandapower's LV benchmark networks.

The networks are built at run time by pandapower itself (`create_dickert_lv_network` and two of
the Kerber `kb_extrem_*` networks), written as the export contract through
importer/from_pandapower.py, and handed to the study exactly as a DSO's folders would be. The fuse
of each house, its roof area and the coordinates are drawn here from a fixed seed, and so is the
service cable pandapower draws at random for each house of the Kerber networks
(importer/templates.seeded), so the demo is the same everywhere; every station says it is synthetic
in its provenance.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from ..importer.templates import FUSE_SHARE, FUSES, customer_map, layout, seeded
from . import Study, init_study
from .intake import add_contract, check, split

NAME = "Demo DSO (synthetic)"


def _networks() -> list[tuple[str, str, str, Callable]]:
    import pandapower.networks as pn

    return [
        ("demo-dickert", "Dickert LV network, middle feeders",
         "pandapower.networks.create_dickert_lv_network(feeders_range='middle', customer='multiple', "
         "case='average', trafo_type_name='0.25 MVA 10/0.4 kV')",
         lambda: pn.create_dickert_lv_network(feeders_range="middle", linetype="cable", customer="multiple",
                                              case="average", trafo_type_name="0.25 MVA 10/0.4 kV")),
        ("demo-landnetz", "Kerber Landnetz, cable", "pandapower.networks.kb_extrem_landnetz_kabel_trafo()",
         pn.kb_extrem_landnetz_kabel_trafo),
        ("demo-dorfnetz", "Kerber Dorfnetz", "pandapower.networks.kb_extrem_dorfnetz()", pn.kb_extrem_dorfnetz),
    ]


def create(path: str | Path, allow_git: bool = False) -> Study:
    """A new study with the three synthetic stations imported and checked (not yet built)."""
    import tempfile

    from ..config import settings
    from ..importer.from_pandapower import contract_tables, write_contract

    cfg = settings()
    ref = cfg.site
    study = init_study(path, NAME, allow_git=allow_git,
                       site={"name": "Synthetic stations near the reference site", "lat": ref.center_lat,
                             "lon": ref.center_lon, "temperature_station": cfg.temperature_station,
                             "zone": ref.bidding_zone})
    for k, (sid, name, generator, make) in enumerate(_networks()):
        with seeded(7100 + k):       # pandapower draws the Kerber networks' service cables at random
            net = make()
        tables = contract_tables(net, 0, customer_map(net, seed=7100 + k))
        # the stations sit about 1.5 km apart, around the reference site
        angle = 2 * math.pi * k / 3
        lat = ref.center_lat + 1500.0 * math.sin(angle) / 111_320.0
        lon = ref.center_lon + 1500.0 * math.cos(angle) / (111_320.0 * math.cos(math.radians(ref.center_lat)))
        tables["buses"] = layout(tables, lat, lon)
        origin = {"format": "pandapower", "name": f"{name} (synthetic)", "generator": generator,
                  "synthetic": True,
                  "synthetic_parts": ["fuses and roof areas: drawn from a fixed seed for the demo",
                                      "coordinates: a schematic layout around the reference site"]}
        with tempfile.TemporaryDirectory() as tmp:
            write_contract(tables, tmp)
            add_contract(study, tmp, sid, origin=origin)
    split(study)
    check(study)
    return study


__all__ = ["FUSES", "FUSE_SHARE", "NAME", "create", "customer_map", "layout"]
