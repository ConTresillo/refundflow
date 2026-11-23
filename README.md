# RefundFlow

RefundFlow is a local, synthetic enterprise-RAG and agent workflow simulation under phased development. It is not a production refund system.

## Try the current product slice

On Windows, run `setup-demo.bat` once, then `run-demo.bat` and open <http://127.0.0.1:8000>. See [the demo guide](docs/DEMO.md) for the manual command, API endpoints, and security/fidelity limits.

The current visible slice is a loopback-only development identity simulator. It demonstrates tenant/group context without authenticating real users. Use only by direct localhost connection; reverse proxies and tunnels are unsupported. OIDC, authorization, permission-scoped retrieval, async query jobs, and agent/HITL flows are not implemented yet; see the [enterprise contract](docs/architecture/enterprise-rag-contract.md) and [roadmap](docs/ROADMAP.md).

## Development checks

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check app tests
```

Active integration work stays on `dev`; `main` is the stable line.
