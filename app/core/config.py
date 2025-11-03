from pydantic import BaseModel
import os

class Settings(BaseModel):
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))

    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    result_ttl_secs: int = int(os.getenv("RESULT_TTL_SECS", "86400"))
    request_timeout_secs: int = int(os.getenv("REQUEST_TIMEOUT_SECS", "15"))

    proxy_file: str | None = os.getenv("PROXY_FILE")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    request_delay_secs: float = float(os.getenv("REQUEST_DELAY_SECS", "1.0"))
    use_http2: bool = os.getenv("USE_HTTP2", "true").lower() == "true"

settings = Settings()
