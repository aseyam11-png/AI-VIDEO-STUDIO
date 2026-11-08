from fastapi import APIRouter, HTTPException

from app.media.storage import StorageError, create_project, get_project
from app.schemas.projects import ProjectCreate

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.post("", status_code=201)
def create_project_route(payload: ProjectCreate) -> dict:
    return create_project(payload.model_dump())


@router.get("/{project_id}")
def get_project_route(project_id: str) -> dict:
    try:
        return get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
