import math
import os
import subprocess
import tempfile
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
        if (
            not math.isfinite(start)
            or not math.isfinite(duration)
            or start < 0
            or duration <= 0
        ):
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


def build_multi_source_timeline(
    *,
    project_id: str,
    sources: dict[str, Path],
    clips: list[dict],
    fps: int = 30,
) -> tuple[dict, Path]:
    """Build a project-wide sequential timeline from project-scoped video assets."""
    _check_cancel(None)
    try:
        import opentimelineio as otio
    except ImportError as exc:
        raise OptionalMediaDependencyError(
            "Timeline generation requires OpenTimelineIO. Install the 'media' extra."
        ) from exc

    if fps < 1 or fps > 60 or not clips or len(clips) > 100:
        raise ValueError("A multi-source timeline requires 1–100 clips and an FPS from 1 to 60.")

    safe_project_id = str(UUID(project_id))
    normalized_sources = {
        str(UUID(key)): Path(value).expanduser().resolve()
        for key, value in sources.items()
    }
    source_metadata: dict[str, dict] = {}
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline = otio.schema.Timeline(name=f"Project {safe_project_id}")
    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)

    normalized_clips: list[dict] = []
    timeline_cursor = 0.0
    total_duration = 0.0

    for index, item in enumerate(clips, start=1):
        try:
            asset_id = str(UUID(str(item["asset_id"])))
            clip_id = (
                str(UUID(str(item["clip_id"])))
                if item.get("clip_id")
                else f"clip_{index:04d}"
            )
            start = float(item["source_start_seconds"])
            duration = float(item["duration_seconds"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                "Each timeline clip requires a valid asset_id, source start and duration."
            ) from exc

        if asset_id not in normalized_sources:
            raise ValueError("Every clip must reference a video asset in the same project.")
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise ValueError(
                "Clip source range must be finite, non-negative and have positive duration."
            )

        source = normalized_sources[asset_id]
        if not source.is_file():
            raise ValueError("A timeline source asset file is missing.")
        metadata = source_metadata.get(asset_id)
        if metadata is None:
            metadata = probe_media(source)
            if not any(
                stream.get("codec_type") == "video"
                for stream in metadata.get("streams", [])
            ):
                raise ValueError("Every timeline source must contain a video stream.")
            source_metadata[asset_id] = metadata

        duration_value = metadata.get("format", {}).get("duration")
        try:
            source_duration = float(duration_value) if duration_value is not None else None
        except (TypeError, ValueError):
            source_duration = None
        if source_duration is not None and start + duration > source_duration + (1.0 / fps):
            raise ValueError("Timeline clip exceeds its source video duration.")

        video_reference = otio.schema.ExternalReference(target_url=source.as_uri())
        video_clip = otio.schema.Clip(name=clip_id, media_reference=video_reference)
        video_clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(start * fps, fps),
            duration=otio.opentime.RationalTime(duration * fps, fps),
        )
        video_track.append(video_clip)

        has_audio = any(
            stream.get("codec_type") == "audio"
            for stream in metadata.get("streams", [])
        )
        if has_audio:
            audio_reference = otio.schema.ExternalReference(target_url=source.as_uri())
            audio_clip = otio.schema.Clip(name=clip_id, media_reference=audio_reference)
            audio_clip.source_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(start * fps, fps),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            )
            audio_track.append(audio_clip)
        else:
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(duration * fps, fps),
                    )
                )
            )

        normalized_clips.append(
            {
                "clip_id": clip_id,
                "asset_id": asset_id,
                "source_start_seconds": round(start, 6),
                "duration_seconds": round(duration, 6),
                "timeline_start_seconds": round(timeline_cursor, 6),
            }
        )
        timeline_cursor += duration
        total_duration += duration
        if total_duration > 3600:
            raise ValueError("Multi-source timeline duration cannot exceed one hour.")

    project_path = project_dir(safe_project_id)
    timeline_dir = project_path / "timelines"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = timeline_dir / f"project-{safe_project_id}.otio"
    otio.adapters.write_to_file(timeline, str(timeline_path))

    manifest = {
        "schema_version": 2,
        "timeline_id": safe_project_id,
        "project_id": safe_project_id,
        "fps": fps,
        "duration_seconds": round(total_duration, 6),
        "clips": normalized_clips,
        "otio_filename": timeline_path.name,
        "status": "draft",
    }
    atomic_write_json(timeline_dir / f"project-{safe_project_id}.json", manifest)
    return manifest, timeline_path


