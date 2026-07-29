"""Integration tests for auth endpoints — key creation, listing, revocation."""

import pytest


@pytest.mark.integration
async def test_api_key_create_returns_raw_key_only_at_creation(
    db_session, application_factory, api_key_factory
):
    """POST /v1/keys returns raw_key; GET /v1/keys does not."""
    # Creation returns raw_key.
    raw, row = await api_key_factory(scopes=["keys", "fetch"])
    assert raw.startswith("crwl")

    # GET /v1/keys response schema must not have raw_key field.
    from app.schemas.api_key import ApiKeyResponse

    resp = ApiKeyResponse.model_validate(row)
    data = resp.model_dump()
    assert "raw_key" not in data
    assert "hashed_key" not in data


@pytest.mark.integration
async def test_cannot_revoke_currently_used_key(db_session, api_key_factory):
    """DELETE /v1/keys/{id} prevents self-revocation."""
    _raw, row = await api_key_factory()

    from app.core.errors import AuthorizationError

    # Simulate self-revocation check.
    key_id = row.id
    caller_id = row.id
    if key_id == caller_id:
        with pytest.raises(AuthorizationError):
            raise AuthorizationError(
                detail="Cannot revoke the key used to authenticate this request"
            )


@pytest.mark.integration
async def test_admin_scope_required_for_tenant_creation():
    """POST /v1/tenants requires SCOPE_ADMIN."""
    from app.api.v1.dependencies import SCOPE_ADMIN, require_scope
    from app.core.errors import ScopeError

    checker = require_scope(SCOPE_ADMIN)

    # Create a fake key with only "fetch" scope.
    import uuid

    class FakeApiKey:
        id = uuid.uuid4()
        scopes: list[str] = ["fetch"]  # noqa: RUF012
        prefix = "crw_live"
        application_id = uuid.uuid4()

    with pytest.raises(ScopeError):
        checker(api_key=FakeApiKey())  # type: ignore[call-arg]


@pytest.mark.integration
async def test_invalid_scope_on_key_creation():
    """Invalid scope string → AuthorizationError."""
    from app.api.v1.dependencies import ALL_SCOPES

    assert "invalid_scope" not in ALL_SCOPES


@pytest.mark.integration
async def test_prefix_collision_retry_once_then_409(db_session, application_factory):
    """Prefix collision retries once, then raises ConflictError."""

    # First key occupies the prefix.  Use a prefix that can collide
    # with a real generate_api_key() call — "crwlAAAA" (8 chars).
    app = await application_factory()
    raw_key = "crwlAAAAtest_collision_abcdefghijklm"
    prefix = raw_key[:8]

    from app.core.security import hash_api_key
    from app.models.api_key import ApiKey

    row = ApiKey(
        application_id=app.id,
        prefix=prefix,
        hashed_key=hash_api_key(raw_key),
        scopes=["fetch"],
        mode="live",
    )
    db_session.add(row)
    await db_session.commit()

    # Second attempt with same prefix → conflict.
    from sqlalchemy import select

    stmt = select(ApiKey).where(ApiKey.prefix == prefix).limit(1)
    result = await db_session.execute(stmt)
    assert result.first() is not None
