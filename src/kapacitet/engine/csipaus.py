"""A day's export limits as an inverter would receive them: IEEE 2030.5 with the CSIP-AUS extension.

CSIP-AUS (Common Smart Inverter Profile, Australia; SA HB 218:2023 as v1.1a, SA TS 5573:2025 as
v1.2) is the profile SA Power Networks' Flexible Exports trial sent its limits in. Its extension
puts the export limit in DERControlBase as `csipaus:opModExpLimW`, an ActivePower (a value and a
power-of-ten multiplier), inside each DERControl event; DefaultDERControl carries the limit a
device keeps when it holds no event. The element names follow the open schema of BSGIP's envoy
(envoy-schema, sep2/der.py); the namespace is the v1.2 one its README lists.

This is a format modelled on that profile, written with the standard library: it is not
certified, and no device is connected to Kapacitet+. Every document says so in its first lines.
"""

from __future__ import annotations

import hashlib
from xml.etree import ElementTree as ET

NS = "urn:ieee:std:2030.5:ns"
CSIP = "https://csipaus.org/ns/v1.2"
NOTICE = ("Kapacitet+: format modelled on CSIP-AUS v1.2 (IEEE 2030.5); not certified; no device is "
          "connected. Simulated limits; not for operational use.")
ET.register_namespace("", NS)
ET.register_namespace("csipaus", CSIP)


def active_power(watts: float) -> tuple[int, int]:
    """IEEE 2030.5 ActivePower: an Int16 value and a power-of-ten multiplier, the smallest
    multiplier that keeps the value in range (values are rounded down, never above the limit)."""
    w = max(0.0, float(watts))
    multiplier = 0
    while w / 10 ** multiplier > 32767:
        multiplier += 1
    return int(w // 10 ** multiplier), multiplier


def _q(tag: str, ns: str = NS) -> str:
    return f"{{{ns}}}{tag}"


def _limit(parent: ET.Element, watts: float) -> None:
    base = ET.SubElement(parent, _q("DERControlBase"))
    lim = ET.SubElement(base, _q("opModExpLimW", CSIP))
    value, multiplier = active_power(watts)
    ET.SubElement(lim, _q("multiplier")).text = str(multiplier)
    ET.SubElement(lim, _q("value")).text = str(value)


def mrid(*parts: object) -> str:
    """A stable 128-bit identifier for an event, as IEEE 2030.5 mRIDs are (hex)."""
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32].upper()


def der_control_list(cid: int, rows: list[dict], issued_epoch: int, house_label: str = "") -> bytes:
    """One DERControl per hour of the day: start (epoch seconds, UTC), one hour long, the export
    limit in watts. `rows` hold `start_epoch` and `limit_kw`."""
    root = ET.Element(_q("DERControlList"), {"all": str(len(rows)), "results": str(len(rows))})
    root.append(ET.Comment(f" {NOTICE} "))
    for r in rows:
        ev = ET.SubElement(root, _q("DERControl"))
        ET.SubElement(ev, _q("mRID")).text = mrid("kapacitet", cid, r["start_epoch"])
        ET.SubElement(ev, _q("description")).text = f"Export limit, house {cid}{' ' + house_label if house_label else ''}"
        ET.SubElement(ev, _q("creationTime")).text = str(int(issued_epoch))
        status = ET.SubElement(ev, _q("EventStatus"))
        ET.SubElement(status, _q("currentStatus")).text = "0"          # scheduled
        ET.SubElement(status, _q("dateTime")).text = str(int(issued_epoch))
        ET.SubElement(status, _q("potentiallySuperseded")).text = "false"
        interval = ET.SubElement(ev, _q("interval"))
        ET.SubElement(interval, _q("duration")).text = "3600"
        ET.SubElement(interval, _q("start")).text = str(int(r["start_epoch"]))
        _limit(ev, 1000.0 * float(r["limit_kw"]))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def default_der_control(cid: int, default_kw: float) -> bytes:
    """The limit the device keeps when it holds no event: what it falls back to on losing contact."""
    root = ET.Element(_q("DefaultDERControl"))
    root.append(ET.Comment(f" {NOTICE} "))
    ET.SubElement(root, _q("mRID")).text = mrid("kapacitet-default", cid)
    ET.SubElement(root, _q("description")).text = f"Fallback export limit, house {cid}"
    _limit(root, 1000.0 * float(default_kw))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