def _escape_filter_path(value: str) -> str:
    """Escape a filesystem path for an FFmpeg filter option value."""
    return "".join("\\" + char if char in "\\':,[];" else char for char in value)


def _title_font_path() -> Path:
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        windows_fonts / "arial.ttf",
        windows_fonts / "segoeui.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise MediaToolError(
        "Title rendering requires a supported font. Install DejaVu Sans or configure a system font."
    )


def render_multi_source_timeline(
    *,
    sources: dict[str, Path],
    timeline_manifest: dict,
    destination: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    crf: int = 20,
    audio_mix_manifest: dict | None = None,
    audio_sources: dict[str, Path] | None = None,
    visual_effects_manifest: dict | None = None,
    overlay_sources: dict[str, Path] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Render a project timeline with synchronized audio, transitions, titles and image overlays."""
    _check_cancel(cancel_check)
    if width < 320 or width > 3840 or height < 240 or height > 2160:
        raise ValueError("Render resolution is outside the supported range.")
    if fps < 1 or fps > 60 or crf < 0 or crf > 35:
        raise ValueError("Render settings are outside the supported range.")
    clips = timeline_manifest.get("clips", [])
    if (
        timeline_manifest.get("schema_version") != 2
        or not clips
        or len(clips) > 100
    ):
        raise ValueError("A valid multi-source timeline with 1–100 clips is required.")

    normalized_sources = {
        str(UUID(key)): Path(value).expanduser().resolve()
        for key, value in sources.items()
    }
    normalized_audio_sources = {
        str(UUID(key)): Path(value).expanduser().resolve()
        for key, value in (audio_sources or {}).items()
    }
    inputs: list[Path] = []
    clip_metadata: list[dict] = []
    any_source_audio = False
    source_audio_enabled = True
    source_audio_denoise = False
    levels_db = {"voice": 0.0, "music": 0.0, "sfx": 0.0}
    audio_mix_clips: list[dict] = []

    if audio_mix_manifest is not None:
        if audio_mix_manifest.get("schema_version") != 1:
            raise ValueError("Unsupported audio mix manifest version.")
        if audio_mix_manifest.get("project_id") != timeline_manifest.get("project_id"):
            raise ValueError("Audio mix project does not match the video timeline.")
        source_audio_enabled = bool(audio_mix_manifest.get("source_audio_enabled", True))
        source_audio_denoise = bool(audio_mix_manifest.get("source_audio_denoise", False))
        raw_levels = audio_mix_manifest.get("levels_db", {})
        for role in ("voice", "music", "sfx"):
            try:
                level = float(raw_levels.get(role, 0.0))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("Audio mix contains an invalid master level.") from exc
            if not math.isfinite(level) or level < -60 or level > 12:
                raise ValueError("Audio master levels must be between -60 and +12 dB.")
            levels_db[role] = level
        raw_mix_clips = audio_mix_manifest.get("clips", [])
        if not isinstance(raw_mix_clips, list) or len(raw_mix_clips) > 100:
            raise ValueError("Audio mix must contain between 0 and 100 clips.")
        audio_mix_clips = raw_mix_clips

    timeline_duration = sum(float(item["duration_seconds"]) for item in clips)
    if not math.isfinite(timeline_duration) or timeline_duration <= 0 or timeline_duration > 3600:
        raise ValueError("Video timeline duration is invalid.")

    effects = visual_effects_manifest or {}
    if effects:
        if (
            effects.get("schema_version") != 1
            or effects.get("project_id") != timeline_manifest.get("project_id")
        ):
            raise ValueError("Visual effects project or manifest version is invalid.")
    raw_transitions = effects.get("transitions", [])
    raw_titles = effects.get("titles", [])
    raw_overlays = effects.get("overlays", [])
    if (
        not isinstance(raw_transitions, list)
        or len(raw_transitions) > 99
        or not isinstance(raw_titles, list)
        or len(raw_titles) > 20
        or not isinstance(raw_overlays, list)
        or len(raw_overlays) > 20
    ):
        raise ValueError("Visual effects exceed the supported item limits.")

    clip_positions: dict[str, int] = {}
    clip_durations: dict[str, float] = {}
    for index, item in enumerate(clips):
        try:
            clip_id = str(UUID(str(item["clip_id"])))
            clip_duration = float(item["duration_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Timeline clip identifiers or durations are invalid.") from exc
        if clip_id in clip_positions:
            raise ValueError("Timeline clip identifiers must be unique.")
        clip_positions[clip_id] = index
        clip_durations[clip_id] = clip_duration

    transitions_by_clip: dict[str, dict] = {}
    transition_usage: dict[str, float] = {}
    for item in raw_transitions:
        try:
            after_clip_id = str(UUID(str(item["after_clip_id"])))
            style = str(item["style"])
            duration = float(item["duration_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("A transition definition is invalid.") from exc
        index = clip_positions.get(after_clip_id)
        if (
            index is None
            or index >= len(clips) - 1
            or after_clip_id in transitions_by_clip
            or style not in {"dip_to_black", "flash_white"}
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError("A transition must target one existing clip boundary.")
        next_clip_id = str(UUID(str(clips[index + 1]["clip_id"])))
        if duration > min(clip_durations[after_clip_id], clip_durations[next_clip_id]) / 2:
            raise ValueError("A transition cannot exceed half of either adjacent clip.")
        transitions_by_clip[after_clip_id] = {
            "style": style,
            "duration": duration,
            "next_clip_id": next_clip_id,
        }
        transition_usage[after_clip_id] = transition_usage.get(after_clip_id, 0.0) + duration
        transition_usage[next_clip_id] = transition_usage.get(next_clip_id, 0.0) + duration
    for clip_id, used in transition_usage.items():
        if used > clip_durations[clip_id] + 0.000001:
            raise ValueError("Incoming and outgoing transitions overlap inside a clip.")

    title_metadata: list[dict] = []
    seen_title_ids: set[str] = set()
    for item in raw_titles:
        try:
            title_id = str(UUID(str(item["title_id"])))
            text = str(item["text"])
            start = float(item["start_seconds"])
            duration = float(item["duration_seconds"])
            font_size = int(item.get("font_size", 48))
            color = str(item.get("color", "#FFFFFF"))
            position = str(item.get("position", "bottom"))
            animation = str(item.get("animation", "fade"))
            background = bool(item.get("background", True))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("A title definition is invalid.") from exc
        end = start + duration
        if (
            title_id in seen_title_ids
            or not text.strip()
            or len(text) > 280
            or not math.isfinite(start)
            or not math.isfinite(duration)
            or start < 0
            or duration <= 0
            or end > timeline_duration + 0.001
            or font_size < 18
            or font_size > 120
            or len(color) != 7
            or not color.startswith("#")
            or any(char not in "0123456789abcdefABCDEF" for char in color[1:])
            or position not in {"top", "center", "bottom"}
            or animation not in {"none", "fade", "slide_up"}
        ):
            raise ValueError("A title has invalid text, timing, font or animation settings.")
        seen_title_ids.add(title_id)
        title_metadata.append({
            "text": text,
            "start": start,
            "duration": duration,
            "end": end,
            "font_size": font_size,
            "color": "0x" + color[1:],
            "position": position,
            "animation": animation,
            "background": background,
        })

    normalized_overlay_sources = {
        str(UUID(key)): Path(value).expanduser().resolve()
        for key, value in (overlay_sources or {}).items()
    }
    overlay_metadata: list[dict] = []
    seen_overlay_ids: set[str] = set()
    for item in raw_overlays:
        try:
            overlay_id = str(UUID(str(item["overlay_id"])))
            asset_id = str(UUID(str(item["asset_id"])))
            start = float(item["start_seconds"])
            duration = float(item["duration_seconds"])
            width_ratio = float(item.get("width_ratio", 0.2))
            opacity = float(item.get("opacity", 1.0))
            position = str(item.get("position", "top_right"))
            animation = str(item.get("animation", "none"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("An image overlay definition is invalid.") from exc
        if (
            overlay_id in seen_overlay_ids
            or asset_id not in normalized_overlay_sources
            or not math.isfinite(start)
            or not math.isfinite(duration)
            or not math.isfinite(width_ratio)
            or not math.isfinite(opacity)
            or start < 0
            or duration <= 0
            or start + duration > timeline_duration + 0.001
            or width_ratio < 0.05
            or width_ratio > 0.6
            or opacity < 0.1
            or opacity > 1.0
            or position not in {"top_left", "top_right", "bottom_left", "bottom_right", "center"}
            or animation not in {"none", "slide_up"}
        ):
            raise ValueError("An image overlay has invalid asset, timing or layout settings.")
        source = normalized_overlay_sources[asset_id]
        if not source.is_file():
            raise ValueError("An image overlay source file is missing.")
        metadata = probe_media(source)
        if not any(stream.get("codec_type") == "video" for stream in metadata.get("streams", [])):
            raise ValueError("An image overlay must contain a decodable image stream.")
        seen_overlay_ids.add(overlay_id)
        overlay_metadata.append({
            "asset_id": asset_id,
            "source": source,
            "start": start,
            "duration": duration,
            "end": start + duration,
            "width_ratio": width_ratio,
            "opacity": opacity,
            "position": position,
            "animation": animation,
        })

    for clip in clips:
        _check_cancel(cancel_check)
        try:
            asset_id = str(UUID(str(clip["asset_id"])))
            start = float(clip["source_start_seconds"])
            duration = float(clip["duration_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Timeline contains an invalid source reference or clip range."
            ) from exc
        if asset_id not in normalized_sources:
            raise ValueError(
                "Timeline references a source asset outside the supplied project assets."
            )
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise ValueError("Timeline contains an invalid clip range.")

        source = normalized_sources[asset_id]
        if not source.is_file():
            raise ValueError("A timeline source asset file is missing.")
        metadata = probe_media(source)
        streams = metadata.get("streams", [])
        if not any(stream.get("codec_type") == "video" for stream in streams):
            raise ValueError("Every timeline source must contain a video stream.")
        duration_value = metadata.get("format", {}).get("duration")
        try:
            source_duration = float(duration_value) if duration_value is not None else None
        except (TypeError, ValueError):
            source_duration = None
        if source_duration is not None and start + duration > source_duration + (1.0 / fps):
            raise ValueError("Timeline clip exceeds its source video duration.")

        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
        any_source_audio = any_source_audio or (source_audio_enabled and has_audio)
        inputs.append(source)
        clip_metadata.append(
            {
                "clip_id": str(UUID(str(clip["clip_id"]))),
                "start": start,
                "duration": duration,
                "has_audio": has_audio,
            }
        )

    audio_input_metadata: list[dict] = []
    seen_clip_ids: set[str] = set()
    for item in audio_mix_clips:
        _check_cancel(cancel_check)
        try:
            clip_id = str(UUID(str(item["clip_id"])))
            asset_id = str(UUID(str(item["asset_id"])))
            role = str(item["role"])
            start = float(item["source_start_seconds"])
            duration = float(item["duration_seconds"])
            timeline_start = float(item["timeline_start_seconds"])
            gain_db = float(item.get("gain_db", 0.0))
            fade_in = float(item.get("fade_in_seconds", 0.0))
            fade_out = float(item.get("fade_out_seconds", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Audio mix contains an invalid clip definition.") from exc
        if clip_id in seen_clip_ids:
            raise ValueError("Audio mix clip identifiers must be unique.")
        seen_clip_ids.add(clip_id)
        if role not in {"voice", "music", "sfx"}:
            raise ValueError("Audio clip role must be voice, music or sfx.")
        if asset_id not in normalized_audio_sources:
            raise ValueError("Audio mix references an asset outside the supplied project assets.")
        values = (start, duration, timeline_start, gain_db, fade_in, fade_out)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Audio clip timing and levels must be finite numbers.")
        if (
            start < 0
            or duration <= 0
            or timeline_start < 0
            or gain_db < -60
            or gain_db > 12
            or fade_in < 0
            or fade_out < 0
            or fade_in + fade_out > duration
            or timeline_start + duration > timeline_duration + (1.0 / fps)
        ):
            raise ValueError("Audio clip range, fades or gain are outside the supported limits.")

        source = normalized_audio_sources[asset_id]
        if not source.is_file():
            raise ValueError("An audio mix source file is missing.")
        metadata = probe_media(source)
        streams = metadata.get("streams", [])
        if not any(stream.get("codec_type") == "audio" for stream in streams):
            raise ValueError("Every audio mix source must contain an audio stream.")
        source_duration_value = metadata.get("format", {}).get("duration")
        try:
            source_duration = (
                float(source_duration_value)
                if source_duration_value is not None
                else None
            )
        except (TypeError, ValueError):
            source_duration = None
        if source_duration is not None and start + duration > source_duration + (1.0 / fps):
            raise ValueError("Audio clip exceeds its source media duration.")
        inputs.append(source)
        audio_input_metadata.append(
            {
                "role": role,
                "source_start": start,
                "duration": duration,
                "timeline_start": timeline_start,
                "gain_db": gain_db,
                "fade_in": fade_in,
                "fade_out": fade_out,
                "denoise": bool(item.get("denoise", False)),
            }
        )

    first_overlay_input_index = len(inputs)
    for overlay in overlay_metadata:
        inputs.append(overlay["source"])

    filters: list[str] = []
    video_concat_inputs: list[str] = []
    audio_output_inputs: list[str] = []
    for index, clip in enumerate(clip_metadata):
        _check_cancel(cancel_check)
        start = clip["start"]
        duration = clip["duration"]
        video_chain = (
            f"[{index}:v:0]trim=start={start:.6f}:duration={duration:.6f},"
            f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
            "format=yuv420p"
        )
        previous_transition = (
            transitions_by_clip.get(clip_metadata[index - 1]["clip_id"])
            if index > 0 else None
        )
        outgoing_transition = transitions_by_clip.get(clip["clip_id"])
        if previous_transition:
            color = "black" if previous_transition["style"] == "dip_to_black" else "white"
            video_chain += (
                f",fade=t=in:st=0:d={previous_transition['duration']:.6f}:color={color}"
            )
        if outgoing_transition:
            color = "black" if outgoing_transition["style"] == "dip_to_black" else "white"
            fade_start = duration - outgoing_transition["duration"]
            video_chain += (
                f",fade=t=out:st={fade_start:.6f}:d={outgoing_transition['duration']:.6f}:"
                f"color={color}"
            )
        filters.append(video_chain + f"[v{index}]")
        video_concat_inputs.append(f"[v{index}]")
        if any_source_audio:
            if source_audio_enabled and clip["has_audio"]:
                voice_gain = 10 ** (levels_db["voice"] / 20.0)
                audio_chain = (
                    f"[{index}:a:0]atrim=start={start:.6f}:duration={duration:.6f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"volume={voice_gain:.8f}"
                )
                if source_audio_denoise:
                    audio_chain += ",afftdn=nr=12:nf=-30"
                audio_chain += f",apad,atrim=duration={duration:.6f}[a{index}]"
                filters.append(audio_chain)
            else:
                filters.append(
                    f"anullsrc=r=48000:cl=stereo,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"atrim=duration={duration:.6f}[a{index}]"
                )
            video_concat_inputs.append(f"[a{index}]")

    if any_source_audio:
        filters.append(
            f"{''.join(video_concat_inputs)}concat=n={len(clips)}:v=1:a=1[vout][aout]"
        )
        audio_output_inputs.append("[aout]")
    else:
        filters.append(
            f"{''.join(video_concat_inputs)}concat=n={len(clips)}:v=1:a=0[vout]"
        )

    first_audio_input_index = len(clip_metadata)
    for index, clip in enumerate(audio_input_metadata):
        _check_cancel(cancel_check)
        input_index = first_audio_input_index + index
        master_gain = levels_db[clip["role"]]
        linear_gain = 10 ** ((master_gain + clip["gain_db"]) / 20.0)
        chain = (
            f"[{input_index}:a:0]atrim=start={clip['source_start']:.6f}:"
            f"duration={clip['duration']:.6f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={linear_gain:.8f}"
        )
        if clip["denoise"]:
            chain += ",afftdn=nr=12:nf=-30"
        if clip["fade_in"] > 0:
            chain += f",afade=t=in:st=0:d={clip['fade_in']:.6f}"
        if clip["fade_out"] > 0:
            fade_start = clip["duration"] - clip["fade_out"]
            chain += f",afade=t=out:st={fade_start:.6f}:d={clip['fade_out']:.6f}"
        delay_ms = max(0, round(clip["timeline_start"] * 1000))
        chain += (
            f",adelay=delays={delay_ms}|{delay_ms}:all=1,"
            f"apad,atrim=duration={timeline_duration:.6f}[mix{index}]"
        )
        filters.append(chain)
        audio_output_inputs.append(f"[mix{index}]")

    video_output_label = "[vout]"
    current_video_label = video_output_label
    title_temp = None
    if overlay_metadata:
        for index, overlay in enumerate(overlay_metadata):
            input_index = first_overlay_input_index + index
            overlay_label = f"[overlay_asset_{index}]"
            scaled_width = max(16, int(width * overlay["width_ratio"]))
            scaled_height = max(16, int(height * 0.35))
            overlay_chain = (
                f"[{input_index}:v]scale=w={scaled_width}:h={scaled_height}:"
                f"force_original_aspect_ratio=decrease,format=rgba,"
                f"colorchannelmixer=aa={overlay['opacity']:.4f}"
            )
            overlay_chain += (
                f",setpts=PTS-STARTPTS,fps={fps},"
                f"trim=duration={timeline_duration:.6f},setpts=PTS-STARTPTS"
            )
            filters.append(overlay_chain + overlay_label)

            margin = 32
            if overlay["position"] == "top_left":
                x_expr, y_expr = str(margin), str(margin)
            elif overlay["position"] == "top_right":
                x_expr, y_expr = f"W-w-{margin}", str(margin)
            elif overlay["position"] == "bottom_left":
                x_expr, y_expr = str(margin), f"H-h-{margin}"
            elif overlay["position"] == "bottom_right":
                x_expr, y_expr = f"W-w-{margin}", f"H-h-{margin}"
            else:
                x_expr, y_expr = "(W-w)/2", "(H-h)/2"
            if overlay["animation"] == "slide_up":
                target_y = y_expr
                y_expr = (
                    f"if(lt(t\\,{overlay['start'] + 0.4:.6f})\\,"
                    f"({target_y})+40*(1-(t-{overlay['start']:.6f})/0.4)\\,"
                    f"({target_y}))"
                )
            next_label = f"[video_overlay_{index}]"
            enable = (
                f"between(t\\,{overlay['start']:.6f}\\,{overlay['end']:.6f})"
            )
            filters.append(
                f"{current_video_label}{overlay_label}overlay=x={x_expr}:y={y_expr}:"
                f"eval=frame:eof_action=repeat:enable='{enable}'{next_label}"
            )
            current_video_label = next_label

    ffmpeg = require_binary("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if title_metadata:
        font = _escape_filter_path(str(_title_font_path()))
        title_temp = tempfile.TemporaryDirectory(
            prefix="studio-titles-", dir=str(destination.parent)
        )
        title_directory = Path(title_temp.name)
        for index, title in enumerate(title_metadata):
            text_path = title_directory / f"title-{index:02d}.txt"
            text_path.write_text(title["text"], encoding="utf-8")
            escaped_text_path = _escape_filter_path(str(text_path))
            if title["position"] == "top":
                target_y = "h*0.08"
            elif title["position"] == "center":
                target_y = "(h-text_h)/2"
            else:
                target_y = "h-text_h-h*0.08"
            y_expr = target_y
            if title["animation"] == "slide_up":
                y_expr = (
                    f"if(lt(t\\,{title['start'] + 0.4:.6f})\\,"
                    f"({target_y})+40*(1-(t-{title['start']:.6f})/0.4)\\,"
                    f"({target_y}))"
                )
            alpha_option = ""
            if title["animation"] == "fade":
                fade_duration = min(0.3, title["duration"] / 2)
                alpha = (
                    f"if(lt(t\\,{title['start'] + fade_duration:.6f})\\,"
                    f"max(0\\,(t-{title['start']:.6f})/{fade_duration:.6f})\\,"
                    f"if(gt(t\\,{title['end'] - fade_duration:.6f})\\,"
                    f"max(0\\,({title['end']:.6f}-t)/{fade_duration:.6f})\\,1))"
                )
                alpha_option = f":alpha='{alpha}'"
            enable = (
                f"between(t\\,{title['start']:.6f}\\,{title['end']:.6f})"
            )
            box_option = ":box=1:boxcolor=black@0.45:boxborderw=14" if title["background"] else ""
            next_label = f"[video_title_{index}]"
            filters.append(
                f"{current_video_label}drawtext=fontfile='{font}':"
                f"textfile='{escaped_text_path}':text_shaping=1:"
                f"fontsize={title['font_size']}:fontcolor={title['color']}:"
                f"x=(w-text_w)/2:y='{y_expr}'{box_option}{alpha_option}:"
                f"fix_bounds=1:enable='{enable}'{next_label}"
            )
            current_video_label = next_label

    video_output_label = current_video_label
    has_audio_output = bool(audio_output_inputs)
    if len(audio_output_inputs) == 1:
        filters.append(f"{audio_output_inputs[0]}alimiter=limit=0.95[a_final]")
    elif len(audio_output_inputs) > 1:
        filters.append(
            f"{''.join(audio_output_inputs)}amix=inputs={len(audio_output_inputs)}:"
            "duration=longest:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[a_final]"
        )

    temporary = destination.with_name(destination.stem + ".partial" + destination.suffix)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for input_index, source in enumerate(inputs):
        if input_index >= first_overlay_input_index:
            command.extend(["-loop", "1", "-i", str(source)])
        else:
            command.extend(["-i", str(source)])
    command.extend([
        "-filter_complex", ";".join(filters), "-map", video_output_label,
        "-t", f"{timeline_duration:.6f}",
    ])
    if has_audio_output:
        command.extend(["-map", "[a_final]", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    try:
        _run(command, timeout=7200, cancel_check=cancel_check)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise MediaToolError("FFmpeg did not create the multi-source rendered video.")
        metadata = probe_media(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
        if title_temp is not None:
            title_temp.cleanup()

    return {
        "filename": destination.name,
        "size_bytes": destination.stat().st_size,
        "clip_count": len(clips),
        "transition_count": len(transitions_by_clip),
        "title_count": len(title_metadata),
        "overlay_count": len(overlay_metadata),
        "audio_clip_count": len(audio_input_metadata),
        "settings": {"width": width, "height": height, "fps": fps, "crf": crf},
        "metadata": metadata,
    }

