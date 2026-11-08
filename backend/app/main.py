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
    version="0.3.0",
    description=(
        "Phase 1 control plane: durable projects and assets, transactional job outbox, "
        "Celery workers, and the Phase 0.5 deterministic media pipeline."
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
    return {
        "phase": "1",
        "media_ingestion": True,
        "ffprobe": True,
        "proxy_generation": True,
        "scene_detection": True,
        "opentimelineio": True,
        "ffmpeg_render": True,
        "database_backed_projects_and_assets": True,
        "durable_job_api": True,
        "transactional_outbox": True,
        "worker_execution": "Celery CPU queue; requires a running worker and scheduler",
        "database_migrations_required": True,
        "local_whisperx": "optional_not_installed_by_default",
        "gpu_worker_enabled": False,
        "video_generation_enabled": False,
        "external_model_apis_enabled": False,
        "note": (
            "Phase 1 adds durable metadata and job orchestration. Model inference and GPU workers "
            "are not enabled. Media binaries remain in the private MEDIA_ROOT volume."
        ),
    }
