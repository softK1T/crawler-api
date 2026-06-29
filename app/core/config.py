from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Comma-separated API keys, e.g. "key1,key2,key3"
    # Leave empty to disable auth (dev/local only)
    api_keys_raw: str = ""

    # SSRF protection toggle (disable only in fully trusted internal envs)
    ssrf_enabled: bool = True

    # Database
    database_url: str = "postgresql+asyncpg://crawler:crawler@localhost:5432/crawlerdb"

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    redis_url: str = "redis://localhost:6379/2"
    result_ttl_secs: int = 86400

    proxy_file: Optional[str] = None
    max_retries: int = 3
    request_timeout_secs: int = 15
    request_delay_secs: float = 1.0
    use_http2: bool = True

    max_batch_size: int = 1000
    batch_timeout_secs: int = 900

    # Webshare auto-sync
    # Get your API key at: https://proxy.webshare.io/userapi/keys
    webshare_api_key: Optional[str] = None
    # Path where synced proxy list will be written (also used as PROXY_FILE)
    webshare_proxy_file: str = "proxies.txt"
    # How often to re-sync in seconds (default: 6 hours)
    webshare_sync_interval_secs: int = 21600

    @property
    def api_keys(self) -> List[str]:
        """Return parsed list of non-empty API keys."""
        return [k.strip() for k in self.api_keys_raw.split(",") if k.strip()]

    @property
    def effective_proxy_file(self) -> Optional[str]:
        """
        Returns the proxy file path to use.
        If WEBSHARE_API_KEY is set, always use WEBSHARE_PROXY_FILE.
        Otherwise fall back to PROXY_FILE.
        """
        if self.webshare_api_key:
            return self.webshare_proxy_file
        return self.proxy_file


settings = Settings()
