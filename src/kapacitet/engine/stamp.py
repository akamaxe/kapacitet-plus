"""With what a results file was computed: the `computed` block of every JSON under results/.

    "computed": {"at": "2026-11-12T09:30:00+00:00",
                 "code": {"version": "0.1.0", "commit": "<git commit, or null outside a checkout>"},
                 "versions": {"kapacitet": ..., "numpy": ..., "pandapower": ..., ...}}

The results of one data folder are written by stages run at different times, and a stage can be run
again alone (`compute --stage extra --only ...`) after the code or a library has moved: without the
block, nothing in a file says which code its figures came from. Every writer of a results file
goes through `stamped`, so a file carries the block of the run that wrote it. A file written before
the block existed has none, and nothing fills one in afterwards: an absent block means "not
recorded", never a guess.
"""

from __future__ import annotations

from datetime import datetime, timezone

LIBRARIES = ("numpy", "scipy", "pandas", "pandapower", "cvxpy", "pvlib", "lightgbm")


def versions() -> dict[str, str]:
    """Library versions behind the numbers, for reproducibility."""
    import importlib.metadata as md

    from .. import __version__

    out = {"kapacitet": __version__}
    for name in LIBRARIES:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:      # an optional one that is not installed (lightgbm)
            pass
    return out


def computed() -> dict:
    """When, by which commit of this code and with which libraries: one results file's block."""
    from ..study import code_version

    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": dict(code_version()),
            "versions": versions()}


def stamped(obj):
    """`obj` with its `computed` block, when it is a results document (a JSON object)."""
    return {**obj, "computed": computed()} if isinstance(obj, dict) else obj


__all__ = ["LIBRARIES", "computed", "stamped", "versions"]
