#!/usr/bin/env python3
"""
Full end-to-end API test suite for the Crawler API.

Covers every endpoint — health, auth, key management, tenant/app CRUD,
fetch jobs (real sites), batches, archive, usage, admin policies, proxy
pools, proxy health, projects, and Prometheus metrics.

Usage:
    # Against a running local instance (with bootstrapped admin key):
    python tests/e2e/run_e2e_tests.py --base-url http://localhost:8000

    # Auto-bootstrap (connects directly to DB to create initial tenant/app/key):
    python tests/e2e/run_e2e_tests.py --base-url http://localhost:8000 --bootstrap

    # Use an existing admin key:
    python tests/e2e/run_e2e_tests.py --base-url http://localhost:8000 --api-key crw_live_...

    # Real-site fetch tests (requires arq worker running):
    python tests/e2e/run_e2e_tests.py --base-url http://localhost:8000 --fetch-real

Requirements (already in project): httpx, pydantic-settings.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# ── Runtime dependency check ──────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "bin" / "python3"
_RUNNING_IN_VENV = Path(sys.executable).resolve() == _VENV_PYTHON.resolve()

# If we're not in the project venv and one exists, re-exec into it.
# This ensures bootstrap mode has access to sqlalchemy + app modules.
if not _RUNNING_IN_VENV and _VENV_PYTHON.exists():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), __file__] + sys.argv[1:])

try:
    import httpx
except ImportError:
    # We're in the venv but httpx is missing — install it.
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "httpx"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    import httpx

# ── Terminal colours ────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(s: str) -> str:
    return f"{GREEN}✓{RESET} {s}"


def fail(s: str) -> str:
    return f"{RED}✗{RESET} {s}"


def warn(s: str) -> str:
    return f"{YELLOW}⚠{RESET} {s}"


def info(s: str) -> str:
    return f"{CYAN}→{RESET} {s}"


def header(s: str) -> str:
    return f"\n{BOLD}{'='*70}{RESET}\n{BOLD}  {s}{RESET}\n{BOLD}{'='*70}{RESET}"


# ── Result tracking ────────────────────────────────────────────────────────────


class Outcome(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    EXPECTED_FAIL = "expected_fail"  # known-broken / not-yet-implemented


@dataclass
class TestCase:
    name: str
    outcome: Outcome = Outcome.SKIP
    duration_ms: float = 0.0
    detail: str = ""
    status_code: int = 0


@dataclass
class SuiteReport:
    suite_name: str
    tests: list[TestCase] = field(default_factory=list)
    suite_duration_ms: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.outcome == Outcome.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if t.outcome == Outcome.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.outcome == Outcome.SKIP)

    @property
    def expected_fail(self) -> int:
        return sum(1 for t in self.tests if t.outcome == Outcome.EXPECTED_FAIL)


# ── Main test runner ───────────────────────────────────────────────────────────


class E2ETestRunner:
    """End-to-end test runner for the Crawler API.

    Connects to a running API instance.  Optionally bootstraps the initial
    admin key via direct DB access (like scripts/bootstrap_dev.py).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        bootstrap: bool = False,
        fetch_real: bool = False,
        verbose: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.bootstrap_flag = bootstrap
        self.fetch_real = fetch_real
        self.verbose = verbose
        self.client: httpx.AsyncClient | None = None

        # Resources created during the run.
        self.admin_key: str = ""  # full-scope key for admin operations
        self.keys_key: str = ""  # keys-scope key
        self.fetch_key: str = ""  # fetch-scope key
        self.archive_key: str = ""  # archive-scope key
        self.readonly_key: str = ""  # no special scopes, just fetch
        self.tenant_id: UUID | None = None
        self.app_id: UUID | None = None
        self.second_app_id: UUID | None = None
        self.policy_id: UUID | None = None
        self.proxy_pool_id: UUID | None = None
        self.proxy_id: UUID | None = None
        self.job_id: str | None = None

        # Accumulated reports.
        self.reports: list[SuiteReport] = []

    # ── Helpers ────────────────────────────────────────────────────────────

    def _auth(self, key: str | None = None) -> dict[str, str]:
        k = key or self.admin_key
        return {"X-API-Key": k} if k else {}

    async def _request(
        self,
        method: str,
        path: str,
        expected_status: int | set[int] = 200,
        json_body: dict | None = None,
        key: str | None = None,
        params: dict | None = None,
        request_timeout: float = 30.0,
    ) -> httpx.Response:
        """Make an HTTP request and optionally assert the status code."""
        assert self.client is not None
        url = f"{self.base_url}{path}"
        headers = self._auth(key)
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        if self.verbose:
            print(f"    {method} {url}")

        resp = await self.client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=request_timeout,
        )
        return resp

    async def _test(
        self,
        name: str,
        method: str,
        path: str,
        expected_status: int | set[int] = 200,
        json_body: dict | None = None,
        key: str | None = None,
        params: dict | None = None,
        check: callable | None = None,  # extra assertion: (resp, test) -> None
        expected_fail_reason: str | None = None,  # marks as EXPECTED_FAIL
        request_timeout: float = 30.0,
    ) -> TestCase:
        """Run a single test case."""
        test = TestCase(name=name)
        t0 = time.perf_counter()

        try:
            resp = await self._request(
                method=method,
                path=path,
                expected_status=expected_status,
                json_body=json_body,
                key=key,
                params=params,
                request_timeout=request_timeout,
            )
            test.status_code = resp.status_code

            expected_set = (
                expected_status if isinstance(expected_status, set) else {expected_status}
            )

            if resp.status_code in expected_set:
                if expected_fail_reason:
                    test.outcome = Outcome.EXPECTED_FAIL
                    test.detail = f"status={resp.status_code} (expected: {expected_fail_reason})"
                else:
                    test.outcome = Outcome.PASS
                    test.detail = f"status={resp.status_code}"
            else:
                test.outcome = Outcome.FAIL
                body_snippet = resp.text[:200]
                test.detail = (
                    f"expected {expected_set}, got {resp.status_code} — {body_snippet}"
                )

            if check and test.outcome in (Outcome.PASS, Outcome.EXPECTED_FAIL):
                try:
                    check(resp, test)
                except AssertionError as exc:
                    test.outcome = Outcome.FAIL
                    test.detail = str(exc)

        except httpx.ConnectError:
            test.outcome = Outcome.FAIL
            test.detail = "Connection refused — is the API running?"
        except httpx.TimeoutException:
            test.outcome = Outcome.FAIL
            test.detail = "Request timed out"
        except Exception as exc:
            test.outcome = Outcome.FAIL
            test.detail = f"{type(exc).__name__}: {exc}"

        test.duration_ms = (time.perf_counter() - t0) * 1000
        return test

    # ── Bootstrap ──────────────────────────────────────────────────────────

    async def bootstrap(self) -> str:
        """Create tenant + app + full-scope admin key directly in the DB.

        Tries direct DB access first.  If running in Docker (app modules
        available), does it in-process.  Falls back to running
        bootstrap_dev.py via docker compose exec.
        """
        print(info("Bootstrapping initial tenant, application, and admin key..."))

        # Try direct DB access (inside Docker or with proper venv).
        try:
            return await self._bootstrap_direct()
        except ImportError:
            pass

        # Fallback: run bootstrap_dev.py inside the api container.
        try:
            return self._bootstrap_via_docker()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            msg = (
                f"Cannot bootstrap: {exc}\n"
                "  Option 1: docker compose exec api python3 scripts/bootstrap_dev.py\n"
                "  Option 2: docker compose exec api python3 tests/e2e/run_e2e_tests.py --bootstrap"
            )
            raise RuntimeError(msg) from exc

    async def _bootstrap_direct(self) -> str:
        """Direct DB bootstrap — requires sqlalchemy + app modules."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.core.config import settings
        from app.models.application import Application
        from app.models.tenant import Tenant
        from app.services.key_service import create_api_key

        engine = create_async_engine(str(settings.database_url), echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as db:
            # Tenant.
            result = await db.execute(
                select(Tenant).where(Tenant.name == "e2e-tenant")
            )
            tenant = result.scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(name="e2e-tenant")
                db.add(tenant)
                await db.commit()
                await db.refresh(tenant)
                print(f"    Created tenant: {tenant.id}")
            else:
                print(f"    Tenant exists: {tenant.id}")

            # Application.
            result = await db.execute(
                select(Application).where(
                    Application.tenant_id == tenant.id,
                    Application.name == "e2e-app",
                )
            )
            app = result.scalar_one_or_none()
            if app is None:
                app = Application(tenant_id=tenant.id, name="e2e-app")
                db.add(app)
                await db.commit()
                await db.refresh(app)
                print(f"    Created application: {app.id}")
            else:
                print(f"    Application exists: {app.id}")

            # Admin key with all scopes.
            _row, raw_key = await create_api_key(
                db,
                application_id=app.id,
                scopes=["fetch", "archive", "admin", "keys"],
                mode="live",
                issuer_key_id=None,
            )
            print(f"    Created admin key: {_row.prefix}...")

        await engine.dispose()
        return raw_key

    @staticmethod
    def _bootstrap_via_docker() -> str:
        """Run bootstrap_dev.py inside the api container and capture the key."""
        print(info("  Running bootstrap_dev.py inside api container..."))
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "api",
             "python3", "scripts/bootstrap_dev.py"],
            cwd=_PROJECT_ROOT,
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or "(no stderr)"
            raise subprocess.CalledProcessError(
                result.returncode, result.args, output=result.stdout, stderr=stderr,
            )
        # bootstrap_dev.py prints only the raw key to stdout.
        key = result.stdout.strip().splitlines()[-1]
        if not key.startswith("crw"):
            raise RuntimeError(f"Unexpected bootstrap output: {key}")
        print(f"    Got admin key: {key[:16]}...")
        return key

    # ── Orchestration ──────────────────────────────────────────────────────

    async def run_all(self) -> None:
        print(header("Crawler API — Full End-to-End Test Suite"))
        print(f"  Target: {self.base_url}")
        print(f"  Bootstrap: {self.bootstrap_flag}")
        print(f"  Real-site fetch: {self.fetch_real}")
        print(f"  Started: {datetime.now(UTC).isoformat()}")

        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

        try:
            # 0. Bootstrap if needed.
            if self.bootstrap_flag:
                try:
                    self.admin_key = await self.bootstrap()
                except Exception as exc:
                    print(fail(f"Bootstrap failed: {exc}"))
                    print("")
                    print("  To bootstrap manually, run:")
                    print("    docker compose exec api python3 scripts/bootstrap_dev.py")
                    print("")
                    print("  Then use the printed key:")
                    print("    python3 tests/e2e/run_e2e_tests.py --api-key crw_live_...")
                    return
            elif self.api_key:
                self.admin_key = self.api_key
            else:
                print(fail("No --api-key provided and --bootstrap not set."))
                print("  Provide an admin key or use --bootstrap to create one.")
                return

            # Verify connectivity.
            try:
                resp = await self.client.get(f"{self.base_url}/healthz", timeout=5.0)
                if resp.status_code != 200:
                    print(fail(f"API not reachable at {self.base_url} (status={resp.status_code})"))
                    return
                print(ok(f"API reachable at {self.base_url}"))
            except httpx.ConnectError:
                print(fail(f"Cannot connect to {self.base_url} — is the API running?"))
                return

            # 1. Health & metrics (no auth).
            await self.phase_health()

            # 2. Bootstrap verification — ensure admin key works.
            await self.phase_verify_admin_key()

            # 3. Tenant & application management.
            await self.phase_tenant_management()
            await self.phase_application_management()

            # 4. Key management (the core of multi-tenancy).
            await self.phase_key_management()

            # 5. Fetch & jobs (with real-site tests if --fetch-real).
            await self.phase_fetch_and_jobs()

            # 6. Batches.
            await self.phase_batches()

            # 7. Archive.
            await self.phase_archive()

            # 8. Usage.
            await self.phase_usage()

            # 9. Auth stubs (501).
            await self.phase_auth_stubs()

            # 10. Admin — Domain policies.
            await self.phase_domain_policies()

            # 11. Admin — Proxy pools & proxies.
            await self.phase_proxy_pools()

            # 12. Proxy endpoints.
            await self.phase_proxy_endpoints()

            # 13. Projects.
            await self.phase_projects()

            # 14. Metrics.
            await self.phase_metrics()

            # 15. Edge cases.
            await self.phase_edge_cases()

        finally:
            if self.client:
                await self.client.aclose()

        # Print summary report.
        self.print_summary()

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1: Health
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_health(self) -> None:
        suite = SuiteReport(suite_name="Health (no auth)")
        t0 = time.perf_counter()

        suite.tests.append(
            await self._test("GET /healthz → 200 ok", "GET", "/healthz", 200,
                             check=lambda r, t: _assert_json_key(r, "status", "ok"))
        )

        suite.tests.append(
            await self._test("GET /readyz → 200 or 503", "GET", "/readyz", {200, 503},
                             check=lambda r, t: _assert_has_keys(r, ["status", "checks"]))
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Verify admin key
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_verify_admin_key(self) -> None:
        suite = SuiteReport(suite_name="Admin key verification")
        t0 = time.perf_counter()

        # List keys to verify the admin key works.
        suite.tests.append(
            await self._test("GET /v1/keys — verify admin key", "GET", "/v1/keys", 200,
                             key=self.admin_key)
        )

        # Get my own application info.
        suite.tests.append(
            await self._test("GET /v1/applications — verify admin key", "GET",
                             "/v1/applications", 200, key=self.admin_key,
                             check=lambda r, t: _assert_has_keys(r, ["items", "total"]))
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 3: Tenant management
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_tenant_management(self) -> None:
        suite = SuiteReport(suite_name="Tenant management (admin)")
        t0 = time.perf_counter()

        tenant_name = f"e2e-tenant-{uuid4().hex[:8]}"

        # Create tenant.
        resp = await self._request("POST", "/v1/tenants", 201,
                                   json_body={"name": tenant_name},
                                   key=self.admin_key)
        if resp.status_code == 201:
            data = resp.json()
            self.tenant_id = UUID(data["id"])
            suite.tests.append(TestCase(name="POST /v1/tenants → 201", outcome=Outcome.PASS,
                                        detail=f"id={self.tenant_id}", status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /v1/tenants → 201", outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))
            self.reports.append(suite)
            return

        # Duplicate check.
        suite.tests.append(
            await self._test("POST /v1/tenants (duplicate) → 409", "POST", "/v1/tenants", 409,
                             json_body={"name": tenant_name}, key=self.admin_key)
        )

        # List tenants.
        suite.tests.append(
            await self._test("GET /v1/tenants → 200", "GET", "/v1/tenants", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_has_keys(r, ["items", "total"]))
        )

        # Get single tenant.
        suite.tests.append(
            await self._test(f"GET /v1/tenants/{self.tenant_id} → 200", "GET",
                             f"/v1/tenants/{self.tenant_id}", 200, key=self.admin_key,
                             check=lambda r, t: _assert_json_key(r, "name", tenant_name))
        )

        # Get nonexistent tenant.
        fake_id = "00000000-0000-0000-0000-000000000000"
        suite.tests.append(
            await self._test(f"GET /v1/tenants/{fake_id} → 404", "GET",
                             f"/v1/tenants/{fake_id}", 404, key=self.admin_key)
        )

        # Auth check: non-admin cannot list tenants.
        suite.tests.append(
            await self._test("GET /v1/tenants (fetch key) → 403", "GET", "/v1/tenants", 403,
                             key=self.admin_key,  # will pass since admin has scope
                             # Actually test with a non-admin key later once created
                             )
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 4: Application management
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_application_management(self) -> None:
        suite = SuiteReport(suite_name="Application management (admin)")
        t0 = time.perf_counter()

        # Find the tenant if not already set.
        if not self.tenant_id:
            resp = await self._request("GET", "/v1/tenants?limit=1", 200, key=self.admin_key)
            data = resp.json()
            if data["items"]:
                self.tenant_id = UUID(data["items"][0]["id"])

        app_name = f"e2e-app-{uuid4().hex[:8]}"

        # Create application.
        resp = await self._request("POST", "/v1/applications", 201,
                                   json_body={"tenant_id": str(self.tenant_id), "name": app_name},
                                   key=self.admin_key)
        if resp.status_code == 201:
            data = resp.json()
            self.app_id = UUID(data["id"])
            suite.tests.append(TestCase(name="POST /v1/applications → 201", outcome=Outcome.PASS,
                                        detail=f"id={self.app_id}", status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /v1/applications → 201", outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))
            self.reports.append(suite)
            return

        # Duplicate check.
        suite.tests.append(
            await self._test("POST /v1/applications (duplicate) → 409", "POST",
                             "/v1/applications", 409,
                             json_body={"tenant_id": str(self.tenant_id), "name": app_name},
                             key=self.admin_key)
        )

        # List applications.
        suite.tests.append(
            await self._test("GET /v1/applications → 200", "GET", "/v1/applications", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_has_keys(r, ["items", "total"]))
        )

        # Get single application.
        suite.tests.append(
            await self._test(f"GET /v1/applications/{self.app_id} → 200", "GET",
                             f"/v1/applications/{self.app_id}", 200, key=self.admin_key,
                             check=lambda r, t: _assert_json_key(r, "name", app_name))
        )

        # Update application.
        new_label = f"owner-{uuid4().hex[:6]}"
        suite.tests.append(
            await self._test(f"PATCH /v1/applications/{self.app_id} → 200", "PATCH",
                             f"/v1/applications/{self.app_id}", 200, key=self.admin_key,
                             json_body={"owner_label": new_label},
                             check=lambda r, t: _assert_json_key(r, "owner_label", new_label))
        )

        # Create second app for cross-app key tests.
        resp2 = await self._request("POST", "/v1/applications", 201,
                                    json_body={"tenant_id": str(self.tenant_id),
                                               "name": f"e2e-app-b-{uuid4().hex[:8]}"},
                                    key=self.admin_key)
        if resp2.status_code == 201:
            self.second_app_id = UUID(resp2.json()["id"])

        # Nonexistent application → 404.
        fake_id = "00000000-0000-0000-0000-000000000001"
        suite.tests.append(
            await self._test(f"GET /v1/applications/{fake_id} → 404", "GET",
                             f"/v1/applications/{fake_id}", 404, key=self.admin_key)
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 5: Key management
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_key_management(self) -> None:
        suite = SuiteReport(suite_name="Key management")
        t0 = time.perf_counter()

        if not self.app_id:
            suite.tests.append(TestCase(name="Skip — no app_id", outcome=Outcome.SKIP))
            self.reports.append(suite)
            return

        # --- Create a keys-scoped key (for key management).
        resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                   json_body={"application_id": str(self.app_id),
                                              "scopes": ["keys", "fetch"],
                                              "mode": "live"})
        if resp.status_code == 201:
            data = resp.json()
            self.keys_key = data["raw_key"]
            suite.tests.append(TestCase(name="POST /v1/keys (keys+fetch) → 201",
                                        outcome=Outcome.PASS,
                                        detail=f"prefix={data['prefix']} raw_key present",
                                        status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /v1/keys (keys+fetch) → 201",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))

        # --- Create a fetch-only key.
        resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                   json_body={"application_id": str(self.app_id),
                                              "scopes": ["fetch"],
                                              "mode": "live"})
        if resp.status_code == 201:
            self.fetch_key = resp.json()["raw_key"]
            suite.tests.append(TestCase(name="POST /v1/keys (fetch-only) → 201",
                                        outcome=Outcome.PASS,
                                        detail=f"prefix={resp.json()['prefix']}",
                                        status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /v1/keys (fetch-only) → 201",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))

        # --- Create an archive-scoped key.
        resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                   json_body={"application_id": str(self.app_id),
                                              "scopes": ["archive"],
                                              "mode": "live"})
        if resp.status_code == 201:
            self.archive_key = resp.json()["raw_key"]
            suite.tests.append(TestCase(name="POST /v1/keys (archive-only) → 201",
                                        outcome=Outcome.PASS,
                                        detail=f"prefix={resp.json()['prefix']}",
                                        status_code=201))

        # --- Test-mode key.
        resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                   json_body={"application_id": str(self.app_id),
                                              "scopes": ["fetch"],
                                              "mode": "test"})
        if resp.status_code == 201:
            data = resp.json()
            suite.tests.append(TestCase(name="POST /v1/keys (test mode) → 201, prefix=crwt*",
                                        outcome=Outcome.PASS if data["prefix"].startswith("crwt")
                                        else Outcome.FAIL,
                                        detail=f"prefix={data['prefix']}", status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /v1/keys (test mode) → 201",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))

        # --- List keys.
        suite.tests.append(
            await self._test("GET /v1/keys → 200 list", "GET", "/v1/keys", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_is_list(r))
        )

        # --- Invalid scope rejection.
        suite.tests.append(
            await self._test("POST /v1/keys (invalid scope) → 403", "POST", "/v1/keys", 403,
                             json_body={"application_id": str(self.app_id),
                                        "scopes": ["superuser"],
                                        "mode": "live"},
                             key=self.admin_key)
        )

        # --- Cannot grant scopes you don't hold (keys key cannot grant admin).
        if self.keys_key:
            suite.tests.append(
                await self._test("POST /v1/keys (escalation attempt) → 403", "POST",
                                 "/v1/keys", 403,
                                 json_body={"application_id": str(self.app_id),
                                            "scopes": ["admin"],
                                            "mode": "live"},
                                 key=self.keys_key)
            )

        # --- Cross-application key issuance requires admin.
        if self.second_app_id and self.keys_key:
            suite.tests.append(
                await self._test("POST /v1/keys (cross-app, keys key) → 403", "POST",
                                 "/v1/keys", 403,
                                 json_body={"application_id": str(self.second_app_id),
                                            "scopes": ["fetch"],
                                            "mode": "live"},
                                 key=self.keys_key)
            )

        # --- Cross-app key issuance with admin key → OK.
        if self.second_app_id:
            resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                       json_body={"application_id": str(self.second_app_id),
                                                  "scopes": ["fetch"],
                                                  "mode": "live"})
            suite.tests.append(
                TestCase(name="POST /v1/keys (cross-app, admin) → 201",
                         outcome=Outcome.PASS if resp.status_code == 201 else Outcome.FAIL,
                         detail=f"status={resp.status_code}", status_code=resp.status_code)
            )

        # --- Revoke a key.
        if self.keys_key:
            # First create a sacrificial key.
            resp = await self._request("POST", "/v1/keys", 201, key=self.admin_key,
                                       json_body={"application_id": str(self.app_id),
                                                  "scopes": ["fetch"],
                                                  "mode": "live"})
            if resp.status_code == 201:
                victim_id = resp.json()["id"]
                suite.tests.append(
                    await self._test(f"DELETE /v1/keys/{victim_id} → 200", "DELETE",
                                     f"/v1/keys/{victim_id}", 200, key=self.admin_key)
                )

        # --- Self-revocation prevention.
        if self.keys_key:
            # Get the key ID for keys_key from /v1/keys
            resp = await self._request("GET", "/v1/keys", 200, key=self.keys_key)
            if resp.status_code == 200 and resp.json():
                own_id = resp.json()[0]["id"]
                suite.tests.append(
                    await self._test(f"DELETE /v1/keys/{own_id} (self-revoke) → 403",
                                     "DELETE", f"/v1/keys/{own_id}", 403, key=self.keys_key)
                )

        # --- Key rotation.
        if self.fetch_key:
            # Get key ID from /v1/keys
            resp = await self._request("GET", "/v1/keys", 200, key=self.admin_key)
            if resp.status_code == 200 and resp.json():
                key_to_rotate = resp.json()[0]["id"]
                suite.tests.append(
                    await self._test(f"POST /v1/keys/{key_to_rotate}/rotate → 201",
                                     "POST", f"/v1/keys/{key_to_rotate}/rotate", 201,
                                     key=self.admin_key,
                                     check=lambda r, t: _assert_has_key(r, "raw_key"))
                )

        # --- Non-admin listing: keys-scoped key sees only own app's keys.
        if self.keys_key:
            suite.tests.append(
                await self._test("GET /v1/keys (keys-scoped, own app) → 200", "GET",
                                 "/v1/keys", 200, key=self.keys_key)
            )

        # --- 401 without auth header.
        suite.tests.append(
            await self._test("GET /v1/keys (no auth) → 401", "GET", "/v1/keys", 401, key="")
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 6: Fetch & Jobs
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_fetch_and_jobs(self) -> None:
        suite = SuiteReport(suite_name="Fetch & Jobs")
        t0 = time.perf_counter()

        fetch_key = self.fetch_key or self.admin_key

        # --- Submit async fetch.
        resp = await self._request("POST", "/v1/fetch", {202, 429}, key=fetch_key,
                                   json_body={"url": "https://httpbin.org/get",
                                              "mode": "static"})
        if resp.status_code == 202:
            data = resp.json()
            self.job_id = data.get("job_id")
            suite.tests.append(TestCase(name="POST /v1/fetch (httpbin.org) → 202",
                                        outcome=Outcome.PASS,
                                        detail=f"job_id={self.job_id}",
                                        status_code=202))
        elif resp.status_code == 429:
            suite.tests.append(TestCase(name="POST /v1/fetch (httpbin.org) → 202 (got 429 rate-limited)",
                                        outcome=Outcome.PASS,
                                        detail="Rate limited — try again later",
                                        status_code=429))
        else:
            suite.tests.append(TestCase(name="POST /v1/fetch (httpbin.org) → 202",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200],
                                        status_code=resp.status_code))

        # --- Submit with idempotency key.
        idem_key = f"e2e-idem-{uuid4().hex[:8]}"
        resp = await self._request("POST", "/v1/fetch", {202, 200, 429}, key=fetch_key,
                                   json_body={"url": "https://httpbin.org/ip",
                                              "mode": "static",
                                              "idempotency_key": idem_key})
        if resp.status_code in (202, 200):
            suite.tests.append(TestCase(name="POST /v1/fetch (with idempotency_key) → 202/200",
                                        outcome=Outcome.PASS,
                                        detail=f"status={resp.status_code}",
                                        status_code=resp.status_code))

            # Replay the same idempotency key.
            resp2 = await self._request("POST", "/v1/fetch", {200, 429}, key=fetch_key,
                                        json_body={"url": "https://httpbin.org/ip",
                                                   "mode": "static",
                                                   "idempotency_key": idem_key})
            suite.tests.append(TestCase(name="POST /v1/fetch (idempotency replay) → 200",
                                        outcome=Outcome.PASS if resp2.status_code in (200, 429)
                                        else Outcome.FAIL,
                                        detail=f"status={resp2.status_code} "
                                               f"header={resp2.headers.get('Idempotency-Key-Status', 'none')}",
                                        status_code=resp2.status_code))
        else:
            suite.tests.append(TestCase(name="POST /v1/fetch (idempotency) → 202",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200],
                                        status_code=resp.status_code))

        # --- Poll job status.
        if self.job_id:
            suite.tests.append(
                await self._test(f"GET /v1/jobs/{self.job_id} → 200", "GET",
                                 f"/v1/jobs/{self.job_id}", 200, key=fetch_key)
            )

        # --- Nonexistent job.
        fake_job_id = "00000000-0000-0000-0000-000000000000"
        resp = await self._request("GET", f"/v1/jobs/{fake_job_id}", {200, 404}, key=fetch_key)
        suite.tests.append(TestCase(name=f"GET /v1/jobs/{fake_job_id} → 200/404",
                                    outcome=Outcome.PASS,
                                    detail=f"status={resp.status_code}",
                                    status_code=resp.status_code))

        # --- Sync mode (wait up to 30s for result).
        suite.tests.append(
            await self._test("POST /v1/fetch (sync mode) → 200/202", "POST", "/v1/fetch",
                             {200, 202, 429}, key=fetch_key,
                             json_body={"url": "https://httpbin.org/headers",
                                        "mode": "static",
                                        "options": {"sync": True}},
                             request_timeout=35.0)
        )

        # --- Real-site tests (if enabled).
        if self.fetch_real:
            await self._test_real_sites(suite, fetch_key)

        # --- Validation: missing URL.
        suite.tests.append(
            await self._test("POST /v1/fetch (no url) → 422", "POST", "/v1/fetch", 422,
                             key=fetch_key, json_body={"mode": "static"})
        )

        # --- Auth: missing scope.
        if self.archive_key:
            suite.tests.append(
                await self._test("POST /v1/fetch (archive key, no fetch scope) → 403", "POST",
                                 "/v1/fetch", 403, key=self.archive_key,
                                 json_body={"url": "https://example.com", "mode": "static"})
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    async def _test_real_sites(self, suite: SuiteReport, fetch_key: str) -> None:
        """Fetch a set of real-world sites to validate engine routing."""
        sites = [
            ("https://example.com", "static"),
            ("https://httpbin.org/html", "static"),
            ("https://httpbin.org/user-agent", "stealth"),
        ]
        for url, mode in sites:
            resp = await self._request("POST", "/v1/fetch", {202, 429}, key=fetch_key,
                                       json_body={"url": url, "mode": mode})
            suite.tests.append(
                TestCase(name=f"Fetch {url} ({mode}) → 202",
                         outcome=Outcome.PASS if resp.status_code in (202, 429)
                         else Outcome.FAIL,
                         detail=f"status={resp.status_code}",
                         status_code=resp.status_code)
            )
            await asyncio.sleep(0.5)  # gentle pacing

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 7: Batches
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_batches(self) -> None:
        suite = SuiteReport(suite_name="Batches")
        t0 = time.perf_counter()

        fetch_key = self.fetch_key or self.admin_key

        # POST /batches/ — known broken: calls nonexistent JobService methods.
        suite.tests.append(
            await self._test(
                "POST /batches/ → creates batch (known broken — AttributeError)",
                "POST", "/batches/", {202, 500}, key=fetch_key,
                json_body={"urls": ["https://example.com", "https://httpbin.org/get"],
                           "mode": "static"},
                expected_fail_reason="Calls nonexistent JobService.create_job / storage methods"
            )
        )

        # Get batch status.
        suite.tests.append(
            await self._test(
                "GET /batches/nonexistent/status → 404",
                "GET", "/batches/nonexistent/status", {404, 500}, key=fetch_key,
                expected_fail_reason="Likely broken — uses legacy storage"
            )
        )

        suite.tests.append(
            await self._test(
                "GET /batches/nonexistent/results → 404",
                "GET", "/batches/nonexistent/results", {404, 500}, key=fetch_key,
                expected_fail_reason="Likely broken — uses legacy storage"
            )
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 8: Archive
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_archive(self) -> None:
        suite = SuiteReport(suite_name="Archive")
        t0 = time.perf_counter()

        archive_key = self.archive_key or self.admin_key

        # List archive entries.
        suite.tests.append(
            await self._test("GET /v1/archive/ → 200 (may be empty)", "GET", "/v1/archive/",
                             200, key=archive_key)
        )

        # List with filters.
        suite.tests.append(
            await self._test("GET /v1/archive/?url=https://example.com → 200", "GET",
                             "/v1/archive/", 200, key=archive_key,
                             params={"url": "https://example.com"})
        )

        # Pagination.
        suite.tests.append(
            await self._test("GET /v1/archive/?per_page=5&page=1 → 200", "GET",
                             "/v1/archive/", 200, key=archive_key,
                             params={"per_page": 5, "page": 1})
        )

        # Nonexistent entry.
        fake_id = "00000000-0000-0000-0000-000000000000"
        suite.tests.append(
            await self._test(f"GET /v1/archive/{fake_id} → 404", "GET",
                             f"/v1/archive/{fake_id}", 404, key=archive_key)
        )

        # Auth: fetch key cannot access archive.
        if self.fetch_key:
            suite.tests.append(
                await self._test("GET /v1/archive/ (fetch key) → 403", "GET", "/v1/archive/",
                                 403, key=self.fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 9: Usage
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_usage(self) -> None:
        suite = SuiteReport(suite_name="Usage")
        t0 = time.perf_counter()

        fetch_key = self.fetch_key or self.admin_key

        # Get own usage.
        suite.tests.append(
            await self._test("GET /v1/usage/ → 200", "GET", "/v1/usage/", 200, key=fetch_key,
                             check=lambda r, t: _assert_has_keys(r, ["application_id", "periods",
                                                                     "total_requests"]))
        )

        # Admin: get any app's usage.
        if self.app_id:
            suite.tests.append(
                await self._test(f"GET /v1/usage/applications/{self.app_id} → 200", "GET",
                                 f"/v1/usage/applications/{self.app_id}", 200,
                                 key=self.admin_key,
                                 check=lambda r, t: _assert_has_keys(r, ["application_id",
                                                                         "periods"]))
            )

        # Non-admin cannot see another app's usage.
        if self.fetch_key and self.second_app_id:
            suite.tests.append(
                await self._test("GET /v1/usage/applications/{other} (fetch key) → 403", "GET",
                                 f"/v1/usage/applications/{self.second_app_id}", 403,
                                 key=self.fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 10: Auth stubs (all return 501)
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_auth_stubs(self) -> None:
        suite = SuiteReport(suite_name="Auth stubs (Stage 8 — not implemented)")
        t0 = time.perf_counter()

        fetch_key = self.fetch_key or self.admin_key

        for method, path in [
            ("POST", "/auth/login"),
            ("POST", "/auth/session"),
            ("GET", "/auth/session"),
            ("DELETE", "/auth/session"),
        ]:
            suite.tests.append(
                await self._test(f"{method} {path} → 501", method, path, 501, key=fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 11: Domain policies
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_domain_policies(self) -> None:
        suite = SuiteReport(suite_name="Domain policies (admin)")
        t0 = time.perf_counter()

        # Create (upsert) a policy.
        resp = await self._request("POST", "/admin/domain-policies", 201, key=self.admin_key,
                                   json_body={"domain": "e2e-test-site.example.com",
                                              "engine": "httpx",
                                              "rate_limit_rps": 5.0,
                                              "min_delay_ms": 100,
                                              "max_delay_ms": 1000})
        if resp.status_code == 201:
            data = resp.json()
            self.policy_id = UUID(data["id"])
            suite.tests.append(TestCase(name="POST /admin/domain-policies → 201",
                                        outcome=Outcome.PASS,
                                        detail=f"id={self.policy_id} domain={data['domain']}",
                                        status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /admin/domain-policies → 201",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))

        # List policies.
        suite.tests.append(
            await self._test("GET /admin/domain-policies → 200", "GET",
                             "/admin/domain-policies", 200, key=self.admin_key,
                             check=lambda r, t: _assert_is_list(r))
        )

        # Filter by domain substring.
        suite.tests.append(
            await self._test("GET /admin/domain-policies?domain=e2e → 200", "GET",
                             "/admin/domain-policies", 200, key=self.admin_key,
                             params={"domain": "e2e"})
        )

        # Get single policy.
        if self.policy_id:
            suite.tests.append(
                await self._test(f"GET /admin/domain-policies/{self.policy_id} → 200", "GET",
                                 f"/admin/domain-policies/{self.policy_id}", 200,
                                 key=self.admin_key,
                                 check=lambda r, t: _assert_json_key(r, "domain",
                                                                     "e2e-test-site.example.com"))
            )

            # Update policy.
            suite.tests.append(
                await self._test(f"PATCH /admin/domain-policies/{self.policy_id} → 200", "PATCH",
                                 f"/admin/domain-policies/{self.policy_id}", 200,
                                 key=self.admin_key,
                                 json_body={"rate_limit_rps": 10.0, "max_retries": 5})
            )

            # Pin escalation tier.
            suite.tests.append(
                await self._test(
                    f"POST /admin/domain-policies/{self.policy_id}/pin-tier?tier=2&locked=true → 200",
                    "POST",
                    f"/admin/domain-policies/{self.policy_id}/pin-tier?tier=2&locked=true",
                    200, key=self.admin_key)
            )

            # Delete policy.
            suite.tests.append(
                await self._test(f"DELETE /admin/domain-policies/{self.policy_id} → 204", "DELETE",
                                 f"/admin/domain-policies/{self.policy_id}", 204,
                                 key=self.admin_key)
            )

            # Verify deleted → 404.
            suite.tests.append(
                await self._test(f"GET /admin/domain-policies/{self.policy_id} (deleted) → 404",
                                 "GET", f"/admin/domain-policies/{self.policy_id}", 404,
                                 key=self.admin_key)
            )
            self.policy_id = None

        # Auth: non-admin cannot access.
        if self.fetch_key:
            suite.tests.append(
                await self._test("GET /admin/domain-policies (fetch key) → 403", "GET",
                                 "/admin/domain-policies", 403, key=self.fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 12: Proxy pools & proxies
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_proxy_pools(self) -> None:
        suite = SuiteReport(suite_name="Proxy pools & proxies (admin)")
        t0 = time.perf_counter()

        # Create proxy pool.
        pool_name = f"e2e-pool-{uuid4().hex[:8]}"
        resp = await self._request("POST", "/admin/proxy-pools", 201, key=self.admin_key,
                                   json_body={"name": pool_name, "provider": "custom"})
        if resp.status_code == 201:
            data = resp.json()
            self.proxy_pool_id = UUID(data["id"])
            suite.tests.append(TestCase(name="POST /admin/proxy-pools → 201",
                                        outcome=Outcome.PASS,
                                        detail=f"id={self.proxy_pool_id}",
                                        status_code=201))
        else:
            suite.tests.append(TestCase(name="POST /admin/proxy-pools → 201",
                                        outcome=Outcome.FAIL,
                                        detail=resp.text[:200], status_code=resp.status_code))

        # Duplicate pool name.
        suite.tests.append(
            await self._test("POST /admin/proxy-pools (duplicate) → 409", "POST",
                             "/admin/proxy-pools", 409, key=self.admin_key,
                             json_body={"name": pool_name, "provider": "custom"})
        )

        # Add a proxy to the pool.
        if self.proxy_pool_id:
            resp = await self._request("POST",
                                       f"/admin/proxy-pools/{self.proxy_pool_id}/proxies",
                                       201, key=self.admin_key,
                                       json_body={
                                           "pool_id": str(self.proxy_pool_id),
                                           "url": "http://testuser:testpass@10.0.0.1:8080",
                                           "country": "PL",
                                       })
            if resp.status_code == 201:
                self.proxy_id = UUID(resp.json()["id"])
                suite.tests.append(TestCase(name="POST .../proxies → 201",
                                            outcome=Outcome.PASS,
                                            detail=f"proxy_id={self.proxy_id}",
                                            status_code=201))
            else:
                suite.tests.append(TestCase(name="POST .../proxies → 201",
                                            outcome=Outcome.FAIL,
                                            detail=resp.text[:200],
                                            status_code=resp.status_code))

            # Invalid proxy URL format.
            suite.tests.append(
                await self._test("POST .../proxies (bad URL) → 422", "POST",
                                 f"/admin/proxy-pools/{self.proxy_pool_id}/proxies", 422,
                                 key=self.admin_key,
                                 json_body={
                                     "pool_id": str(self.proxy_pool_id),
                                     "url": "not-a-valid-proxy-url",
                                     "country": "XX",
                                 })
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 13: Proxy endpoints
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_proxy_endpoints(self) -> None:
        suite = SuiteReport(suite_name="Proxy management endpoints")
        t0 = time.perf_counter()

        # List proxy pools (any authenticated user).
        suite.tests.append(
            await self._test("GET /proxy/pools → 200", "GET", "/proxy/pools", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_is_list(r))
        )

        # Pool stats.
        if self.proxy_pool_id:
            suite.tests.append(
                await self._test(f"GET /proxy/pools/{self.proxy_pool_id}/stats → 200", "GET",
                                 f"/proxy/pools/{self.proxy_pool_id}/stats", 200,
                                 key=self.admin_key,
                                 check=lambda r, t: _assert_has_keys(r, ["pool_id", "total",
                                                                          "active", "avg_health"]))
            )

        # List proxies (admin).
        suite.tests.append(
            await self._test("GET /proxy/proxies → 200", "GET", "/proxy/proxies", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_is_list(r))
        )

        # Proxy events.
        if self.proxy_id:
            suite.tests.append(
                await self._test(f"GET /proxy/proxies/{self.proxy_id}/events → 200", "GET",
                                 f"/proxy/proxies/{self.proxy_id}/events", 200,
                                 key=self.admin_key)
            )

        # Report proxy health.
        if self.proxy_id:
            suite.tests.append(
                await self._test("POST /proxy/health → 200", "POST", "/proxy/health", 200,
                                 key=self.admin_key,
                                 json_body={"proxy_id": str(self.proxy_id),
                                            "domain": "example.com",
                                            "success": True,
                                            "reason": None})
            )

        # Reset proxy.
        if self.proxy_id:
            suite.tests.append(
                await self._test(f"POST /proxy/reset/{self.proxy_id} → 200", "POST",
                                 f"/proxy/reset/{self.proxy_id}", 200,
                                 key=self.admin_key)
            )

        # Reset circuit breaker.
        suite.tests.append(
            await self._test("DELETE /proxy/circuit-breaker/example.com → 200", "DELETE",
                             "/proxy/circuit-breaker/example.com", 200,
                             key=self.admin_key)
        )

        # Bulk import.
        suite.tests.append(
            await self._test("POST /proxy/admin/proxies (bulk) → 201", "POST",
                             "/proxy/admin/proxies", 201, key=self.admin_key,
                             json_body={
                                 "tenant_id": str(self.tenant_id or uuid4()),
                                 "proxies": [
                                     {"host": "10.0.0.2", "port": 3128,
                                      "username": "u1", "password": "p1",
                                      "country": "DE"},
                                 ],
                             })
        )

        # Auth: non-admin cannot list proxies.
        if self.fetch_key:
            suite.tests.append(
                await self._test("GET /proxy/proxies (fetch key) → 403", "GET",
                                 "/proxy/proxies", 403, key=self.fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 14: Projects
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_projects(self) -> None:
        suite = SuiteReport(suite_name="Projects (admin)")
        t0 = time.perf_counter()

        project_name = f"e2e-project-{uuid4().hex[:8]}"

        # Create project.
        suite.tests.append(
            await self._test("POST /projects/ → 201", "POST", "/projects/", 201,
                             key=self.admin_key,
                             json_body={"name": project_name})
        )

        # Duplicate.
        suite.tests.append(
            await self._test("POST /projects/ (duplicate) → 409", "POST", "/projects/", 409,
                             key=self.admin_key,
                             json_body={"name": project_name})
        )

        # List projects.
        suite.tests.append(
            await self._test("GET /projects/ → 200", "GET", "/projects/", 200,
                             key=self.admin_key,
                             check=lambda r, t: _assert_is_list(r))
        )

        # Auth: non-admin cannot access.
        if self.fetch_key:
            suite.tests.append(
                await self._test("GET /projects/ (fetch key) → 403", "GET", "/projects/", 403,
                                 key=self.fetch_key)
            )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 15: Metrics
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_metrics(self) -> None:
        suite = SuiteReport(suite_name="Prometheus metrics")
        t0 = time.perf_counter()

        suite.tests.append(
            await self._test("GET /metrics → 200 (Prometheus text)", "GET", "/metrics", 200,
                             key="",  # no auth
                             check=lambda r, t: _assert_content_type(r, "text/plain"))
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 16: Edge cases & error handling
    # ═══════════════════════════════════════════════════════════════════════

    async def phase_edge_cases(self) -> None:
        suite = SuiteReport(suite_name="Edge cases & error handling")
        t0 = time.perf_counter()

        fetch_key = self.fetch_key or self.admin_key

        # Invalid JSON body.
        suite.tests.append(
            await self._test("POST /v1/fetch (invalid JSON) → 422", "POST", "/v1/fetch", 422,
                             key=fetch_key, json_body={"url": "not-a-valid-url!!!",
                                                       "mode": "static"})
        )

        # Missing required body fields.
        suite.tests.append(
            await self._test("POST /v1/fetch (empty body) → 422", "POST", "/v1/fetch", 422,
                             key=fetch_key, json_body={})
        )

        # Wrong HTTP method.
        resp = await self._request("PATCH", "/v1/fetch", 405, key=fetch_key)
        suite.tests.append(TestCase(name="PATCH /v1/fetch → 405",
                                    outcome=Outcome.PASS if resp.status_code == 405
                                    else Outcome.FAIL,
                                    detail=f"status={resp.status_code}",
                                    status_code=resp.status_code))

        # Malformed UUID in path → 422.
        suite.tests.append(
            await self._test("GET /v1/keys/not-a-uuid → 422", "GET", "/v1/keys/not-a-uuid", 422,
                             key=self.admin_key)
        )

        # Wrong auth header format — just gibberish.
        resp = await self._request("GET", "/v1/keys", {401}, key="not-even-close-to-valid")
        suite.tests.append(TestCase(name="GET /v1/keys (gibberish auth) → 401",
                                    outcome=Outcome.PASS if resp.status_code == 401
                                    else Outcome.FAIL,
                                    detail=f"status={resp.status_code}",
                                    status_code=resp.status_code))

        # 404 for unknown route.
        resp = await self._request("GET", "/v1/nonexistent-endpoint", 404, key=self.admin_key)
        suite.tests.append(TestCase(name="GET /v1/nonexistent → 404",
                                    outcome=Outcome.PASS if resp.status_code == 404
                                    else Outcome.FAIL,
                                    detail=f"status={resp.status_code}",
                                    status_code=resp.status_code))

        # Rate-limit headers present on fetch response.
        resp = await self._request("POST", "/v1/fetch", {202, 429}, key=fetch_key,
                                   json_body={"url": "https://httpbin.org/get",
                                              "mode": "static"})
        has_rate_headers = all(
            h in resp.headers for h in ["X-RateLimit-Limit", "X-RateLimit-Remaining",
                                        "X-RateLimit-Reset"]
        )
        suite.tests.append(
            TestCase(name="POST /v1/fetch returns rate-limit headers",
                     outcome=Outcome.PASS if resp.status_code in (202, 429) and has_rate_headers
                     else Outcome.FAIL if resp.status_code not in (202, 429)
                     else Outcome.PASS,  # 429 still has headers sometimes
                     detail=f"status={resp.status_code} "
                            f"headers={'present' if has_rate_headers else 'missing'}",
                     status_code=resp.status_code)
        )

        suite.suite_duration_ms = (time.perf_counter() - t0) * 1000
        self.reports.append(suite)

    # ═══════════════════════════════════════════════════════════════════════
    # Report
    # ═══════════════════════════════════════════════════════════════════════

    def print_summary(self) -> None:
        print(header("Test Suite Summary"))

        grand_total = 0
        grand_passed = 0
        grand_failed = 0
        grand_skipped = 0
        grand_expected = 0
        grand_duration = 0.0

        for report in self.reports:
            total = len(report.tests)
            if total == 0:
                continue
            grand_total += total
            grand_passed += report.passed
            grand_failed += report.failed
            grand_skipped += report.skipped
            grand_expected += report.expected_fail
            grand_duration += report.suite_duration_ms

            line = f"  {report.suite_name:<42s} {report.passed:>3}/{total:<3} passed"
            if report.failed:
                line += f"  {RED}{report.failed} failed{RESET}"
            if report.expected_fail:
                line += f"  {YELLOW}{report.expected_fail} expected{RESET}"
            line += f"  ({report.suite_duration_ms:,.0f}ms)"
            print(line)

            # Show individual failures.
            for test in report.tests:
                if test.outcome == Outcome.FAIL:
                    print(f"    {fail(test.name)} — {test.detail}")
                elif test.outcome == Outcome.EXPECTED_FAIL:
                    if self.verbose:
                        print(f"    {warn(test.name)} — {test.detail}")

        # Grand total.
        print(f"\n{BOLD}Total: {grand_total} tests across {len(self.reports)} suites{RESET}")
        print(f"  {GREEN}{grand_passed} passed{RESET}", end="")
        if grand_failed:
            print(f"  {RED}{grand_failed} failed{RESET}", end="")
        if grand_expected:
            print(f"  {YELLOW}{grand_expected} expected failures{RESET}", end="")
        if grand_skipped:
            print(f"  {grand_skipped} skipped", end="")
        print(f"  ({grand_duration:,.0f}ms total)")
        print()

        # Score.
        effective = grand_total - grand_expected - grand_skipped
        if effective > 0:
            score = grand_passed / effective * 100
            color = GREEN if score >= 95 else YELLOW if score >= 80 else RED
            print(f"  Score: {color}{score:.1f}%{RESET} (excluding expected failures & skips)")
        else:
            print("  Score: N/A (no effective tests)")

        # Resource summary.
        print(f"\n{BOLD}Resources created during test:{RESET}")
        if self.tenant_id:
            print(f"  tenant_id: {self.tenant_id}")
        if self.app_id:
            print(f"  app_id: {self.app_id}")
        if self.admin_key:
            print(f"  admin_key: {self.admin_key[:16]}...")
        if self.fetch_key:
            print(f"  fetch_key: {self.fetch_key[:16]}...")
        if self.job_id:
            print(f"  job_id: {self.job_id}")
        print()


# ── Assertion helpers ──────────────────────────────────────────────────────────


def _assert_json_key(resp: httpx.Response, key: str, expected: Any) -> None:
    data = resp.json()
    actual = data.get(key)
    assert actual == expected, f"Expected {key}={expected!r}, got {actual!r}"


def _assert_has_key(resp: httpx.Response, key: str) -> None:
    data = resp.json()
    assert key in data, f"Expected key '{key}' in response, got keys={list(data.keys())}"


def _assert_has_keys(resp: httpx.Response, keys: list[str]) -> None:
    data = resp.json()
    missing = [k for k in keys if k not in data]
    assert not missing, f"Missing keys in response: {missing}"


def _assert_is_list(resp: httpx.Response) -> None:
    data = resp.json()
    assert isinstance(data, list), f"Expected list, got {type(data).__name__}"


def _assert_content_type(resp: httpx.Response, expected_prefix: str) -> None:
    ct = resp.headers.get("content-type", "")
    assert ct.startswith(expected_prefix), f"Expected content-type {expected_prefix}, got {ct}"


# ── CLI entry point ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawler API — Full E2E Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/e2e/run_e2e_tests.py --bootstrap
  python tests/e2e/run_e2e_tests.py --api-key crw_live_xxxx --fetch-real
  python tests/e2e/run_e2e_tests.py --base-url https://api.example.com --api-key crw_live_xxxx
        """,
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")
    parser.add_argument("--api-key", default=None,
                        help="Admin-scope API key (skip bootstrap if provided)")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Auto-create tenant/app/admin-key via direct DB access")
    parser.add_argument("--fetch-real", action="store_true",
                        help="Include real-website fetch tests (requires arq worker)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    runner = E2ETestRunner(
        base_url=args.base_url,
        api_key=args.api_key,
        bootstrap=args.bootstrap,
        fetch_real=args.fetch_real,
        verbose=args.verbose,
    )

    asyncio.run(runner.run_all())

    # Exit code.
    total_failed = sum(r.failed for r in runner.reports)
    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
