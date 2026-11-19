from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProxyCheckSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY_CHECK_", env_file=".env", extra="ignore")
    enabled: bool = False
    endpoint: str = "https://1.1.1.1/cdn-cgi/trace"
    database_path: str | None = None
    timeout_s: float = Field(default=8, gt=0, le=15)
    concurrency: int = Field(default=4, ge=1, le=8)
    batch_size: int = Field(default=20, ge=1, le=100)
    ttl_s: int = Field(default=21600, ge=60)
    retry_s: int = Field(default=600, ge=60)


proxy_check_settings = ProxyCheckSettings()
