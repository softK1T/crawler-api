# ADR-016: Playwright Chromium Runtime Installation

**Date:** 2026-08-03
**Status:** Accepted

## Context

The worker Docker image installs the Playwright Python package via
`pip install ".[browser]"` but does **not** run `playwright install`.
The runtime image copies only Python packages and executables from the
builder stage — Chromium browser binaries are never placed on disk.

This means any `mode=browser` or `mode=camoufox` job that reaches a worker
fails at runtime when `playwright.chromium.launch()` is called.

## Decision

Install Chromium directly in the runtime stage with:

```dockerfile
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps --only-shell chromium \
    && chmod -R a+rx /ms-playwright
```

`--only-shell` is sufficient because the worker always launches headless
(`headless: True` in `PlaywrightFetcher`). It pulls fewer system
dependencies than the full Chromium package.

We keep `python:3.12-slim` as the base image rather than switching to
`mcr.microsoft.com/playwright/python`. Benefits:
- Single Python base image across API and worker.
- Explicit version coupling: the Chromium version is tied to the
  installed `playwright` Python package, not an opaque Docker tag.
- Smaller surface area for supply-chain risk.

### docker-compose

The worker service adds `ipc: host` to share host IPC namespace (Chromium
uses shared memory). If `ipc: host` is undesirable, `shm_size: "1gb"`
is the alternative.

### Startup self-check

A `_verify_chromium()` function runs in the worker `startup` hook before
any job is accepted:

```python
async def _verify_chromium() -> None:
    from pathlib import Path
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)

    if not executable.is_file():
        raise RuntimeError(
            "PLAYWRIGHT_CHROMIUM_MISSING: "
            f"expected executable at {executable}; "
            "rebuild the worker image with Playwright Chromium installed"
        )
```

If the binary is missing, worker startup fails hard — readiness stays
negative and no jobs are silently degraded to httpx.

## Alternatives considered

1. **Switch to `mcr.microsoft.com/playwright/python` base image.**
   Rejected: adds a second base image, loss of explicit version control,
   larger supply-chain surface.

2. **Install full Chromium (`--with-deps` without `--only-shell`).**
   Rejected: adds X11 libraries that are never needed in headless mode.

3. **Install at build time via Python in builder stage.**
   Rejected: builder doesn't set `PLAYWRIGHT_BROWSERS_PATH`, so
   Chromium lands in the user's home directory and is lost when the
   runtime stage switches to the `crawler` user.

## Consequences

- Worker image grows by ~150 MB (Chromium + system deps).
- Worker startup fails hard if Chromium is missing — no silent
  degradation to httpx.
- Browser-mode jobs require the worker service to have `ipc: host`
  (or `shm_size: "1gb"`).
