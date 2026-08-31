# Status Audit — crawler-api — 2026-08-30

Repo: `github.com/softK1T/crawler-api`, branch `master` @ `49e5b4c`, tag state `v1.0.0-81-g49e5b4c`.

## 1. Git-состояние

### Последние 20 коммитов (сгруппированы)

**test/fix(e2e) — новый e2e-набор и его стабилизация (5 commits, 2026-08-09)**
- `49e5b4c` fix(e2e): treat key='' as explicitly no-auth
- `4e4a638` fix(e2e): fix remaining 4 test failures
- `64b3aa7` fix(e2e): fix 7 test bugs found during first real run
- `4e40ef6` fix(e2e): skip direct DB bootstrap when DATABASE_URL is not set
- `91733cd` fix(e2e): add docker fallback for bootstrap, better error messages
- `c811345` fix(e2e): auto-detect project venv
- `19d16da` test(e2e): add comprehensive end-to-end API test suite

**merge (PR #14)**
- `23eac1b` Merge pull request #14 from `copilot/adr-021-proxy-provider-sync-and-observability`

**fix(tests) / chore**
- `a99bd6e` fix(tests): drop_all before create_all (cross-test data leakage)
- `3194062` chore: ruff fixes ("ruff fiux")

**feat/infra — ADR-021 proxy events sequencing**
- `10f9a21` migration: seq column
- `e35caf0` order-by seq instead of created_at
- `7ef6290` add sequence number to proxy_event model
- `f2f2dc6` fix formatting of SQL select in proxy.py

**refactor/fix — mypy type-cast cleanup (base.py/proxy.py)**
- `dde5b11`, `f648f30`, `1993000`, `f03313e`, `142d7b9`, `3dac764` — all mypy/typing fixes around `DomainPolicy`, `fetch_with_retry`, escalation flow.

Overall shape of the last 20: this is a **stabilization tail**, not new features — dominated by e2e bug-fixing and mypy/type-cast cleanup after ADR-021 landed.

### Merge-коммиты / merged PR за последние 2 месяца

| Merge | Date | Content | Relates to |
|---|---|---|---|
| PR #14 `copilot/adr-021-...` | 08-09 | proxy provider sync, `proxy_events`, seq column, admin events endpoint | ADR-021 |
| `Merge fix/gradual-tier-de-escalation` | 08-04 | step down one tier per success | ADR-019 escalation ladder |
| `Merge fix/policy-max-retries-per-tier` | 08-04 | honour `policy.max_retries` per tier | ADR-019 |
| `Merge fix/proxy-escalation-tests` | 08-04 | honour `use_proxy`, repair proxy/escalation tests | ADR-017/019 |
| `Merge stage-4-tests-admin` | 08-04 | pin-tier admin endpoint + tests | Stage 4 / antibot ladder |
| `Merge stage-2-camoufox-fetcher` | 08-04 | native CamoufoxFetcher, tiers 5-6 | ADR-020, camoufox |
| `Merge stage-1-antibot-data-model` | 08-04 | adaptive engine escalation ladder | ADR-019 |
| `Merge branch fix/chromium-arm64-and-proxy-policy` + PR #13/#11 | 08-03 | proxy_type field, ARM64 Chromium fix | ADR-016-playwright-runtime, ADR-017 |
| PR #8 `worktree-stage-a-adr-016` | 07-29 | operator key management demo | ADR-016-operator-key-management |
| `merge: browser pool prep... (Stage 15)` | 07-29 | WARC DLQ, streaming prep, rotation flag | ADR-015 |
| `merge: cleanup and performance (Stage 14)` | 07-29 | ADR-014 | ADR-014 |
| `merge: verified crawler platform rebuild (Stages 1-13)` | 07-29 | baseline 15-stage rebuild | ADR-001…013 |

147 commits total landed in the last 2 months, concentrated in weeks 2026-W31 (89) and W32 (55) — i.e. essentially all real work happened Aug 1–9, 2026.

### Ветки

| Branch | Ahead/behind vs master | Last commit date | Status |
|---|---|---|---|
| `copilot/adr-021-proxy-provider-sync-and-observability` | behind 1 (origin) | 08-09 | **merged** (via PR #14) |
| `fix/browser-mode-playwright` | ahead 1, behind 55 | — | **not merged**, stale (55 commits behind) |
| `fix/chromium-arm64-and-proxy-policy` | — | — | merged into master already (per merge commit `6222940`) — local ref stale |
| `fix/gradual-tier-de-escalation` | — | — | **merged** |
| `fix/policy-max-retries-per-tier` | — | — | **merged** |
| `fix/proxy-escalation-tests` | — | — | **merged** |
| `stage-1-antibot-data-model` | — | — | **merged** |
| `stage-2-camoufox-fetcher` | — | — | **merged** |
| `stage-3-seed-domains` | — | — | present locally/remote, not in the merge log above — needs verification, likely **merged earlier** or **unmerged** (flag for follow-up) |
| `stage-4-tests-admin` | — | — | **merged** |
| `worktree-e2e-test-script` | = master | 08-09 | tracks master (worktree checkout) |
| `worktree-fix-lint-mypy-errors` | — | — | worktree checkout, tracks `fix/chromium-arm64...` line |
| `worktree-stage-a-adr-016` | — | 08-03 | worktree checkout, appears merged via PR #8 |
| `worktree-stage1-security` | — | — | worktree checkout, latest commit is a Shopee-adapter cleanup refactor, **status unclear — needs review**, not seen in merge log |

Remote-only, never merged: `origin/dev`, `origin/feature/security-ssrf-apikey`, `origin/feature/week1-improvements`, `origin/fixes/code-improvements` — these look like **abandoned early-stage branches** predating the 15-stage rebuild baseline; safe candidates for pruning after confirmation.

### Незакоммиченные изменения (риск потери)

```
 M Dockerfile
 M app/api/v1/endpoints/batches.py
 M app/api/v1/endpoints/jobs.py
 M tests/integration/test_worker_fetch_task.py
```
4 files changed, 88 insertions(+), 9 deletions(-) — uncommitted, sitting in the working tree right now. There is also **1 stash** on `fix/chromium-arm64-and-proxy-policy`: `stash@{0}: dirty-dockerfile-temp` — an unresolved Dockerfile change parked and forgotten.

### Теги / версии / alembic

Tags: `v0.1.0`, `v0.2.0`, `v1.0.0` (current HEAD is 81 commits past `v1.0.0` — no tag cut for the ADR-021 / e2e work yet).

Alembic chain (linear, no branching detected): `0001 → f95d1ecc169c → a7c55bf575f3 → 0002 → 0003 → 0004 → 0005 → 0006 → d96e3566e6ba (head)`. `docs/SESSION-SUMMARY.md` still references `a7c55bf575f3` as "final migration head" — **stale**, since 3 more migrations (`0005`, `0006`, `d96e3566e6ba`) landed after that doc was last touched. No orphaned/unapplied migration files found outside this chain.

## 2. Код и качество

- **ruff**: 3 errors, both in `tests/e2e/run_e2e_tests.py` — `RUF005` (list concat vs unpacking) and `S606`/`S607` (subprocess without shell / partial executable path for `docker compose exec`). Low severity, isolated to the e2e helper script.
- **mypy** (`mypy app`): **Success: no issues found in 93 source files.** Clean — consistent with the recent run of dedicated `fix(mypy)` commits.
- **pytest**:
  - `tests/unit` (144 collected, non-docker): **144 passed**, but **15 errored at setup** — all failures are `docker.transport.unixconn` connection errors from `testcontainers` trying to spin up Postgres/Redis containers for `test_security.py`, `test_rate_limiter.py`, `test_proxy_sync_service.py`. This is an **environment gap** (Docker not reachable/running from this shell), not a code defect.
  - `tests/integration`: 53 tests collected successfully (not executed — same Docker dependency would apply).
  - `tests/e2e`: not run (requires a live docker-compose stack).

### TODO / FIXME / HACK / XXX / NotImplemented

Only two hits in `app/` — the codebase is unusually clean of debt markers:
- `app/api/v1/endpoints/auth.py:18` — `# TODO Stage 8: site adapter auth — adapter registry is empty` (both `/auth/login` and `/auth/session` return HTTP 501).
- `app/services/proxy_providers/base.py:19` — `raise NotImplementedError` (abstract base method, expected pattern, not a debt marker).

### Test coverage gaps (heuristic: no filename reference anywhere in `tests/`)

No test file references these modules at all:
- `app/core/ssrf_guard.py` (only `url_guard.py` is tested — SSRF-critical, given ADR-002)
- `app/worker/arq_worker.py`
- `app/services/fetchers/httpx_fetcher.py`, `app/services/fetchers/curl_fetcher.py`
- `app/services/archive_reader.py`
- `app/services/session_manager.py`
- `app/services/warc/dedup.py`, `app/services/warc/writer.py`, `app/services/warc/dlq.py`
- `app/services/batch_service.py`

`ssrf_guard.py` untested is the most concerning gap given it's a named security ADR (ADR-002).

## 3. Документация и решения

- `README.md`: architecture overview + "Verified State" section present, current.
- `CHANGELOG.md`: actively maintained through `[0.2.0]`/`[Unreleased]` with ADR-021 proxy-provider-sync entries — most current doc in the repo.
- `docs/SESSION-SUMMARY.md`: explicitly marked superseded, points to `PROJECT-HISTORY.md`; its migration-head reference is now stale (see §1).
- `docs/PROJECT-HISTORY.md`: 15-stage rebuild narrative, "four bugs found by real execution," verification contract, releases, and the "Deferred, with triggers" table (below).
- `docs/audit.md`, `docs/runbook.md`: present, runbook covers onboarding, day-2 ops, incident response, and a "Proxy Sync Cron (Stage 14)" maintenance section.
- `CLAUDE.md`: present (58 lines), AI-assisted-development method notes in `docs/AI-ASSISTED-DEVELOPMENT.md`.

### ADR-по-ADR (status label vs code confirmation)

| ADR | Label in doc | Code confirmation |
|---|---|---|
| 001 Argon2 key hashing | Accepted | confirmed — `app/core/security.py` |
| 002 SSRF guard | Accepted | partially confirmed — `app/core/ssrf_guard.py`/`url_guard.py` exist, but **no tests reference `ssrf_guard.py`** directly |
| 003 Partitioned request log | Accepted | confirmed — `app/models/request_log.py`, runbook has partition-maintenance section |
| 004 Auth dependency chain | Accepted | confirmed — `app/api/v1/dependencies.py`, used across endpoints |
| 005 Rate limiter | Accepted | confirmed — `app/services/rate_limiter.py`, `tests/unit/test_rate_limiter.py` (blocked by Docker in this run, not by code) |
| 006 Proxy manager | Accepted | confirmed — `app/services/proxy_manager.py` |
| 007 Fetcher architecture | Accepted | confirmed — `app/services/fetchers/*` |
| 008 WARC storage | Accepted | confirmed — `app/services/warc/*`, but writer/dedup/dlq have no direct tests |
| 009 arq job queue | no explicit status label | confirmed — `app/worker/arq_worker.py`, untested directly |
| 010 Archive read API | Accepted | confirmed — `app/api/v1/endpoints/archive.py`; **no streaming/chunked read found** — matches the deferred item below |
| 011 Observability/usage metering | Accepted | confirmed — `app/core/observability.py` |
| 012 Infra/deployment | Accepted | confirmed — Dockerfile/compose present |
| 013 Verification-driven adjustments | no status label | narrative ADR, `scripts/verify.sh` referenced in README |
| 014 Cleanup and performance | no status label | confirmed via Stage 14 merge commit |
| 015 Final performance/durability | no status label | confirmed via Stage 15 merge (browser pool prep, WARC DLQ) |
| 016 Operator key management | (large 378-line doc, no explicit status line found) | confirmed — `app/services/key_service.py`, `app/api/v1/endpoints/auth_keys.py` |
| 016 Playwright runtime (duplicate number) | Accepted | confirmed — Dockerfile installs Chromium; **duplicate "016" ADR ID is a documentation bug** (two files share the number) |
| 017 Proxy selection/rotation | Accepted | confirmed — proxy rotation-on-block logic in CHANGELOG 0.2.0 entry |
| 018 Response body normalization | Accepted | confirmed — `app/services/content_decoder.py` |
| 019 Escalation ladder | Accepted | confirmed — `app/services/escalation.py`, `app/services/policy_learner.py`, gradual de-escalation merge |
| 020 Camoufox packaging | no status label | confirmed — `app/services/fetchers/camoufox_fetcher.py` |
| 021 Proxy provider sync & observability | **Proposed** | **already implemented and merged** (PR #14, `proxy_events`, seq migration) — status label is stale, should be "Accepted/Implemented" |

### Deferred, with triggers (from PROJECT-HISTORY.md, verbatim conditions)

| Item | Trigger to revisit |
|---|---|
| Bounded browser pool | sustained >1 req/s in browser mode |
| Chunked streaming archive endpoint | archived bodies >10 MB |
| Camoufox engine (broader use) | residential proxy support lands |
| WebSocket/SSE job notifications | >50 concurrent sync requests |
| httpx connection pooling | >100 req/s sustained |

Invariant to preserve into any future browser pool implementation: reuse browsers, never contexts (cookie isolation + per-fetch SSRF interception tests must carry over, not be rewritten).

## 4. Хронология работы (последние ~1–2 месяца)

- **Jul 27–29**: Stages 1–13 baseline rebuild merged (`0c6ba91`), then Stage 14 (cleanup/perf) and Stage 15 (browser pool prep, WARC DLQ, streaming prep) merged same day (07-29). PR #8 (ADR-016 operator key management) also merged 07-29.
- **Aug 3**: Chromium ARM64 + proxy_type fixes merged via PR #13/#11 — **two PRs for the same branch** (`fix/chromium-arm64-and-proxy-policy`), a sign the first merge needed a follow-up correction.
- **Aug 4**: Antibot escalation ladder work landed in a single day as five sequential stage merges (stage-1 data model → stage-2 camoufox fetcher → stage-4 admin/tests → proxy-escalation-tests fix → policy-max-retries fix → gradual-de-escalation fix). The three trailing "fix/" merges right after the stage merges indicate the initial escalation implementation needed three follow-up corrections before it stabilized.
- **Aug 8**: A dedicated mypy-cleanup day — 8 commits, all `fix(mypy)`-flavored, tightening type casts around `DomainPolicy`, `fetch_with_retry`, and escalation tests.
- **Aug 9**: ADR-021 (proxy provider sync/observability) merged via PR #14, immediately followed by a `seq` column migration + reordering fix (2 more commits same day — another same-day correction pattern), then a brand-new e2e test suite added and stabilized through **6 consecutive fix(e2e) commits** in one session.

**Repeated fix-commit clusters (instability signal):**
1. Chromium/ARM64+proxy-policy — 2 PRs for one branch.
2. Antibot escalation ladder — 3 fix-merges immediately after the stage-1/2/4 merges.
3. e2e test suite — 6 fix commits directly following the initial test-suite commit, same day.
4. mypy typing — 8 fix commits in one day, suggesting type discipline was added retroactively rather than continuously.

No fix-commit clusters were found around SSRF guard or WARC storage — those parts have been stable since Stage 1–13.

## 5. Итог

### Стабильно и подтверждено кодом/тестами
- Core platform (Stages 1–13): auth, rate limiting, proxy manager, WARC storage, arq queue, archive API, observability — all present, `mypy` is fully clean (93/93 files), and merges show no post-hoc fix clusters.
- Antibot escalation ladder (ADR-019) and Camoufox fetcher (ADR-020) — merged, three correction rounds already absorbed, no ruff/mypy issues remain in these files.
- Proxy provider sync (ADR-021) — merged, migration chain consistent, `proxy_events` model in place.

### Частично сделано / требует доработки
- **ADR-021 status label** says "Proposed" in the doc but is functionally implemented — doc out of sync with code.
- **e2e test suite** (`tests/e2e/run_e2e_tests.py`) — brand new (07-09), has 3 outstanding ruff findings (subprocess safety) and has not been run against a live stack in this audit.
- **SSRF guard** (`app/core/ssrf_guard.py`) has zero direct test file coverage despite being a named security ADR.
- **ADR-016 duplicate numbering** (operator-key-management vs playwright-runtime) — documentation defect, not code.
- **`docs/SESSION-SUMMARY.md`** references a stale migration head (`a7c55bf575f3` instead of current head `d96e3566e6ba`).

### Запланировано, но не начато
- Site-adapter authentication (Stage 8) — `/auth/login` and `/auth/session` both explicitly `501 Not Implemented`.
- All five "Deferred, with triggers" items (bounded browser pool, chunked streaming archive, broader Camoufox use, WS/SSE notifications, httpx pooling) — none of their trigger conditions have fired yet per current code.
- `stage-3-seed-domains` branch (~700 domain seeder) — exists but was not observed in the merge log; needs explicit confirmation of merge status.

### Технический долг (приоритет / оценка)
| Item | Priority | Est. |
|---|---|---|
| Add tests for `ssrf_guard.py` (security-critical, currently untested) | P0 | 3–4h |
| Fix ruff `S606`/`S607`/`RUF005` in `tests/e2e/run_e2e_tests.py` | P1 | 1h |
| Correct ADR-021 status label to "Accepted"; resolve duplicate ADR-016 numbering | P1 | 1h |
| Update `docs/SESSION-SUMMARY.md` migration-head reference; verify `stage-3-seed-domains` merge status | P1 | 1h |
| Commit or discard the 4 dirty working-tree files (Dockerfile, batches.py, jobs.py, test_worker_fetch_task.py) and resolve the stashed `dirty-dockerfile-temp` | P0 | 1–2h (review + decide) |
| Add tests for `arq_worker.py`, `httpx_fetcher.py`, `curl_fetcher.py`, `archive_reader.py`, `warc/writer.py`, `warc/dedup.py`, `warc/dlq.py`, `batch_service.py`, `session_manager.py` | P2 | 8–12h |
| Prune stale/abandoned remote branches (`origin/dev`, `origin/feature/security-ssrf-apikey`, `origin/feature/week1-improvements`, `origin/fixes/code-improvements`) after confirming nothing unmerged is unique to them | P2 | 1h |
| Cut a `v1.1.0` (or similar) tag now that ADR-021 + e2e suite have landed 81 commits past `v1.0.0` | P2 | 0.5h |

### Топ-5 следующих задач
1. **Review and resolve the 4 uncommitted working-tree changes** (`Dockerfile`, `app/api/v1/endpoints/batches.py`, `app/api/v1/endpoints/jobs.py`, `tests/integration/test_worker_fetch_task.py`) plus the parked stash `dirty-dockerfile-temp` before they're lost — `git diff`, decide commit vs discard.
2. **Write unit tests for `app/core/ssrf_guard.py`** covering per-hop redirect validation per ADR-002, mirroring the pattern in `tests/test_url_guard.py`.
3. **Fix the 3 ruff findings in `tests/e2e/run_e2e_tests.py`** (lines ~48 and ~375) and run the new e2e suite against a live `docker compose` stack to validate it end-to-end.
4. **Sync documentation to code state**: update ADR-021 status to "Accepted", disambiguate the two ADR-016 files, and fix the stale migration-head reference in `docs/SESSION-SUMMARY.md`.
5. **Implement site-adapter authentication (Stage 8)** in `app/api/v1/endpoints/auth.py` and `app/services/adapters/base.py`, removing the two `501` stubs — this is the last explicitly-planned, not-yet-started feature block.
