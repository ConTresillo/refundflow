# RefundFlow local demo (Phase 1)

This slice provides a visible FastAPI page and a development-only identity selector. It does **not** implement OIDC, production authentication, RBAC, document authorization, or RAG access controls.

## Run on Windows

1. From the repository root, double-click `setup-demo.bat` once. It creates `.venv` on the repository drive and installs the project's declared dependencies.
2. Double-click `run-demo.bat`.
3. Open <http://127.0.0.1:8000> in a browser. Close the console window to stop the server.

Manual equivalent from the repository root:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
set REFUNDFLOW_ENV=development
.venv\Scripts\python.exe -m uvicorn app.web:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

The app defaults to `REFUNDFLOW_ENV=production`; the demo mode must be enabled explicitly. The development launcher binds to loopback and disables Uvicorn proxy-header rewriting. The app rejects non-loopback peers and standard forwarded-IP/host/proto headers; login/logout require a CSRF token and a matching `Origin` header (missing or mismatched origins are rejected). **Do not run behind a reverse proxy, tunnel, or multi-worker server**; the simulator cannot establish a trusted client address through an intermediary.

## What to try

Choose Alice Chen or Bob Patel in the Northstar Retail tenant, or Dana Ross in Contoso Logistics. The page displays the selected fixture's tenant, groups, and informational demo role. `GET /api/me` returns the server-resolved identity; `/health/live` and `/health/ready` expose liveness/readiness. The browser login/logout actions use CSRF tokens. Only identities loaded from `app/dev_identities.json` can be selected; client-supplied identity, tenant, group, and role headers are ignored.

## Security and fidelity limits

- This is a *simulator*, not an OIDC login: users can choose any seeded identity. There are no passwords, external identity provider, signed JWTs, or authorization decisions.
- The identity mapping is behind an `IdentityPort`, but no OIDC adapter or issuer/client configuration exists yet. Demo roles are display-only; no document access is granted or denied by them.
- Session IDs are random opaque tokens stored server-side in process memory. Cookies are HttpOnly, SameSite=Lax, and expire after one hour. Sessions are lost on restart; use one Uvicorn worker. This local HTTP cookie is not suitable for production or multi-instance deployment.
- The fixture data is synthetic. No customer records or external service calls are used.
- Docker is not required for this slice. The current environment has no Docker executable or configured OIDC provider.

With `REFUNDFLOW_ENV=production`, the root page and readiness check report that no trusted identity provider is configured; development login and `/api/me` are disabled. Do not expose this app publicly.
