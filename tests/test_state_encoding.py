"""The map's binary contract, pinned by a golden file that both sides read.

`tests/fixtures/state_tiny.json` holds a tiny synthetic year (3 buses, 2 cables, 2 customers,
4 hours: no OpenStreetMap data), its header, its bytes in base64 and a few decoded probes.
This test encodes it with `engine/stateblob.py`; `web/src/lib/api.state.test.ts` decodes the same
file with `decodeState`. A change to either side without the other fails one of them.

Regenerate the fixture only for a deliberate change of the contract:
    ./.venv/Scripts/python.exe tests/test_state_encoding.py --write
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "state_tiny.json"


def _tiny():
    from kapacitet.engine.stateblob import encode_state

    v = np.array([[1.00, 1.02, 0.97, 1.10],
                  [0.95, 1.05, 1.16, 0.80],     # 1.16 and 0.80 sit outside the range and clip
                  [1.00, 0.99, 1.01, 1.03]])
    loading = np.array([[0.0, 50.0, 100.0, 150.0],
                        [10.0, 75.5, 160.0, 5.0]])
    p = np.array([[-25.0, -5.0, 0.0, 25.0],
                  [3.2, -12.7, 30.0, -30.0]])
    agg = {"trafo_loading": [10.0, 20.0, 30.0, 40.0], "violation": [0, 0, 1, 0], "curtailed_kw": None}
    return encode_state("tiny", v, loading, p, [0, 1, 2], [0, 1], agg)


def _probes(header: dict, blob: bytes) -> list[dict]:
    """A reference reader of the layout, written from its description, not from encode_state."""
    b = np.frombuffer(blob, dtype=np.uint8)
    H, nb, nl, nc = header["hours"], header["n_bus"], header["n_line"], header["n_cust"]
    off_l, off_p = H * nb, H * nb + H * nl
    out = []
    for row, hour in ((0, 0), (1, 2), (1, 3), (2, 1)):
        out.append({"kind": "voltage", "row": row, "hour": hour,
                    "value": header["v_offset"] + b[hour * nb + row] * header["v_scale"]})
    for row, hour in ((0, 3), (1, 1), (1, 2)):
        out.append({"kind": "loading", "row": row, "hour": hour,
                    "value": b[off_l + hour * nl + row] * header["loading_scale"]})
    for row, hour in ((0, 0), (1, 0), (1, 2), (1, 3)):
        out.append({"kind": "power", "row": row, "hour": hour,
                    "value": header["p_offset"] + b[off_p + hour * nc + row] * header["p_scale"]})
    return [{**p, "value": float(p["value"])} for p in out]


def test_state_encoding_matches_the_golden_fixture():
    header, blob = _tiny()
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert header == golden["header"]
    assert base64.b64encode(blob).decode("ascii") == golden["blob_b64"]
    assert len(blob) == header["hours"] * (header["n_bus"] + header["n_line"] + header["n_cust"])


def test_golden_probes_follow_the_layout():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    probes = _probes(golden["header"], base64.b64decode(golden["blob_b64"]))
    assert probes == golden["probes"]
    by = {(p["kind"], p["row"], p["hour"]): p["value"] for p in probes}
    # quantisation keeps a value within half a code of what went in, and clips at the ends
    assert abs(by[("voltage", 0, 0)] - 1.00) <= golden["header"]["v_scale"] / 2 + 1e-12
    assert abs(by[("voltage", 1, 2)] - 1.15) < 1e-9 and abs(by[("voltage", 1, 3)] - 0.85) < 1e-9
    assert abs(by[("loading", 1, 2)] - 150.0) < 1e-9
    assert abs(by[("power", 1, 2)] - 25.0) < 1e-9 and abs(by[("power", 1, 3)] + 25.0) < 1e-9


if __name__ == "__main__" and "--write" in sys.argv:
    h, data = _tiny()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps({"header": h, "blob_b64": base64.b64encode(data).decode("ascii"),
                                   "probes": _probes(h, data)}, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
