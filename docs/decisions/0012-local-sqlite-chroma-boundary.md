# Decision 0012 — SQLite authority and replaceable local vector search

- Date: 2026-11-16
- Status: Owner-approved target direction; dependency adoption is security-gated.
- Owner direction: keep the project minimal with SQLite and ChromaDB locally, while making production migration practical.

## Context

The current implementation has a SQLite policy catalog, a separate SQLite FTS5 chunk index, and deterministic in-memory token-overlap retrieval in `app/policies.py`. The FTS5 index is not wired into that application path. ChromaDB and an embedding provider are not installed or implemented. The project has no approved production deployment target.

Chroma's official Python client supports a persistent local client; its documentation describes this mode for local development/testing and points production users toward a server-backed deployment. Chroma also supplies a default embedding function that may automatically download `all-MiniLM-L6-v2`. The Python package dry-run on the current Python 3.14 Windows environment resolved, but showed a substantial transitive dependency set. Security review then found that PyPI's current `chromadb` release is 1.5.9 and its metadata lists eight vulnerability entries, including duplicate GHSA/PYSEC identifiers. GitHub Advisory GHSA-f4j7-r4q5-qw2c marks 1.0.0–1.5.9 affected by critical pre-authentication code injection (CVE-2026-45829) with no patched version listed; additional advisories report authorization and code-injection issues affecting releases through 1.5.9. These are sufficient to block adding the dependency; do not assume local persistence removes the risk. No package or model was installed during this decision.

## Decision

1. SQLite is canonical for policy/version metadata, status, effective dates, provenance, citations, chunk text, and the lexical FTS5 index. Retrieval may use vector search for candidate generation, but must rehydrate candidate IDs from SQLite and enforce current department/policy/version/status/effective-date eligibility there before returning evidence.
2. ChromaDB's local `PersistentClient` is the intended local vector-index adapter, subject to an upstream patched release and security review. It stores stable chunk IDs, vectors, and only the metadata needed for candidate filtering. It is derived/rebuildable data, never the authority for policy freshness or citations.
3. Keep Chroma behind a narrow `VectorIndexPort`; keep vector creation behind an explicit `EmbeddingPort` that exposes a model/config fingerprint. Do not call Chroma's implicit default embedding function, download a model, or add a model dependency until the embedding choice, quality, license, resource cost, and supported-platform behavior have been reviewed.
4. Add Chroma as an optional dependency extra when the adapter is implemented. Keep the base install and SQLite lexical path usable without it. Pin and security-review the selected package and relevant lock before enabling it in CI.
5. Production migration means replacing adapters, not promoting local files as production infrastructure: move canonical relational state through the catalog/storage port, deploy a server-backed vector service or another selected vector adapter, rebuild embeddings from canonical chunks when model/index configuration changes, and rerun retrieval/freshness evaluations. Do not promise byte-compatible local-to-production storage migration.
6. Cross-store writes are not one atomic transaction. SQLite remains the source of truth; the vector index must be repairable/rebuildable from canonical data and ingestion must expose incomplete synchronization rather than silently returning stale evidence.

## Alternatives considered

- SQLite FTS5 only: smallest dependency footprint and useful lexical retrieval, but does not meet the selected local vector-search direction.
- Make Chroma canonical: rejected because status, provenance, effective dates, and citations would be duplicated or coupled to vector-store records, making freshness and migration harder.
- Install a default embedding model immediately: rejected because Chroma may download model files implicitly and no model/resource/license decision has been made.
- Couple retrieval directly to Chroma: rejected because it would make policy freshness, tests, and later production adapter replacement harder.
- Adopt a managed database/vector service now: rejected because the product is local-first and no production target is approved.

## Consequences and follow-up

This decision records the target boundary, not a completed vector feature. The next code slice should define/test narrow ports and connect the safe SQLite FTS path to cited, freshness-filtered policy evidence. The Chroma adapter remains blocked until a patched upstream release is verified and the package plus transitive dependencies pass security review; if no safe release is available, evaluate a safer local alternative without changing the SQLite authority. After the Chroma gate clears, test stable IDs, idempotent upsert, filtering, deletes, persistence/reopen, freshness rehydration, and recovery after partial cross-store failure. The existing app retrieval must not be described as Chroma-backed until that integration passes.

## References

- [Chroma client modes and PersistentClient](https://docs.trychroma.com/docs/run-chroma/clients)
- [Chroma embedding functions and default model behavior](https://docs.trychroma.com/docs/embeddings/embedding-functions)
- [Chroma collection API: upsert, query, metadata filters, delete](https://docs.trychroma.com/reference/python/collection)
- [Chroma license (Apache-2.0)](https://github.com/chroma-core/chroma/blob/main/LICENSE)
- [ChromaDB 1.5.9 package metadata](https://pypi.org/project/chromadb/1.5.9/)
- [GitHub Advisory GHSA-f4j7-r4q5-qw2c / CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) — critical code-injection advisory; no patched release listed for versions through 1.5.9.
- [GitHub Advisory GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) — cross-tenant authorization issue.
- [GitHub Advisory GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf) — embedding-function configuration code injection.
- [GitHub Advisory GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) — authorization scope issue.
- [OSV advisory record for GHSA-f4j7-r4q5-qw2c](https://osv.dev/vulnerability/GHSA-f4j7-r4q5-qw2c) — affected package/version range.
