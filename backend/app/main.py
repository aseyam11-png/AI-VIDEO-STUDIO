from fastapi import FastAPI

from app.core.config import settings
from app.orchestration.graph import build_graph

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Single-user AI video production control plane. Initial scaffold only.",
)
workflow = build_graph()


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get("/v1/system/capabilities", tags=["system"])
def capabilities() -> dict:
    return {
        "phase": "scaffold",
        "gpu_worker_enabled": False,
        "generation_enabled": False,
        "external_model_apis_enabled": False,
        "approval_required_before_generation": settings.require_approval_before_generation,
        "approval_required_before_export": settings.require_approval_before_export,
        "note": "No model weights or third-party inference workers are installed by this scaffold.",
    }
