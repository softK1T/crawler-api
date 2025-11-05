import json
import redis
from typing import Optional, Dict, Any
from app.core.config import settings


class StorageService:
    def __init__(self):
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def save_job_result(self, job_id: str, result_data: Dict[str, Any]) -> None:
        key = f"job:{job_id}"
        self._redis.setex(name=key, time=settings.result_ttl_secs, value=json.dumps(result_data))

    def get_job_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        key = f"job:{job_id}"
        raw = self._redis.get(key)
        return json.loads(raw) if raw else None

    def save_batch_info(self, batch_id: str, batch_info: Dict[str, Any]) -> None:
        key = f"batch:{batch_id}"
        self._redis.setex(name=key, time=settings.result_ttl_secs, value=json.dumps(batch_info))

    def get_batch_info(self, batch_id: str) -> Optional[Dict[str, Any]]:
        key = f"batch:{batch_id}"
        raw = self._redis.get(key)
        return json.loads(raw) if raw else None


storage = StorageService()
