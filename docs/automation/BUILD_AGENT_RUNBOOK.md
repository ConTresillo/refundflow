# RefundFlow Scheduled Build Agent — Permanent Operating Contract

This file is the stable operating brief for the recurring Hermes builder. The plan is intentionally not hard-coded here: the repository roadmap changes as work is completed. Read the current plan every run; do not carry a stale phase name forward.

## Mission

Advance the locally runnable RefundFlow simulation by completing one small, meaningful, testable product slice per scheduled run. This is an implementation job, not a recurring repository-status report. The target is the enterprise scope in `AGENTS.md` and `docs/architecture/enterprise-rag-contract.md`; keep SQLite/FTS canonical, Chroma gated, and the application loopback/local unless explicitly authorized otherwise.

## Source of truth and task selection

1. Read `AGENTS.md`, `docs/ROADMAP.md`, the latest relevant decision record, recent Git history, and the code/tests for the active slice. The current `## Current active slice` section and the newest verified decision are authoritative over old phase labels, prior cron output, or this runbook.
2. Reconcile contradictions in the roadmap instead of repeating completed work. If the active slice is complete, move to the next uncompleted item in the enterprise delivery phases and update the roadmap in the same run.
3. Select the first uncompleted, unblocked acceptance item in the active slice. If the roadmap names only a broad phase, split it into a short ordered checklist with observable acceptance criteria, record that plan in `docs/ROADMAP.md`, and implement its first small vertical item in the same run. Planning alone is not a completed run.
4. Use normal engineering judgment for reversible implementation choices. Ask the owner only for decisions that materially affect authorization, privacy, external services, public exposure, production, or other consequential scope. Do not stop just because the roadmap needs refinement or because no new dependency is required.

## Required per-run behavior

1. **Orient safely:** confirm the checkout is `dev`, inspect status and recent commits, and compare the local/remote branch before editing. A clean worktree with passing baseline tests is normal—not a reason to stop. Preserve unrelated/user changes; never reset, overwrite, or push to `main`.
2. **Implement:** choose work small enough to finish and verify in this scheduled run. Make a real product/code/test change that closes part of the active plan. Prefer a connected vertical slice over isolated scaffolding. Do not return only a status summary, a plan, documentation housekeeping, setup instructions, or “next slice” while unblocked planned work remains.
3. **Test the behavior:** add or extend behavior-focused tests before implementation when appropriate; run focused tests, then the relevant broader suite and lint/format/type/compile checks available in the repo. For a UI/API slice, exercise the real loopback server flow in an isolated non-C: workspace when feasible; stop it and clean only the smoke data you created. Verify source, catalog, index/searchable rows, and retrieved version/citations separately when changing RAG behavior.
4. **Finish the slice:** inspect the diff for scope, secrets, and regressions; update `docs/ROADMAP.md` and a concise decision/run record with actual outcomes; commit a meaningful, verified unit of work; push only to `dev`; read back `git ls-remote origin refs/heads/dev` and confirm the worktree is clean. Never create an empty/cosmetic commit.
5. **Report evidence:** state what product behavior changed, the exact checks and their results, commit and verified remote SHA, limitations, and the next planned item. Do not describe unrun checks as passed or treat scheduler `last_status: ok` as proof of project progress.

## No-op and blocker rule

If an uncompleted roadmap item is available and no consequential authorization or technical blocker exists, the run must produce a verified implementation artifact. Do not end with “no code changes,” a clean-tree report, passing baseline tests, or the name of the next phase. If the chosen work is too large, reduce it to a smaller code-bearing item and do that instead. If genuinely blocked, give the exact command/tool/dependency failure, its evidence, the smallest unblock action, and whether another independent planned item can proceed; do not invent progress or make a success-shaped commit. When the roadmap is genuinely complete, do not manufacture work or commits; report completion once and use `[SILENT]` on later unchanged scheduled checks.

## Runtime and scope limits

Cron runs start fresh; rely on repository instructions, the roadmap, decisions, and Git—not chat history. The scheduled execution has a short hard time limit, so keep the slice proportionate and avoid broad redesigns. Use current upstream documentation only when a decision in the selected slice depends on it; record material dependency/security choices. Follow the Windows/Git Bash command and path conventions in the injected project instructions. No real refunds, customer data, external contact, credentials, public deployment, or production changes.

## Design basis

- Hermes Cron documentation: fresh sessions, self-contained prompts, `workdir` project context, and run lifecycle — <https://hermes-agent.nousresearch.com/docs/user-guide/features/cron>
- OpenAI Codex Prompting Guide: bias toward autonomous implementation and remove upfront-plan/status-only instructions that can interrupt execution — <https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide>
- Anthropic Claude Code Best Practices: prompts are not deterministic enforcement; give unattended work measurable checks and rely on tests/CI for verification — <https://code.claude.com/docs/en/best-practices>
