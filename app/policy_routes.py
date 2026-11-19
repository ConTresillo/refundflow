"""FastAPI endpoints and browser UI for controlled local policy changes."""

from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import tempfile
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

EVIDENCE_EXPLORER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="__CSRF__"><title>RefundFlow · Policy desk</title>
<style>
:root{color-scheme:light;--ink:#1d2433;--muted:#788093;--line:#e6e7ed;--paper:#fff;--canvas:#f6f5f2;--nav:#182033;--nav-muted:#aab2c1;--accent:#ef684c;--accent-dark:#c94b35;--mint:#dff2e9;--mint-ink:#287254;--lav:#eeeaff;--shadow:0 12px 34px #24304a0b}
*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}button,input{font:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:238px minmax(0,1fr)}
aside{position:sticky;top:0;height:100vh;background:var(--nav);color:#fff;padding:25px 16px 18px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:11px;padding:0 11px 25px;color:#fff;text-decoration:none;font-size:17px;font-weight:700;letter-spacing:-.35px}.mark{width:31px;height:31px;border-radius:10px;background:var(--accent);display:grid;place-items:center;font-size:17px}.workspace{margin:0 3px 24px;padding:12px;border:1px solid #ffffff1a;border-radius:11px;background:#ffffff08}.workspace small,.nav-label,.eyebrow,.micro{display:block;font-size:10px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase}.workspace small{color:var(--nav-muted);margin-bottom:4px}.workspace strong{font-size:13px}.nav-label{color:#8791a4;padding:0 12px 7px}.nav{display:grid;gap:4px}.nav a{padding:10px 12px;border-radius:8px;color:var(--nav-muted);text-decoration:none;display:flex;align-items:center;gap:11px;font-weight:550}.nav a:hover{background:#ffffff0c;color:white}.nav a.active{background:#ffffff15;color:white}.nav .ico{width:18px;text-align:center;color:#b8c2d3}.nav a.active .ico{color:#ff9b84}.aside-bottom{margin-top:auto;border-top:1px solid #ffffff18;padding:17px 10px 0;color:var(--nav-muted);font-size:12px}.demo-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#72d6a6;margin-right:7px;box-shadow:0 0 0 3px #72d6a622}
main{min-width:0}.topbar{height:68px;background:#ffffffc9;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 clamp(20px,4vw,58px)}.crumb{color:var(--muted);font-size:13px}.profile{display:flex;align-items:center;gap:9px;font-size:12px;color:#596174}.avatar{width:29px;height:29px;border-radius:50%;background:var(--lav);color:#6151aa;display:grid;place-items:center;font-weight:700}
.content{max-width:1120px;margin:0 auto;padding:42px clamp(20px,4vw,58px) 70px}.eyebrow{color:var(--accent-dark);margin-bottom:9px}.intro{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:26px}.intro h1{font-size:34px;line-height:1.12;letter-spacing:-1.2px;margin:0 0 10px}.intro p{max-width:650px;color:#667084;margin:0}.mode-badge{white-space:nowrap;border:1px solid #d8e9e0;background:#f2faf5;color:var(--mint-ink);padding:7px 10px;border-radius:999px;font-size:11px;font-weight:700}.mode-badge::before{content:"";display:inline-block;width:6px;height:6px;margin:0 7px 1px 0;border-radius:50%;background:#47a976}
.search-card{background:var(--paper);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:22px 23px 20px}.search-card label{display:block;font-size:12px;font-weight:650;margin:0 0 8px}.query-wrap{position:relative}.query-wrap .spark{position:absolute;left:15px;top:13px;color:var(--accent);font-size:17px}.query-wrap input{width:100%;padding:13px 15px 13px 43px;border:1px solid #d9dce5;border-radius:9px;background:#fff;color:var(--ink);outline:none}.query-wrap input:focus,.scope input:focus{border-color:#ef947e;box-shadow:0 0 0 3px #ef684c1d}.form-bottom{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-top:16px}.scope{width:min(260px,48%)}.scope input{width:100%;padding:10px 12px;border:1px solid #d9dce5;border-radius:8px;color:var(--ink);background:white;outline:none}.scope-note{font-size:11px;color:var(--muted);margin-top:5px}.primary{border:0;border-radius:8px;background:var(--accent);color:#fff;font-weight:650;padding:11px 17px;cursor:pointer;transition:background .15s,transform .15s}.primary:hover{background:var(--accent-dark);transform:translateY(-1px)}.primary:disabled{opacity:.55;cursor:wait;transform:none}.search-hint{margin:13px 0 0;color:var(--muted);font-size:11px}.disclaimer{margin:15px 0 0;display:flex;align-items:flex-start;gap:9px;padding:11px 13px;border-radius:9px;background:#fff7ed;color:#7e5a3f;font-size:12px}.disclaimer strong{color:#61442f}.section-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:33px 0 13px}.section-head h2{font-size:16px;margin:0 0 2px;letter-spacing:-.25px}.section-head p{margin:0;color:var(--muted);font-size:12px}.trust-chip{font-size:10px;color:#6f668d;background:var(--lav);padding:6px 9px;border-radius:999px;white-space:nowrap;font-weight:650}
#status{min-height:20px;margin:0 0 12px;color:#697184;font-size:12px}#results{display:grid;gap:11px}.evidence-card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:17px 19px;box-shadow:0 4px 16px #24304a06}.evidence-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.source-name{font-size:14px;font-weight:700}.version{display:block;font-size:11px;color:var(--muted);margin-top:3px}.citation{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:#6457a3;background:#f3f0ff;border-radius:6px;padding:5px 7px;white-space:nowrap}.evidence-text{margin:13px 0 0;color:#424b5e;line-height:1.65;font-size:13px}.empty{border:1px dashed #d5d7df;border-radius:12px;background:#ffffff9c;padding:21px;color:#777f90;font-size:13px}.empty strong{color:#424b5e;display:block;margin-bottom:4px}.footer-note{margin-top:28px;border-top:1px solid var(--line);padding-top:15px;color:#8a90a0;font-size:11px;display:flex;justify-content:space-between;gap:14px}
@media(max-width:760px){.shell{grid-template-columns:66px minmax(0,1fr)}aside{padding:18px 8px}.brand{padding:0 8px 22px}.brand-word,.workspace,.nav-label,.nav-text,.aside-bottom{display:none}.nav a{justify-content:center;padding:12px 0}.nav .ico{font-size:16px}.topbar{height:58px;padding:0 17px}.content{padding:30px 17px 50px}.intro{display:block}.mode-badge{display:inline-block;margin-top:16px}.intro h1{font-size:29px}.form-bottom{align-items:stretch;flex-direction:column}.scope{width:100%}.primary{width:100%}.section-head{align-items:flex-start;flex-direction:column}.footer-note{flex-direction:column}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;scroll-behavior:auto!important}}
</style></head>
<body><div class="shell"><aside><a class="brand" href="/"><span class="mark">↗</span><span class="brand-word">RefundFlow</span></a><div class="workspace"><small>Demo workspace</small><strong>__TENANT_NAME__</strong></div><span class="nav-label">Workspace</span><nav class="nav" aria-label="Workspace"><a class="active" href="/evidence" aria-current="page"><span class="ico">⌕</span><span class="nav-text">Policy desk</span></a><a href="/policies"><span class="ico">▤</span><span class="nav-text">Policy library</span></a><a href="/"><span class="ico">◉</span><span class="nav-text">Identity profile</span></a></nav><div class="aside-bottom"><span class="demo-dot"></span>Local simulation · no live refunds</div></aside>
<main><header class="topbar"><div class="crumb">Refund operations <span aria-hidden="true">/</span> <strong>Policy desk</strong></div><div class="profile"><span class="avatar" aria-hidden="true">RF</span>__IDENTITY_NAME__</div></header><div class="content"><div class="eyebrow">Evidence Explorer · Refund support</div><div class="intro"><div><h1>Find the policy behind the answer.</h1><p>Check a customer scenario against the policy evidence your signed-in identity is allowed to see.</p></div><span class="mode-badge">Permission-checked search</span></div>
<div class="search-card"><label for="query">Customer scenario or policy question</label><div class="query-wrap"><span class="spark" aria-hidden="true">✳</span><input id="query" maxlength="300" placeholder="e.g. The customer lost their receipt — can we still refund?" autocomplete="off"></div><div class="form-bottom"><div class="scope"><label for="department">Department scope</label><input id="department" list="department-options" value="billing" maxlength="64"><datalist id="department-options"><option value="support"><option value="billing"><option value="shipping"><option value="risk"></datalist><div class="scope-note">Search stays within this department and your document grants.</div></div><button id="search" class="primary">Find policy evidence <span aria-hidden="true">→</span></button></div><p class="search-hint">Try “refund receipt” or “damaged item” to explore the synthetic demo policies.</p></div>
<p class="disclaimer"><span aria-hidden="true">ⓘ</span><span><strong>Evidence-only prototype.</strong> Search is real, local lexical retrieval with citations. It does not generate a refund decision, contact a customer, or move money.</span></p>
<div class="section-head"><div><h2>Policy evidence</h2><p>Sources are scoped to your current demo identity.</p></div><span class="trust-chip">Lexical retrieval · no AI-generated answer</span></div><p id="status" role="status" aria-live="polite">Enter a scenario to see permitted evidence and source citations.</p><section id="results" aria-live="polite"><div class="empty"><strong>Your evidence will appear here.</strong>Run a policy search to inspect matching clauses, versions, and citations.</div></section><div class="footer-note"><span>RefundFlow local prototype</span><span>Development identity is simulated · loopback only</span></div></div></main></div><script>
const csrf=document.querySelector('meta[name="csrf-token"]').content;const statusNode=document.getElementById('status');const resultsNode=document.getElementById('results');const searchButton=document.getElementById('search');
async function searchEvidence(){resultsNode.replaceChildren();const query=document.getElementById('query').value.trim();const department=document.getElementById('department').value.trim();if(!query||!department){statusNode.textContent='Enter a question and department to search.';return;}searchButton.disabled=true;statusNode.textContent='Checking permitted policy evidence…';try{const params=new URLSearchParams({department_id:department,query,as_of:new Date().toISOString().slice(0,10),top_k:'5'});const response=await fetch('/api/policy-search?'+params,{credentials:'same-origin',headers:{'X-CSRF-Token':csrf}});const body=await response.json();if(!response.ok)throw new Error(body.detail||'Search failed');if(body.length===0){statusNode.textContent='No authorized evidence found for this query.';const empty=document.createElement('div');empty.className='empty';const title=document.createElement('strong');title.textContent='No matching evidence';const detail=document.createElement('span');detail.textContent='Try another phrase or check that this identity has access to the selected department.';empty.append(title,detail);resultsNode.append(empty);return;}statusNode.textContent=`${body.length} authorized evidence item${body.length===1?'':'s'} found.`;for(const item of body){const article=document.createElement('article');article.className='evidence-card';const head=document.createElement('div');head.className='evidence-head';const source=document.createElement('div');const name=document.createElement('div');name.className='source-name';name.textContent=item.source_name;const meta=document.createElement('span');meta.className='version';meta.textContent=`Version ${item.version} · effective ${item.effective_from}${item.effective_to?' to '+item.effective_to:''}`;source.append(name,meta);const citation=document.createElement('span');citation.className='citation';citation.textContent=item.citation;head.append(source,citation);const text=document.createElement('p');text.className='evidence-text';text.textContent=item.text;article.append(head,text);resultsNode.append(article);}}catch(error){statusNode.textContent=error.message||'Search unavailable.';const empty=document.createElement('div');empty.className='empty';empty.textContent='Search could not be completed. Check the local service and try again.';resultsNode.append(empty);}finally{searchButton.disabled=false;}}
searchButton.addEventListener('click',searchEvidence);document.getElementById('query').addEventListener('keydown',event=>{if(event.key==='Enter')searchEvidence();});
</script></body></html>"""


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
    <h2>Replace synthetic policy from PDF</h2>

    <p>Upload a digital-text PDF as a new immutable version. Your persisted document.write grant is checked before reading the file.</p>
    <label for="pdf-version">New version</label><input id="pdf-version" maxlength="32" value="2.0">
    <label for="pdf-date">Effective from</label><input id="pdf-date" type="date" value="2026-11-19">
    <label for="pdf-file">PDF file (maximum 8 MiB)</label><input id="pdf-file" type="file" accept="application/pdf,.pdf">
    <button id="upload-pdf">Upload and index PDF</button><pre id="ingestion-status" role="status" aria-live="polite"></pre>
    <button id="refresh-ingestion">Refresh ingestion status</button>
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
document.getElementById('upload-pdf').addEventListener('click',async()=>{
  const file=document.getElementById('pdf-file').files[0];const status=document.getElementById('ingestion-status');
  if(!file){status.textContent='Choose a PDF first.';return;}
  status.textContent='Uploading and indexing…';
  try{const params=new URLSearchParams({new_version:document.getElementById('pdf-version').value.trim(),effective_from:document.getElementById('pdf-date').value});
    const response=await fetch(`/api/policies/${encodeURIComponent(policySelect.value)}/pdf?${params}`,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/pdf'},body:file});
    const body=await response.json();if(!response.ok)throw new Error(body.detail||`HTTP ${response.status}`);
    status.textContent=JSON.stringify(body,null,2);await loadPolicies();
  }catch(error){status.textContent=error.message||'Upload failed.';}
});
document.getElementById('refresh-ingestion').addEventListener('click',async()=>{
  try{document.getElementById('ingestion-status').textContent=JSON.stringify(await api('/api/policy-ingestion-status'),null,2);}
  catch(error){document.getElementById('ingestion-status').textContent=error.message;}
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

    @app.get("/evidence", response_class=HTMLResponse)
    def evidence_explorer_page(request: Request) -> HTMLResponse:
        identity = current_identity(request)
        session = request_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign-in required")
        content = (
            EVIDENCE_EXPLORER_HTML.replace(
                "__CSRF__", html.escape(session.csrf_token, quote=True)
            )
            .replace("__TENANT_NAME__", html.escape(identity.tenant_name))
            .replace("__IDENTITY_NAME__", html.escape(identity.display_name))
        )
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

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

    @app.post("/api/policies/{policy_id}/pdf")
    async def upload_policy_pdf(
        request: Request,
        policy_id: str,
        new_version: Annotated[str, Query(min_length=1, max_length=32)],
        effective_from: Annotated[str, Query(min_length=10, max_length=10)],
        content_type: Annotated[str | None, Header()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        from datetime import date, datetime, timezone
        from pathlib import Path

        from app.document_parsers import MAX_PDF_BYTES
        from app.iam import AccessRequest, CasbinAuthorizationAdapter, Role
        from app.iam_store import SQLiteIAMStore
        from app.policy_ingestion import PolicyIngestionService

        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), x_csrf_token)
        if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/pdf":
            raise HTTPException(status_code=415, detail="application/pdf required")
        try:
            content_length = int(request.headers.get("content-length", "-1"))
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from error
        if content_length <= 0:
            raise HTTPException(status_code=411, detail="Content-Length is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,31}", new_version):
            raise HTTPException(status_code=422, detail="invalid policy version")
        if content_length > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="PDF exceeds the 8 MiB upload limit")
        store = SQLiteIAMStore(service._database)
        revision, principals = store.authorization_principal_snapshot()
        document_revision, documents = store.authorization_document_snapshot()
        if revision != document_revision:
            raise HTTPException(status_code=503, detail="authorization snapshot unavailable")
        principal = next((item for item in principals if item.user_id == identity.subject_id), None)
        document = next((item for item in documents if item.id == policy_id and item.tenant_id == identity.tenant_id), None)
        if principal is None or document is None:
            raise HTTPException(status_code=403, detail="document write denied")
        roles = tuple(Role(id=role, tenant_id=identity.tenant_id, permissions=store.list_role_permissions(identity.tenant_id, role)) for role in store.list_roles(identity.tenant_id))
        adapter = CasbinAuthorizationAdapter(roles, principals=principals, documents=documents)
        request_access = AccessRequest(action="document.write", scope=document.scope, category=document.category)
        if not adapter.authorize(principal, document, request_access):
            raise HTTPException(status_code=403, detail="document write denied")
        try:
            date.fromisoformat(effective_from)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="effective_from must be ISO date") from error
        current_revision, _ = store.authorization_principal_snapshot()
        current_doc_revision, _ = store.authorization_document_snapshot()
        if current_revision != revision or current_doc_revision != document_revision:
            raise HTTPException(status_code=403, detail="document write grant changed")
        current = service._catalog.current(policy_id)
        if current is None or current["department_id"] != document.scope.removeprefix("department:"):
            raise HTTPException(status_code=404, detail="policy unavailable")
        record = dict(current)
        record.update(version=new_version, status="active", effective_from=effective_from,
                      source_name="Uploaded synthetic policy PDF", source_type="synthetic_demo",
                      provenance="Locally uploaded synthetic PDF; bytes are transient during ingestion only.",
                      license_notes="Synthetic demo content; uploaded locally.", excerpts=[])
        record["source_url"] = ""
        if service._catalog.version(policy_id, new_version) is not None:
            raise HTTPException(status_code=409, detail="policy version is immutable")
        stage_dir = Path(service._database).parent / ".staging"
        stage_dir.mkdir(exist_ok=True)
        staged_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="pdf-", suffix=".pdf", dir=stage_dir, delete=False) as staged:
                staged_path = Path(staged.name)
                received = 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > MAX_PDF_BYTES or received > content_length:
                        raise HTTPException(status_code=413, detail="PDF exceeds the 8 MiB upload limit")
                    staged.write(chunk)
                if received != content_length:
                    raise HTTPException(status_code=413, detail="invalid PDF body length")
            from app.document_parsers import parse_pdf
            parsed = parse_pdf(staged_path)
            record["excerpts"] = [section.text for section in parsed]
            record["content_hash"] = hashlib.sha256("\n".join(record["excerpts"]).encode("utf-8")).hexdigest()
            ingestion_path = stage_dir / f"pdf-{policy_id}.pdf"
            staged_path.replace(ingestion_path)
            staged_path = ingestion_path
            outcome = PolicyIngestionService(service._source_dir, service._catalog, service._index).replace_pdf(
                staged_path, record, occurred_at=datetime.now(timezone.utc).isoformat(), tenant_id=identity.tenant_id)
        except HTTPException:
            raise
        except (OSError, ValueError, sqlite3.Error) as error:
            raise HTTPException(status_code=422, detail="PDF replacement failed; active version preserved") from error
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
        return JSONResponse({"status": "indexed", "policy_id": policy_id, "version": new_version,
                             "indexed_chunks": outcome.index.processed,
                             "reused_chunks": outcome.index.reused,
                             "retired_chunks": outcome.index.retired}, headers={"Cache-Control": "no-store"})

    @app.get("/api/policy-ingestion-status")
    def policy_ingestion_status(request: Request) -> JSONResponse:
        identity = current_identity(request)
        from app.iam import AccessRequest, CasbinAuthorizationAdapter, Role
        from app.iam_store import SQLiteIAMStore
        store = SQLiteIAMStore(service._database)
        revision, principals = store.authorization_principal_snapshot()
        doc_revision, documents = store.authorization_document_snapshot()
        if revision != doc_revision:
            raise HTTPException(status_code=503, detail="authorization snapshot unavailable")
        principal = next((item for item in principals if item.user_id == identity.subject_id), None)
        if principal is None or not any(member.tenant_id == identity.tenant_id for member in principal.memberships):
            return JSONResponse([], headers={"Cache-Control": "no-store"})
        roles = tuple(Role(id=role, tenant_id=identity.tenant_id, permissions=store.list_role_permissions(identity.tenant_id, role)) for role in store.list_roles(identity.tenant_id))
        adapter = CasbinAuthorizationAdapter(roles, principals=principals, documents=documents)
        writable = {item.id for item in documents if item.tenant_id == identity.tenant_id and adapter.authorize(principal,item,AccessRequest(action="document.write",scope=item.scope,category=item.category))}
        file_status = service._catalog.file_status(identity.tenant_id)
        return JSONResponse([item for item in file_status if item.get("document_id") in writable],headers={"Cache-Control":"no-store"})

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
