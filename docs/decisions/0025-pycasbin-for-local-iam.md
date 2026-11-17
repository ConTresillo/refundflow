# Decision 0025 — Use PyCasbin for the local IAM policy adapter

- Date/run: 2026-11-17; follow-up to the scheduled IAM slice on `dev`.
- Status: selected for the local IAM simulation; not a production authorization deployment.
- Supersedes: Decision 0024's hand-written `AuthorizationEngine` implementation. Decision 0024 remains unchanged as historical evidence; its custom evaluator is removed.

## Context and reuse review

The previous slice added a local policy evaluator despite `AGENTS.md` and the enterprise contract requiring an established authorization library behind `AuthorizationPort`. That code was unintegrated and did not satisfy the project architecture. The repository had no authorization dependency or application authorization path. The next choice must reuse a maintained engine, not grow a custom evaluator.

### Candidates

- **PyCasbin 2.8.0 — selected for this local embedded slice.** The official project documents ACL, RBAC, ABAC, tenant/domain roles and policy adapters. The Python package is Apache-2.0; the upstream release page shows 2.8.0 dated 2026-02-02 and the repository reports a long release history. It runs in-process, fits this Python app and current no-service local demo, and lets the application keep identity/entity storage separate from policy evaluation. The project does not authenticate users or own their authoritative directory, so our models remain data records and the adapter only maps trusted snapshots into Casbin policy. The repo's model uses tenant domains, membership/group/role relationships, explicit document grants, and exact action/scope/category comparisons.
- **OpenFGA — strong alternative for relationship-heavy production authorization.** Its official multi-tenant SaaS and group modeling guides directly cover organization membership, parent-child resources, group/role relationships and relation tuples. Its relationship queries also support searching objects by permissions, which may fit permission-filtered retrieval. It is a separate authorization service, not an embedded Python library. Docker is not installed in this environment, and the current slice must run without a new service, so it is not selected for the initial local adapter. Re-evaluate it before the retrieval/scale phase if relationship queries, tuple consistency, or cross-service authorization need outweigh the operational cost.
- **Legacy Oso OSS — excluded.** Its upstream repository states that the legacy open-source library is archived and no longer maintained; it fails the maintenance requirement.

This is a local fit decision, not a claim that the embedded engine alone provides enterprise identity, persistence, audit, distributed policy consistency, or production readiness. PyCasbin 2.8.0 resolved to `simpleeval` 1.0.8, `wcmatch` 12.0.3 and `bracex` 3.2.3 in the project venv. Package metadata reports Apache-2.0 for PyCasbin and MIT for those three transitive dependencies. The app dependency is constrained to `pycasbin>=2.8.0,<3.0`; installation and `pip check` are verified locally. No separate vulnerability scanner was run.

## Decision

Replace the home-grown evaluator with `CasbinAuthorizationAdapter`, implementing `AuthorizationPort`. It creates one `casbin.SyncedEnforcer` per immutable IAM snapshot, loads the policy/relationships once, and reuses it for decisions; it does not construct an enforcer per request. User-supplied membership data is not used to establish authority: the adapter resolves the principal and document by ID from its trusted snapshot, then asks Casbin. Unknown users/documents, missing tenant membership, unmatched actions/scopes/categories, or absent grants deny.

## Consequences and limits

- PyCasbin performs policy evaluation; application code only maps the project's typed IAM records into Casbin model/policy inputs.
- The adapter is an in-memory startup snapshot. Persistence, refresh/revocation semantics, mutation audit, authenticated identity wiring, retrieval prefiltering, citations, and cross-tenant RAG non-interference remain unimplemented. Do not label this enterprise-ready or wire it into a query path without those controls.
- The enforcer uses Casbin's synchronized variant and is not rebuilt on each authorization request, following upstream deployment guidance. Policy changes are not yet applied live; the next slice must design authoritative persistence and safe snapshot refresh/revocation before integration.
- If local deployment later adds Compose, reassess OpenFGA for relationship-heavy grants and `ListObjects`-style permission search rather than reimplementing those semantics in Python.

## Changed files

- `app/iam.py`: replace custom decision logic with a Casbin-backed port adapter.
- `tests/test_iam.py`: cover tenant membership, role permission, user/group grants, scope/category matching, default-deny, and resistance to request-supplied membership spoofing.
- `pyproject.toml`: declare the PyCasbin version range.
- `docs/ROADMAP.md`: update the active slice and next acceptance item.
- `docs/automation/BUILD_AGENT_RUNBOOK.md`: add a reuse-before-rebuild gate requiring library-fit review and correct upstream usage.

## Verification record

- TDD migration tests first failed because the Casbin adapter did not exist; the focused suite passed after replacement.
- A new test exposed that a direct user grant could pass without current tenant membership in the first adapter draft. It failed before the fix; adding a distinct tenant-membership relation to the Casbin model made it pass. A further test confirmed a user cannot forge membership by supplying a modified principal object.
- A test for a same-ID document with forged tenant/category/scope failed against the first snapshot adapter draft; the adapter now rejects supplied principal/document records that differ from the trusted snapshot. Document keys are tenant-scoped so identical document IDs in different tenants remain separate.
- Focused IAM tests: 11 passed. Full repository suite: 70 passed, with 13 existing LangSmith deprecation warnings. `ruff check app/iam.py tests/test_iam.py`, `ruff format --check app/iam.py tests/test_iam.py`, `compileall app tests`, editable package installation, `pip check`, and `git diff --check` passed.
- Independent code review passed with no concerns. It verified tenant-membership gating for every grant, snapshot equality, tenant-scoped duplicate document IDs, single synchronized enforcer reuse, and fail-closed/action/scope/category behavior.
- No web route or retrieval flow uses this module; no live server smoke was applicable to this domain-only slice. Persistence, real-time revocation, audit, and RAG integration remain untested and unimplemented.

## Sources

- PyCasbin project and license: https://github.com/apache/casbin-pycasbin
- PyCasbin releases: https://github.com/apache/casbin-pycasbin/releases
- Casbin overview and supported models: https://casbin.apache.org/docs/overview/
- Casbin RBAC with tenant domains: https://casbin.apache.org/docs/rbac-with-domains/
- Casbin production deployment/concurrency guidance: https://casbin.apache.org/docs/production-deployment
- OpenFGA multi-tenant SaaS model: https://openfga.dev/docs/use-cases/multi-tenant-saas
- OpenFGA permission search: https://openfga.dev/docs/interacting/search-with-permissions
- Legacy Oso repository archive notice: https://github.com/osohq/oso
