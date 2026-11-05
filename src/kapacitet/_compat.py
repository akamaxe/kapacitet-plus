"""NumPy 2.x compatibility for pandapower 2.14.

The project's environment pins NumPy 1.26.4 (constraints.txt), where pandapower 2.14 runs as is
and this module adds nothing. It stays for an install without the constraints, where pip may pick
NumPy 2.x, which removed aliases pandapower 2.14 still uses (np.Inf, np.in1d, ...): they are
restored here, only when missing, before pandapower is imported.
"""

from __future__ import annotations

import numpy as np


def _in1d(ar1, ar2, assume_unique=False, invert=False, **kwargs):
    return np.isin(np.ravel(ar1), ar2, assume_unique=assume_unique, invert=invert, **kwargs)


_ALIASES = {
    "Inf": np.inf,
    "Infinity": np.inf,
    "PINF": np.inf,
    "NINF": -np.inf,
    "infty": np.inf,
    "NaN": np.nan,
    "NAN": np.nan,
    "float_": np.float64,
    "complex_": np.complex128,
    "cfloat": np.complex128,
    "string_": np.bytes_,
    "unicode_": np.str_,
    "in1d": _in1d,
    "row_stack": np.vstack,
    "product": np.prod,
    "cumproduct": np.cumprod,
    "sometrue": np.any,
    "alltrue": np.all,
}
if hasattr(np, "trapezoid"):
    _ALIASES["trapz"] = np.trapezoid

for _name, _value in _ALIASES.items():
    if not hasattr(np, _name):
        setattr(np, _name, _value)

# cvxpy probes every installed solver, and Xpress and CBC (cylp) builds compiled against
# NumPy 1.x fail to import on NumPy 2.x. Neither is used here, so skip both.
import sys as _sys

_sys.modules.setdefault("xpress", None)
_sys.modules.setdefault("cylp", None)
