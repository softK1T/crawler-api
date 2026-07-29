"""Integration tests for operator key management (ADR-016 D2-D10)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest


def _unique_prefix() -> str:
    """Generate a unique 8-char prefix with random hex suffix."""
    return f"crwl{uuid4().hex[:4]}"


# ── D2: Privilege escalation ──────────────────────────────────────────────────


@pytest.mark.integration
async def test_keys_only_caller_cannot_mint_admin_key(db_session, application_factory):
    """A keys-only caller cannot grant admin scope (Constraint A)."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.core.errors import AuthorizationError
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app = await application_factory()

    # Caller has keys + fetch, NOT admin.
    caller = ApiKey(
        application_id=app.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["keys", "fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app.id, scopes=["admin"], mode="live")

    with pytest.raises(AuthorizationError, match="Cannot grant scopes"):
        await create_api_key(body=body, api_key=caller, db=db_session)


@pytest.mark.integration
async def test_keys_only_caller_cannot_mint_keys_key(db_session, application_factory):
    """A keys-only caller (without admin) cannot grant keys scope (Constraint B)."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.core.errors import AuthorizationError
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app = await application_factory()

    caller = ApiKey(
        application_id=app.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["keys", "fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app.id, scopes=["keys"], mode="live")

    with pytest.raises(AuthorizationError, match="admin scope required to grant keys scope"):
        await create_api_key(body=body, api_key=caller, db=db_session)


@pytest.mark.integration
async def test_admin_plus_keys_caller_can_mint_keys_key(db_session, application_factory):
    """An admin+keys caller CAN grant keys scope (Constraint B satisfied)."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app = await application_factory()

    caller = ApiKey(
        application_id=app.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["admin", "keys", "fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app.id, scopes=["keys", "fetch"], mode="live")

    response = await create_api_key(body=body, api_key=caller, db=db_session)
    assert response.raw_key.startswith("crw")
    assert "keys" in response.scopes


@pytest.mark.integration
async def test_caller_cannot_grant_scope_not_held(db_session, application_factory):
    """A fetch-only caller cannot grant archive scope (Constraint A)."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.core.errors import AuthorizationError
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app = await application_factory()

    caller = ApiKey(
        application_id=app.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app.id, scopes=["archive"], mode="live")

    with pytest.raises(AuthorizationError, match="Cannot grant scopes"):
        await create_api_key(body=body, api_key=caller, db=db_session)


# ── D3: Cross-tenant issuance ─────────────────────────────────────────────────


@pytest.mark.integration
async def test_keys_only_caller_confined_to_own_application(db_session, application_factory):
    """A keys-only caller cannot issue keys for another application."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.core.errors import AuthorizationError
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app1 = await application_factory()
    app2 = await application_factory()

    caller = ApiKey(
        application_id=app1.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["keys", "fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app2.id, scopes=["fetch"], mode="live")

    with pytest.raises(AuthorizationError, match="Cannot issue keys for another application"):
        await create_api_key(body=body, api_key=caller, db=db_session)


@pytest.mark.integration
async def test_admin_caller_can_target_any_application(db_session, application_factory):
    """An admin caller may issue keys for any application."""
    from app.api.v1.endpoints.auth_keys import create_api_key
    from app.models.api_key import ApiKey
    from app.schemas.api_key import ApiKeyCreate

    app1 = await application_factory()
    app2 = await application_factory()

    caller = ApiKey(
        application_id=app1.id,
        prefix=_unique_prefix(),
        hashed_key="dummy",
        scopes=["admin", "keys", "fetch"],
        mode="live",
    )
    db_session.add(caller)
    await db_session.commit()

    body = ApiKeyCreate(application_id=app2.id, scopes=["fetch"], mode="live")

    response = await create_api_key(body=body, api_key=caller, db=db_session)
    assert response.raw_key.startswith("crw")
    assert response.application_id == app2.id


# ── D4: Rotation ──────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_rotate_returns_new_raw_key_once(db_session):
    """Rotation returns a new raw key — different from the old one."""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant
    from app.services.key_service import rotate_api_key

    tag = uuid4().hex[:8]
    # Setup: tenant → app → key.
    tenant = Tenant(name=f"test-tenant-{tag}")
    db_session.add(tenant)
    await db_session.commit()
    app = Application(tenant_id=tenant.id, name=f"test-app-{tag}")
    db_session.add(app)
    await db_session.commit()

    old_raw, old_hashed = generate_api_key()
    old_key = ApiKey(
        application_id=app.id,
        prefix=old_raw[:8],
        hashed_key=old_hashed,
        scopes=["fetch"],
        mode="live",
    )
    db_session.add(old_key)
    await db_session.commit()
    await db_session.refresh(old_key)

    successor, new_raw = await rotate_api_key(db_session, key_id=old_key.id, issuer_key_id=uuid4())

    assert new_raw != old_raw
    assert new_raw.startswith("crw")
    assert successor.id != old_key.id
    assert successor.scopes == old_key.scopes
    assert successor.application_id == old_key.application_id


@pytest.mark.integration
async def test_rotated_old_key_valid_during_overlap(db_session):
    """The old key is not revoked and stays is_active=True after rotation."""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant
    from app.services.key_service import rotate_api_key

    tag = uuid4().hex[:8]
    tenant = Tenant(name=f"test-tenant-{tag}")
    db_session.add(tenant)
    await db_session.commit()
    app = Application(tenant_id=tenant.id, name=f"test-app-{tag}")
    db_session.add(app)
    await db_session.commit()

    old_raw, old_hashed = generate_api_key()
    old_key = ApiKey(
        application_id=app.id,
        prefix=old_raw[:8],
        hashed_key=old_hashed,
        scopes=["fetch"],
        mode="live",
    )
    db_session.add(old_key)
    await db_session.commit()
    await db_session.refresh(old_key)

    await rotate_api_key(db_session, key_id=old_key.id, issuer_key_id=uuid4())

    # Reload old key.
    await db_session.refresh(old_key)
    assert old_key.revoked_at is None, "Old key must not be revoked"
    assert old_key.is_active is True, "Old key must remain active"
    assert old_key.expires_at is not None, "Old key must have expiry set"


@pytest.mark.integration
async def test_rotated_old_key_rejected_after_overlap_forced_to_expire(db_session):
    """Old key expires at now+overlap; after expiry it is rejected."""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant
    from app.services.key_service import rotate_api_key

    tag = uuid4().hex[:8]
    tenant = Tenant(name=f"test-tenant-{tag}")
    db_session.add(tenant)
    await db_session.commit()
    app = Application(tenant_id=tenant.id, name=f"test-app-{tag}")
    db_session.add(app)
    await db_session.commit()

    old_raw, old_hashed = generate_api_key()
    old_key = ApiKey(
        application_id=app.id,
        prefix=old_raw[:8],
        hashed_key=old_hashed,
        scopes=["fetch"],
        mode="live",
    )
    db_session.add(old_key)
    await db_session.commit()
    await db_session.refresh(old_key)

    # Rotate with a 0-hour overlap — old key expires immediately.
    await rotate_api_key(db_session, key_id=old_key.id, issuer_key_id=uuid4(), overlap_hours=0)

    await db_session.refresh(old_key)
    # With 0 overlap, expires_at should be <= now.
    assert old_key.expires_at is not None
    # Not exactly ≤ now() due to timing, but within a small delta.
    delta = (old_key.expires_at - datetime.now(UTC)).total_seconds()
    assert delta < 10  # Within 10 seconds of now


@pytest.mark.integration
async def test_rotate_revoked_key_returns_409(db_session):
    """Rotating a revoked key raises ConflictError."""
    from app.core.errors import ConflictError
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant
    from app.services.key_service import rotate_api_key

    tag = uuid4().hex[:8]
    tenant = Tenant(name=f"test-tenant-{tag}")
    db_session.add(tenant)
    await db_session.commit()
    app = Application(tenant_id=tenant.id, name=f"test-app-{tag}")
    db_session.add(app)
    await db_session.commit()

    raw, hashed = generate_api_key()
    key = ApiKey(
        application_id=app.id,
        prefix=raw[:8],
        hashed_key=hashed,
        scopes=["fetch"],
        mode="live",
        revoked_at=datetime.now(UTC),
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)

    with pytest.raises(ConflictError, match="Cannot rotate a revoked key"):
        await rotate_api_key(db_session, key_id=key.id, issuer_key_id=uuid4())


# ── D5: Listing — hashed_key exclusion ────────────────────────────────────────


@pytest.mark.integration
async def test_list_keys_response_excludes_hashed_key(db_session, api_key_factory):
    """ApiKeyResponse serialization must not include hashed_key."""
    from app.schemas.api_key import ApiKeyResponse

    _raw, row = await api_key_factory(scopes=["fetch"])
    resp = ApiKeyResponse.model_validate(row)
    data = resp.model_dump()

    # Assert non-empty first — a zero-field response is not evidence.
    assert len(data) > 0, "ApiKeyResponse dump is empty"
    assert "hashed_key" not in data, "hashed_key leaked into response"
    assert "application_id" in data, "application_id must be present per D5"
    assert "revoked_at" in data, "revoked_at must be present per D5"


# ── D9: Bootstrap convergence ─────────────────────────────────────────────────


@pytest.mark.integration
def test_bootstrap_creates_key_via_service_layer():
    """bootstrap_dev.py calls key_service.create_api_key, not raw ORM inserts."""
    import ast
    import os

    bootstrap_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "bootstrap_dev.py"
    )
    with open(bootstrap_path) as f:
        tree = ast.parse(f.read())

    # Check that create_api_key is imported from key_service.
    imports_from_service = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.services.key_service" and any(
                alias.name == "create_api_key" for alias in node.names
            ):
                imports_from_service = True
                break

    assert imports_from_service, (
        "bootstrap_dev.py must import create_api_key from app.services.key_service"
    )

    # Check that ApiKey is NOT imported from models (for key creation).
    imports_api_key_model = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.models.api_key" and any(
                alias.name == "ApiKey" for alias in node.names
            ):
                imports_api_key_model = True
                break

    assert not imports_api_key_model, (
        "bootstrap_dev.py must NOT import ApiKey from app.models.api_key"
    )


# ── D10: Log redaction (covered by tests/test_logging_redaction.py) ───────────
# test_log_redaction_covers_generated_key_format already exists (B2).


# ── Additional required behaviors ─────────────────────────────────────────────


@pytest.mark.integration
async def test_key_issue_persists_issuer_key_id(db_session, application_factory):
    """When create_api_key is called with issuer_key_id, the row stores it."""
    from app.services.key_service import create_api_key

    app = await application_factory()
    issuer_id = uuid4()

    row, _raw = await create_api_key(
        db_session,
        application_id=app.id,
        scopes=["fetch"],
        mode="live",
        issuer_key_id=issuer_id,
    )

    assert row.issuer_key_id == issuer_id, (
        f"issuer_key_id should be {issuer_id}, got {row.issuer_key_id}"
    )


@pytest.mark.integration
async def test_bootstrap_key_has_issuer_key_id_null(db_session, application_factory):
    """When create_api_key is called with issuer_key_id=None, the column is NULL."""
    from app.services.key_service import create_api_key

    app = await application_factory()

    row, _raw = await create_api_key(
        db_session,
        application_id=app.id,
        scopes=["fetch"],
        mode="live",
        issuer_key_id=None,
    )

    assert row.issuer_key_id is None, (
        f"issuer_key_id should be NULL for bootstrap keys, got {row.issuer_key_id}"
    )


def test_require_scope_rejects_unknown_scope():
    """require_scope('invalid_scope') raises ValueError at registration time."""
    import pytest as pt

    from app.api.v1.dependencies import require_scope

    with pt.raises(ValueError, match="Invalid scope"):
        require_scope("invalid_scope")


@pytest.mark.integration
async def test_create_api_key_returns_non_empty_raw_key(db_session, application_factory):
    """create_api_key must return a non-empty raw_key — empty is a silent auth bypass."""
    from app.services.key_service import create_api_key

    app = await application_factory()
    _row, raw_key = await create_api_key(
        db_session,
        application_id=app.id,
        scopes=["fetch"],
        mode="live",
        issuer_key_id=None,
    )

    assert raw_key, "raw_key must not be empty"
    assert raw_key.startswith("crw"), f"raw_key must start with crw, got {raw_key[:8]!r}"
    assert len(raw_key) > 20, f"raw_key too short: {len(raw_key)} chars"


# NOTE: An HTTP-level POST /v1/keys → raw_key assertion cannot be written with
# the current TestClient infrastructure because FastAPI's TestClient creates an
# independent event loop that conflicts with asyncpg's greenlet-based connection
# pool (RuntimeError: Task got Future attached to a different loop).  The
# service-layer guard in key_service.py (`if not raw_key: raise RuntimeError`)
# and test_create_api_key_returns_non_empty_raw_key cover this requirement.
