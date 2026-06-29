import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.services.session_manager import load_session, delete_session, SHOPEE_SESSION_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    proxy_url: Optional[str] = None


class LoginJobResponse(BaseModel):
    job_id: str
    message: str


class SessionStatusResponse(BaseModel):
    session_key: str
    active: bool
    cookie_count: Optional[int] = None


@router.post("/shopee/login", response_model=LoginJobResponse, status_code=202)
async def shopee_login(
    request: LoginRequest,
    _api_key: str = Depends(verify_api_key),
):
    """
    Trigger async Shopee login via camoufox.
    Returns job_id — poll /jobs/{job_id}/status to check completion.
    """
    from app.worker.tasks.auth import task_shopee_login
    task = task_shopee_login.delay(
        username=request.username,
        password=request.password,
        proxy_url=request.proxy_url,
    )
    return LoginJobResponse(
        job_id=task.id,
        message="Login job enqueued. Poll /api/v1/jobs/{job_id}/status for result.",
    )


@router.get("/shopee/session", response_model=SessionStatusResponse)
async def get_shopee_session(
    _api_key: str = Depends(verify_api_key),
):
    """Check if a valid Shopee session exists in Redis."""
    cookies = load_session(SHOPEE_SESSION_KEY)
    if cookies:
        return SessionStatusResponse(
            session_key=SHOPEE_SESSION_KEY,
            active=True,
            cookie_count=len(cookies),
        )
    return SessionStatusResponse(session_key=SHOPEE_SESSION_KEY, active=False)


@router.delete("/shopee/session", status_code=204)
async def delete_shopee_session(
    _api_key: str = Depends(verify_api_key),
):
    """Invalidate the current Shopee session."""
    delete_session(SHOPEE_SESSION_KEY)
