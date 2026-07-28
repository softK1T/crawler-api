"""arq WorkerSettings — entry point for ``arq app.worker.arq_worker.WorkerSettings``."""

import arq

from app.core.config import settings
from app.worker.tasks.fetch_task import fetch_task, shutdown, startup

_redis_dsn = settings.arq_redis_url or settings.redis_url

redis_settings = arq.connections.RedisSettings.from_dsn(_redis_dsn)


class WorkerSettings:
    functions = [fetch_task]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = "arq:crawler"
    max_jobs = 20
    job_timeout = 120
    keep_result = 0
    retry_jobs = False
    redis_settings = redis_settings
