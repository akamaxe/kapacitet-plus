"""Kapacitet+: digital twin of a Swedish LV neighbourhood.

Compares the conservative worst-case hosting capacity used in connection studies with
Dynamic Operating Envelopes (DOE) on a synthetic LV grid built on real OpenStreetMap
buildings, driven by real SMHI weather and SE3 spot prices.
"""

from importlib.metadata import PackageNotFoundError, version

try:  # one source of truth: the version in pyproject.toml, as installed
    __version__ = version("kapacitet")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
