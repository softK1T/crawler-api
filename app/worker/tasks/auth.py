import logging
from typing import Optional

from app.worker.celery_app import celery_app
from app.services.session_manager import save_session, delete_session, load_session

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="shopee_login", acks_late=True)
def task_shopee_login(
    self,
    username: str,
    password: str,
    proxy_url: Optional[str] = None,
):
    import asyncio
    from app.services.shopee_auth import shopee_login, SHOPEE_SESSION_KEY

    logger.info("[task] shopee_login started")
    cookies = asyncio.run(shopee_login(username, password, proxy_url=proxy_url))

    if not cookies:
        raise RuntimeError("Shopee login failed — check credentials or CAPTCHA")

    return {
        "status": "ok",
        "session_key": SHOPEE_SESSION_KEY,
        "cookie_count": len(cookies),
    }


@celery_app.task(name="shopee_logout")
def task_shopee_logout():
    from app.services.shopee_auth import SHOPEE_SESSION_KEY
    delete_session(SHOPEE_SESSION_KEY)
    return {"status": "ok", "message": "Session deleted"}
