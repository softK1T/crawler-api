from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "crawler",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.task_routes = {"app.worker.tasks.*": {"queue": "crawler"}}
celery_app.autodiscover_tasks(["app.worker.tasks"])