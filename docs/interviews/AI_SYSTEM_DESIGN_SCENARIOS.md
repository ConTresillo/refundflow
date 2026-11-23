# AI system-design interview scenarios

This is a curated practice set, not an exhaustive dump or a claim that every interviewer asks these exact questions. It synthesizes themes from a public community question bank[1]. Official RAG and agentic-system guidance informs the design topics[2][3]. OpenAI agent-evaluation guidance informs evaluation prompts[6]. Prompts below are original, scenario-based exercises; use them to practice architecture trade-offs, not memorize one diagram.

For every exercise, answer in this order: clarify users and trust boundaries; quantify workload and SLOs; draw the data/control flow; define identity and authorization; describe async/failure behavior; cover privacy/security; explain evaluation and observability; estimate cost; identify what you would build first and what you would defer.

## Core scenarios

1. **Enterprise RAG with document-level permissions** — Design a multi-tenant assistant for Support, Billing, and Risk. Users belong to groups, documents have categories and scopes, and the assistant must cite only evidence each user may read. Explain how permissions constrain retrieval before top-k and how revocation takes effect.[7][8][9]
2. **Directory and policy changes during a run** — A user is removed from a group while an agent is queued, and one of its source documents changes ACL during processing. Define the source of truth, cache/checkpoint behavior, reauthorization points, and whether the run continues, restarts, or fails closed.[8][9][13]
3. **Large, changing document corpus** — Design ingestion for versioned PDFs/HTML/policies with duplicates, malformed files, deletions, conflicting effective dates, and partial parser failures. Cover provenance, incremental indexing, rollback/rebuild, freshness, and authorized deletion.[2][7]
4. **Async AI query service** — Design an API that accepts a long-running query immediately, returns a run ID, streams progress, handles cancellation/retries, survives worker restart, and resumes after human approval. Distinguish async I/O from a durable background-job contract.[4][5][12]
5. **Safe refund-support agent** — Design a bounded agent that can retrieve policy, calculate an outcome, and propose (but never issue) a refund. Define tool schemas, least privilege, idempotency, decision thresholds, approval roles, and audit events.[3][6]
6. **Prompt injection and data exfiltration** — A retrieved document contains hostile instructions and asks the agent to reveal another department’s policy. Show how ingestion, retrieval ACLs, prompt boundaries, tool authorization, output validation, and tests prevent the leak.[7]
7. **RAG quality and model-change evaluation** — Design a repeatable evaluation system for retrieval recall, citation correctness, freshness, answer faithfulness, refusal behavior, latency, and cost. Explain labeled datasets, regression gates, traces, and the limits of model-graded evaluation.[2][6]
8. **Queue and worker reliability** — A provider times out after a tool action, workers deliver tasks twice, and the broker restarts. Design idempotent commands, checkpoints, retries/backoff, dead-lettering, leases, cancellation, and operator recovery.[11][12]
9. **Latency and cost under load** — At rising concurrency, determine whether latency comes from auth checks, lexical/vector retrieval, reranking, model calls, or queue wait. Propose batching/caching/limits/fallbacks without bypassing authorization or returning stale evidence.[2][6]
10. **Multi-agent versus one bounded graph** — Decide whether specialist agents are justified for Support, Billing, and Risk. Compare a single graph with bounded specialist nodes to free-form delegation; cover shared state, permission boundaries, termination, cost, and evaluation.[3][6]
11. **Operational incident: retrieval or authorization outage** — Explain health/readiness, safe degraded behavior, queue pause/replay, stale-index policy, incident audit, recovery, and how the system proves it did not answer from unauthorized or missing evidence.[3][7]
12. **Privacy, audit, and retention** — Design tenant-scoped audit records and deletion/retention for user inputs, documents, embeddings, checkpoints, events, and caches. Explain what can be audited without logging raw sensitive text.[7]

## Strong-answer rubric

A strong design states assumptions; keeps tenant and document authorization server-side; filters unauthorized documents before text reaches the model; treats model and retrieved text as untrusted; makes long-running work durable and idempotent; distinguishes hard guarantees from heuristics; fails closed for access-control uncertainty; covers rollback and operator recovery; and proposes measurable tests/SLOs.[1][3][7] An answer that only lists an LLM, vector database, and agents is incomplete.

## Project mapping

RefundFlow should serve as a coherent end-to-end case study for scenarios 1–8, 11, and 12. Scenario 9 should be demonstrated through measured local load/evaluation rather than speculative scale claims. Scenario 10 is a decision exercise: do not add extra agents unless tests show a real boundary or quality benefit. Keep unrelated infrastructure designs as standalone interview exercises rather than bolting them onto the refund simulation.

For an authorization-model follow-up, compare OpenFGA relationship modeling with Casbin domain-aware RBAC.[8][10]

## Sources

[1] https://github.com/ombharatiya/ai-system-design-guide/blob/0a6e0d59e6b465e5a3f1d0de961fd3a66960b8f0/00-interview-prep/01-question-bank.md — AI System Design Interview Question Bank (pinned public guide)
[2] https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-solution-design-and-evaluation-guide — Microsoft Azure RAG solution design and evaluation guide
[3] https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentic-ai-lens.html — AWS Well-Architected Agentic AI Lens
[4] https://docs.langchain.com/oss/python/langgraph/graph-api — LangGraph Graph API overview
[5] https://docs.langchain.com/oss/python/langgraph/streaming — LangGraph streaming
[6] https://developers.openai.com/api/docs/guides/agent-evals — OpenAI: Evaluate agent workflows
[7] https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html — OWASP RAG Security Cheat Sheet
[8] https://openfga.dev/docs/use-cases/multi-tenant-saas — OpenFGA multi-tenant SaaS authorization
[9] https://openfga.dev/docs/modeling/user-groups — OpenFGA user groups
[10] https://casbin.apache.org/docs/rbac-overview — Apache Casbin RBAC overview
[11] https://fastapi.tiangolo.com/tutorial/background-tasks — FastAPI background tasks
[12] https://docs.langchain.com/oss/python/langgraph/checkpointers — LangGraph checkpointers
[13] https://openid.net/specs/openid-connect-core-1_0.html — OpenID Connect Core 1.0
