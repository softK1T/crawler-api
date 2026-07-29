# ADR-016 Operator-Issued Application and API Key Management

## Status
Accepted

## Context

The crawler API serves multiple scraping pipelines (cee-price-intel, future projects)
under a single deployment. Each pipeline needs its own API key with a bounded
set of scopes, issued by a trusted operator rather than via self-service signup.
This ADR defines the operator-facing key-management surface — creation, rotation,
revocation, and listing — building on the auth dependency chain established in
ADR-004. The model is closed: only an operator holding `keys` (and where required
`admin`) scope can mint credentials; there is no public registration flow.

## Inventory

### Command 1: `grep -rn "@router" app/api/v1/endpoints/ | grep -iE "key|application"`

```
app/api/v1/endpoints/auth_keys.py:47:@router.post("/v1/keys", response_model=ApiKeyCreateResponse, status_code=201)
app/api/v1/endpoints/auth_keys.py:100:@router.get("/v1/keys", response_model=list[ApiKeyResponse])
app/api/v1/endpoints/auth_keys.py:115:@router.delete("/v1/keys/{key_id}", response_model=ApiKeyResponse)
app/api/v1/endpoints/auth_keys.py:159:@router.post("/v1/tenants", response_model=TenantResponse, status_code=201)
app/api/v1/endpoints/auth_keys.py:177:@router.post("/v1/applications", response_model=ApplicationResponse, status_code=201)
app/api/v1/endpoints/usage.py:30:@router.get("/applications/{application_id}", response_model=UsageSummaryResponse)
```

### Command 2: `grep -rn "SCOPE_KEYS\|SCOPE_ADMIN\|ALL_SCOPES" app/`

```
app/api/v1/endpoints/auth_keys.py:12:    ALL_SCOPES,
app/api/v1/endpoints/auth_keys.py:13:    SCOPE_ADMIN,
app/api/v1/endpoints/auth_keys.py:14:    SCOPE_KEYS,
app/api/v1/endpoints/auth_keys.py:50:    api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
app/api/v1/endpoints/auth_keys.py:59:        if scope not in ALL_SCOPES:
app/api/v1/endpoints/auth_keys.py:119:    api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
app/api/v1/endpoints/auth_keys.py:162:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/auth_keys.py:180:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/proxy.py:10:from app.api.v1.dependencies import SCOPE_ADMIN, require_scope, resolve_api_key
app/api/v1/endpoints/proxy.py:58:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/proxy.py:77:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/proxy.py:97:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/proxy.py:116:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/dependencies.py:28:SCOPE_ADMIN = "admin"
app/api/v1/dependencies.py:29:SCOPE_KEYS = "keys"
app/api/v1/dependencies.py:31:ALL_SCOPES = frozenset({SCOPE_FETCH, SCOPE_ARCHIVE, SCOPE_ADMIN, SCOPE_KEYS})
app/api/v1/dependencies.py:92:            api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
app/api/v1/dependencies.py:96:    if scope not in ALL_SCOPES:
app/api/v1/endpoints/admin.py:9:from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
app/api/v1/endpoints/admin.py:34:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:54:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:71:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:84:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:105:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:121:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/admin.py:139:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/usage.py:10:from app.api.v1.dependencies import SCOPE_ADMIN, require_scope, resolve_api_key
app/api/v1/endpoints/usage.py:33:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/projects.py:7:from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
app/api/v1/endpoints/projects.py:19:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
app/api/v1/endpoints/projects.py:38:    _api_key: ApiKey = Depends(require_scope(SCOPE_ADMIN)),
```

### Command 3: `grep -rn "class ApiKey\|class Application" app/models/ -A 25`

