"""FastAPI endpoints and browser UI for controlled local policy changes."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.dev_sessions import DevelopmentSession
from app.policy_change_service import PolicyChangeError, PolicyChangeService


class PolicyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=64)
    expected_version: str = Field(min_length=1, max_length=32)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    effective_from: str = Field(min_length=10, max_length=10)
    effective_to: str | None = Field(default=None, max_length=10)
    excerpts: list[str] = Field(min_length=1, max_length=20)


class DraftReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diff_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


POLICY_LAB_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="csrf-token" content="__CSRF__">
  <title>RefundFlow policy change lab</title>
  <style>
    body{font:16px system-ui,sans-serif;max-width:1050px;margin:2rem auto;padding:0 1rem;color:#18202b;background:#f4f7fb}
    main{background:white;padding:1.5rem 2rem;border-radius:16px;box-shadow:0 8px 30px #13243a12}
    label{display:block;font-weight:600;margin:.8rem 0 .25rem}input,select,textarea{box-sizing:border-box;width:100%;padding:.65rem;border:1px solid #9aa9bb;border-radius:6px;font:inherit}
    textarea{min-height:12rem;font-family:ui-monospace,monospace}button{margin:.7rem .5rem .7rem 0;padding:.65rem 1rem;border:0;border-radius:7px;background:#2159a6;color:white;cursor:pointer}button:disabled{background:#8493a6}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101a28;color:#e4edf8;padding:1rem;border-radius:8px;max-height:32rem;overflow:auto}
    .notice{background:#fff4d2;padding:.8rem;border-radius:8px}.status{min-height:1.5rem}section{border-top:1px solid #d9e1eb;margin-top:1.5rem;padding-top:1rem}a{color:#2159a6}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:1rem}@media(max-width:650px){.row{grid-template-columns:1fr}}
  </style>
</head>
<body><main>
  <p><a href="/">← Identity demo</a></p>
  <h1>Policy Change Lab</h1>
  <p class="notice">Local synthetic-policy sandbox. Changes are drafted, diffed, explicitly reviewed, then published through SQLite FTS. “Reflected” means the catalog, active chunk snapshot, and searchable FTS rows exactly match. This is lexical retrieval only; no embedding model or answer-generating LLM is connected.</p>
  <p id="status" class="status" role="status" aria-live="polite"></p>
  <section>
    <h2>Current policy snapshot</h2>
    <label for="policy">Policy</label><select id="policy"></select>
    <pre id="snapshot" aria-live="polite"></pre>
  </section>
  <section>
    <h2>Propose a controlled new version</h2>
    <p>Only title, effective dates, and clauses are editable here. Policy ID, department, and provenance stay fixed. A draft does not change the active catalog or RAG index.</p>
    <div class="row"><div><label for="new-version">New immutable version</label><input id="new-version" required maxlength="32" placeholder="e.g. 1.1"></div><div><label for="title">Title</label><input id="title" required maxlength="200"></div></div>
    <div class="row"><div><label for="effective-from">Effective from (ISO date)</label><input id="effective-from" type="date" required></div><div><label for="effective-to">Effective to (optional)</label><input id="effective-to" type="date"></div></div>
    <label for="excerpts">Policy clauses (separate clauses with a blank line)</label><textarea id="excerpts" required></textarea>
    <button id="draft">Create draft and show exact diff</button>
  </section>
  <section id="draft-panel" hidden>
    <h2>Review this exact change</h2>
    <p id="draft-meta"></p><pre id="diff"></pre>
    <button id="review">Acknowledge reviewed diff</button>
    <button id="publish" disabled>Publish and re-index</button>
    <pre id="publish-report"></pre>
  </section>
  <section>
    <h2>Verify retrieval</h2>
    <label for="query">Search this policy's active indexed clauses</label><input id="query" placeholder="Enter terms from a policy clause">
    <button id="search">Search current RAG evidence</button><pre id="search-results"></pre>
  </section>
  <section><h2>Publication history</h2><pre id="history"></pre></section>
</main>
<script>
const csrf=document.querySelector('meta[name="csrf-token"]').content;
const statusNode=document.getElementById('status');
const policySelect=document.getElementById('policy');
const snapshotNode=document.getElementById('snapshot');
let policies=[];let currentDraft=null;
function setStatus(text){statusNode.textContent=text;}
async function api(url,options={}){
  const method=(options.method||'GET').toUpperCase();
  const headers=new Headers(options.headers||{});
  if(method!=='GET')headers.set('X-CSRF-Token',csrf);
  const response=await fetch(url,{...options,headers,credentials:'same-origin'});
  const body=await response.json().catch(()=>({detail:'Non-JSON response'}));
  if(!response.ok)throw new Error(body.detail||`HTTP ${response.status}`);
  return body;
}
function selected(){return policies.find(item=>item.policy_id===policySelect.value);}
function renderSnapshot(){
  const item=selected();if(!item)return;
  document.getElementById('title').value=item.title;
  document.getElementById('effective-from').value=item.effective_from;
  document.getElementById('effective-to').value=item.effective_to||'';
  document.getElementById('excerpts').value=item.excerpts.join('\n\n');
  snapshotNode.textContent=JSON.stringify({policy_id:item.policy_id,department:item.department_id,version:item.version,status:item.status,content_hash:item.content_hash,source_state:item.source_state,source_diff:item.source_diff,index_reflected:item.index_reflected,index_verification:item.index_verification,outcome:item.outcome},null,2);
}
async function loadPolicies(){
  policies=await api('/api/policies');policySelect.replaceChildren();
  for(const item of policies){const option=document.createElement('option');option.value=item.policy_id;option.textContent=`${item.policy_id} — v${item.version} — ${item.index_reflected?'reflected':'MISMATCH'}`;policySelect.append(option);}
  renderSnapshot();
}
async function loadHistory(){document.getElementById('history').textContent=JSON.stringify(await api('/api/policy-change-runs'),null,2);}
policySelect.addEventListener('change',renderSnapshot);
document.getElementById('draft').addEventListener('click',async()=>{
  try{
    const item=selected();const expires=document.getElementById('effective-to').value;
    currentDraft=await api('/api/policy-drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({policy_id:item.policy_id,expected_version:item.version,expected_hash:item.content_hash,new_version:document.getElementById('new-version').value.trim(),title:document.getElementById('title').value,effective_from:document.getElementById('effective-from').value,effective_to:expires||null,excerpts:document.getElementById('excerpts').value.split(/\n\s*\n/).map(value=>value.trim()).filter(Boolean)})});
    document.getElementById('draft-panel').hidden=false;document.getElementById('draft-meta').textContent=`Draft ${currentDraft.draft_id}: ${currentDraft.base_version} → ${currentDraft.new_version}; not yet active.`;document.getElementById('diff').textContent=currentDraft.diff;document.getElementById('publish-report').textContent='';document.getElementById('publish').disabled=true;setStatus('Draft saved. Active RAG is unchanged until you review and publish.');
  }catch(error){setStatus(error.message);}
});
document.getElementById('review').addEventListener('click',async()=>{
  try{currentDraft=await api(`/api/policy-drafts/${currentDraft.draft_id}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({diff_hash:currentDraft.diff_hash})});document.getElementById('publish').disabled=false;setStatus('Exact diff acknowledged. You may publish this version.');}
  catch(error){setStatus(error.message);}
});
document.getElementById('publish').addEventListener('click',async()=>{
  try{const report=await api(`/api/policy-drafts/${currentDraft.draft_id}/publish`,{method:'POST'});document.getElementById('publish-report').textContent=JSON.stringify(report,null,2);setStatus(report.index_reflected?'Published and verified in the active lexical RAG index.':'Publish completed but index verification failed.');await loadPolicies();await loadHistory();}
  catch(error){setStatus(error.message);}
});
document.getElementById('search').addEventListener('click',async()=>{
  try{const params=new URLSearchParams({policy_id:policySelect.value,query:document.getElementById('query').value,as_of:new Date().toISOString().slice(0,10)});document.getElementById('search-results').textContent=JSON.stringify(await api(`/api/policy-search?${params}`),null,2);}
  catch(error){setStatus(error.message);}
});
loadPolicies().then(loadHistory).catch(error=>setStatus(error.message));
</script>
</body></html>"""


