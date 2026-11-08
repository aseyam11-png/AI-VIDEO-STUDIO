from fastapi import APIRouter, HTTPException, Response, status

from app.core.config import settings
from app.integrations.ollama import OllamaError
from app.orchestration.graph import build_graph
from app.schemas.jobs import JobCreate
from app.schemas.tools import AsyncJobToolCall, DirectorPlanToolCall, ToolExecutionRequest
from app.services.jobs import JobConflictError, JobRequestError, create_job
from app.services.tool_gateway import get_tool_catalog

router = APIRouter(prefix="/v1/tools", tags=["tools"])


@router.get("")
def list_tools() -> dict:
    """Discover every operation through one catalog and one execution contract."""
    return get_tool_catalog()


@router.post("/execute")
def execute_tool(payload: ToolExecutionRequest, response: Response) -> dict:
    """Execute one approved tool through a shared entry point.

    Director planning returns a draft. Media/model operations become durable jobs
    and continue to use the Phase 1 queue, idempotency, retry and cancellation logic.
    """
    if isinstance(payload, DirectorPlanToolCall):
        try:
            state = build_graph().invoke(
                {
                    "user_brief": payload.brief,
                    "target_duration_seconds": payload.target_duration_seconds,
                    "language": payload.language,
                    "approved": False,
                }
            )
            plan = state.get("creative_brief")
            if not plan:
                raise OllamaError("The director graph did not produce a creative plan.")
            return {
                "kind": "plan",
                "tool_id": "director.plan",
                "plan": plan,
                "status": state.get("status", "awaiting_approval"),
                "model": settings.ollama_model,
                "approval_required": True,
            }
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not isinstance(payload, AsyncJobToolCall):
        raise HTTPException(status_code=422, detail="Unsupported tool request.")

    try:
        job_request = JobCreate(
            project_id=payload.project_id,
            asset_id=payload.asset_id,
            job_type=payload.tool_id,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
        )
        job, created = create_job(job_request)
        response.status_code = status.HTTP_202_ACCEPTED
        if not created:
            response.headers["Idempotent-Replay"] = "true"
        return {
            "kind": "job",
            "tool_id": payload.tool_id,
            "job": job,
            "replayed": not created,
        }
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
