# ADR-013: Verification-Driven Adjustments (Stage 13)

## Status
Accepted — 2026-07-28

## Context
Stage 13 verification uncovered critical blocking issues that prevented the API
container from starting inside Docker. The container was OOM-killed (exit 137)
before serving a single request, while `import app.main` from a shell succeeded.

## Root Cause: Infinite Recursion in Logging Bridge

The `_StructlogHandler` in `app/core/logging_config.py` bridged stdlib logging
to structlog. structlog was configured with `LoggerFactory`, which routes output
back into stdlib logging — creating an infinite loop:

1. `logger.info("msg")` → stdlib → `_StructlogHandler.emit()`
2. `emit()` → `structlog.get_logger().log()` → JSONRenderer
3. JSONRenderer output → stdlib → `_StructlogHandler.emit()` (again!)
4. Nested JSON strings: `{"event": "{\"event\": \"...\"}"}`
5. Exhausts 500+ MB in seconds → OOM kill

This only manifested inside Docker because the container had a memory limit;
locally with no limit the recursion would also occur but was never exercised
(smoke tests had not been run).

## Fix

`_StructlogHandler.emit()` now writes a JSON log line directly to `sys.stderr`
instead of calling `structlog.get_logger().log()`. structlog native users
(`get_logger()`) are unaffected — they still use the full processor chain.

## Dependency Consolidation

Per Blocker 4, dependencies were consolidated from three files into one:

| Before | After |
|---|---|
| `requirements.txt` (runtime) | `pyproject.toml` `[project] dependencies` |
| `requirements-runtime.txt` (lean) | Deleted — merged into core deps |
| `requirements-dev.txt` (test) | `pyproject.toml` `[project.optional-dependencies] test` |

Browser engines (playwright, camoufox) are in `[project.optional-dependencies] browser`.
The Dockerfile installs `".[browser]"` since the single image serves both API and worker.

## Celery Compat Shim

`app/services/job_service.py` line 16-20 imports `celery` transitively via
`app.worker.tasks.crawl`. This is a Stage-8 compat shim scheduled for removal
when the legacy Celery worker is retired. Do not add new celery-dependent code.

## Other Adjustments

- `docker-compose.yml`: `version` field is obsolete (Compose v2 ignores it) but
  kept for tooling compatibility.
- Worker "unhealthy" in `docker compose ps`: the image's HEALTHCHECK curls
  `localhost:8000/healthz` which only the API serves. Not a runtime issue —
  the worker connects to Redis and processes jobs normally.

## Consequences

- Log bridge no longer uses structlog processors for stdlib-originated records.
  Timestamps and log-level enrichment are done inline in the handler.
- All dependency declarations live in `pyproject.toml` as the single source of
  truth. Adding a dep requires updating `pyproject.toml` only.
