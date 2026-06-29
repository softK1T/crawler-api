import json
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

SESSION_TTL = 60 * 60 * 6  # 6 hours


def _redis():
    import redis as redis_lib
    from app.core.config import settings
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def save_session(session_key: str, cookies: Dict[str, str]) -> None:
    """Persist cookies dict to Redis with TTL."""
    r = _redis()
    r.setex(f"session:{session_key}", SESSION_TTL, json.dumps(cookies))
    logger.info("[session] Saved session '%s' (%d cookies, TTL=%ds)", session_key, len(cookies), SESSION_TTL)


def load_session(session_key: str) -> Optional[Dict[str, str]]:
    """Load cookies dict from Redis. Returns None if expired/missing."""
    r = _redis()
    raw = r.get(f"session:{session_key}")
    if not raw:
        logger.info("[session] No session found for '%s'", session_key)
        return None
    cookies = json.loads(raw)
    logger.info("[session] Loaded session '%s' (%d cookies)", session_key, len(cookies))
    return cookies


def delete_session(session_key: str) -> None:
    r = _redis()
    r.delete(f"session:{session_key}")
    logger.info("[session] Deleted session '%s'", session_key)


def cookies_to_header(cookies: Dict[str, str]) -> str:
    """Convert cookies dict to Cookie header string."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