```
app/models/api_key.py:15:class ApiKey(Base):
app/models/api_key.py-16-    __tablename__ = "api_keys"
app/models/api_key.py-17-    __table_args__ = (
app/models/api_key.py-18-        Index("ix_api_keys_prefix", "prefix"),
app/models/api_key.py-19-        Index("ix_api_keys_application_id", "application_id"),
app/models/api_key.py-20-        Index("ix_api_keys_is_active_expires_at", "is_active", "expires_at"),
app/models/api_key.py-21-    )
app/models/api_key.py-22-
app/models/api_key.py-23-    id: Mapped[uuid.UUID] = mapped_column(
app/models/api_key.py-24-        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
app/models/api_key.py-25-    )
app/models/api_key.py-26-    application_id: Mapped[uuid.UUID] = mapped_column(
app/models/api_key.py-27-        UUID(as_uuid=True),
app/models/api_key.py-28-        ForeignKey("applications.id", ondelete="CASCADE"),
app/models/api_key.py-29-        nullable=False,
app/models/api_key.py-30-    )
app/models/api_key.py-31-    prefix: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
app/models/api_key.py-32-    hashed_key: Mapped[str] = mapped_column(Text, nullable=False)
app/models/api_key.py-33-    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
app/models/api_key.py-34-    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # "live" or "test"
app/models/api_key.py-35-    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
app/models/api_key.py-36-    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
app/models/api_key.py-37-    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
app/models/api_key.py-38-    created_at: Mapped[datetime] = mapped_column(
app/models/api_key.py-39-        DateTime(timezone=True), nullable=False, server_default=func.now()
app/models/api_key.py-40-    )
app/models/application.py:15:class Application(Base):
app/models/application.py-16-    __tablename__ = "applications"
app/models/application.py-17-    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_application_tenant_name"),)
app/models/application.py-18-
app/models/application.py-19-    id: Mapped[uuid.UUID] = mapped_column(
app/models/application.py-20-        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
app/models/application.py-21-    )
app/models/application.py-22-    tenant_id: Mapped[uuid.UUID] = mapped_column(
app/models/application.py-23-        UUID(as_uuid=True),
app/models/application.py-24-        ForeignKey("tenants.id", ondelete="CASCADE"),
app/models/application.py-25-        nullable=False,
app/models/application.py-26-        index=True,
app/models/application.py-27-    )
app/models/application.py-28-    name: Mapped[str] = mapped_column(String(255), nullable=False)
app/models/application.py-29-    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
app/models/application.py-30-    created_at: Mapped[datetime] = mapped_column(
app/models/application.py-31-        DateTime(timezone=True), nullable=False, server_default=func.now()
app/models/application.py-32-    )
app/models/application.py-33-
app/models/application.py-34-    # Back-populates ApiKey.application; lazy="raise" prevents N+1 queries.
app/models/application.py-35-    api_keys: Mapped[list["ApiKey"]] = relationship(
app/models/application.py-36-        "ApiKey", back_populates="application", lazy="raise"
app/models/application.py-37-    )
app/models/application.py-38-
app/models/application.py-39-    def __repr__(self) -> str:
app/models/application.py-40-        return f"<Application id={self.id} name={self.name!r}>"
```

### Command 4: `grep -rn "generate_api_key\|hash_api_key\|verify_api_key" app/`

```
app/core/security.py:27:def hash_api_key(raw_key: str) -> str:
app/core/security.py:32:def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
app/core/security.py:46:def generate_api_key(mode: str = "live") -> tuple[str, str]:
app/core/security.py:55:    hashed_key = hash_api_key(raw_key)
app/core/security.py:67:        hashed = hash_api_key(raw_key)
app/core/security.py:104:        if verify_api_key_hash(api_key, stored_hash):
app/core/security.py:114:verify_api_key = get_api_key
app/api/v1/dependencies.py:20:from app.core.security import update_last_used, verify_api_key_hash
app/api/v1/dependencies.py:65:    if not verify_api_key_hash(x_api_key, row.hashed_key):
app/api/v1/endpoints/auth_keys.py:24:from app.core.security import generate_api_key
app/api/v1/endpoints/auth_keys.py:70:        raw_key, hashed_key = generate_api_key()
```

### Command 5: `cat scripts/bootstrap_dev.py`

```python
#!/usr/bin/env python3
"""Bootstrap script — creates a tenant, application, and admin API key for local dev.

Usage: python scripts/bootstrap_dev.py
Prints the raw API key to stdout — capture it and use as X-API-Key.
Idempotent: skips tenant/app creation if they already exist.
"""

import asyncio
import os
import sys

# Ensure the project root is on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _bootstrap() -> str:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant

    engine = create_async_engine(str(settings.database_url), echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        # Tenant — idempotent.
        result = await db.execute(select(Tenant).where(Tenant.name == "dev-tenant"))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="dev-tenant")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        # Application — idempotent.
        result = await db.execute(
            select(Application).where(
                Application.tenant_id == tenant.id, Application.name == "dev-app"
            )
        )
        app = result.scalar_one_or_none()
        if app is None:
            app = Application(tenant_id=tenant.id, name="dev-app")
            db.add(app)
            await db.commit()
            await db.refresh(app)

        # API key with admin + fetch scopes — always create a new one.
        raw_key, hashed_key = generate_api_key(mode="live")
        api_key = ApiKey(
            application_id=app.id,
            prefix=raw_key[:8],
            hashed_key=hashed_key,
            scopes=["fetch", "archive", "admin", "keys"],
            mode="live",
        )
        db.add(api_key)
        await db.commit()

        print(raw_key)
        return raw_key

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_bootstrap())
```

