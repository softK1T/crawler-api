"""arq WorkerSettings — entry point for ``arq app.worker.arq_worker.WorkerSettings``."""

from app.core.config import settings
from app.worker.tasks.fetch_task import fetch_task, shutdown, startup


class WorkerSettings:
    functions = [fetch_task]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = "arq:crawler"
    max_jobs = 20
    job_timeout = 120
    keep_result = 0
    retry_jobs = False

    @property
    def redis_settings(self) -> str:
        return settings.arq_redis_url or settings.redis_url
