# Development

[Documentation](README.md) · [Architecture](ARCHITECTURE.md) · [CI](CI.md)

Docker startup needs no host Python. Local contributors use Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy app/ tests/
```

Fast checks (Node needed only for CI-planner tests):

```bash
node --test .github/ci-plan.test.cjs
DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:1/testdb \
REDIS_URL=redis://127.0.0.1:1/0 \
python -m pytest -p scripts.ci_test_selection --ci-unit-only -m "not slow" -q
```

The selector excludes service tests by resolved fixtures/markers/paths and rejects
accidental dynamic service fixtures. Normal pytest without this flag is unchanged.
Full non-slow suite: python -m pytest -m "not slow" --maxfail=1 -q. Integration
fixtures use disposable testcontainers or explicit TEST_DATABASE_URL/TEST_REDIS_URL.
Never point them at deployment services: they drop tables and flush Redis.
Browser packages/binaries are separate. Test real browsers in the built Docker
image via scripts/browser_smoke.py; do not restore upstream camoufox fetch.

## Change checklist

| Area | Regression focus |
|---|---|
| Endpoints/schemas | Scopes, ownership, validation, actual OpenAPI |
| Proxies | Pause, TTL, strict country/type/pool and sticky constraints |
| Fetch/retry | Byte limits, cancellation, health and per-attempt audit |
| DB/migrations | Disposable up/down/up and restore validation |
| Deployment | Clean start, real browsers and repeated initialization |
| CI policy | Planner tests, incremental diff and service-fixture guard |

Keep browser contexts isolated; never silently substitute Chromium for Camoufox.
Do not infer commercial proxy type from location or weaken filters to pass a test.
Update implemented API/configuration guides, not claims copied from legacy schemas.
Check relative links, JSON and shell examples. Do not invent successful target runs.

Develop in Draft PRs; use Ready for review for full acceptance. A fast green gate
is not proof a full run occurred. Branch protection/reviews must be configured
separately. Dependencies are not fully locked; report versions with failures.
Existing servers need deliberate data/credential migration; git pull does not
transfer volumes or deploy code. Keep secrets, raw HTML/proxies and dumps out of Git.
