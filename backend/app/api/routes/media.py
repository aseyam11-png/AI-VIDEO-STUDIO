import math
import mimetypes
from pathlib import Path
from typing import Annotated, Literal
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
from app.media.quality import (
    analyze_video_quality,
    persist_quality_report,
    quality_report_matches_file,
    quality_report_path,
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


class ProjectSubtitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_alignment: bool = False


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
    clips: list[MultiSourceClipRequest] = Field(max_length=100)


class ProjectTimelineRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1920, ge=320, le=3840)
    height: int = Field(default=1080, ge=240, le=2160)
    fps: int = Field(default=30, ge=1, le=60)
    crf: int = Field(default=20, ge=0, le=35)


class AudioMixClipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    role: Literal["voice", "music", "sfx"]
    timeline_start_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    source_start_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    gain_db: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    fade_in_seconds: float = Field(default=0, ge=0, le=30, allow_inf_nan=False)
    fade_out_seconds: float = Field(default=0, ge=0, le=30, allow_inf_nan=False)
    denoise: bool = False


class AudioMixLevels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    music: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    sfx: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)


class ProjectAudioMixRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_audio_enabled: bool = True
    source_audio_denoise: bool = False
    levels_db: AudioMixLevels = Field(default_factory=AudioMixLevels)
    clips: list[AudioMixClipRequest] = Field(default_factory=list, max_length=100)

class VisualTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_clip_id: UUID
    style: Literal["dip_to_black", "flash_white"]
    duration_seconds: float = Field(gt=0, le=2, allow_inf_nan=False)


class VisualTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=280)
    start_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    font_size: int = Field(default=48, ge=18, le=120)
    color: str = Field(default="#FFFFFF", pattern="^#[0-9A-Fa-f]{6}$")
    position: Literal["top", "center", "bottom"] = "bottom"
    animation: Literal["none", "fade", "slide_up"] = "fade"
    background: bool = True


class ImageOverlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overlay_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    start_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    width_ratio: float = Field(default=0.2, ge=0.05, le=0.6, allow_inf_nan=False)
    opacity: float = Field(default=1.0, ge=0.1, le=1.0, allow_inf_nan=False)
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = (
        "top_right"
    )
    animation: Literal["none", "slide_up"] = "none"


class ProjectVisualEffectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transitions: list[VisualTransitionRequest] = Field(default_factory=list, max_length=99)
    titles: list[VisualTitleRequest] = Field(default_factory=list, max_length=20)
    overlays: list[ImageOverlayRequest] = Field(default_factory=list, max_length=20)


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
        if manifest["asset_type"] == "audio" and not any(
            stream.get("codec_type") == "audio" for stream in streams
        ):
            raise ValueError("Uploaded file does not contain an audio stream.")
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


@router.get("/assets/{asset_id}/file")
def download_asset_file(project_id: str, asset_id: str) -> FileResponse:
    """Serve only the selected asset file after project-scoped path validation."""
    asset, source = _source_path(project_id, asset_id)
    media_type = mimetypes.guess_type(asset.get("original_filename", ""))[0]
    return FileResponse(
        source,
        media_type=media_type or "application/octet-stream",
        filename=asset.get("original_filename") or source.name,
    )


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