### Command 6: `alembic heads`

```
a7c55bf575f3 (head)
```

### Inventory Answers

**ENDPOINTS:** The file `auth_keys.py` contains five routes:
- `POST /v1/keys` — creates an API key; requires `keys` scope; **exists**.
- `GET /v1/keys` — lists keys for the caller's application; any authenticated key (no scope guard, only `resolve_api_key`); **exists**.
- `DELETE /v1/keys/{key_id}` — revokes a key; requires `keys` scope; **exists**.
- `POST /v1/tenants` — creates a tenant; requires `admin` scope; **exists**.
- `POST /v1/applications` — creates an application; requires `admin` scope; **exists**.
- `GET /applications/{application_id}` (in `usage.py`) — returns usage summary; requires `admin` scope; **exists** but is a usage endpoint, not part of the key-management surface.

**MISSING:** The following routes required by this feature do NOT yet exist:
- `POST /v1/keys/{key_id}/rotate`
- `GET /v1/applications`
- `GET /v1/applications/{app_id}`
- `PATCH /v1/applications/{app_id}`
- `GET /v1/tenants`
- `GET /v1/tenants/{tenant_id}`

**APIKEY COLUMNS:** `id` (UUID PK), `application_id` (UUID FK), `prefix` (String 8, unique), `hashed_key` (Text), `scopes` (ARRAY Text), `mode` (String 8), `is_active` (Boolean), `last_used_at` (DateTime tz, nullable), `expires_at` (DateTime tz, nullable), `created_at` (DateTime tz, server_default now()), `revoked_at` (DateTime tz, nullable). An `issuer_key_id` column does **not** exist.

**APPLICATION COLUMNS:** `id` (UUID PK), `tenant_id` (UUID FK), `name` (String 255), `is_active` (Boolean), `created_at` (DateTime tz, server_default now()). `owner_label` (text, nullable) does **not** exist. `monthly_quota` (integer, nullable) does **not** exist.

**BOOTSTRAP:** `scripts/bootstrap_dev.py` inserts rows directly via the SQLAlchemy ORM, bypassing any service or endpoint layer.

**MIGRATION HEAD:** `a7c55bf575f3 (head)` — matches the expected current head.

## Design Decisions

### D1. Operator-Only Onboarding

We will enforce a closed operator-onboarding model: every tenant, application, and API key is created by an authenticated operator holding `keys` (and where required `admin`) scope. No unauthenticated or self-service credential-minting endpoint will exist.

This rules out: `POST /v1/register`, email verification, invite codes, approval workflows, captcha, and any Account/Owner layer above Application. The trigger for revisiting this decision is: **first external paying customer requiring self-service signup**.

### D2. Privilege Escalation

We will enforce two constraints on scope grants when `POST /v1/keys` is called by a `keys`-scoped caller:

- **Constraint A (scope subset):** The caller may only grant scopes it already holds — `requested_scopes ⊆ caller.scopes`. This prevents a `keys`-only caller from minting an `admin` key.
- **Constraint B (keys-grant gate):** If `keys` is in the requested scopes, the caller must also hold `admin`. This ensures that the ability to delegate key-management authority itself requires elevated privilege.

The specific enforcement code in the endpoint will be:

```
if not set(body.scopes).issubset(set(api_key.scopes)):
    raise AuthorizationError("Cannot grant scopes you do not hold")
if "keys" in body.scopes and "admin" not in api_key.scopes:
    raise AuthorizationError("admin scope required to grant keys scope")
```

Test name: `test_keys_only_caller_cannot_mint_admin_key`.

### D3. Cross-Tenant Issuance

We will confine a caller holding only `keys` to its own application: `application_id == caller.application_id`. A caller holding `admin` (in addition to `keys`) may target any `application_id`.

Every cross-application issuance (where `caller.application_id != body.application_id`) must be logged at INFO level with fields: `issuer_key_id=<uuid>`, `target_application_id=<uuid>`, `scopes=<list>`. The raw key must never appear in this log line.

The current redaction regex in `logging_config.py` is `\b(crw_(?:live|test)_[A-Za-z0-9]{8})_[A-Za-z0-9_-]+`, which matches the old format `crw_live_XXXXXXXX_...` / `crw_test_XXXXXXXX_...`. The actual `generate_api_key()` output format is `crwl<4-random><28-random>` (live) and `crwt<4-random><28-random>` (test) — these are **not** covered by the existing regex. The regex must be updated in Stage B (see D10).

