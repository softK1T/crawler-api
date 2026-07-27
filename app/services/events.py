import json
import logging
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
        )
    return _redis_client


def publish_event(stream: str, event_type: str, payload: dict[str, Any]) -> None:
    """
    Publish an event to a Redis Stream.
    Stream naming convention: events:<domain>
    e.g. publish_event('crawl', 'crawl.completed', {...})
    """
    try:
        r = _get_redis()
        r.xadd(
            f"events:{stream}",
            {
                "type": event_type,
                "payload": json.dumps(payload),
            },
            maxlen=100_000,
            approximate=True,
        )
        logger.debug("Published event %s to events:%s", event_type, stream)
    except Exception as exc:
        logger.warning("Failed to publish event %s: %s", event_type, exc)
