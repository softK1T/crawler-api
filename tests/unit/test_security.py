"""Unit tests for security module — argon2 hashing, key resolution, scopes."""

from unittest.mock import AsyncMock

import pytest

from app.core.security import (
    generate_api_key,
    hash_api_key,
    verify_api_key_hash,
)


def test_hash_and_verify_happy_path():
    raw_key = "crw_live_test1234567890abcdefghij"
    hashed = hash_api_key(raw_key)
    assert hashed.startswith("$argon2id$")
    assert verify_api_key_hash(raw_key, hashed) is True


def test_verify_rejects_wrong_key():
    raw_key = "crw_live_test1234567890abcdefghij"
    hashed = hash_api_key(raw_key)
    assert verify_api_key_hash("crw_live_wrong_key_1234567890", hashed) is False


def test_verify_rejects_empty_hash():
    assert verify_api_key_hash("crw_live_test", "") is False
    assert verify_api_key_hash("crw_live_test", None) is False  # type: ignore[arg-type]


def test_generate_api_key_format():
    raw, hashed = generate_api_key()
    assert raw.startswith("crwl")  # live key format: crwl<4 random>_...
    assert len(raw) > 32
    assert hashed.startswith("$argon2id$")

    raw_t, _hashed_t = generate_api_key(mode="test")
    assert raw_t.startswith("crwt")  # test key format: crwt<4 random>_...


class TestResolveApiKey:
    async def test_rejects_key_shorter_than_8_chars(self):
        from app.api.v1.dependencies import resolve_api_key
        from app.core.errors import AuthenticationError

        db = AsyncMock()
        with pytest.raises(AuthenticationError):
            await resolve_api_key(x_api_key="short", db=db)

    async def test_revoked_key_raises(self, db_session, api_key_factory):
        from app.api.v1.dependencies import resolve_api_key
        from app.core.errors import KeyRevokedError

        raw, row = await api_key_factory()
        row.revoked_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        await db_session.commit()

        with pytest.raises(KeyRevokedError):
            await resolve_api_key(x_api_key=raw, db=db_session)

    async def test_expired_key_raises(self, db_session, api_key_factory):
        from datetime import UTC, datetime, timedelta

        from app.api.v1.dependencies import resolve_api_key
        from app.core.errors import KeyExpiredError

        raw, row = await api_key_factory()
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db_session.commit()

        with pytest.raises(KeyExpiredError):
            await resolve_api_key(x_api_key=raw, db=db_session)

    async def test_valid_key_passes(self, db_session, api_key_factory):
        from app.api.v1.dependencies import resolve_api_key

        raw, row = await api_key_factory(scopes=["fetch", "archive"])
        result = await resolve_api_key(x_api_key=raw, db=db_session)
        assert result.id == row.id

    async def test_missing_scope_raises(self, db_session, api_key_factory):
        from app.api.v1.dependencies import SCOPE_KEYS, require_scope
        from app.core.errors import ScopeError

        _raw, row = await api_key_factory(scopes=["fetch"])

        async def _resolve():
            return row

        checker = require_scope(SCOPE_KEYS)
        # require_scope returns a sync callable, not an async one.
        with pytest.raises(ScopeError):
            checker(api_key=row)  # type: ignore[call-arg]

    async def test_exactly_one_argon2_verify_per_auth(self, db_session, api_key_factory):
        """With N keys in the DB (distinct prefixes), auth calls Argon2 exactly once."""
        from unittest.mock import patch

        from app.api.v1.dependencies import resolve_api_key
        from app.core import security as sec_module

        # Seed several keys with different prefixes.
        raw1, _row1 = await api_key_factory(scopes=["fetch"])
        _raw2, _row2 = await api_key_factory(scopes=["fetch"])
        _raw3, _row3 = await api_key_factory(scopes=["fetch"])

        # Spy on verify_api_key_hash to count calls.
        with patch(
            "app.api.v1.dependencies.verify_api_key_hash",
            wraps=sec_module.verify_api_key_hash,
        ) as spy:
            result = await resolve_api_key(x_api_key=raw1, db=db_session)
            assert result.id == _row1.id
            # Exactly one Argon2 verification call — NOT iterating over all rows.
            assert spy.call_count == 1, f"Expected 1 Argon2 call, got {spy.call_count}"
