import hmac
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.api.router import api_router
from app.core.config import settings
from app.db.session import get_engine

app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    docs_url=None if settings.app_env.lower() == "production" else "/docs",
    redoc_url=None if settings.app_env.lower() == "production" else "/redoc",
    openapi_url=None if settings.app_env.lower() == "production" else "/openapi.json",
    description=(
        "Arabic-first video production API with durable sequential workflows, "
        "approved local model adapters, and deterministic media processing."
    ),
)
app.include_router(api_router)

@app.middleware("http")
async def enforce_private_api_access(request, call_next):
    path = request.url.path
    response = None
    if path.startswith("/v1/"):
        token = settings.studio_api_token
        production = settings.app_env.lower() == "production"
        if production and (not token or len(token) < 32):
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Production API access is disabled until STUDIO_API_TOKEN "
                        "is configured with at least 32 characters."
                    )
                },
            )
        elif token and path != "/v1/auth/session":
            authorization = request.headers.get("authorization", "")
            scheme, separator, supplied = authorization.partition(" ")
            if (
                not separator
                or scheme.lower() != "bearer"
                or not hmac.compare_digest(supplied, token)
            ):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            "A valid bearer token is required to access "
                            "this private Studio API."
                        )
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
    if response is None:
        response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


STUDIO_UI_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount(
    "/studio",
    StaticFiles(directory=str(STUDIO_UI_DIR), html=True),
    name="studio-ui",
)


@app.get("/", include_in_schema=False)
def studio_home() -> RedirectResponse:
    return RedirectResponse(url="/studio/", status_code=307)


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
        "worker_execution": (
            "Celery control, CPU, speech and single-concurrency model queues; "
            "workers must be running"
        ),
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
        "phase_6_10_quality_gate": {
            "implemented": True,
            "black_and_frozen_frame_detection": True,
            "audio_silence_and_peak_detection": True,
            "full_media_decode": True,
            "sha256_bound_export_report": True,
            "export_blocked_on_critical_errors": True,
        },
        "phase_7_security_operations": {
            "bearer_token_authentication": bool(settings.studio_api_token),
            "production_fails_closed_without_token": True,
            "backup_restore_helpers_added": True,
            "target_host_restore_drill_verified": False,
        },
        "external_model_apis_enabled": False,
        "model_weights_downloaded_automatically": False,
        "note": (
            "A configured runtime or implemented adapter is not proof of a successful model run. "
            "Real inference, output validation and target-GPU acceptance must be recorded "
            "separately for every enabled model."
        ),
    }
