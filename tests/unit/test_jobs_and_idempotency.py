"""Unit tests for job idempotency and Celery shim."""

from uuid import uuid4


async def test_idempotency_namespaced_by_application(redis_client):
    from app.services.job_service import JobService

    svc = JobService(redis_client)
    app_a = uuid4()
    app_b = uuid4()

    # Same key, different applications → different namespaces.
    r1 = await svc.handle_idempotency("key-1", app_a)
    assert r1 is None

    await svc.store_idempotency("key-1", "job-a-1", app_a)

    r2 = await svc.handle_idempotency("key-1", app_a)
    assert r2 == "job-a-1"

    r3 = await svc.handle_idempotency("key-1", app_b)
    assert r3 is None  # Different namespace.


def test_submit_crawl_shim_exists():
    from app.services.job_service import submit_crawl

    job_id = submit_crawl("https://example.com", mode="static")
    assert isinstance(job_id, str)
    assert len(job_id) > 0
