"""The map's binary contract: a year of grid state as uint8 blocks, one header to read them.

`web/src/lib/api.ts` (`decodeState`) reads exactly this layout with the scales in the header, so
the two sides change together or the map paints noise. Both are pinned by the same golden file,
`tests/fixtures/state_tiny.json`: `tests/test_state_encoding.py` encodes it here and
`web/src/lib/api.state.test.ts` decodes it there.

Standard library and numpy only, so the operation stage, the queue and the tests can write a state
without importing the engine.
"""

from __future__ import annotations

import numpy as np

V_OFFSET = 0.85          # pu at code 0
V_RANGE = 0.30           # pu across the 255 codes: 0.85–1.15 pu
LOADING_RANGE = 150.0    # % across the 255 codes
P_OFFSET = -25.0         # kW at code 0
P_RANGE = 50.0           # kW across the 255 codes: -25–25 kW
LAYOUT = "time-major blocks: V[H][bus] uint8, loading[H][line] uint8, p[H][cust] uint8"


def _codes(values: np.ndarray, offset: float, span: float) -> np.ndarray:
    return np.clip(np.round((np.asarray(values, dtype=float) - offset) / span * 255), 0, 255).astype(np.uint8)


def encode_state(name: str, v_abs: np.ndarray, line_loading: np.ndarray, p_net: np.ndarray,
                 bus_index: list[int], line_index: list[int], aggregates: dict) -> tuple[dict, bytes]:
    """The header and the bytes of one scenario's state.

    `v_abs` is (bus, hour) in pu, `line_loading` (line, hour) in %, `p_net` (customer, hour) in kW.
    Values outside a block's range are clipped to its ends.
    """
    v_abs = np.asarray(v_abs, dtype=float)
    line_loading = np.asarray(line_loading, dtype=float)
    p_net = np.asarray(p_net, dtype=float)
    hours = int(v_abs.shape[1])
    blob = np.concatenate([
        _codes(v_abs, V_OFFSET, V_RANGE).T.ravel(),
        _codes(line_loading, 0.0, LOADING_RANGE).T.ravel(),
        _codes(p_net, P_OFFSET, P_RANGE).T.ravel(),
    ])
    header = {
        "name": name, "hours": hours, "n_bus": int(v_abs.shape[0]), "n_line": int(line_loading.shape[0]),
        "n_cust": int(p_net.shape[0]),
        "layout": LAYOUT,
        "v_offset": V_OFFSET, "v_scale": V_RANGE / 255, "loading_scale": LOADING_RANGE / 255,
        "p_offset": P_OFFSET, "p_scale": P_RANGE / 255,
        "bus_index": [int(b) for b in bus_index], "line_index": [int(i) for i in line_index],
        "aggregates": aggregates,
    }
    return header, blob.tobytes()
