"""Regression tests for fetch_task: engine mapping and result serialization."""

import importlib
import json
from uuid import uuid4

import pytest


def _fetch_task_source() -> str:
    module = importlib.import_module("app.worker.tasks.fetch_task")
    assert module.__file__ is not None
    with open(module.__file__, encoding="utf-8") as f:
        return f.read()


def test_mode_to_engine_camoufox_maps_to_camoufox_engine():
    """MODE_TO_ENGINE['camoufox'] must be 'camoufox', never 'playwright'.

    Regression guard for the bug where API mode='camoufox' silently ran
    on PlaywrightFetcher (Chromium) instead of CamoufoxFetcher (Firefox),
    defeating the entire purpose of requesting the camoufox engine.
    """
    content = _fetch_task_source()

    assert '"camoufox": "camoufox"' in content, (
        "MODE_TO_ENGINE['camoufox'] regressed back to mapping onto "
        "playwright/chromium instead of the camoufox engine"
    )
    assert '"camoufox": "playwright"' not in content


def test_fetch_task_serializes_result_with_mode_json():
    """Step 8 must call schema.model_dump(mode='json').

    Without mode='json', proxy_id stays a UUID object and json.dumps()
    inside _set_status raises 'Object of type UUID is not JSON
    serializable' — every successful proxied fetch was marked failed.
    """
    content = _fetch_task_source()

    assert 'schema.model_dump(mode="json")' in content, (
        "fetch_task step 8 regressed to model_dump() without mode='json' — "
        "proxied fetches will fail serialization again"
    )


class _FakeRedis:
    """Minimal in-memory stand-in for redis.asyncio used by _set_status."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex=None) -> None:
        self.store[key] = value


@pytest.mark.asyncio
async def test_set_status_roundtrips_uuid_proxy_id_as_string():
    """_set_status must store proxy_id as a JSON string, not a UUID object."""
    from app.schemas.fetch import FetchResultSchema
    from app.worker.tasks.fetch_task import _set_status

    proxy_id = uuid4()
    schema = FetchResultSchema(
        url="https://example.com",
        status_code=200,
        headers={},
        body_b64="aGVsbG8=",
        body_bytes=5,
        content_sha256="sha256-test",
        elapsed_ms=10,
        proxy_id=proxy_id,
        engine="httpx",
    )

    redis = _FakeRedis()
    await _set_status(
        redis,
        "job-1",
        "completed",
        60,
        result_data=schema.model_dump(mode="json"),
    )

    payload = json.loads(redis.store["job:job-1:status"])
    assert payload["status"] == "completed"
    assert payload["result"]["proxy_id"] == str(proxy_id)
    assert isinstance(payload["result"]["proxy_id"], str)
