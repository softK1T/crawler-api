# Changelog

All notable changes to crawler-api are documented here.

## [Unreleased]

### Fixed

- Prevented legacy or unknown block-reason values from failing completed jobs.
- Removed broad bot-keyword matching that falsely blocked normal HTML pages.
- Prevented repeated direct-IP attempts after a detected IP or WAF block.
- Enforced fail-fast proxy behavior when `use_proxy=true` and the eligible
  proxy pool is empty or exhausted.
- Preserved explicit `use_proxy=false` through API-to-worker option handling.
- Added a writable runtime home/cache for the non-root `crawler` user.
- Disabled runtime Public Suffix List downloads by using tldextract's bundled
  snapshot.

## [0.2.0] — 2026-08-03

### Added

- **Chromium runtime in Docker image.** The worker Docker image now includes
  Chromium (headless shell) installed via `playwright install --with-deps
  --only-shell chromium`. Worker startup verifies the executable exists and
  fails fast with `PLAYWRIGHT_CHROMIUM_MISSING` if it is absent — browser-mode
  jobs are never silently degraded to httpx. (ADR-016)

- **Request-level proxy override.** `POST /v1/fetch` gains `use_proxy` and
  `proxy_country` fields (both optional). When set, they take priority over
  `DomainPolicy` settings. (ADR-017)

- **Proxy rotation on block.** When a proxy returns a block page, it is
  excluded from subsequent retry picks. If all eligible proxies for a
  tenant/country are exhausted, the job fails with `PROXY_POOL_EXHAUSTED`
  rather than retrying the same proxy indefinitely. (ADR-017)

- **Fail-fast proxy selection.** When `use_proxy=true` and no healthy proxy
  is available, the worker raises `PROXY_POOL_EMPTY` (502) instead of
  silently falling back to a direct connection. (ADR-017)

- **Server-side content decompression.** Response bodies are decompressed
  server-side before being stored in the API result. WARC records preserve
  the original transport bytes. Supported encodings: gzip, deflate (zlib +
  raw), brotli, zstd, plus magic-byte sniffing for mislabeled responses.
  (ADR-018)

- **API result v2 fields.** `FetchResultSchema` now includes `api_version`,
  `body_is_compressed`, `body_bytes`, `content_sha256`, and
  `original_content_encoding`. All existing fields remain unchanged.
  (ADR-018)

- **ADR-016** Playwright Chromium Runtime Installation.
- **ADR-017** Proxy Selection and Rotation Policy.
- **ADR-018** Response Body Normalization.

### Changed

- **Dockerfile** now sets `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` and runs
  `playwright install chromium` in the runtime stage.
- **docker-compose.yml** worker service adds `ipc: host` and
  `PLAYWRIGHT_BROWSERS_PATH` / `MAX_CONCURRENT_BROWSERS` environment variables.
- **DomainPolicy** gains `use_proxy` (bool, default true) and `proxy_country`
  (ISO 3166-1 alpha-2, nullable) columns (migration `0003`).
- **ProxyManager.get_proxy()** accepts `exclude_ids` and `country` parameters.
- **ProxyManager.report_result()** emits `crawler_proxy_health_score` metric
  after every health mutation.
- **httpx fetcher** now uses `client.stream()` with `aiter_raw()` to capture
  raw transport bytes for WARC archival.
- **FetchResult** dataclass gains `raw_body` and `raw_headers` fields.
- **WarcStorage.archive()** accepts optional `warc_body` parameter.

### Dependencies

- Added `brotli>=1.1.0` (Brotli decompression).
- Added `zstandard>=0.22.0` (Zstandard decompression).

## [0.1.0] — 2026-07-29

Initial release. Multi-tenant crawler-as-a-service with:
- FastAPI + SQLAlchemy 2.x + PostgreSQL + Redis + arq
- httpx, curl_cffi, and Playwright fetchers
- WARC archival to S3/MinIO
- API key authentication, rate limiting, usage metering
- Domain policy engine with tldextract normalization
- Proxy pool management with health scoring and circuit breaker
