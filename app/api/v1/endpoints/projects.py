import secrets
from fastapi import APIRouter, HTTPException
from app.schemas.requests import ProjectCreateRequest
from app.schemas.responses import ProjectResponse
from datetime import datetime, timezone

router = APIRouter(prefix="/projects", tags=["projects"])

# In-memory project store for MVP (replace with DB query in next phase)
_projects: dict = {}


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectCreateRequest):
    """Create a new project and generate an API key."""
    project_id = secrets.token_hex(8)
    api_key = f"ck_{secrets.token_urlsafe(32)}"
    project = {
        "id": project_id,
        "name": request.name,
        "api_key": api_key,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    _projects[api_key] = project
    return ProjectResponse(**project)


@router.get("/", response_model=list[ProjectResponse])
async def list_projects():
    """List all registered projects."""
    return [ProjectResponse(**p) for p in _projects.values()]
