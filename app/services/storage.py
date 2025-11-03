import json
import redis
from app.core.config import settings

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

def save_result(task_id: str, payload: dict) -> None:
    key = f"result:{task_id}"
    _redis.setex(name=key, time=settings.result_ttl_secs, value=json.dumps(payload))

def get_result(task_id: str) -> dict | None:
    key = f"result:{task_id}"
    raw = _redis.get(key)
    return json.loads(raw) if raw else None
