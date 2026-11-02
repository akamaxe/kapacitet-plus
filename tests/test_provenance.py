"""A credential in a request never reaches an error message or the manifest.

ENTSO-E takes its token as a query parameter, so the URL of every request carries it. An error
message is printed whole by the terminal (and by CI), and data/raw/manifest.json travels with
the published data release; neither may hold the token. No network and no data are needed.
"""

from __future__ import annotations

import pytest
import requests

from kapacitet.ingest import provenance

TOKEN = "0123abcd-not-a-real-token-4567"
ENTSOE = "https://web-api.tp.entsoe.eu/api"


class _Response:
    def __init__(self, status: int, url: str):
        self.status_code = status
        self.url = url


class _Session:
    def __init__(self, outcome):
        self.outcome = outcome

    def request(self, method, url, params=None, data=None, timeout=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_a_refused_request_does_not_print_the_token(monkeypatch):
    # a wrong or expired token: ENTSO-E answers 401, and the response's URL holds the token
    refused = _Response(401, f"{ENTSOE}?securityToken={TOKEN}&documentType=A44")
    monkeypatch.setattr(provenance, "session", lambda: _Session(refused))
    with pytest.raises(provenance.FetchError) as err:
        provenance.http("GET", ENTSOE, params={"securityToken": TOKEN, "documentType": "A44"})
    assert TOKEN not in str(err.value)
    assert "securityToken=***" in str(err.value) and "documentType=A44" in str(err.value)


def test_a_failed_connection_does_not_print_the_token(monkeypatch):
    lost = requests.ConnectionError(
        f"HTTPSConnectionPool(host='web-api.tp.entsoe.eu', port=443): Max retries exceeded with url: "
        f"/api?securityToken={TOKEN}&documentType=A44")
    monkeypatch.setattr(provenance, "session", lambda: _Session(lost))
    with pytest.raises(provenance.FetchError) as err:
        provenance.http("GET", ENTSOE, params={"securityToken": TOKEN}, retries=2, backoff=0)
    assert TOKEN not in str(err.value)


@pytest.mark.parametrize("text", [
    f"?securityToken={TOKEN}", f"?token={TOKEN}&zone=SE3", f"&access_token={TOKEN}",
    f"?api_key={TOKEN}", f"?apikey={TOKEN}", f"&client_secret={TOKEN}'", f"password={TOKEN}",
])
def test_every_credential_like_parameter_is_hidden(text):
    assert TOKEN not in provenance.redact(text)


def test_ordinary_parameters_are_left_alone():
    url = "https://opendata.esett.com/EXP16/Aggregate?mba=10Y1001A1001A46L&start=2025-01-01T00:00:00Z"
    assert provenance.redact(url) == url


def test_the_manifest_never_records_a_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "RAW", tmp_path)
    monkeypatch.setattr(provenance, "MANIFEST", tmp_path / "manifest.json")
    path = tmp_path / "prices" / "entsoe_SE3_2015.xml"
    path.parent.mkdir()
    path.write_bytes(b"<Publication_MarketDocument/>")
    provenance.record("entsoe_SE3_2015", source="entsoe", path=path, url=f"{ENTSOE}?securityToken={TOKEN}",
                      params={"securityToken": TOKEN, "documentType": "A44"})
    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert TOKEN not in manifest
    assert '"documentType": "A44"' in manifest
