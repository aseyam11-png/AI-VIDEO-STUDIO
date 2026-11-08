from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.integrations.ollama import OllamaError
from app.schemas.workflows import WorkflowCreateRequest, WorkflowFromBriefRequest
from app.services.workflow_engine import (
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowRequestError,
    approve_workflow,
    cancel_workflow,
    create_workflow_run,
    create_workflow_from_brief,
    get_workflow,
)

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreateRequest) -> dict:
    """Persist a proposed workflow. Nothing runs until the user approves it."""
    try:
        return create_workflow_run(payload)
    except WorkflowRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/from-brief", status_code=status.HTTP_201_CREATED)
def create_workflow_from_brief_route(payload: WorkflowFromBriefRequest) -> dict:
    """Generate an Arabic/English Qwen plan, persist its steps, and stop for approval."""
    try:
        return create_workflow_from_brief(payload)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkflowRequestError as exc:
        status_code = 503 if str(exc).startswith("AI Director could not create a plan:") else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/{workflow_id}")
def get_workflow_route(workflow_id: UUID) -> dict:
    try:
        return get_workflow(str(workflow_id))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{workflow_id}/approve", status_code=status.HTTP_202_ACCEPTED)
def approve_workflow_route(workflow_id: UUID) -> dict:
    try:
        return approve_workflow(str(workflow_id))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{workflow_id}/cancel")
def cancel_workflow_route(workflow_id: UUID) -> dict:
    try:
        return cancel_workflow(str(workflow_id))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
