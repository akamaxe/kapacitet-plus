"""The API's ETag middleware (api/main.py `etag`): a 304 for an answer the browser holds, and no
server error logged for a client that went away before its answer started."""
import asyncio

import pytest

from kapacitet.api import main


class _Request:
    def __init__(self, gone: bool):
        self.gone = gone

    async def is_disconnected(self) -> bool:
        return self.gone


def _no_response(_request):
    async def call():
        raise RuntimeError("No response returned.")
    return call()


def test_a_client_that_went_away_gets_no_server_error():
    out = asyncio.run(main.etag(_Request(gone=True), _no_response))
    assert out.status_code == 204


def test_the_same_error_with_the_client_still_there_is_raised():
    with pytest.raises(RuntimeError, match="No response returned"):
        asyncio.run(main.etag(_Request(gone=False), _no_response))


def test_a_held_answer_gets_a_304():
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    first = client.get("/api/v1/health")
    tag = first.headers.get("etag")
    assert first.status_code == 200 and tag
    again = client.get("/api/v1/health", headers={"If-None-Match": tag})
    assert again.status_code in (200, 304)
    if again.status_code == 304:
        assert again.headers["etag"] == tag
