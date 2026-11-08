import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.integrations.ollama import OllamaError
from app.media.storage import project_dir
from app.orchestration.graph import build_graph
from app.schemas.models import DirectorPlanRequest, DirectorPlanResponse
from app.services.jobs import JobNotFoundError, get_job
from app.services.model_capabilities import get_model_capabilities

router = APIRouter(tags=["models"])


@router.get("/v1/models/status")
def model_status() -> dict:
    return get_model_capabilities()


@router.post("/v1/director/plan", response_model=DirectorPlanResponse)
def create_director_plan(payload: DirectorPlanRequest) -> dict:
    try:
        # The graph can only produce a draft plan. It has no shell or direct tool access.
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
            "plan": plan,
            "status": state.get("status", "awaiting_approval"),
            "model": settings.ollama_model,
            "approval_required": True,
        }
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/projects/{project_id}/jobs/{job_id}/files/{filename}")
def download_job_output(project_id: str, job_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename or "/" in filename or chr(92) in filename:
        raise HTTPException(status_code=400, detail="Invalid output filename.")

    try:
        job = get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc

    if job["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Job not found in this project.")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Job output is not ready.")

    result = job.get("result") or {}
    output_directory = result.get("files_directory")
    if isinstance(output_directory, str):
        allowed_names = {
            item.get("saved_as")
            for item in result.get("outputs", [])
            if isinstance(item, dict) and isinstance(item.get("saved_as"), str)
        }
        if filename not in allowed_names:
            raise HTTPException(status_code=404, detail="Output file is not part of this job.")
        relative_path = Path(output_directory) / filename
    else:
        relative_value = result.get("relative_path") or result.get("path")
        if not isinstance(relative_value, str):
            raise HTTPException(status_code=404, detail="This job has no downloadable file.")
        relative_path = Path(relative_value)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise HTTPException(status_code=400, detail="Invalid recorded output path.")
        if relative_path.name != filename:
            raise HTTPException(status_code=404, detail="Output file is not part of this job.")

    try:
        root = project_dir(project_id).resolve()
        output_path = (root / relative_path).resolve()
        if not output_path.is_relative_to(root) or not output_path.is_file():
            raise HTTPException(status_code=404, detail="Output file not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project output path.") from exc

    media_type = mimetypes.guess_type(output_path.name)[0] or "application/octet-stream"
    return FileResponse(output_path, media_type=media_type, filename=output_path.name)


def _job_file_url(project_id: str, job_id: str, filename: str) -> str:
    return (
        f"/v1/projects/{project_id}/jobs/{job_id}/files/"
        f"{quote(filename, safe='')}"
    )
