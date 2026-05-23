import secrets
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> str:
    """
    Validate the X-API-Key header against the configured key set.
    Uses constant-time comparison to prevent timing attacks.
    """
    if not settings.api_keys:
        # No keys configured → auth disabled (dev/local mode only)
        return "anonymous"

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Pass X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    for valid_key in settings.api_keys:
        if secrets.compare_digest(api_key.encode(), valid_key.encode()):
            return api_key

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key.",
    )
