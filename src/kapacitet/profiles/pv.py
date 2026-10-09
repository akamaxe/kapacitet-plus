"""Rooftop PV: roof geometry from OSM footprints and hourly AC production with pvlib.

Roof faces: the ridge follows the long side of the footprint's minimum rotated rectangle
(or the short side when OSM says roof:orientation=across); the two faces point perpendicular
to it. PV goes on the face closest to south; when both faces point east/west the array is
split over both. Production per kWp uses STRÅNG irradiance (hour-beginning means, sun position
at mid-interval), Hay–Davies transposition, physical IAM, SAPM cell temperature with SMHI air
temperature, PVWatts DC with standard losses, and a PVWatts inverter.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pvlib
from pyproj import Transformer
from shapely.geometry import Polygon

from ..config import A
from ..paths import processed
from .weather import load_weather

TO_M = Transformer.from_crs(4326, 3006, always_xy=True)


def _bearing(dx: float, dy: float) -> float:
    """Compass bearing (deg, clockwise from north) of a vector in EPSG:3006 (x east, y north)."""
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def roof_geometry(customers: pd.DataFrame) -> pd.DataFrame:
    tilt_default = A("pv_roof_tilt_deg")
    rows = []
    for c in customers.itertuples():
        if not c.polygon:
            # An imported network has no roof outline, so the fallback the contract
            # documents applies: one south-facing pitched face of half the footprint.
            face = float(c.footprint_m2) / 2.0 / math.cos(math.radians(tilt_default))
            kwp = float(np.clip(face * A("pv_usable_roof_fraction") * A("pv_power_density_kwp_m2"),
                                A("pv_min_kwp"), A("pv_max_kwp")))
            rows.append(dict(cid=c.cid, ridge_bearing=90.0, azimuth=180.0, tilt=float(tilt_default),
                             east_west=False, usable_area_m2=round(face, 1), kwp=round(kwp, 2),
                             inverter_kw=round(min(kwp / A("pv_dc_ac_ratio"), float(c.fuse_kva)), 2)))
            continue
        ll = json.loads(c.polygon)
        xs, ys = TO_M.transform([p[0] for p in ll], [p[1] for p in ll])
        poly = Polygon(list(zip(xs, ys)))
        rect = poly.minimum_rotated_rectangle
        pts = list(rect.exterior.coords)
        e1 = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        e2 = (pts[2][0] - pts[1][0], pts[2][1] - pts[1][1])
        long_e, short_e = (e1, e2) if math.hypot(*e1) >= math.hypot(*e2) else (e2, e1)
        ridge = long_e if c.roof_orientation != "across" else short_e
        ridge_bearing = _bearing(*ridge) % 180.0
        flat = c.roof_shape == "flat"
        footprint = float(c.footprint_m2)
        if flat:
            tilt, az1, ew = A("pv_flat_roof_tilt_deg"), 180.0, False
            usable_area = footprint * A("pv_flat_roof_usable_fraction")
        else:
            tilt = tilt_default
            faces = [(ridge_bearing + 90.0) % 360.0, (ridge_bearing + 270.0) % 360.0]
            az1 = min(faces, key=lambda a: abs(a - 180.0))
            ew = abs(az1 - 180.0) > A("pv_east_west_threshold_deg")
            face_area = footprint / 2.0 / math.cos(math.radians(tilt))
            usable_area = face_area * A("pv_usable_roof_fraction") * (2.0 if ew else 1.0)
        kwp = usable_area * A("pv_power_density_kwp_m2")
        kwp = float(np.clip(kwp, A("pv_min_kwp"), A("pv_max_kwp")))
        inverter_kw = min(kwp / A("pv_dc_ac_ratio"), float(c.fuse_kva))
        rows.append(dict(cid=c.cid, ridge_bearing=round(ridge_bearing, 1), azimuth=round(az1, 1),
                         tilt=float(tilt), east_west=bool(ew), usable_area_m2=round(usable_area, 1),
                         kwp=round(kwp, 2), inverter_kw=round(inverter_kw, 2)))
    return pd.DataFrame(rows)


def ac_per_kwp(weather: pd.DataFrame, azimuth: float, tilt: float, east_west: bool) -> np.ndarray:
    """AC output (kW) per kWp DC for one orientation, hour-beginning means (inverter at DC/AC ratio)."""
    times = weather.index + pd.Timedelta(minutes=30)
    zen = weather["zenith"].to_numpy(dtype=float)
    saz = weather["azimuth"].to_numpy(dtype=float)
    dni_extra = pvlib.irradiance.get_extra_radiation(times).to_numpy()
    arrays = [(azimuth, 1.0)] if not east_west else [(azimuth, 0.5), ((azimuth + 180.0) % 360.0, 0.5)]
    dc = np.zeros(len(weather))
    params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["close_mount_glass_glass"]
    for az, share in arrays:
        irr = pvlib.irradiance.get_total_irradiance(
            surface_tilt=tilt, surface_azimuth=az, solar_zenith=zen, solar_azimuth=saz,
            dni=weather["dni"].to_numpy(dtype=float), ghi=weather["ghi"].to_numpy(dtype=float),
            dhi=weather["dhi"].to_numpy(dtype=float), dni_extra=dni_extra, model="haydavies",
            albedo=A("pv_albedo"))
        aoi = pvlib.irradiance.aoi(tilt, az, zen, saz)
        iam = pvlib.iam.physical(aoi)
        eff = np.nan_to_num(irr["poa_direct"] * iam + irr["poa_sky_diffuse"] + irr["poa_ground_diffuse"])
        t_cell = pvlib.temperature.sapm_cell(np.nan_to_num(irr["poa_global"]),
                                             weather["t_air"].to_numpy(dtype=float), A("pv_wind_speed_ms"),
                                             **params)
        dc += share * pvlib.pvsystem.pvwatts_dc(eff, t_cell, pdc0=1.0, gamma_pdc=A("pv_gamma_pdc_per_c"))
    losses = pvlib.pvsystem.pvwatts_losses() / 100.0
    dc = np.clip(dc * (1.0 - losses), 0.0, None)
    pac0 = 1.0 / A("pv_dc_ac_ratio")
    ac = pvlib.inverter.pvwatts(dc, pdc0=pac0 / A("pv_inverter_eff"), eta_inv_nom=A("pv_inverter_eff"))
    return np.clip(np.nan_to_num(ac), 0.0, pac0).astype(np.float32)


def build_pv(roofs: pd.DataFrame, years) -> None:
    """Per-customer AC profiles per kWp (C, H) for each weather year."""
    for y in years:
        w = load_weather(y)
        cache: dict[tuple, np.ndarray] = {}
        out = np.empty((len(roofs), len(w)), dtype=np.float32)
        for i, r in enumerate(roofs.itertuples()):
            key = (round(r.azimuth), r.tilt, r.east_west)
            if key not in cache:
                cache[key] = ac_per_kwp(w, *key)
            out[i] = cache[key]
        np.save(processed("pv", f"ac_per_kwp_{y}.npy"), out)


def load_ac_per_kwp(year: int) -> np.ndarray:
    return np.load(processed("pv", f"ac_per_kwp_{year}.npy"))
