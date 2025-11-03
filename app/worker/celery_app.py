from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "crawler-api",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.task_routes = {
    "crawl_page": {"queue": "crawler"}
}

celery_app.autodiscover_tasks(["app.worker.tasks"])

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=settings.batch_timeout_secs,
    task_soft_time_limit=settings.batch_timeout_secs - 10,
)
