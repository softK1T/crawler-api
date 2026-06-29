from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "crawler",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks.crawl", "app.worker.tasks.sync"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.result_ttl_secs,
    task_default_queue="crawler",
    beat_schedule={
        "sync-webshare-proxies": {
            "task": "sync_webshare_proxies",
            "schedule": settings.webshare_sync_interval_secs,
            "options": {"expires": 300},
        },
    },
)
