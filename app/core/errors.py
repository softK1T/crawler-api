"""Unified application error hierarchy.

All exceptions inherit from :class:`CrawlerAPIError` so a single FastAPI
exception handler can render consistent JSON error responses.
"""


class CrawlerAPIError(Exception):
    """Base for all application errors."""

    status_code: int = 500
    error_code: str = "internal_error"
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class AuthenticationError(CrawlerAPIError):
    status_code = 401
    error_code = "authentication_failed"
    detail = "Invalid or missing API key"


class AuthorizationError(CrawlerAPIError):
    status_code = 403
    error_code = "forbidden"
    detail = "Insufficient permissions"


class KeyExpiredError(AuthenticationError):
    error_code = "key_expired"
    detail = "API key has expired"


class KeyRevokedError(AuthenticationError):
    error_code = "key_revoked"
    detail = "API key has been revoked"


class ScopeError(AuthorizationError):
    error_code = "insufficient_scope"
    detail = "Required scope not granted"


class NotFoundError(CrawlerAPIError):
    status_code = 404
    error_code = "not_found"
    detail = "Resource not found"


class ConflictError(CrawlerAPIError):
    status_code = 409
    error_code = "conflict"
    detail = "Resource already exists"


# ── Proxy errors ─────────────────────────────────────────────────────────────


class ProxyPoolUnavailableError(CrawlerAPIError):
    """Raised when ``use_proxy=True`` but no healthy proxy is available for selection.

    This is a hard failure — the job must NOT silently fall back to a direct
    connection when the caller explicitly requested a proxy.
    """

    status_code = 502
    error_code = "proxy_pool_unavailable"
    detail = "No healthy proxy available for the requested parameters"


class ProxyPoolExhaustedError(CrawlerAPIError):
    """Raised when all eligible proxies were tried and blocked/failed.

    Differs from :class:`ProxyPoolUnavailableError`: the pool *had* proxies
    but every one of them was exhausted during retry rotation.
    """

    status_code = 502
    error_code = "proxy_pool_exhausted"
    detail = "All eligible proxies were blocked or unhealthy"
