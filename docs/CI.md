# CI levels

CI is the only automatic workflow entrypoint. compose-smoke.yml is an internal
reusable job, not a second push/PR workflow. No schedules are added.

## Development policy

- Documentation-only updates: policy check and final gate only; no Python install,
  PostgreSQL/Redis service containers or Docker application build.
- Draft PR/push with pure policy or CI-policy changes: Ruff, formatting, mypy and
  tests that do not use live services. No PostgreSQL/Redis containers in this job.
- Changes to service boundaries, models, migrations, APIs, workers or mixed
  legacy tests: also run the existing complete non-slow suite with disposable DB/Redis.
- Runtime/dependency/deployment inputs: additionally build once and reuse the image
  for browser smoke, fresh Compose startup and the disposable restore drill.
- Ready for review: full validation. Later code changes to a ready PR rerun full
  validation; later documentation-only changes do not rebuild the application.
- Code pushes to master/main and v* release tags: full validation. A documentation-only
  master update does not rebuild; a release tag explicitly requests full validation.
- Converted to draft: cancel obsolete PR runs and do not restart heavy checks.

For PR synchronize, scope is the previous head to the new head, not the accumulated
diff against master. Initial opened/reopened PRs compare the PR base and head.
If git cannot determine scope, CI falls back to full validation rather than falsely
passing. Feature pushes with an open PR skip duplicate work; concurrency cancels
obsolete runs within each push/PR group. A small planner/gate job still runs.

## Test classification

Some tests in tests/unit actually request PostgreSQL or Redis. The CI-only plugin
selects fast tests by directory, integration marker and resolved fixture names.
A dynamically requested live-service fixture in fast mode fails with a clear error.
No production fixtures or tests are deleted. Normal pytest behaviour is unchanged
unless -p scripts.ci_test_selection --ci-unit-only is supplied. Full integration
runs preserve the previous complete non-slow suite, including legacy unit tests.
The slow marker remains excluded as before; this is not a claim every slow/load
or external-site test is run.

## Explicit full run

Use Actions -> CI -> Run workflow -> mode=full. mode=fast is the manual default.
The UI/manual trigger requires the workflow on the default branch; until release
changes are merged, ready_for_review is the available automatic full-validation
transition for this PR. Do not mark a release verified on fast checks alone.

CI gate validates the jobs selected for that run; a skipped required job is a
failure. Branch protection should require CI gate and an appropriate review policy.
These files do not themselves modify repository branch-protection settings or
prevent an administrator from bypassing rules. Keep release PRs as draft until
full checks for runtime changes are green. No deployment is performed by CI.

## Resource bounds

Dependency and Docker caches are retained; images are not rebuilt for each smoke
step or transferred as multi-GB workflow artifacts. The first full run or an evicted
cache can still be slow. Job timeouts bound failures, not normal execution time.
No exact runtime/minute savings are promised. Standard hosted runners for public
repositories are free under GitHub's applicable billing rules; cache/storage and
nonstandard runners have separate limits. Do not treat this policy as a billing cap.
