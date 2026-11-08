from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Phase 0.5 deterministic media pipeline: ingest, probe, proxy, "
        "scene detection, optional local speech transcription, OTIO timeline and FFmpeg render."
    ),
)
app.include_router(api_router)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get("/v1/system/capabilities", tags=["system"])
def capabilities() -> dict:
    return {
        "phase": "0.5",
        "media_ingestion": True,
        "ffprobe": True,
        "proxy_generation": True,
        "scene_detection": True,
        "opentimelineio": True,
        "ffmpeg_render": True,
        "local_whisperx": "optional_not_installed_by_default",
        "gpu_worker_enabled": False,
        "video_generation_enabled": False,
        "external_model_apis_enabled": False,
        "note": (
            "The deterministic media pipeline is available when FFmpeg and the media extra are installed. "
            "WhisperX is an explicit optional local worker; no external transcription API is used."
        ),
    }
