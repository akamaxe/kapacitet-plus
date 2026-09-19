"""NumPy 2.x compatibility for pandapower 2.14.

The base environment runs NumPy 2.5, which removed aliases that pandapower 2.14 still uses
(np.Inf, np.in1d, ...). Restoring them here, before pandapower is imported, keeps the
installed pandapower working without changing any package. Aliases are only added when
missing, so nothing changes on older NumPy versions.
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

# cvxpy probes every installed solver; the installed FICO Xpress build fails under NumPy 2.5
# and prints the whole PATH while doing so. It is not used here, so skip it.
import sys as _sys

_sys.modules.setdefault("xpress", None)
_sys.modules.setdefault("cylp", None)  # CBC bindings: binary-incompatible with NumPy 2.5, unused
