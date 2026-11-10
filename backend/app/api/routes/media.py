from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.integrations.ffmpeg import MediaToolError, probe_media
from app.media.pipeline import (
    OptionalMediaDependencyError,
    build_multi_source_timeline,
    build_timeline,
    create_proxy,
    detect_scenes,
    render_multi_source_timeline,
    render_timeline,
)
from app.media.speech import SpeechDependencyError, transcribe_local
from app.media.storage import (
    asset_dir,
    atomic_write_json,
    delete_asset_record,
    get_asset,
    get_project,
    ingest_file_stream,
    list_assets,
    media_root,
    project_dir,
    read_json,
    update_asset_manifest,
)

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["media"])


class ProxyRequest(BaseModel):
    max_width: int = Field(default=1280, ge=160, le=1920)
    fps: int = Field(default=30, ge=1, le=60)


class SceneRequest(BaseModel):
    threshold: float = Field(default=27.0, gt=0, le=100)
    min_scene_len: int = Field(default=15, ge=1, le=1000)


class TranscriptionRequest(BaseModel):
    language: str = Field(default="ar", pattern="^(ar|en)$")
    model_name: str = Field(default="small", pattern="^(tiny|base|small|medium|large-v3)$")
    device: str = Field(default="cpu", pattern="^cpu$")
    align: bool = False


class TimelineRequest(BaseModel):
    fps: int = Field(default=30, ge=1, le=120)


class RenderRequest(BaseModel):
    width: int = Field(default=1920, ge=320, le=3840)
    height: int = Field(default=1080, ge=240, le=2160)
    fps: int = Field(default=30, ge=1, le=60)
    crf: int = Field(default=20, ge=0, le=35)


class MultiSourceClipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    source_start_seconds: float = Field(ge=0, le=3600)
    duration_seconds: float = Field(gt=0, le=3600)


class ProjectTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fps: int = Field(default=30, ge=1, le=60)
    clips: list[MultiSourceClipRequest] = Field(default_factory=list, max_length=100)


class ProjectTimelineRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1920, ge=320, le=3840)
    height: int = Field(default=1080, ge=240, le=2160)
    fps: int = Field(default=30, ge=1, le=60)
    crf: int = Field(default=20, ge=0, le=35)


def _source_path(project_id: str, asset_id: str) -> tuple[dict, Path]:
    try:
        asset = get_asset(project_id, asset_id)
        project_dir(project_id)
        path = (asset_dir(project_id, asset_id) / asset["stored_filename"]).resolve()
        if not path.is_relative_to(asset_dir(project_id, asset_id)) or not path.is_file():
            raise FileNotFoundError("Asset file not found.")
        return asset, path
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project or asset not found.") from exc


@router.post("/assets", status_code=201)
async def upload_asset(project_id: str, file: Annotated[UploadFile, File()]) -> dict:
    try:
        get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc

    max_bytes = settings.max_upload_gb * 1024 * 1024 * 1024
    try:
        manifest, stored_path = await run_in_threadpool(
            ingest_file_stream,
            project_id,
            file.filename or "",
            file.file,
            max_bytes=max_bytes,
        )
        metadata = await run_in_threadpool(probe_media, stored_path)
        streams = metadata.get("streams", [])
        if not streams:
            raise ValueError("Uploaded file is not a supported media file.")
        if manifest["asset_type"] == "video" and not any(
            stream.get("codec_type") == "video" for stream in streams
        ):
            raise ValueError("Uploaded file does not contain a video stream.")
        manifest = await run_in_threadpool(
            update_asset_manifest,
            project_id,
            manifest["id"],
            {"probe": metadata, "status": "ready"},
        )
        return manifest
    except (ValueError, MediaToolError) as exc:
        if "manifest" in locals():
            await run_in_threadpool(delete_asset_record, project_id, manifest["id"])
            directory = asset_dir(project_id, manifest["id"])
            if directory.exists():
                for child in directory.iterdir():
                    if child.is_file():
                        child.unlink(missing_ok=True)
                directory.rmdir()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()


