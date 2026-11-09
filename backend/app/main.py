from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException
from sqlalchemy import inspect, text

from app.api.router import api_router
from app.core.config import settings
from app.db.session import get_engine

app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    description=(
        "Arabic-first video production API with durable sequential workflows, "
        "approved local model adapters, and deterministic media processing."
    ),
)
app.include_router(api_router)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get("/ready", tags=["system"])
def readiness() -> dict:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if not inspect(connection).has_table("alembic_version"):
                raise HTTPException(
                    status_code=503,
                    detail="Database migrations have not been applied.",
                )
            revision_rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
            current = set(revision_rows)
        config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        migration_config = Config(str(config_path))
        migration_config.set_main_option(
            "script_location", str(config_path.parent / "migrations")
        )
        expected = set(ScriptDirectory.from_config(migration_config).get_heads())
        if current != expected:
            raise HTTPException(
                status_code=503,
                detail="Database schema is not at the latest migration revision.",
            )
        return {"status": "ready", "database": "ok", "migrations": "current"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready.") from exc


@app.get("/v1/system/capabilities", tags=["system"])
def capabilities() -> dict:
    """Report implemented control-plane features without overstating model readiness."""
    return {
        "phase": "3",
        "implemented_phases": ["0.5", "1", "2", "3"],
        "media_ingestion": True,
        "ffprobe": True,
        "proxy_generation": True,
        "scene_detection": True,
        "opentimelineio": True,
        "ffmpeg_render": True,
        "database_backed_projects_and_assets": True,
        "durable_job_api": True,
        "transactional_outbox": True,
        "worker_execution": "Celery control, CPU, speech and single-concurrency model queues; workers must be running",
        "workflow_engine": {
            "implemented": True,
            "execution_mode": "sequential_v1",
            "approval_required_before_execution": True,
            "general_branching_dag": False,
        },
        "model_adapters": True,
        "model_runtimes": {
            "ollama_configured": bool(settings.ollama_base_url),
            "comfyui_configured": bool(settings.comfyui_base_url),
            "real_inference_verified": False,
        },
        "database_migrations_required": True,
        "local_whisperx": "optional_worker_and_model_required",
        "gpu_scheduling": "single_concurrency_model_queue; target_hardware_not_benchmarked",
        "video_generation_adapter": True,
        "video_generation_inference_verified": False,
        "external_model_apis_enabled": False,
        "model_weights_downloaded_automatically": False,
        "note": (
            "A configured runtime or implemented adapter is not proof of a successful model run. "
            "Real inference, output validation and target-GPU acceptance must be recorded "
            "separately for every enabled model."
        ),
    }
