# Stage 8 — Site Adapter Auth

Implemented end-to-end adapter-backed login/session support.

## What changed

- `/auth/login` now resolves an adapter from `app/services/adapters/__init__.py`, calls
  `SiteAdapter.login()`, and persists cookies into Redis via `session_manager`.
- `/auth/session` supports manual session seed (`POST`), presence inspection (`GET`),
  and deletion (`DELETE`). Session keys are normalized per registered domain.
- `POST /v1/fetch` accepts optional `session_key`; worker passes it into
  `fetch_with_retry()`, which injects the saved cookies into request headers via
  `headers_for_domain()`.
- Added `ExampleSiteAdapter` as a reference implementation and initial registry entry.

## Current limitation

- Browser-mode fetches (`playwright` / `camoufox`) do not yet import the stored cookie jar
  into browser contexts. Stage 8 is complete for static/stealth fetches (`httpx` /
  `curl_cffi`). Browser cookie hydration is a follow-up enhancement.