@router.get("/assets")
def get_assets(project_id: str) -> list[dict]:
    try:
        get_project(project_id)
        return list_assets(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/assets/{asset_id}")
def get_asset_route(project_id: str, asset_id: str) -> dict:
    asset, _ = _source_path(project_id, asset_id)
    return asset


@router.post("/assets/{asset_id}/proxy")
async def create_proxy_route(project_id: str, asset_id: str, payload: ProxyRequest) -> dict:
    asset, source = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(
            status_code=422, detail="Proxy generation is only available for videos."
        )
    destination = asset_dir(project_id, asset_id) / "proxy.mp4"
    try:
        result = await run_in_threadpool(
            create_proxy, source, destination, max_width=payload.max_width, fps=payload.fps
        )
        await run_in_threadpool(update_asset_manifest, project_id, asset_id, {"proxy": result})
        return result
    except (ValueError, MediaToolError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/assets/{asset_id}/scenes")
async def detect_scenes_route(project_id: str, asset_id: str, payload: SceneRequest) -> dict:
    asset, source = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(status_code=422, detail="Scene detection requires a video.")
    try:
        scenes = await run_in_threadpool(
            detect_scenes, source, threshold=payload.threshold, min_scene_len=payload.min_scene_len
        )
    except OptionalMediaDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, MediaToolError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manifest = {
        "asset_id": asset_id,
        "project_id": project_id,
        "threshold": payload.threshold,
        "min_scene_len": payload.min_scene_len,
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    await run_in_threadpool(
        atomic_write_json, asset_dir(project_id, asset_id) / "scenes.json", manifest
    )
    return manifest


@router.post("/assets/{asset_id}/transcription")
async def transcribe_route(
    project_id: str, asset_id: str, payload: TranscriptionRequest
) -> dict:
    asset, source = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(status_code=422, detail="Transcription requires a video with audio.")
    media_info = await run_in_threadpool(probe_media, source)
    if not any(stream.get("codec_type") == "audio" for stream in media_info.get("streams", [])):
        raise HTTPException(status_code=422, detail="The video does not contain an audio stream.")
    destination = asset_dir(project_id, asset_id) / "transcription.json"
    cache_dir = media_root() / "models" / "whisperx"
    try:
        return await run_in_threadpool(
            transcribe_local,
            source,
            destination,
            language=payload.language,
            model_name=payload.model_name,
            device=payload.device,
            cache_dir=cache_dir,
            align=payload.align,
        )
    except SpeechDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/assets/{asset_id}/timeline")
async def create_timeline_route(
    project_id: str, asset_id: str, payload: TimelineRequest
) -> dict:
    asset, source = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(status_code=422, detail="Timeline generation requires a video.")
    scenes_path = asset_dir(project_id, asset_id) / "scenes.json"
    try:
        if scenes_path.is_file():
            scene_data = read_json(scenes_path)
            scenes = scene_data["scenes"]
        else:
            scenes = await run_in_threadpool(detect_scenes, source)
        manifest, timeline_path = await run_in_threadpool(
            build_timeline,
            project_id=project_id,
            asset_id=asset_id,
            source=source,
            scenes=scenes,
            fps=payload.fps,
        )
        relative_timeline = timeline_path.relative_to(project_dir(project_id))
        return {**manifest, "timeline_path": str(relative_timeline)}
    except OptionalMediaDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, MediaToolError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/assets/{asset_id}/render")
async def render_route(
    project_id: str, asset_id: str, payload: RenderRequest
) -> dict:
    _, source = _source_path(project_id, asset_id)
    timeline_path = project_dir(project_id) / "timelines" / f"{asset_id}.json"
    if not timeline_path.is_file():
        raise HTTPException(status_code=404, detail="Create the timeline before rendering.")
    try:
        timeline = read_json(timeline_path)
        if timeline.get("project_id") != str(UUID(project_id)):
            raise HTTPException(status_code=400, detail="Timeline project mismatch.")
        if timeline.get("asset_id") != str(UUID(asset_id)):
            raise HTTPException(status_code=400, detail="Timeline asset mismatch.")
        destination = project_dir(project_id) / "exports" / f"{asset_id}-preview.mp4"
        result = await run_in_threadpool(
            render_timeline,
            source=source,
            timeline_manifest=timeline,
            destination=destination,
            width=payload.width,
            height=payload.height,
            fps=payload.fps,
            crf=payload.crf,
        )
        return {
            **result,
            "download_url": f"/v1/projects/{project_id}/assets/{asset_id}/rendered",
        }
    except (ValueError, MediaToolError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/rendered")
def download_render(project_id: str, asset_id: str) -> FileResponse:
    _source_path(project_id, asset_id)
    rendered = project_dir(project_id) / "exports" / f"{asset_id}-preview.mp4"
    if not rendered.is_file():
        raise HTTPException(status_code=404, detail="Rendered video not found.")
    return FileResponse(rendered, media_type="video/mp4", filename=rendered.name)


@router.get("/timeline")
def get_project_timeline(project_id: str) -> dict:
    try:
        get_project(project_id)
        timeline_path = (
            project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        )
        if not timeline_path.is_file():
            raise HTTPException(status_code=404, detail="Project timeline not found.")
        return read_json(timeline_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.put("/timeline")
async def save_project_timeline(project_id: str, payload: ProjectTimelineRequest) -> dict:
    try:
        get_project(project_id)
        if not payload.clips:
            timeline_dir = project_dir(project_id) / "timelines"
            timeline_dir.mkdir(parents=True, exist_ok=True)
            timeline_id = str(UUID(project_id))
            manifest = {
                "schema_version": 2,
                "timeline_id": timeline_id,
                "project_id": timeline_id,
                "fps": payload.fps,
                "duration_seconds": 0,
                "clips": [],
                "otio_filename": None,
                "status": "draft",
            }
            atomic_write_json(timeline_dir / f"project-{timeline_id}.json", manifest)
            (timeline_dir / f"project-{timeline_id}.otio").unlink(missing_ok=True)
            return {**manifest, "timeline_path": None}

        sources: dict[str, Path] = {}
        clips: list[dict] = []
        for clip in payload.clips:
            asset_id = str(clip.asset_id)
            asset, source = _source_path(project_id, asset_id)
            if asset["asset_type"] != "video":
                raise HTTPException(
                    status_code=422,
                    detail="Multi-source timeline clips must reference video assets.",
                )
            sources[asset_id] = source
            clips.append(clip.model_dump(mode="json"))

        manifest, timeline_path = await run_in_threadpool(
            build_multi_source_timeline,
            project_id=project_id,
            sources=sources,
            clips=clips,
            fps=payload.fps,
        )
        return {
            **manifest,
            "timeline_path": str(timeline_path.relative_to(project_dir(project_id))),
        }
    except OptionalMediaDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, MediaToolError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.post("/timeline/render")
async def render_project_timeline_route(
    project_id: str, payload: ProjectTimelineRenderRequest
) -> dict:
    try:
        get_project(project_id)
        timeline_path = (
            project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        )
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404, detail="Save the project timeline before rendering."
            )
        timeline = read_json(timeline_path)
        if timeline.get("project_id") != str(UUID(project_id)):
            raise HTTPException(status_code=400, detail="Timeline project mismatch.")

        sources: dict[str, Path] = {}
        for clip in timeline.get("clips", []):
            asset_id = str(UUID(str(clip["asset_id"])))
            if asset_id in sources:
                continue
            asset, source = _source_path(project_id, asset_id)
            if asset["asset_type"] != "video":
                raise HTTPException(
                    status_code=422,
                    detail="Multi-source timeline clips must reference video assets.",
                )
            sources[asset_id] = source

        destination = (
            project_dir(project_id)
            / "exports"
            / f"project-{UUID(project_id)}-preview.mp4"
        )
        result = await run_in_threadpool(
            render_multi_source_timeline,
            sources=sources,
            timeline_manifest=timeline,
            destination=destination,
            width=payload.width,
            height=payload.height,
            fps=payload.fps,
            crf=payload.crf,
        )
        return {
            **result,
            "download_url": f"/v1/projects/{project_id}/timeline/rendered",
        }
    except OptionalMediaDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, MediaToolError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Project or timeline source not found."
        ) from exc


@router.get("/timeline/rendered")
def download_project_timeline_render(project_id: str) -> FileResponse:
    try:
        get_project(project_id)
        rendered = (
            project_dir(project_id)
            / "exports"
            / f"project-{UUID(project_id)}-preview.mp4"
        )
        if not rendered.is_file():
            raise HTTPException(status_code=404, detail="Rendered project timeline not found.")
        return FileResponse(rendered, media_type="video/mp4", filename=rendered.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
