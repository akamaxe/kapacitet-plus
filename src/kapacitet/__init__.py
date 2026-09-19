"""Kapacitet+: digital twin of a Swedish LV neighbourhood.

Compares the conservative worst-case hosting capacity used in connection studies with
Dynamic Operating Envelopes (DOE) on a synthetic LV grid built on real OpenStreetMap
buildings, driven by real SMHI weather and SE3 spot prices.
"""

from . import _compat  # noqa: F401  (NumPy 2.x aliases for pandapower 2.14; must run first)

__version__ = "0.1.0"
