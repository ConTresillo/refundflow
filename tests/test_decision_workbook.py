"""App-level wiring check for the decision-run API (P6).

Boots the real :func:`app.web_runtime.create_app` (development mode) and
verifies the decision-run routes are registered on the FastAPI app and that an
unauthenticated request is refused before any business logic runs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.web import create_app


def test_decision_routes_registered_in_app():
    app = create_app(environment="development")
    paths = {route.path for route in app.routes}
    assert any(path.startswith("/api/decision-runs") for path in paths)


def test_decision_routes_require_authentication():
    app = create_app(environment="development")
    # Fake a loopback client so the dev-mode loopback gate passes, exposing the
    # real authentication gate (no session -> 401).
    client = TestClient(app, client=("127.0.0.1", 12345))
    response = client.post(
        "/api/decision-runs",
        json={
            "tenant_id": "northstar-retail",
            "actor_subject_id": "alice.chen",
            "question": "Is this customer eligible for a refund?",
            "evidence": [
                {
                    "policy_id": "billing-refunds",
                    "version": "v1",
                    "section_id": "s1",
                    "text": "Refund claims require a payment reference.",
                }
            ],
            "as_of": "2026-11-22",
        },
    )
    assert response.status_code == 401
