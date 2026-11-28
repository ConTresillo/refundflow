"""Focused read-back tests for the monitored-source revision readout.

Verifies that the durable, locally recorded observed revision of the tracked
external source ``refundflow-opportunity`` is read back through the authenticated
application boundary, and that unknown sources and unauthenticated callers fail
closed (deny-by-default): 404 / 401.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.web import create_app

SOURCE_ID = "refundflow-opportunity"
# The tracked source flipped again; the freshest observed revision is pinned.
FRESH_OBSERVED = "64b5ce4cb9a443ac"


@pytest.fixture()
def development_env(monkeypatch):
    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    return monkeypatch


def _login(client: TestClient, subject_id: str, csrf: str) -> None:
    client.post(
        "/dev/login",
        json={"subject_id": subject_id},
        headers={"x-csrf-token": csrf, "origin": "http://testserver"},
    )


def _csrf_from(home: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', home)
    assert match is not None, "the dev sign-in page must expose an anti-CSRF token"
    return match.group(1)


def test_monitored_source_readout_unauthenticated_is_denied(development_env):
    client = TestClient(create_app(), client=("127.0.0.1", 8000))
    response = client.get(f"/api/monitored-sources/{SOURCE_ID}")
    assert response.status_code == 401


def test_monitored_source_unknown_source_fails_closed(development_env):
    client = TestClient(create_app(), client=("127.0.0.1", 8000))
    csrf = _csrf_from(client.get("/").text)
    _login(client, "alice.chen", csrf)
    response = client.get(
        "/api/monitored-sources/some-other-source",
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 404


def test_monitored_source_readout_returns_fresh_observed_revision(development_env):
    client = TestClient(create_app(), client=("127.0.0.1", 8000))
    csrf = _csrf_from(client.get("/").text)
    _login(client, "alice.chen", csrf)

    # Persist the fresh observed revision of the external tracked source into the
    # durable tracker, then read it back through the authenticated readout.
    monitored = client.app.state.monitored_sources
    monitored.record(
        SOURCE_ID,
        FRESH_OBSERVED,
        observed_at="2026-11-27T00:00:00+00:00",
    )

    response = client.get(
        f"/api/monitored-sources/{SOURCE_ID}",
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == SOURCE_ID
    assert body["observed_hash"] == FRESH_OBSERVED
    assert body["status"] == "current"


def test_monitored_source_readout_rejects_cross_origin(development_env):
    client = TestClient(create_app(), client=("127.0.0.1", 8000))
    csrf = _csrf_from(client.get("/").text)
    _login(client, "alice.chen", csrf)

    response = client.get(
        f"/api/monitored-sources/{SOURCE_ID}",
        headers={"origin": "https://attacker.example"},
    )
    assert response.status_code == 403