@router.get("/assets/{asset_id}/scenes")
def get_scenes_route(project_id: str, asset_id: str) -> dict:
    asset, _ = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(status_code=422, detail="Scene results require a video asset.")
    scenes_path = asset_dir(project_id, asset_id) / "scenes.json"
    if not scenes_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Scene detection has not been run for this asset.",
        )
    manifest = read_json(scenes_path)
    if (
        manifest.get("project_id") != project_id
        or manifest.get("asset_id") != asset_id
        or not isinstance(manifest.get("scenes"), list)
    ):
        raise HTTPException(
            status_code=409,
            detail="Saved scene manifest is invalid or belongs to another asset.",
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
        result = await run_in_threadpool(
            transcribe_local,
            source,
            destination,
            language=payload.language,
            model_name=payload.model_name,
            device=payload.device,
            cache_dir=cache_dir,
            align=payload.align,
        )
        timeline_id = str(UUID(project_id))
        timeline_dir = project_dir(project_id) / "timelines"
        (timeline_dir / f"project-{timeline_id}.srt").unlink(missing_ok=True)
        (timeline_dir / f"project-{timeline_id}-subtitles.json").unlink(missing_ok=True)
        return result
    except SpeechDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/transcription")
def get_transcription_route(project_id: str, asset_id: str) -> dict:
    asset, _ = _source_path(project_id, asset_id)
    if asset["asset_type"] != "video":
        raise HTTPException(status_code=422, detail="Transcription results require a video asset.")
    transcription_path = asset_dir(project_id, asset_id) / "transcription.json"
    if not transcription_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Speech transcription has not been run for this asset.",
        )
    manifest = read_json(transcription_path)
    if (
        manifest.get("language") not in {"ar", "en"}
        or not isinstance(manifest.get("segments"), list)
    ):
        raise HTTPException(status_code=409, detail="Saved transcription manifest is invalid.")
    return manifest


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
        source_has_audio = any(
            stream.get("codec_type") == "audio"
            for stream in probe_media(source).get("streams", [])
        )
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
        expected_duration = sum(
            float(clip["duration_seconds"]) for clip in timeline.get("clips", [])
        )
        quality_report = await run_in_threadpool(
            analyze_video_quality,
            destination,
            expected_duration_seconds=expected_duration,
            expected_width=payload.width,
            expected_height=payload.height,
            expected_fps=float(payload.fps),
            expected_audio=source_has_audio,
        )
        await run_in_threadpool(
            persist_quality_report, project_id, f"asset-{asset_id}", quality_report
        )
        if not quality_report["can_export"]:
            destination.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Quality gate blocked this export. Review the quality report.",
                    "quality_report": quality_report,
                },
            )
        return {
            **result,
            "quality_report": quality_report,
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
    report_path = quality_report_path(project_id, f"asset-{asset_id}")
    if not report_path.is_file():
        raise HTTPException(status_code=409, detail="Video has no completed quality report.")
    report = read_json(report_path)
    if not quality_report_matches_file(report, rendered):
        raise HTTPException(
            status_code=409,
            detail="Quality report is stale or does not authorize this output.",
        )
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
            preview = (
                project_dir(project_id)
                / "exports"
                / f"project-{timeline_id}-preview.mp4"
            )
            preview.unlink(missing_ok=True)
            (timeline_dir / f"project-{timeline_id}.srt").unlink(missing_ok=True)
            (timeline_dir / f"project-{timeline_id}-subtitles.json").unlink(missing_ok=True)
            _audio_mix_path(project_id).unlink(missing_ok=True)
            _visual_effects_path(project_id).unlink(missing_ok=True)
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
        preview = (
            project_dir(project_id)
            / "exports"
            / f"project-{UUID(project_id)}-preview.mp4"
        )
        preview.unlink(missing_ok=True)
        timeline_dir = project_dir(project_id) / "timelines"
        timeline_id = str(UUID(project_id))
        (timeline_dir / f"project-{timeline_id}.srt").unlink(missing_ok=True)
        (timeline_dir / f"project-{timeline_id}-subtitles.json").unlink(missing_ok=True)
        _audio_mix_path(project_id).unlink(missing_ok=True)
        _visual_effects_path(project_id).unlink(missing_ok=True)
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

        audio_mix_path = _audio_mix_path(project_id)
        audio_mix = (
            read_json(audio_mix_path)
            if audio_mix_path.is_file()
            else _empty_audio_mix(project_id, timeline)
        )
        if (
            audio_mix.get("schema_version") != 1
            or audio_mix.get("project_id") != str(UUID(project_id))
            or not isinstance(audio_mix.get("clips"), list)
        ):
            raise HTTPException(status_code=409, detail="Saved audio mix manifest is invalid.")
        audio_sources: dict[str, Path] = {}
        for audio_clip in audio_mix.get("clips", []):
            asset_id = str(UUID(str(audio_clip["asset_id"])))
            if asset_id in audio_sources:
                continue
            asset, source = _source_path(project_id, asset_id)
            if asset["asset_type"] not in {"audio", "video"}:
                raise HTTPException(
                    status_code=422,
                    detail="Audio mix sources must be audio files or videos with an audio stream.",
                )
            if not any(
                stream.get("codec_type") == "audio"
                for stream in probe_media(source).get("streams", [])
            ):
                raise HTTPException(status_code=422, detail="Audio mix source has no audio stream.")
            audio_sources[asset_id] = source

        destination = (
            project_dir(project_id)
            / "exports"
            / f"project-{UUID(project_id)}-preview.mp4"
        )
        visual_path = _visual_effects_path(project_id)
        visual_effects = (
            read_json(visual_path)
            if visual_path.is_file()
            else _empty_visual_effects(project_id, timeline)
        )
        if (
            visual_effects.get("schema_version") != 1
            or visual_effects.get("project_id") != str(UUID(project_id))
            or not isinstance(visual_effects.get("transitions"), list)
            or not isinstance(visual_effects.get("titles"), list)
            or not isinstance(visual_effects.get("overlays"), list)
        ):
            raise HTTPException(status_code=409, detail="Saved visual effects manifest is invalid.")
        overlay_sources: dict[str, Path] = {}
        for overlay in visual_effects.get("overlays", []):
            asset_id = str(UUID(str(overlay["asset_id"])))
            if asset_id in overlay_sources:
                continue
            asset, source = _source_path(project_id, asset_id)
            if asset["asset_type"] != "image":
                raise HTTPException(
                    status_code=422, detail="Visual overlays must reference image assets."
                )
            if not any(
                stream.get("codec_type") == "video"
                for stream in probe_media(source).get("streams", [])
            ):
                raise HTTPException(
                    status_code=422, detail="Overlay image has no decodable image stream."
                )
            overlay_sources[asset_id] = source

        result = await run_in_threadpool(
            render_multi_source_timeline,
            sources=sources,
            timeline_manifest=timeline,
            destination=destination,
            width=payload.width,
            height=payload.height,
            fps=payload.fps,
            crf=payload.crf,
            audio_mix_manifest=audio_mix,
            audio_sources=audio_sources,
            visual_effects_manifest=visual_effects,
            overlay_sources=overlay_sources,
        )
        source_audio_required = bool(audio_mix.get("clips")) or (
            bool(audio_mix.get("source_audio_enabled", True))
            and any(
                any(
                    stream.get("codec_type") == "audio"
                    for stream in probe_media(source).get("streams", [])
                )
                for source in sources.values()
            )
        )
        quality_report = await run_in_threadpool(
            analyze_video_quality,
            destination,
            expected_duration_seconds=sum(
                float(clip["duration_seconds"]) for clip in timeline.get("clips", [])
            ),
            expected_width=payload.width,
            expected_height=payload.height,
            expected_fps=float(payload.fps),
            expected_audio=source_audio_required,
        )
        await run_in_threadpool(
            persist_quality_report,
            project_id,
            f"project-{UUID(project_id)}",
            quality_report,
        )
        if not quality_report["can_export"]:
            destination.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Quality gate blocked this export. Review the quality report.",
                    "quality_report": quality_report,
                },
            )
        return {
            **result,
            "quality_report": quality_report,
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


def _visual_effects_path(project_id: str) -> Path:
    timeline_id = str(UUID(project_id))
    return project_dir(project_id) / "timelines" / f"project-{timeline_id}-visual-effects.json"


def _empty_visual_effects(project_id: str, timeline: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "project_id": str(UUID(project_id)),
        "timeline_duration_seconds": float((timeline or {}).get("duration_seconds", 0)),
        "transitions": [],
        "titles": [],
        "overlays": [],
        "status": "draft",
    }


@router.get("/visual-effects")
def get_project_visual_effects(project_id: str) -> dict:
    try:
        get_project(project_id)
        timeline_path = project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404, detail="Save the video timeline before editing visual effects."
            )
        timeline = read_json(timeline_path)
        path = _visual_effects_path(project_id)
        if not path.is_file():
            return _empty_visual_effects(project_id, timeline)
        manifest = read_json(path)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("project_id") != str(UUID(project_id))
            or not all(
                isinstance(manifest.get(key), list)
                for key in ("transitions", "titles", "overlays")
            )
        ):
            raise HTTPException(status_code=409, detail="Saved visual effects manifest is invalid.")
        return manifest
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.put("/visual-effects")
def save_project_visual_effects(
    project_id: str, payload: ProjectVisualEffectsRequest
) -> dict:
    try:
        get_project(project_id)
        timeline_path = project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404, detail="Save the video timeline before editing visual effects."
            )
        timeline = read_json(timeline_path)
        if timeline.get("project_id") != str(UUID(project_id)):
            raise HTTPException(
                status_code=409, detail="Video timeline belongs to another project."
            )
        timeline_duration = float(timeline.get("duration_seconds", 0))
        if (
            not math.isfinite(timeline_duration)
            or timeline_duration <= 0
            or timeline_duration > 3600
        ):
            raise HTTPException(status_code=409, detail="Video timeline duration is invalid.")

        clips = timeline.get("clips", [])
        if not isinstance(clips, list) or not clips:
            raise HTTPException(
                status_code=422, detail="Add and save video clips before visual effects."
            )
        clip_positions: dict[str, int] = {}
        clip_durations: dict[str, float] = {}
        for index, clip in enumerate(clips):
            try:
                clip_id = str(UUID(str(clip["clip_id"])))
                duration = float(clip["duration_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=409, detail="Saved video timeline contains an invalid clip."
                ) from exc
            if clip_id in clip_positions or not math.isfinite(duration) or duration <= 0:
                raise HTTPException(
                    status_code=409,
                    detail="Saved video timeline contains duplicate or invalid clips.",
                )
            clip_positions[clip_id] = index
            clip_durations[clip_id] = duration

        transitions = []
        transition_usage: dict[str, float] = {}
        seen_transition_clips: set[str] = set()
        for item in payload.transitions:
            clip_id = str(item.after_clip_id)
            index = clip_positions.get(clip_id)
            if index is None or index >= len(clips) - 1 or clip_id in seen_transition_clips:
                raise HTTPException(
                    status_code=422,
                    detail="Choose a valid clip boundary for each transition.",
                )
            next_clip_id = str(UUID(str(clips[index + 1]["clip_id"])))
            duration = item.duration_seconds
            if duration > min(clip_durations[clip_id], clip_durations[next_clip_id]) / 2:
                raise HTTPException(
                    status_code=422,
                    detail="Transition duration cannot exceed half of either adjacent clip.",
                )
            seen_transition_clips.add(clip_id)
            transition_usage[clip_id] = transition_usage.get(clip_id, 0) + duration
            transition_usage[next_clip_id] = transition_usage.get(next_clip_id, 0) + duration
            transitions.append(item.model_dump(mode="json"))
        if any(
            used > clip_durations[clip_id] + 0.000001
            for clip_id, used in transition_usage.items()
        ):
            raise HTTPException(
                status_code=422,
                detail="Adjacent transitions cannot overlap inside the same clip.",
            )

        titles = []
        seen_title_ids: set[str] = set()
        for item in payload.titles:
            title_id = str(item.title_id)
            if (
                title_id in seen_title_ids
                or not item.text.strip()
                or item.start_seconds + item.duration_seconds > timeline_duration + 0.001
            ):
                raise HTTPException(
                    status_code=422,
                    detail="A title has duplicate identity or invalid text/timing.",
                )
            seen_title_ids.add(title_id)
            titles.append(item.model_dump(mode="json"))

        overlays = []
        seen_overlay_ids: set[str] = set()
        for item in payload.overlays:
            overlay_id = str(item.overlay_id)
            asset, source = _source_path(project_id, str(item.asset_id))
            if asset["asset_type"] != "image":
                raise HTTPException(
                    status_code=422, detail="Visual overlays must reference image assets."
                )
            if not any(
                stream.get("codec_type") == "video"
                for stream in probe_media(source).get("streams", [])
            ):
                raise HTTPException(
                    status_code=422, detail="Overlay image has no decodable image stream."
                )
            if (
                overlay_id in seen_overlay_ids
                or item.start_seconds + item.duration_seconds > timeline_duration + 0.001
            ):
                raise HTTPException(
                    status_code=422,
                    detail="An image overlay has duplicate identity or invalid timing.",
                )
            seen_overlay_ids.add(overlay_id)
            overlays.append(item.model_dump(mode="json"))

        manifest = {
            "schema_version": 1,
            "project_id": str(UUID(project_id)),
            "timeline_duration_seconds": round(timeline_duration, 6),
            "transitions": transitions,
            "titles": titles,
            "overlays": overlays,
            "status": "draft",
        }
        path = _visual_effects_path(project_id)
        atomic_write_json(path, manifest)
        preview = project_dir(project_id) / "exports" / f"project-{UUID(project_id)}-preview.mp4"
        preview.unlink(missing_ok=True)
        return manifest
    except MediaToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Project, timeline or overlay asset not found.",
        ) from exc


