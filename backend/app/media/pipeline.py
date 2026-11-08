import math
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from app.integrations.ffmpeg import MediaToolError, probe_media, require_binary
from app.media.storage import atomic_write_json, project_dir


class OptionalMediaDependencyError(RuntimeError):
    pass


class MediaJobCancelled(RuntimeError):
    pass


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise MediaJobCancelled("Media operation cancelled.")


def _run(
    command: list[str],
    *,
    timeout: int = 3600,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout
    stdout = ""
    stderr = ""
    while True:
        if cancel_check is not None and cancel_check():
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise MediaJobCancelled("FFmpeg process terminated after cancellation.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            raise MediaToolError(
                f"Media command timed out after {timeout} seconds. {stderr[-4000:]}"
            )
        try:
            stdout, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode != 0:
        raise MediaToolError(result.stderr[-4000:] or "Media command failed.")
    return result


def create_proxy(
    source: Path,
    destination: Path,
    *,
    max_width: int = 1280,
    fps: int = 30,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _check_cancel(cancel_check)
    if max_width < 160 or max_width > 1920 or fps < 1 or fps > 60:
        raise ValueError("Proxy settings are outside the supported range.")
    metadata = probe_media(source)
    video_streams = [s for s in metadata.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise ValueError("Proxy generation requires a video asset.")

    ffmpeg = require_binary("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".partial" + destination.suffix)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-vf",
        f"scale=w='min({max_width},iw)':h='min({max_width},ih)':"
        f"force_original_aspect_ratio=decrease:force_divisible_by=2,fps={fps}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(temporary),
    ]
    try:
        _run(command, cancel_check=cancel_check)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise MediaToolError("FFmpeg did not create a valid proxy.")
        probe_media(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": destination.name,
        "size_bytes": destination.stat().st_size,
        "metadata": probe_media(destination),
        "settings": {"max_width": max_width, "fps": fps, "video_codec": "libx264"},
    }


def detect_scenes(
    source: Path,
    *,
    threshold: float = 27.0,
    min_scene_len: int = 15,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    _check_cancel(cancel_check)
    if threshold <= 0 or threshold > 100 or min_scene_len < 1 or min_scene_len > 1000:
        raise ValueError("Invalid scene detection settings.")
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector
    except ImportError as exc:
        raise OptionalMediaDependencyError(
            "Scene detection requires the optional media dependencies. Install the 'media' extra."
        ) from exc

    video = open_video(str(source))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    manager.detect_scenes(video, show_progress=False)
    _check_cancel(cancel_check)
    scene_list = manager.get_scene_list()
    duration = float(video.duration.seconds)
    scenes: list[dict] = []
    for index, (start, end) in enumerate(scene_list, start=1):
        start_seconds = max(0.0, float(start.seconds))
        end_seconds = min(duration, float(end.seconds))
        if end_seconds > start_seconds:
            scenes.append({
                "id": f"scene_{index:04d}",
                "index": index,
                "start_seconds": round(start_seconds, 6),
                "end_seconds": round(end_seconds, 6),
                "duration_seconds": round(end_seconds - start_seconds, 6),
            })
    if not scenes and duration > 0:
        scenes.append({
            "id": "scene_0001",
            "index": 1,
            "start_seconds": 0.0,
            "end_seconds": round(duration, 6),
            "duration_seconds": round(duration, 6),
        })
    return scenes


def build_timeline(
    *,
    project_id: str,
    asset_id: str,
    source: Path,
    scenes: list[dict],
    fps: int = 30,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[dict, Path]:
    _check_cancel(cancel_check)
    try:
        import opentimelineio as otio
    except ImportError as exc:
        raise OptionalMediaDependencyError(
            "Timeline generation requires OpenTimelineIO. Install the 'media' extra."
        ) from exc
    if fps < 1 or fps > 120 or not scenes or len(scenes) > 500:
        raise ValueError("Invalid timeline frame rate or scene count.")

    source_metadata = probe_media(source)
    duration_value = source_metadata.get("format", {}).get("duration")
    try:
        source_duration = float(duration_value) if duration_value is not None else None
    except (TypeError, ValueError):
        source_duration = None

    project_path = project_dir(project_id)
    timeline_dir = project_path / "timelines"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    source_url = source.resolve().as_uri()
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline = otio.schema.Timeline(name=f"Project {project_id}")
    timeline.tracks.append(video)
    normalized_scenes = []

    previous_end = 0.0
    for scene in scenes:
        _check_cancel(cancel_check)
        start = float(scene["start_seconds"])
        duration = float(scene["duration_seconds"])
        end = start + duration
        if (
            not math.isfinite(start)
            or not math.isfinite(duration)
            or start < 0
            or duration <= 0
        ):
            raise ValueError("Scene time range must be finite and positive.")
        if start < previous_end - (1.0 / fps):
            raise ValueError("Scenes must be ordered and cannot overlap.")
        if source_duration is not None and end > source_duration + (1.0 / fps):
            raise ValueError("Scene time range exceeds the source video duration.")
        previous_end = end
        reference = otio.schema.ExternalReference(target_url=source_url)
        clip = otio.schema.Clip(name=str(scene["id"]), media_reference=reference)
        clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(start * fps, fps),
            duration=otio.opentime.RationalTime(duration * fps, fps),
        )
        video.append(clip)
        normalized_scenes.append({
            "id": str(scene["id"]),
            "start_seconds": round(start, 6),
            "duration_seconds": round(duration, 6),
        })

    timeline_path = timeline_dir / f"{asset_id}.otio"
    otio.adapters.write_to_file(timeline, str(timeline_path))
    manifest = {
        "project_id": str(UUID(project_id)),
        "asset_id": str(UUID(asset_id)),
        "source_filename": source.name,
        "fps": fps,
        "resolution": "source",
        "clips": normalized_scenes,
        "otio_filename": timeline_path.name,
        "status": "draft",
    }
    atomic_write_json(timeline_dir / f"{asset_id}.json", manifest)
    return manifest, timeline_path


def render_timeline(
    *,
    source: Path,
    timeline_manifest: dict,
    destination: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    crf: int = 20,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    _check_cancel(cancel_check)
    if width < 320 or width > 3840 or height < 240 or height > 2160:
        raise ValueError("Render resolution is outside the supported range.")
    if fps < 1 or fps > 60 or crf < 0 or crf > 35:
        raise ValueError("Render settings are outside the supported range.")
    clips = timeline_manifest.get("clips", [])
    if not clips or len(clips) > 500:
        raise ValueError("Timeline must contain between 1 and 500 clips.")

    probe = probe_media(source)
    streams = probe.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not video_streams:
        raise ValueError("Rendering requires a video source.")
    duration_value = probe.get("format", {}).get("duration")
    try:
        source_duration = float(duration_value) if duration_value is not None else None
    except (TypeError, ValueError):
        source_duration = None

    filters: list[str] = []
    for index, clip in enumerate(clips):
        _check_cancel(cancel_check)
        start = float(clip["start_seconds"])
        duration = float(clip["duration_seconds"])
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise ValueError("Timeline contains an invalid clip range.")
        if source_duration is not None and start + duration > source_duration + (1.0 / fps):
            raise ValueError("Timeline clip exceeds the source video duration.")
        filters.append(
            f"[0:v:0]trim=start={start:.6f}:duration={duration:.6f},"
            f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
            f"format=yuv420p[v{index}]"
        )
        if has_audio:
            filters.append(
                f"[0:a:0]atrim=start={start:.6f}:duration={duration:.6f},"
                f"asetpts=PTS-STARTPTS,aresample=48000[a{index}]"
            )

    concat_inputs = "".join(
        f"[v{index}]" + (f"[a{index}]" if has_audio else "")
        for index in range(len(clips))
    )
    if has_audio:
        filters.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=1[vout][aout]")
    else:
        filters.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vout]")

    ffmpeg = require_binary("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".partial" + destination.suffix)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-filter_complex", ";".join(filters), "-map", "[vout]",
    ]
    if has_audio:
        command.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend([
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
    ])
    try:
        _run(command, timeout=7200, cancel_check=cancel_check)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise MediaToolError("FFmpeg did not create the rendered video.")
        probe_media(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "filename": destination.name,
        "size_bytes": destination.stat().st_size,
        "settings": {"width": width, "height": height, "fps": fps, "crf": crf},
        "metadata": probe_media(destination),
    }
