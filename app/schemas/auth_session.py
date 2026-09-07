from pydantic import BaseModel, Field, HttpUrl


class LoginRequest(BaseModel):
    url: HttpUrl
    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=1024)
    use_proxy: bool = False
    proxy_country: str | None = Field(default=None, min_length=2, max_length=2)
    proxy_type: str | None = Field(default=None, min_length=1, max_length=32)


class ManualSessionRequest(BaseModel):
    session_key: str = Field(..., min_length=1, max_length=128)
    cookies: dict[str, str] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_key: str
    has_session: bool
    cookie_count: int = 0


class LoginResponse(BaseModel):
    session_key: str
    cookie_count: int
    adapter: str