def _audio_mix_path(project_id: str) -> Path:
    timeline_id = str(UUID(project_id))
    return project_dir(project_id) / "timelines" / f"project-{timeline_id}-audio-mix.json"


def _empty_audio_mix(project_id: str, timeline: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "project_id": str(UUID(project_id)),
        "timeline_duration_seconds": float((timeline or {}).get("duration_seconds", 0)),
        "source_audio_enabled": True,
        "source_audio_denoise": False,
        "levels_db": {"voice": 0.0, "music": 0.0, "sfx": 0.0},
        "clips": [],
        "status": "draft",
    }


@router.get("/audio-mix")
def get_project_audio_mix(project_id: str) -> dict:
    try:
        get_project(project_id)
        timeline_path = project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Save the video timeline before editing audio.",
            )
        timeline = read_json(timeline_path)
        path = _audio_mix_path(project_id)
        if not path.is_file():
            return _empty_audio_mix(project_id, timeline)
        manifest = read_json(path)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("project_id") != str(UUID(project_id))
            or not isinstance(manifest.get("clips"), list)
        ):
            raise HTTPException(status_code=409, detail="Saved audio mix manifest is invalid.")
        return manifest
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.put("/audio-mix")
def save_project_audio_mix(project_id: str, payload: ProjectAudioMixRequest) -> dict:
    try:
        get_project(project_id)
        timeline_path = project_dir(project_id) / "timelines" / f"project-{UUID(project_id)}.json"
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Save the video timeline before editing audio.",
            )
        timeline = read_json(timeline_path)
        if timeline.get("project_id") != str(UUID(project_id)):
            raise HTTPException(
                status_code=409,
                detail="Video timeline belongs to another project.",
            )
        timeline_duration = float(timeline.get("duration_seconds", 0))
        if (
            not math.isfinite(timeline_duration)
            or timeline_duration < 0
            or timeline_duration > 3600
        ):
            raise HTTPException(status_code=409, detail="Video timeline duration is invalid.")

        clips: list[dict] = []
        seen_clip_ids: set[str] = set()
        for clip in payload.clips:
            clip_id = str(clip.clip_id)
            asset_id = str(clip.asset_id)
            if clip_id in seen_clip_ids:
                raise HTTPException(
                    status_code=422,
                    detail="Audio clip identifiers must be unique.",
                )
            seen_clip_ids.add(clip_id)
            if not timeline.get("clips"):
                raise HTTPException(
                    status_code=422,
                    detail="Add and save at least one video clip before adding timeline audio.",
                )
            if clip.timeline_start_seconds + clip.duration_seconds > timeline_duration + 0.001:
                raise HTTPException(
                    status_code=422,
                    detail="Audio clip extends beyond the saved video timeline.",
                )
            if clip.fade_in_seconds + clip.fade_out_seconds > clip.duration_seconds:
                raise HTTPException(
                    status_code=422,
                    detail="Audio fade-in and fade-out cannot exceed the clip duration.",
                )
            asset, source = _source_path(project_id, asset_id)
            if asset["asset_type"] not in {"audio", "video"}:
                raise HTTPException(
                    status_code=422,
                    detail="Audio mix sources must be audio files or videos with an audio stream.",
                )
            metadata = probe_media(source)
            if not any(
                stream.get("codec_type") == "audio"
                for stream in metadata.get("streams", [])
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Selected media does not contain an audio stream.",
                )
            source_duration_value = metadata.get("format", {}).get("duration")
            try:
                source_duration = (
                    float(source_duration_value)
                    if source_duration_value is not None
                    else None
                )
            except (TypeError, ValueError):
                source_duration = None
            if source_duration is not None and (
                clip.source_start_seconds + clip.duration_seconds > source_duration + 0.001
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Audio clip exceeds its source media duration.",
                )
            clips.append(clip.model_dump(mode="json"))

        manifest = {
            "schema_version": 1,
            "project_id": str(UUID(project_id)),
            "timeline_duration_seconds": round(timeline_duration, 6),
            "source_audio_enabled": payload.source_audio_enabled,
            "source_audio_denoise": payload.source_audio_denoise,
            "levels_db": payload.levels_db.model_dump(),
            "clips": clips,
            "status": "draft",
        }
        path = _audio_mix_path(project_id)
        atomic_write_json(path, manifest)
        preview = (
            project_dir(project_id)
            / "exports"
            / f"project-{UUID(project_id)}-preview.mp4"
        )
        preview.unlink(missing_ok=True)
        return manifest
    except MediaToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project or audio source not found.") from exc


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