### D4. Rotation and the Lost-Key Story

We will implement `POST /v1/keys/{key_id}/rotate` as follows:

- **Required scope:** `keys`, with the same application-ID confinement rule as D3.
- **Behaviour:** Creates a new `ApiKey` row with the same `application_id`, `scopes`, and `mode` as the original. Sets the old key's `expires_at` to `now() + KEY_ROTATION_OVERLAP_HOURS` (default 24 hours). The old key is **not** revoked and stays `is_active=True` — it is valid during the overlap window. Returns the new raw key exactly once via `ApiKeyCreateResponse`.
- **Configurable overlap:** `KEY_ROTATION_OVERLAP_HOURS` in settings, default `24`. Must be added to `.env.example`.
- **Revoked keys:** A key with `revoked_at IS NOT NULL` cannot be rotated — return `409 Conflict`.
- **Re-rotation:** A key that was already rotated (has `expires_at` set by a prior rotation) may be rotated again; the new `expires_at` overwrites the old one.

Test names:
- `test_rotate_returns_new_raw_key_once`
- `test_rotated_old_key_valid_during_overlap`
- `test_rotated_old_key_rejected_after_overlap_forced_to_expire`
- `test_rotate_revoked_key_returns_409`

### D5. Listing — hashed_key Exclusion

We will ensure `GET /v1/keys` returns exactly these fields and no others: `id`, `prefix`, `scopes`, `mode`, `created_at`, `last_used_at`, `expires_at`, `revoked_at`, `application_id`. The `hashed_key` field must be absent.

This will be enforced in the Pydantic response schema — either by not declaring `hashed_key` on the schema, or by explicitly excluding it via `model_config` (e.g., `Fields(exclude={"hashed_key"})`). No runtime filter will be applied at the endpoint level.

Test name: `test_list_keys_response_excludes_hashed_key`. The test asserts: for each item in the response list, `"hashed_key" not in item`; the test first asserts the response list is non-empty so a zero-result response cannot be mistaken for evidence.

**Disposition of `is_active` (Stage B, B0):** `is_active` is dropped from the response. It is derivable from `revoked_at IS NOT NULL` (a revoked key is inactive) and `expires_at` (an expired key is rejected by `resolve_api_key` but `is_active` stays `TRUE` internally). No consumer — frontend, test, or integration — reads `is_active` from the API key response. The column remains on the model for query filtering in `resolve_api_key` (which uses `is_active.is_(True)`).

### D6. Test vs Live Mode

We will treat `mode` as a label only at this time. Keys with `mode="test"` (prefix `crwt`) carry no behavioural difference from `mode="live"`: they use the same proxy pool, incur the same billing, and execute fetches identically. The label exists for future use.

The trigger for giving mode operational meaning is: **first partner requiring a sandbox environment isolated from the live proxy pool and billing**.

### D7. Quota Enforcement

We will defer quota enforcement to a future stage. The `usage_counter` metric already meters requests, bytes, and cost per `(application_id, period_month)`, but nothing enforces a ceiling. No `monthly_quota` column will be added to `Application` in this stage; the inventory confirms it does not exist today.

The trigger for implementing quota enforcement is: **first partner contract that specifies a hard request or cost ceiling per billing period**.

### D8. Migration — Is One Needed?

We will create a new Alembic revision, chained from `a7c55bf575f3`, that adds two columns:

- `issuer_key_id` (UUID, nullable) to `api_keys`. This column records which operator key issued the row. It is nullable (no FK constraint) so existing rows and bootstrap-created keys set `issuer_key_id = NULL`, and to avoid a circular FK dependency (`api_keys` → `api_keys`). Operator-issued keys set it to the issuing key's UUID.
- `owner_label` (VARCHAR 255, nullable) to `applications`. This gives the operator a human-readable label for the key's owner (partner name) at issuance time. Without it, the admin must cross-reference tenant records to identify a key's owner.

The revision ID format will be `0002_add_issuer_key_id_and_owner_label`, chained from `a7c55bf575f3`. The migration will be reviewed in isolation in Stage B before any endpoint code is written.

**Disposition (Stage B):** The shipped revision is `"0002"` (short form) in the `revision` constant, matching the project convention established by migration `0001` which also uses a short revision string despite the file being named `0001_initial_schema.py`. The file is named `0002_add_issuer_key_id_and_owner_label.py` as specified; only the `revision` constant uses the short form for consistency.

### D9. Bootstrap Convergence

