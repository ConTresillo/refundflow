from importlib.util import find_spec


def test_visible_web_application_entrypoint_exists():
    assert find_spec("app.web") is not None, (
        "RefundFlow web application entrypoint is not implemented"
    )


def test_homepage_labels_the_development_identity_simulator(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    response = TestClient(create_app(), client=("127.0.0.1", 8000)).get("/")

    assert response.status_code == 200
    assert "Development-only identity simulator" in response.text


def test_homepage_escapes_identity_labels(monkeypatch):
    from fastapi.testclient import TestClient

    from app import web_runtime
    from app.identity import DemoIdentity, GroupRef

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    untrusted = DemoIdentity(
        subject_id="unsafe.user",
        display_name='<img src=x onerror="alert(1)">',
        tenant_id="tenant-x",
        tenant_name="<script>bad()</script>",
        groups=(GroupRef(id="group-x", name="<svg onload=alert(1)>"),),
        roles=("<b>admin</b>",),
    )
    monkeypatch.setattr(web_runtime, "load_seeded_identities", lambda: (untrusted,))

    response = TestClient(web_runtime.create_app(), client=("127.0.0.1", 8000)).get("/")

    assert response.status_code == 200
    assert "<img src=x" not in response.text
    assert "<script>bad()</script>" not in response.text
    assert "&lt;img" in response.text
    assert "&lt;script&gt;" in response.text


def test_development_homepage_lists_seeded_tenant_identities(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    response = TestClient(create_app(), client=("127.0.0.1", 8000)).get("/")

    assert response.status_code == 200
    assert "Alice Chen" in response.text
    assert "Northstar Retail" in response.text
    assert "Support Agents" in response.text
    assert "Bob Patel" in response.text
    assert "Billing Analysts" in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_development_identity_login_resolves_server_seeded_tenant_context(monkeypatch):
    import base64
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(
        create_app(), client=("127.0.0.1", 8000), follow_redirects=False
    )
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None, "the dev login form must have an anti-CSRF token"
    assert 'data-subject-id="alice.chen"' in homepage.text

    login = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )

    assert login.status_code == 303
    assert login.headers["location"] == "/"
    me = client.get("/api/me")
    assert me.json() == {
        "subject_id": "alice.chen",
        "display_name": "Alice Chen",
        "tenant_id": "northstar-retail",
        "tenant_name": "Northstar Retail",
        "groups": [{"id": "support-agents", "name": "Support Agents"}],
        "roles": ["support-agent"],
    }
    assert me.headers.get("cache-control") == "no-store"
    session_cookie = client.cookies.get("refundflow_dev_session")
    assert session_cookie is not None
    payload = session_cookie.split(".", 1)[0]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert b"alice.chen" not in decoded


def test_development_session_cookie_is_http_only_same_site_and_expires(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    response = TestClient(create_app(), client=("127.0.0.1", 8000)).get("/")

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=3600" in cookie


def test_dev_identity_mode_rejects_non_loopback_clients(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(create_app(), client=("198.51.100.9", 8000))

    response = client.get("/")

    assert response.status_code == 403
    assert response.json()["detail"] == "Development identity mode is loopback-only."


def test_development_mode_rejects_forwarded_proxy_headers(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(create_app(), client=("127.0.0.1", 8000))

    response = client.get("/", headers={"X-Forwarded-For": "198.51.100.9"})

    assert response.status_code == 403
    assert "proxy headers" in response.json()["detail"].lower()


def test_client_identity_headers_do_not_authenticate_and_unknown_subject_is_rejected(
    monkeypatch,
):
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(create_app(), client=("127.0.0.1", 8000))
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None

    assert (
        client.get(
            "/api/me",
            headers={
                "X-User-ID": "alice.chen",
                "X-Tenant-ID": "northstar-retail",
                "X-Groups": "support-agents",
                "X-Roles": "admin",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/dev/login",
            json={"subject_id": "alice.chen"},
            headers={"origin": "http://testserver"},
        ).status_code
        == 403
    )
    unknown = client.post(
        "/dev/login",
        json={"subject_id": "root"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert unknown.status_code == 400
    assert client.get("/api/me").status_code == 401


def test_cross_origin_login_is_rejected_even_with_a_valid_csrf_token(monkeypatch):
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(
        create_app(),
        client=("127.0.0.1", 8000),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    )
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None

    response = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert client.get("/api/me").status_code == 401


def test_browser_mutations_require_origin_header(monkeypatch):
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(
        create_app(), client=("127.0.0.1", 8000), follow_redirects=False
    )
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None

    login_without_origin = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1)},
    )
    assert login_without_origin.status_code == 403

    login = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert login.status_code == 303

    dashboard = client.get("/")
    dashboard_csrf = re.search(
        r'<meta name="csrf-token" content="([^"]+)"', dashboard.text
    )
    assert dashboard_csrf is not None
    logout_without_origin = client.post(
        "/dev/logout", headers={"x-csrf-token": dashboard_csrf.group(1)}
    )
    assert logout_without_origin.status_code == 403
    assert client.get("/api/me").status_code == 200

    logout = client.post(
        "/dev/logout",
        headers={
            "x-csrf-token": dashboard_csrf.group(1),
            "origin": "http://testserver",
        },
    )
    assert logout.status_code == 303


def test_development_mode_fails_startup_if_identity_fixture_is_invalid(monkeypatch):
    import pytest

    from app import web_runtime

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")

    def invalid_fixture():
        raise ValueError("invalid identity fixture")

    monkeypatch.setattr(web_runtime, "load_seeded_identities", invalid_fixture)
    with pytest.raises(ValueError, match="invalid identity fixture"):
        web_runtime.create_app()


def test_production_mode_disables_simulated_identity_and_reports_not_ready():
    from fastapi.testclient import TestClient

    from app.web import create_app

    client = TestClient(
        create_app(environment="production"), client=("127.0.0.1", 8000)
    )

    assert client.get("/").status_code == 503
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 503
    assert (
        client.post("/dev/login", json={"subject_id": "alice.chen"}).status_code == 404
    )
    assert client.get("/api/me").status_code == 404


def test_logout_requires_csrf_and_revokes_the_session(monkeypatch):
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REFUNDFLOW_ENV", "development")
    from app.web import create_app

    client = TestClient(
        create_app(), client=("127.0.0.1", 8000), follow_redirects=False
    )
    initial_page = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', initial_page.text)
    assert csrf is not None
    original_cookie = client.cookies.get("refundflow_dev_session")
    login = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert login.status_code == 303
    authenticated_cookie = client.cookies.get("refundflow_dev_session")
    assert authenticated_cookie is not None
    assert authenticated_cookie != original_cookie

    dashboard = client.get("/")
    dashboard_csrf = re.search(
        r'<meta name="csrf-token" content="([^"]+)"', dashboard.text
    )
    assert dashboard_csrf is not None
    assert (
        client.post("/dev/logout", headers={"origin": "http://testserver"}).status_code
        == 403
    )
    logout = client.post(
        "/dev/logout",
        headers={
            "x-csrf-token": dashboard_csrf.group(1),
            "origin": "http://testserver",
        },
    )

    assert logout.status_code == 303
    assert client.get("/api/me").status_code == 401

    client.cookies.set("refundflow_dev_session", authenticated_cookie)
    replay = client.post(
        "/dev/login",
        json={"subject_id": "bob.patel"},
        headers={
            "x-csrf-token": dashboard_csrf.group(1),
            "origin": "http://testserver",
        },
    )
    assert replay.status_code == 403
    assert client.get("/api/me").status_code == 401