def register_policy_routes(
    app: FastAPI,
    service: PolicyChangeService,
    *,
    current_identity: Callable[[Request], Any],
    request_session: Callable[[Request], DevelopmentSession | None],
    require_same_origin: Callable[[Request], None],
    require_csrf: Callable[[DevelopmentSession | None, str | None], Any],
) -> None:
    """Attach the authenticated local policy lab to one development app instance."""

    def ensure_identity(request: Request) -> None:
        current_identity(request)

    def authorize_mutation(request: Request, token: str | None) -> None:
        ensure_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), token)

    def service_error(error: PolicyChangeError) -> HTTPException:
        status_code = 404 if "not found" in str(error) else 409
        return HTTPException(status_code=status_code, detail=str(error))

    @app.get("/policies", response_class=HTMLResponse)
    def policy_lab_page(request: Request) -> HTMLResponse:
        ensure_identity(request)
        session = request_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign-in required")
        content = POLICY_LAB_HTML.replace(
            "__CSRF__", html.escape(session.csrf_token, quote=True)
        )
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

    @app.get("/api/policies")
    def policy_list(request: Request) -> JSONResponse:
        ensure_identity(request)
        return JSONResponse(
            service.list_policies(), headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/policy-drafts")
    def draft_list(request: Request) -> JSONResponse:
        ensure_identity(request)
        return JSONResponse(
            service.list_drafts(), headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/policy-drafts", status_code=201)
    def create_policy_draft(
        request: Request,
        payload: PolicyDraftRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        authorize_mutation(request, x_csrf_token)
        try:
            draft = service.create_draft(**payload.model_dump())
        except PolicyChangeError as error:
            raise service_error(error) from error
        return JSONResponse(
            draft, status_code=201, headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/policy-drafts/{draft_id}")
    def read_policy_draft(request: Request, draft_id: str) -> JSONResponse:
        ensure_identity(request)
        try:
            draft = service.get_draft(draft_id)
        except PolicyChangeError as error:
            raise service_error(error) from error
        return JSONResponse(draft, headers={"Cache-Control": "no-store"})

    @app.post("/api/policy-drafts/{draft_id}/review")
    def review_policy_draft(
        request: Request,
        draft_id: str,
        payload: DraftReviewRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        authorize_mutation(request, x_csrf_token)
        try:
            draft = service.review_draft(draft_id, diff_hash=payload.diff_hash)
        except PolicyChangeError as error:
            raise service_error(error) from error
        return JSONResponse(draft, headers={"Cache-Control": "no-store"})

    @app.post("/api/policy-drafts/{draft_id}/publish")
    def publish_policy_draft(
        request: Request,
        draft_id: str,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        authorize_mutation(request, x_csrf_token)
        try:
            report = service.publish_draft(draft_id)
        except PolicyChangeError as error:
            raise service_error(error) from error
        return JSONResponse(report, headers={"Cache-Control": "no-store"})

    @app.get("/api/policy-change-runs")
    def policy_change_history(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        ensure_identity(request)
        return JSONResponse(
            service.history(limit=limit), headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/policy-search")
    def policy_search(
        request: Request,
        query: Annotated[str, Query(min_length=1, max_length=300)],
        as_of: Annotated[str, Query(min_length=10, max_length=10)],
        department_id: str | None = None,
        policy_id: str | None = None,
        top_k: Annotated[int, Query(ge=1, le=10)] = 5,
    ) -> JSONResponse:
        identity = current_identity(request)
        if department_id is None and policy_id is None:
            raise HTTPException(status_code=422, detail="department_id is required for cross-policy search")
        if department_id is None:
            assert policy_id is not None
            service_for_lookup = request.scope["app"].state.policy_service
            record = service_for_lookup._catalog.current(policy_id)
            if record is None:
                raise HTTPException(status_code=404, detail="unknown policy")
            department_id = record["department_id"]
        if department_id is None:
            raise HTTPException(status_code=422, detail="department_id is required for cross-policy search")
        try:
            results = service.search_for_identity(
                identity.subject_id, identity.tenant_id,
                query, department_id=department_id, policy_id=policy_id, as_of=as_of, top_k=top_k,
            )
        except PolicyChangeError as error:
            raise service_error(error) from error
        return JSONResponse(results, headers={"Cache-Control": "no-store"})