We will rewrite `scripts/bootstrap_dev.py` in Stage B so that API key creation calls `app.services.key_service.create_api_key()` (the same function `POST /v1/keys` will call), eliminating the dual key-minting path. Tenant and application creation may continue to use direct ORM inserts, as those entities have no equivalent HTTP endpoints invoked from bootstrap — only the key-minting path must converge to a single code path.

Test name: `test_bootstrap_creates_key_via_service_layer`. The test mocks `create_api_key` and asserts it is called with the expected arguments, not that raw ORM inserts occur.

### D10. Key Redaction in Logs

We will update the redaction regex in `app/core/logging_config.py` to cover the actual key format produced by `generate_api_key()`.

The current regex is:

```
_API_KEY = re.compile(r"\b(crw_(?:live|test)_[A-Za-z0-9]{8})_[A-Za-z0-9_-]+")
```

This matches the old `crw_live_XXXXXXXX_...` / `crw_test_XXXXXXXX_...` format. The actual format is:

- Live: `crwl<4-random><28-random>` (e.g., `crwlAb3dxYz9pQ2R...`)
- Test: `crwt<4-random><28-random>` (e.g., `crwtXy7KqmN4vF8s...`)

The regex must be updated to match `crw[lt][A-Za-z0-9]{4}[A-Za-z0-9_-]{28}` and redact everything after the first 8 characters (the prefix). The replacement pattern must preserve the prefix (which is non-sensitive — it is returned in API responses) and redact the remainder.

Test name: `test_log_redaction_covers_generated_key_format`. The test: generates a key with `generate_api_key()`; constructs a log record containing the raw key; passes it through `redact()`; asserts the full raw key substring is absent from the redacted output; asserts the redacted output is non-empty; asserts the 8-character prefix is still present (the prefix is public).

## Consequences

1. Stage B must create a new Alembic migration adding `issuer_key_id` to `api_keys` and `owner_label` to `applications`, chained from `a7c55bf575f3`.
2. Stage B must implement `POST /v1/keys/{key_id}/rotate` with the overlap-window semantics defined in D4.
3. Stage B must add `GET /v1/applications`, `GET /v1/applications/{app_id}`, `PATCH /v1/applications/{app_id}`, `GET /v1/tenants`, and `GET /v1/tenants/{tenant_id}` endpoints.
4. Stage B must enforce privilege-escalation constraints in `POST /v1/keys` per D2.
5. Stage B must enforce cross-tenant issuance rules and logging per D3.
6. Stage B must update the `GET /v1/keys` response schema to include `application_id` and `revoked_at` and exclude `hashed_key` per D5.
7. Stage B must update the key redaction regex in `logging_config.py` per D10.
8. Stage B must rewrite `scripts/bootstrap_dev.py` to call the service layer for key creation per D9.
9. Stage B must add the `KEY_ROTATION_OVERLAP_HOURS` setting to config and `.env.example`.
10. Stage B must write all named tests from D2, D4, D5, D9, and D10.
11. Stage B verify.sh uses `sleep 2` between fetch steps with different URLs to avoid domain-level rate limiting (default 1 RPS per domain). Trigger for revisiting: when the default domain policy is relaxed or verify.sh uses a dedicated test domain with a higher or disabled rate limit.
12. The raw-key leak check uses `grep -Eq 'crw[lt][A-Za-z0-9_-]{30,}'` — matching only full key bodies (~38+ chars), not the 8-char prefix. The prefix (`raw_key[:8]`) is a cleartext column returned in API responses and is not a secret.
13. Endpoint-layer authorization checks (D2 escalation, D3 tenancy, 404-on-cross-tenant-rotate) are currently verified only by `verify.sh`, not by HTTP-level tests. The `test_post_keys_response_boundary` async client fixture (`ASGITransport` + `app.dependency_overrides[get_db]` sharing the test `db_session`) is the mechanism to close this gap. The shared session means HTTP-level tests verify routing, authorization, and response serialization but not per-request transaction boundaries (the same session is reused across setup and the HTTP request). Trigger for adding HTTP-level auth tests: any change to authorization logic in `auth_keys.py`.

## Deferred

| Item | Trigger | Owner |
|---|---|---|
| Self-service signup (registration, email verification, invite codes) | First external paying customer requiring self-service signup | Product |
| Mode semantics (sandbox proxy pool, billing isolation, fetch suppression) | First partner requiring a sandbox environment isolated from live proxy pool and billing | Platform |
| Quota enforcement (monthly_quota column and ceiling checks) | First partner contract specifying a hard request or cost ceiling per billing period | Platform |
| `issuer_key_id` FK constraint on `api_keys.id` | When the key lifecycle audit trail requires referential integrity at the DB level, not just structured logging | Platform |
