import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.services.session_manager import load_session, delete_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    url: str                        # any URL on the target site
    username: str
    password: str
    proxy_url: Optional[str] = None


class ManualSessionRequest(BaseModel):
    url: str                        # used to resolve session_key via adapter
    cookies: dict                   # raw cookies dict from browser DevTools


class LoginJobResponse(BaseModel):
    job_id: str
    session_key: str
    message: str


class SessionStatusResponse(BaseModel):
    session_key: str
    active: bool
    cookie_count: Optional[int] = None


def _resolve_adapter(url: str):
    try:
        from app.services.adapters import get_adapter
        return get_adapter(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# Login (async via Celery)
# ------------------------------------------------------------------

@router.post("/login", response_model=LoginJobResponse, status_code=202)
async def login(
    request: LoginRequest,
    _api_key: str = Depends(verify_api_key),
):
    """
    Universal login endpoint. Resolves the correct adapter by URL domain.
    Supported: shopee.sg, shopee.com.my, shopee.co.id, ...
    Returns job_id — poll GET /api/v1/jobs/{job_id}/status.
    """
    adapter = _resolve_adapter(request.url)
    from app.worker.tasks.auth import task_site_login
    task = task_site_login.delay(
        url=request.url,
        username=request.username,
        password=request.password,
        proxy_url=request.proxy_url,
    )
    return LoginJobResponse(
        job_id=task.id,
        session_key=adapter.session_key,
        message=f"Login job enqueued for {adapter.session_key}. Poll /api/v1/jobs/{{job_id}}/status.",
    )


# ------------------------------------------------------------------
# Manual session injection (when auto-login hits CAPTCHA)
# ------------------------------------------------------------------

@router.post("/session", response_model=SessionStatusResponse, status_code=201)
async def set_manual_session(
    request: ManualSessionRequest,
    _api_key: str = Depends(verify_api_key),
):
    """
    Manually inject cookies from browser DevTools.
    Use when auto-login is blocked by CAPTCHA.
    """
    adapter = _resolve_adapter(request.url)
    from app.services.session_manager import save_session
    save_session(adapter.session_key, request.cookies)
    return SessionStatusResponse(
        session_key=adapter.session_key,
        active=True,
        cookie_count=len(request.cookies),
    )


# ------------------------------------------------------------------
# Session status
# ------------------------------------------------------------------

@router.get("/session", response_model=SessionStatusResponse)
async def get_session(
    url: str,
    _api_key: str = Depends(verify_api_key),
):
    """Check if a valid session exists for the given site URL."""
    adapter = _resolve_adapter(url)
    cookies = load_session(adapter.session_key)
    return SessionStatusResponse(
        session_key=adapter.session_key,
        active=bool(cookies),
        cookie_count=len(cookies) if cookies else None,
    )


@router.delete("/session", status_code=204)
async def clear_session(
    url: str,
    _api_key: str = Depends(verify_api_key),
):
    """Invalidate the session for the given site URL."""
    adapter = _resolve_adapter(url)
    delete_session(adapter.session_key)
