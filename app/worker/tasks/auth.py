import logging
from typing import Optional

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="site_login", acks_late=True)
def task_site_login(
    self,
    url: str,
    username: str,
    password: str,
    proxy_url: Optional[str] = None,
):
    """
    Universal login task. Resolves adapter by URL domain.
    """
    import asyncio
    from app.services.adapters import get_adapter

    adapter = get_adapter(url)
    logger.info("[task] site_login: adapter=%s url=%s", adapter.session_key, url)

    cookies = asyncio.run(adapter.login(
        username=username,
        password=password,
        proxy_url=proxy_url,
    ))

    if not cookies:
        raise RuntimeError(
            f"Login failed for {adapter.session_key} — "
            f"check credentials, or CAPTCHA may require manual cookie injection "
            f"via POST /api/v1/auth/session"
        )

    return {
        "status": "ok",
        "session_key": adapter.session_key,
        "cookie_count": len(cookies),
    }
