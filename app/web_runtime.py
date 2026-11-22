import html
import ipaddress
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated

from langgraph.checkpoint.sqlite import SqliteSaver

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.dev_sessions import DevelopmentSession, DevelopmentSessionStore
from app.identity import (
    DevelopmentIdentityAdapter,
    IdentityPort,
    load_seeded_identities,
)
from app.policy_change_service import PolicyChangeService

SESSION_COOKIE = "refundflow_dev_session"
SESSION_TTL_SECONDS = 3600
DEVELOPMENT_PROXY_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "x-real-ip",
)


class DevLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1, max_length=128)


def create_app(
    environment: str | None = None,
    *,
    policy_service: PolicyChangeService | None = None,
) -> FastAPI:
    runtime_environment = environment or os.getenv("REFUNDFLOW_ENV", "production")
    if runtime_environment not in {"development", "production"}:
        raise ValueError("REFUNDFLOW_ENV must be 'development' or 'production'")
    dev_enabled = runtime_environment == "development"
    policy_lab_enabled = os.getenv("REFUNDFLOW_POLICY_LAB") == "1"
    evidence_explorer_enabled = dev_enabled
    if policy_service is not None and not dev_enabled:
        raise ValueError("the policy lab is available only in development mode")
    if policy_lab_enabled and not dev_enabled:
        raise ValueError("REFUNDFLOW_POLICY_LAB requires REFUNDFLOW_ENV=development")
    if policy_service is None and (policy_lab_enabled or evidence_explorer_enabled):
        from app.policy_workspace import (
            create_local_policy_service,
            seed_development_evidence_grants,
        )

        configured_data_dir = os.getenv("REFUNDFLOW_DATA_DIR")
        data_dir = (
            Path(configured_data_dir).expanduser()
            if configured_data_dir
            else Path(__file__).resolve().parents[1] / "var"
        )
        policy_service = create_local_policy_service(data_dir)
        seed_development_evidence_grants(policy_service)

    app = FastAPI(title="RefundFlow")
    session_store = DevelopmentSessionStore(ttl_seconds=SESSION_TTL_SECONDS)
    adapter = (
        DevelopmentIdentityAdapter(
            load_seeded_identities(), session_store, SESSION_COOKIE
        )
        if dev_enabled
        else None
    )
    app.state.identity_adapter = adapter
    app.state.identity_mode = "development-simulated" if dev_enabled else "unconfigured"
    app.state.session_store = session_store
    app.state.policy_service = policy_service

    @app.middleware("http")
    async def restrict_development_mode_to_loopback(request: Request, call_next):
        if dev_enabled:
            if any(header in request.headers for header in DEVELOPMENT_PROXY_HEADERS):
                return JSONResponse(
                    {
                        "detail": "Forwarded proxy headers are unsupported in development mode."
                    },
                    status_code=403,
                )
            client = request.client
            try:
                local_client = (
                    client is not None and ipaddress.ip_address(client.host).is_loopback
                )
            except ValueError:
                local_client = False
            if not local_client:
                return JSONResponse(
                    {"detail": "Development identity mode is loopback-only."},
                    status_code=403,
                )
        return await call_next(request)

    def ensure_development_mode() -> None:
        if not dev_enabled:
            raise HTTPException(status_code=404, detail="Not found")

    def request_session(request: Request) -> DevelopmentSession | None:
        return session_store.get(request.cookies.get(SESSION_COOKIE))

    def require_same_origin(request: Request) -> None:
        supplied_origin = request.headers.get("origin")
        if supplied_origin is None:
            raise HTTPException(status_code=403, detail="Origin header required")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if supplied_origin != expected_origin:
            raise HTTPException(status_code=403, detail="Cross-origin request denied")

    def require_csrf(
        session: DevelopmentSession | None,
        supplied_token: str | None,
    ) -> DevelopmentSession:
        if (
            session is None
            or not supplied_token
            or not secrets.compare_digest(session.csrf_token, supplied_token)
        ):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        return session

    def attach_session_cookie(response: Response, session: DevelopmentSession) -> None:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session.session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )

    def current_identity(request: Request):
        ensure_development_mode()
        identity_port: IdentityPort = request.app.state.identity_adapter
        identity = identity_port.authenticate(request)
        if identity is None:
            raise HTTPException(status_code=401, detail="Sign-in required")
        return identity

    if policy_service is not None:
        from app.evidence_run import EvidenceRunRepository
        from app.evidence_queue import EvidenceQueue
        from app.policy_workspace import build_evidence_scope
        from app.policy_run_routes import register_evidence_run_routes

        evidence_repository = EvidenceRunRepository(policy_service._database)
        # The async LangGraph evidence run is wired at startup behind an async
        # SQLite checkpointer (AsyncSqliteSaver); it is created here with a
        # deferred runner that is assigned on startup, after the event loop is up.
        evidence_queue = EvidenceQueue(evidence_repository, runner=None)
        app.state.evidence_repository = evidence_repository
        app.state.evidence_queue = evidence_queue
        register_evidence_run_routes(
            app,
            policy_service,
            evidence_queue,
            current_identity=current_identity,
            request_session=request_session,
            require_same_origin=require_same_origin,
            require_csrf=require_csrf,
        )
    else:
        evidence_queue = None
        app.state.evidence_queue = None

    if policy_service is not None:
        async def _startup_evidence_run() -> None:
            from app.evidence_run import EvidenceRunRepository
            from app.evidence_runner import EvidenceRunner, build_evidence_graph
            from app.evidence_queue import EvidenceQueue
            from app.policy_workspace import build_evidence_scope
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            database = policy_service._database
            conn = await aiosqlite.connect(database)
            conn.row_factory = sqlite3.Row
            checkpointer = AsyncSqliteSaver(conn)
            graph = build_evidence_graph(
                retriever=policy_service._retriever,
                scope=build_evidence_scope(policy_service),
                checkpointer=checkpointer,
            )
            evidence_runner = EvidenceRunner(
                repository=evidence_repository,
                graph=graph,
                checkpointer=checkpointer,
                retriever=policy_service._retriever,
                scope=build_evidence_scope(policy_service),
            )
            evidence_queue = app.state.evidence_queue
            evidence_queue._runner = evidence_runner
            app.state.evidence_runner = evidence_runner
            app.state.evidence_queue = evidence_queue
            app.state.evidence_conn = conn

        async def _shutdown_evidence_run() -> None:
            conn = app.state.evidence_conn
            if conn is not None:
                await conn.close()
                app.state.evidence_conn = None

        app.on_event("startup")(_startup_evidence_run)
        app.on_event("shutdown")(_shutdown_evidence_run)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        if not dev_enabled:
            return HTMLResponse(
                "<!doctype html><html><head><title>RefundFlow</title></head>"
                "<body><h1>RefundFlow</h1><p>No trusted identity provider is configured.</p></body></html>",
                status_code=503,
            )

        session = request_session(request)
        new_session = session is None
        if session is None:
            session = session_store.create()
        identity_port: DevelopmentIdentityAdapter = request.app.state.identity_adapter
        identity = identity_port.authenticate(request)
        if identity is None:
            cards = "".join(
                '<article class="identity-card"><h2>'
                + html.escape(item.display_name)
                + "</h2><p>Tenant: "
                + html.escape(item.tenant_name)
                + "</p><p>Groups: "
                + html.escape(", ".join(group.name for group in item.groups))
                + '</p><button type="button" class="identity-choice" data-subject-id="'
                + html.escape(item.subject_id, quote=True)
                + '">Continue as '
                + html.escape(item.display_name)
                + "</button></article>"
                for item in identity_port.identities
            )
            body = (
                "<h1>RefundFlow</h1>"
                '<p role="alert">Development-only identity simulator; this is not OIDC authentication.</p>'
                "<p>Choose a seeded identity. No user, tenant, group, or role headers are accepted.</p>"
                "<h2>Available seeded identities</h2>"
                + cards
                + '<p id="login-status" aria-live="polite"></p>'
                "<script>"
                "const csrf=document.querySelector('meta[name=csrf-token]').content;"
                "document.querySelectorAll('.identity-choice').forEach(button=>button.addEventListener('click',async()=>{"
                "const response=await fetch('/dev/login',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({subject_id:button.dataset.subjectId})});"
                "if(response.ok){window.location.assign('/evidence');}else{document.getElementById('login-status').textContent='Sign-in failed ('+response.status+').';}}));"
                "</script>"
            )
        else:
            body = (
                "<h1>RefundFlow</h1>"
                '<p role="status">Development-only simulated identity.</p>'
                "<h2>Signed in as "
                + html.escape(identity.display_name)
                + "</h2><dl><dt>Tenant</dt><dd>"
                + html.escape(identity.tenant_name)
                + " ("
                + html.escape(identity.tenant_id)
                + ")</dd><dt>Groups</dt><dd>"
                + html.escape(", ".join(group.name for group in identity.groups))
                + "</dd><dt>Demo roles</dt><dd>"
                + html.escape(", ".join(identity.roles))
                + "</dd></dl>"
                + (
                    '<p><a href="/evidence">Open the Evidence Explorer</a></p>'
                    '<p><a href="/policies">Open the local Policy Change Lab</a></p>'
                    if policy_service is not None
                    else ""
                )
                + (
                    "<p>Evidence search checks your current document grants before retrieval. "
                    "Demo role labels above are informational only; policy administration permissions are separate.</p>"
                    if policy_service is not None
                    else "<p>Authorization and document access are not available in this mode.</p>"
                )
                + '<button type="button" id="logout">Sign out</button>'
                '<p id="login-status" aria-live="polite"></p>'
                "<script>"
                "const csrf=document.querySelector('meta[name=csrf-token]').content;"
                "document.getElementById('logout').addEventListener('click',async()=>{"
                "const response=await fetch('/dev/logout',{method:'POST',headers:{'X-CSRF-Token':csrf}});"
                "if(response.ok){window.location.assign('/');}else{document.getElementById('login-status').textContent='Sign-out failed ('+response.status+').';}});"
                "</script>"
            )
        response = HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="'
            + html.escape(session.csrf_token, quote=True)
            + '"><title>RefundFlow</title><style>body{font:16px system-ui;max-width:860px;margin:3rem auto;padding:0 1rem;color:#18202b;background:#f4f7fb}main{background:white;padding:2rem;border-radius:16px;box-shadow:0 8px 30px #13243a12}article{border:1px solid #d9e1eb;border-radius:12px;padding:1rem;margin:1rem 0}button{padding:.65rem 1rem;border-radius:8px;border:0;background:#2159a6;color:white;cursor:pointer}p[role=alert]{background:#fff4d2;padding:.75rem;border-radius:8px}</style></head><body><main>'
            + body
            + "</main></body></html>",
            headers={"Cache-Control": "no-store"},
        )
        if new_session:
            attach_session_cookie(response, session)
        return response

    @app.post("/dev/login")
    async def development_login(
        request: Request,
        payload: DevLoginRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> RedirectResponse:
        ensure_development_mode()
        require_same_origin(request)
        previous_session = require_csrf(request_session(request), x_csrf_token)
        identity_port: DevelopmentIdentityAdapter = request.app.state.identity_adapter
        if identity_port.for_subject(payload.subject_id) is None:
            raise HTTPException(status_code=400, detail="Unknown seeded identity")
        session = session_store.rotate(previous_session.session_id, payload.subject_id)
        if session is None:
            raise HTTPException(status_code=403, detail="Login session is stale")
        response = RedirectResponse("/", status_code=303)
        attach_session_cookie(response, session)
        return response

    @app.post("/dev/logout")
    async def development_logout(
        request: Request,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> RedirectResponse:
        ensure_development_mode()
        require_same_origin(request)
        session = require_csrf(request_session(request), x_csrf_token)
        session_store.revoke(session.session_id)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(
            key=SESSION_COOKIE,
            path="/",
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return response

    @app.get("/api/me")
    async def who_am_i(request: Request) -> JSONResponse:
        identity = current_identity(request)
        return JSONResponse(
            identity.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
        )

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        if not dev_enabled:
            return JSONResponse(
                {"status": "not_ready", "identity_mode": "unconfigured"},
                status_code=503,
            )
        return JSONResponse(
            {"status": "ready", "identity_mode": "development-simulated"}
        )

    if policy_service is not None:
        from app.policy_routes import register_policy_routes

        register_policy_routes(
            app,
            policy_service,
            current_identity=current_identity,
            request_session=request_session,
            require_same_origin=require_same_origin,
            require_csrf=require_csrf,
        )

    return app


app = create_app()
