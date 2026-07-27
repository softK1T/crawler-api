from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_api_key

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("/", status_code=201)
async def create_project(_api_key: str = Depends(get_api_key)):
    """Create a new project — stub, coming in Stage 2."""
    raise HTTPException(
        status_code=503,
        detail="Project persistence not yet implemented — coming in Stage 2",
    )


@router.get("/")
async def list_projects(_api_key: str = Depends(get_api_key)):
    """List all registered projects — stub, coming in Stage 2."""
    raise HTTPException(
        status_code=503,
        detail="Project persistence not yet implemented — coming in Stage 2",
    )
