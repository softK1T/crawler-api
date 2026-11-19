# Public proxy candidate checks

This feature is opt-in. It checks one bounded HTTPS control request through a
proxy, records its exit IP/country and activates passing imported candidates.
It does not prove anonymity, browser compatibility or access to any shop.

## Deployment prerequisites

Apply migration 0009 before starting updated API/worker code. Test migrations
on a disposable database first. Preserve the existing Camoufox/browser cache;
this feature does not require fetching or updating browser binaries. No
deployment is performed by committing this code.

Worker environment:

```env
PROXY_CHECK_ENABLED=true
PROXY_CHECK_ENDPOINT=https://1.1.1.1/cdn-cgi/trace
PROXY_CHECK_TIMEOUT_S=8
PROXY_CHECK_CONCURRENCY=4
PROXY_CHECK_BATCH_SIZE=20
PROXY_CHECK_TTL_S=21600
PROXY_CHECK_RETRY_S=600
```

Without PROXY_CHECK_DATABASE_PATH, country comes from client-location `loc`
in the HTTPS trace response, not data-center `colo`. This diagnostic endpoint
is not a guaranteed stable geolocation API. Missing/invalid country leaves
country=NULL while a valid exit-IP response still passes. Optionally mount
a licensed GeoLite2-Country MMDB read-only and set PROXY_CHECK_DATABASE_PATH.
Database download/licensing/updates are the operator's responsibility. Bad
configured database or endpoint fails preflight without changing candidates.
Geolocation is approximate and rotating gateways are not country guarantees.

## Import

Create a separate active pool using existing POST /v1/admin/proxy-pools:

```json
{"name":"public-candidates-001","provider":"public-candidates"}
```

The pool is active, but imported proxy records start inactive (quarantine).
POST /v1/proxy/candidates/import with admin scope:

```json
{"pool_id":"<pool UUID>","proxies":["http://8.8.8.8:3128","socks5://1.1.1.1:1080"]}
```

Addresses above illustrate syntax only; they are not verified proxies. Maximum
500 entries per request. Accepts public IP literals only, HTTP/HTTPS, SOCKS4/4a,
SOCKS5/5h and optional URL-encoded authentication. Type is unknown, not inferred
as datacenter/residential. Duplicates by (provider, URL) are not moved, updated
or reactivated. Existing Webshare/provider rows are not enrolled.

## Management

All new endpoints require admin scope:

- GET /v1/proxy/candidates/pools/{pool_id}/stats
- GET /v1/proxy/candidates/pools/{pool_id}/results?limit=100&offset=0
- POST /v1/proxy/candidates/pools/{pool_id}/recheck
- PATCH /v1/proxy/candidates/{proxy_id}/enabled with {"enabled":false} pauses
  and disables; true resumes checking, not immediate activation.

Use this pause endpoint rather than changing is_active alone. Reimport and
recheck do not unpause manually paused candidates. Result APIs never return
proxy URLs or credentials. Known country is stored in Proxy.country; unknown
country can be selected only without a country requirement. A datacenter-only
request will not select unknown-type public proxies. Omitting proxy_type
allows them but does not override existing pool/tenant selection rules.

## Checks and safeguards

The minute cron polls a durable DB queue. Bounded batch/concurrency, per-probe
and total-pass timeouts, row locks and a transaction advisory lock prevent
unbounded work and duplicate passes. Pausing waits for any in-flight row lock
and cannot be undone by that pass. Pass timeout rolls back writes. Expired
checks are excluded from selection. Only managed, unpaused candidates may be
automatically activated. Failure disables the candidate and schedules retry;
only exception categories, not credential-bearing messages, are recorded.
Successful HTTPS with unknown country activates but clears current country
to prevent stale routing. Sticky and ordinary selection share constraints.

HTTPS verification stays enabled, redirects/environment proxy fallback are
disabled, response size is limited to 16 KiB, proxy DNS results are public and
pinned, endpoint is a public HTTPS IP literal. No direct fallback or automatic
per-probe retry. Do not send sensitive sessions through public proxies.

## Verification

```sh
python -m unittest discover -s tests -p test_proxy_geo_unit.py -v
python -m unittest discover -s tests -p test_proxy_candidates_unit.py -v
ruff check app tests
ruff format --check app tests
mypy app/ tests/
pytest tests/integration/test_proxy_candidates.py -v
```

Integration tests use disposable Postgres/Redis fixtures or TEST_DATABASE_URL
and TEST_REDIS_URL; never point them at production. Probes are mocked. Actual
curl/TLS and control endpoint behavior require a deployment smoke test.
Migration round trips also require a disposable DB. Downgrade removes check
metadata but retains enum member unknown to avoid corrupting surviving rows.
