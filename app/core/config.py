from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000

    # Root log level for the JSON handler (DEBUG|INFO|WARNING|ERROR).
    log_level: str = "INFO"

    # Comma-separated API keys, e.g. "key1,key2,key3"
    # Leave empty to disable auth (dev/local only)
    api_keys_raw: str = ""

    # CORS allowed origins as comma-separated list, e.g. "http://localhost:3000,https://app.example.com"
    # Defaults to "*" for dev convenience; restrict in production.
    allowed_origins_raw: str = "*"

    # SSRF protection toggle (disable only in fully trusted internal envs)
    ssrf_enabled: bool = True

    # REQUIRED — no default. Set via DATABASE_URL env var.
    # Example: postgresql+asyncpg://user:pass@localhost:5432/crawlerdb
    database_url: PostgresDsn

    redis_url: str = "redis://localhost:6379/2"
    result_ttl_secs: int = 86400

    proxy_file: str | None = None
    max_retries: int = 3
    request_timeout_secs: int = 15
    request_delay_secs: float = 1.0
    use_http2: bool = True

    max_batch_size: int = 1000
    batch_timeout_secs: int = 900

    # Rate limiter defaults
    key_rate_limit_rpm: int = 60  # per-key requests per minute
    default_monthly_quota: int = 100_000  # per-application monthly quota

    # Circuit breaker settings
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout_s: int = 300
    proxy_sticky_ttl_s: int = 1800

    # Webshare auto-sync
    # Get your API key at: https://proxy.webshare.io/userapi/keys
    webshare_api_key: str | None = None
    # Path where synced proxy list will be written (also used as PROXY_FILE)
    webshare_proxy_file: str = "proxies.txt"
    # How often to re-sync in seconds (default: 6 hours)
    webshare_sync_interval_secs: int = 21600
    # Enable/disable the arq cron proxy sync (Stage 14).
    proxy_sync_enabled: bool = True

    # Fetcher tuning (Stage 14).
    curl_executor_max_workers: int = 8
    browser_pool_size: int = 2
    browser_idle_timeout_s: int = 300

    # WARC / S3 archival settings
    s3_endpoint_url: str | None = None  # None = AWS S3; set for MinIO
    s3_bucket: str = "crawler-warc"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    warc_max_size_bytes: int = 1_000_000_000  # 1 GB rotation threshold
    warc_max_age_s: int = 3600  # 1 hour rotation threshold
    warc_prefix: str = "warc"

    # arq worker settings
    arq_redis_url: str = ""  # defaults to redis_url if empty
    job_result_ttl_s: int = 86400  # 24h TTL for job results in Redis

    # Callback settings
    callback_hmac_secret: str = ""  # REQUIRED in prod; empty = callbacks disabled
    callback_timeout_s: float = 10.0
    callback_max_retries: int = 3

    # Observability
    enable_metrics: bool = True
    enable_tracing: bool = True
    otlp_endpoint: str = ""
    service_name: str = "crawler-api"
    service_version: str = "0.1.0"
    metrics_path: str = "/metrics"

    @property
    def api_keys(self) -> list[str]:
        """Return parsed list of non-empty API keys."""
        return [k.strip() for k in self.api_keys_raw.split(",") if k.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        """Return parsed list of allowed CORS origins."""
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

    @property
    def effective_proxy_file(self) -> str | None:
        """
        Returns the proxy file path to use.
        If WEBSHARE_API_KEY is set, always use WEBSHARE_PROXY_FILE.
        Otherwise fall back to PROXY_FILE.
        """
        if self.webshare_api_key:
            return self.webshare_proxy_file
        return self.proxy_file


settings = Settings()