@router.post("/timeline/subtitles")
def create_timeline_subtitles(
    project_id: str, payload: ProjectSubtitleRequest
) -> dict:
    try:
        get_project(project_id)
        timeline_id = str(UUID(project_id))
        timeline_path = (
            project_dir(project_id) / "timelines" / f"project-{timeline_id}.json"
        )
        if not timeline_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Save the project timeline before creating subtitles.",
            )
        timeline = read_json(timeline_path)
        clips = timeline.get("clips")
        if timeline.get("project_id") != timeline_id or not isinstance(clips, list) or not clips:
            raise HTTPException(
                status_code=422,
                detail="A non-empty valid project timeline is required.",
            )

        transcriptions: dict[str, dict] = {}
        missing_transcriptions: list[str] = []
        for clip in clips:
            try:
                asset_id = str(UUID(str(clip["asset_id"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="Timeline contains an invalid asset reference.",
                ) from exc
            if asset_id in transcriptions or asset_id in missing_transcriptions:
                continue
            asset, _ = _source_path(project_id, asset_id)
            if asset["asset_type"] != "video":
                raise HTTPException(
                    status_code=422,
                    detail="Timeline subtitles require video source assets.",
                )
            transcription_path = asset_dir(project_id, asset_id) / "transcription.json"
            if not transcription_path.is_file():
                missing_transcriptions.append(asset_id)
                continue
            manifest = read_json(transcription_path)
            if manifest.get("language") not in {"ar", "en"} or not isinstance(
                manifest.get("segments"), list
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A source transcription manifest is invalid.",
                )
            transcriptions[asset_id] = manifest

        if missing_transcriptions:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Transcribe every video source used by the timeline "
                        "before creating subtitles."
                    ),
                    "missing_asset_ids": missing_transcriptions,
                },
            )

        valid_alignment_states = {"completed", "unavailable", "not_requested"}
        alignment_states = {
            manifest.get("alignment_status", "not_requested")
            for manifest in transcriptions.values()
        }
        if not alignment_states.issubset(valid_alignment_states):
            raise HTTPException(
                status_code=409,
                detail="A source transcription has an unknown alignment state.",
            )
        if payload.require_alignment and alignment_states != {"completed"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Word alignment is required, but one or more source "
                        "transcriptions are not word-aligned."
                    ),
                    "alignment_states": sorted(alignment_states),
                },
            )

        captions: list[dict] = []
        timeline_cursor = 0.0
        for clip in clips:
            asset_id = str(UUID(str(clip["asset_id"])))
            try:
                source_start = float(clip["source_start_seconds"])
                clip_duration = float(clip["duration_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="Timeline contains an invalid clip range.",
                ) from exc
            if (
                not math.isfinite(source_start)
                or not math.isfinite(clip_duration)
                or source_start < 0
                or clip_duration <= 0
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Timeline contains an invalid clip range.",
                )
            source_end = source_start + clip_duration
            transcription = transcriptions[asset_id]
            for segment in transcription["segments"]:
                if not isinstance(segment, dict):
                    continue
                try:
                    segment_start = float(segment["start"])
                    segment_end = float(segment["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                text = str(segment.get("text", "")).replace("\r", " ").replace("\n", " ").strip()
                if (
                    not text
                    or not math.isfinite(segment_start)
                    or not math.isfinite(segment_end)
                    or segment_end <= segment_start
                ):
                    continue
                overlap_start = max(source_start, segment_start)
                overlap_end = min(source_end, segment_end)
                if overlap_end <= overlap_start:
                    continue
                captions.append(
                    {
                        "start_seconds": timeline_cursor + overlap_start - source_start,
                        "end_seconds": timeline_cursor + overlap_end - source_start,
                        "text": text,
                    }
                )
            timeline_cursor += clip_duration

        captions.sort(key=lambda caption: (caption["start_seconds"], caption["end_seconds"]))
        if not captions:
            raise HTTPException(
                status_code=422,
                detail="No speech segments overlap the selected timeline clip ranges.",
            )
        if len(captions) > 20000:
            raise HTTPException(
                status_code=422,
                detail="The timeline produces too many subtitle cues.",
            )

        srt_lines: list[str] = []
        for index, caption in enumerate(captions, start=1):
            srt_lines.extend(
                [
                    str(index),
                    (
                        f"{_srt_timestamp(caption['start_seconds'])} --> "
                        f"{_srt_timestamp(caption['end_seconds'])}"
                    ),
                    caption["text"],
                    "",
                ]
            )

        timeline_dir = project_dir(project_id) / "timelines"
        timeline_dir.mkdir(parents=True, exist_ok=True)
        srt_path = timeline_dir / f"project-{timeline_id}.srt"
        temporary = srt_path.with_name(srt_path.name + ".partial")
        try:
            temporary.write_text("\n".join(srt_lines), encoding="utf-8-sig")
            temporary.replace(srt_path)
        finally:
            temporary.unlink(missing_ok=True)

        alignment_status = (
            "completed"
            if alignment_states == {"completed"}
            else "unavailable"
            if "unavailable" in alignment_states
            else "not_requested"
        )
        manifest = {
            "project_id": timeline_id,
            "timeline_id": timeline_id,
            "subtitle_format": "srt",
            "subtitle_filename": srt_path.name,
            "caption_count": len(captions),
            "duration_seconds": round(timeline_cursor, 6),
            "languages": sorted(
                {str(item.get("language")) for item in transcriptions.values()}
            ),
            "alignment_status": alignment_status,
            "download_url": f"/v1/projects/{project_id}/timeline/subtitles",
        }
        atomic_write_json(
            timeline_dir / f"project-{timeline_id}-subtitles.json", manifest
        )
        return manifest
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Project or timeline source not found.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/timeline/subtitles/manifest")
def get_timeline_subtitle_manifest(project_id: str) -> dict:
    try:
        get_project(project_id)
        timeline_id = str(UUID(project_id))
        path = (
            project_dir(project_id)
            / "timelines"
            / f"project-{timeline_id}-subtitles.json"
        )
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Timeline subtitles have not been generated.",
            )
        return read_json(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/timeline/subtitles")
def download_timeline_subtitles(project_id: str) -> FileResponse:
    try:
        get_project(project_id)
        timeline_id = str(UUID(project_id))
        srt_path = (
            project_dir(project_id) / "timelines" / f"project-{timeline_id}.srt"
        )
        if not srt_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Timeline subtitles have not been generated.",
            )
        return FileResponse(
            srt_path,
            media_type="application/x-subrip; charset=utf-8",
            filename=srt_path.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


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
        report_path = quality_report_path(
            project_id, f"project-{UUID(project_id)}"
        )
        if not report_path.is_file():
            raise HTTPException(
                status_code=409,
                detail="Project export has no completed quality report.",
            )
        report = read_json(report_path)
        if not quality_report_matches_file(report, rendered):
            raise HTTPException(
            status_code=409,
            detail="Quality report is stale or does not authorize this output.",
        )
        return FileResponse(rendered, media_type="video/mp4", filename=rendered.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/quality-report")
def get_project_quality_report(project_id: str, asset_id: str | None = None) -> dict:
    try:
        get_project(project_id)
        report_key = f"project-{UUID(project_id)}"
        if asset_id is not None:
            normalized_asset_id = str(UUID(asset_id))
            get_asset(project_id, normalized_asset_id)
            report_key = f"asset-{normalized_asset_id}"
        report_path = quality_report_path(project_id, report_key)
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail="No quality report exists for this export.")
        return read_json(report_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Project or asset identifier is invalid.",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Project or asset was not found.",
        ) from exc
