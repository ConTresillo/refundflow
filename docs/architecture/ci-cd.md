# RefundFlow CI/CD

## Current delivery boundary

RefundFlow has no approved deployment destination and must stay local-first. Continuous delivery currently means producing a verified, commit-addressed source bundle—not deploying a live service, publishing a package, or creating a public release.

## Continuous integration

`.github/workflows/ci.yml` runs on pull requests, pushes to `main` and `dev`, manual dispatch, and reusable-workflow calls. It runs `pip check`, `ruff check app tests`, `compileall`, and the full pytest suite with bounded job timeouts on Ubuntu/Python 3.11, 3.12, and 3.14, plus Windows/Python 3.14 using the repository's exact `requirements-dev.lock`. Ruff is pinned as a development-only dependency (`ruff==0.17.5`), and lint checks cover `app` and `tests`. The new index module and tests are formatted and checked locally; CI does not yet gate formatting because existing files are not all Ruff-formatted.

The lock is explicitly for the provisioned Windows/Python 3.14 environment and includes Windows-specific packages. Linux jobs therefore resolve the ranges in `pyproject.toml`; they are compatibility checks, not lock-identical reproductions. A portable cross-platform lock remains a separate decision.

CI has read-only `contents` permission, no secrets, per-PR concurrency cancellation, and third-party actions pinned to verified full commit SHAs:

- `actions/checkout` v7.0.1 — `3d3c42e5aac5ba805825da76410c181273ba90b1`
- `actions/setup-python` v7.1.0 — `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0`
- `actions/upload-artifact` v7.0.2 — `cf430e030ddbb5b0abf93d22962f4752f3646cd9`

The actions and versions are pinned to immutable source revisions. Update them deliberately: inspect upstream release/source changes, change the full SHA and version comment together, then verify the workflow run.

## Continuous delivery

`.github/workflows/deliver-source.yml` is manually dispatched from `main`. It calls the same CI workflow first, then archives only tracked files from the checked-out commit, emits a SHA-256 checksum and commit manifest, and uploads the artifact for 14 days. The artifact name includes the full source commit SHA. It uses read-only repository permissions and no credentials.

This is not production deployment. Do not add public deployment, package publication, a release, or deployment secrets without an owner-approved target and rollout plan. Once a target is chosen, use a protected GitHub environment with required approval and branch restrictions where available, short-lived OIDC credentials rather than long-lived keys when supported, post-deployment health checks, and a tested rollback procedure. Repository settings/branch protections are not modified by these workflow files.

## Operations and failure recovery

For every pushed commit, inspect the GitHub Actions run for that exact SHA using `gh run list` and `gh run view`/`gh run watch`; read full failed logs. A green local suite does not imply CI is green. Distinguish a product defect, an invalid test expectation, a dependency/platform issue, and a workflow defect before changing anything. Make one targeted correction at a time, rerun the failing job, then the full required matrix. Do not weaken a correct assertion to silence CI. After a fix, record the Actions run URL/ID, conclusion, tested SHA, local commands/results, and remaining limitation in the versioned run record.

## Sources

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) — workflow events, permissions, jobs, and reusable workflows.
- [GitHub Actions secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use) — least-privilege token permissions and secret handling.
- [GitHub Actions deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) — environment approvals, branch/tag deployment restrictions, and environment secrets.
- [Checkout v7.0.1 source](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1), [setup-python v7.1.0 source](https://github.com/actions/setup-python/tree/6d4fd22b20465ccc0f7354476588e8df7c9ca0e0), and [upload-artifact v7.0.2 source](https://github.com/actions/upload-artifact/tree/cf430e030ddbb5b0abf93d22962f4752f3646cd9).
- [Ruff documentation](https://docs.astral.sh/ruff/) and [MIT license](https://github.com/astral-sh/ruff/blob/main/LICENSE) for the pinned development-only linter.
