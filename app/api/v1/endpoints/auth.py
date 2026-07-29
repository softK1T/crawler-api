"""Site adapter authentication — deferred to Stage 8.

The adapter registry is currently empty (no site adapters implemented).
When adapters are registered, this endpoint will handle login + session
management. For now, all handlers return 501 Not Implemented.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import SCOPE_FETCH, require_scope
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# TODO Stage 8: site adapter auth — adapter registry is empty


@router.post("/login", status_code=501)
async def login(_api_key: ApiKey = Depends(require_scope(SCOPE_FETCH))):
    raise HTTPException(status_code=501, detail="Site adapter auth not implemented (Stage 8)")


@router.post("/session", status_code=501)
async def set_manual_session(_api_key: ApiKey = Depends(require_scope(SCOPE_FETCH))):
    raise HTTPException(status_code=501, detail="Site adapter auth not implemented (Stage 8)")


@router.get("/session", status_code=501)
async def get_session(_api_key: ApiKey = Depends(require_scope(SCOPE_FETCH))):
    raise HTTPException(status_code=501, detail="Site adapter auth not implemented (Stage 8)")


@router.delete("/session", status_code=501)
async def clear_session(_api_key: ApiKey = Depends(require_scope(SCOPE_FETCH))):
    raise HTTPException(status_code=501, detail="Site adapter auth not implemented (Stage 8)")
