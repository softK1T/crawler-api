# crawler-api

Multi-tenant crawler-as-a-service. Clients: my own scraping pipelines
(cee-price-intel — price intelligence over Ceneo + OLX).

## Stack (FIXED — never add anything else)
FastAPI, SQLAlchemy 2.x + Alembic, PostgreSQL, Redis, arq, httpx,
curl_cffi>=0.15, Playwright, warcio, aioboto3, prometheus-client,
opentelemetry, pytest + testcontainers, Docker Compose, Terraform.

## BANNED
Kafka, Kubernetes, Celery, Scrapy, GraphQL, microservices, custom ORM,
any new dependency without justification in the commit body.

## Conventions
- Python 3.12, full type hints, mypy strict on `app/`.
- ruff for lint+format. Line length 100.
- Async everywhere in request path. No blocking calls in async functions.
- SQLAlchemy 2.x style only (`select()`, no legacy Query).
- Pydantic v2 models for all API schemas, separate from ORM models.
- Structured JSON logging via structlog. Never `print()`.
- All config via pydantic-settings from env. No secrets in code, ever.
- Errors: custom exception hierarchy + FastAPI exception handlers.
  Never leak internal messages to API responses.

## Layout
app/api/          routers, dependencies, schemas
app/core/         config, security, exceptions, logging
app/db/           models, session, repositories
app/services/     business logic (proxy manager, policy resolver, limiter)
app/fetchers/     engine implementations behind a Protocol
app/storage/      WARC writer, S3 client, CDXJ index
app/workers/      arq tasks
alembic/          migrations
tests/            unit/, integration/
docs/decisions/   ADRs
infra/            terraform, docker

## Git
- Conventional commits: `feat(proxy): weighted picker with health scoring`
- One commit per logical unit. Commit after each completed stage.
- Never force-push. Never commit .env, secrets, or WARC files.
- Branch per stage: `stage-N-<slug>`, no PR needed, merge to main directly.

## Rules for you (Claude Code)
- Work autonomously. Do NOT ask the user questions. If a decision is ambiguous,
  pick the option that is simpler and cheaper to run, and record it in
  docs/decisions/ as an ADR with the alternatives you rejected.
- Read the actual files before editing. Never assume file contents.
- Run `ruff check --fix`, `ruff format`, `mypy app/` after each stage.
  Fix everything you break.
- Do NOT run pytest or docker builds until the final verification stage
  is explicitly requested. Write tests, don't execute them yet.
- If a stage needs an env var, add it to `.env.example` with a comment.
- Budget: cloud spend must stay under EUR 20/month. Residential proxies are
  NOT purchased — build the abstraction, gate the implementation behind a flag.
- Report at the end of each stage: files changed, decisions made, open risks.
  Max 15 lines.
