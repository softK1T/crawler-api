"""Site adapter authentication endpoints (Stage 8).

These endpoints provide the missing glue between per-site login adapters,
Redis-backed cookie sessions, and normal fetch jobs. The fetch pipeline reuses
saved cookies when ``session_key`` is passed to ``POST /v1/fetch``.
"""

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.core.db import get_db
from app.models.api_key import ApiKey
from app.schemas.auth_session import (
    LoginRequest,
    LoginResponse,
    ManualSessionRequest,
    SessionResponse,
)
from app.services.policy_resolver import normalize_domain, resolve_policy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _domain_session_key(url: str) -> str:
    host = urlparse(url).hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL: hostname missing")
    return normalize_domain(host)


@router.post("/login", response_model=LoginResponse, status_code=200)
async def login(
    body: LoginRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    _api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    from app.services.adapters import get_adapter
    from app.services.session_manager import load_session

    try:
        adapter = get_adapter(str(body.url))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    session_key = getattr(adapter, "session_key", None) or _domain_session_key(str(body.url))
    adapter.session_key = session_key

    proxy_url = None
    if body.use_proxy:
        policy = await resolve_policy(str(body.url), db)
        proxy_manager = req.app.state.proxy_manager
        proxy = await proxy_manager.get_proxy(
            pool_id=getattr(policy, "proxy_pool_id", None),
            domain=session_key,
            sticky_key=session_key,
            country=body.proxy_country.upper() if body.proxy_country else None,
            proxy_type=body.proxy_type,
        )
        if proxy is None:
            raise HTTPException(status_code=503, detail="No healthy proxy available for login")
        proxy_url = getattr(proxy, "url", None)

    cookies = await adapter.login(body.username, body.password, proxy_url=proxy_url)
    if not cookies:
        raise HTTPException(status_code=401, detail="Login failed: adapter did not return cookies")

    stored = await asyncio.to_thread(load_session, session_key)
    cookie_count = len(stored or cookies)
    return LoginResponse(
        session_key=session_key,
        cookie_count=cookie_count,
        adapter=adapter.__class__.__name__,
    )


@router.post("/session", response_model=SessionResponse, status_code=200)
async def set_manual_session(
    body: ManualSessionRequest,
    _api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    from app.services.session_manager import save_session

    if not body.cookies:
        raise HTTPException(status_code=400, detail="cookies must not be empty")
    await asyncio.to_thread(save_session, body.session_key, body.cookies)
    return SessionResponse(
        session_key=body.session_key,
        has_session=True,
        cookie_count=len(body.cookies),
    )


@router.get("/session", response_model=SessionResponse, status_code=200)
async def get_session(
    url: str,
    _api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    from app.services.session_manager import load_session

    session_key = _domain_session_key(url)
    cookies = await asyncio.to_thread(load_session, session_key)
    return SessionResponse(
        session_key=session_key,
        has_session=bool(cookies),
        cookie_count=len(cookies or {}),
    )


@router.delete("/session", response_model=SessionResponse, status_code=200)
async def clear_session(
    url: str,
    _api_key: ApiKey = Depends(require_scope(SCOPE_FETCH)),
):
    from app.services.session_manager import delete_session, load_session

    session_key = _domain_session_key(url)
    cookies = await asyncio.to_thread(load_session, session_key)
    await asyncio.to_thread(delete_session, session_key)
    return SessionResponse(
        session_key=session_key,
        has_session=False,
        cookie_count=len(cookies or {}),
    )
