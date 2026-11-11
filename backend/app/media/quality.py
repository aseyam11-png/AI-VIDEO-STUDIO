from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
from pathlib import Path
import re
import subprocess
import time
from typing import Callable

from app.integrations.ffmpeg import MediaToolError, probe_media, require_binary
from app.media.pipeline import MediaJobCancelled
from app.media.storage import atomic_write_json, project_dir


_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"


def _fraction(value: object) -> float | None:
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", maxsplit=1)
            result = float(numerator) / float(denominator)
        else:
            result = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if result > 0 and result < float("inf") else None


def _run_diagnostic(
    command: list[str],
    *,
    timeout_seconds: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[str, str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancel_check is not None and cancel_check():
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise MediaJobCancelled("Video quality assessment was cancelled.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            _, stderr = process.communicate()
            raise MediaToolError(
                f"Quality analysis timed out after {timeout_seconds} seconds: {stderr[-2000:]}"
            )
        try:
            stdout, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode != 0:
        raise MediaToolError(stderr[-3000:] or "FFmpeg quality analysis failed.")
    return stdout, stderr


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_video_quality(
    source: str | Path,
    *,
    expected_duration_seconds: float | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    expected_audio: bool | None = None,
    black_min_duration_seconds: float = 0.5,
    freeze_min_duration_seconds: float = 2.0,
    silence_min_duration_seconds: float = 1.0,
    silence_noise_db: float = -50.0,
    timeout_seconds: int = 7200,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Inspect media metadata, fully decode streams, and detect black/frozen/silent spans."""
    path = Path(source).expanduser().resolve()
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "can_export": False,
        "file": {"name": path.name, "size_bytes": None, "sha256": None},
        "metrics": {},
        "checks": [],
        "issues": [],
        "thresholds": {
            "black_min_duration_seconds": black_min_duration_seconds,
            "freeze_min_duration_seconds": freeze_min_duration_seconds,
            "silence_min_duration_seconds": silence_min_duration_seconds,
            "silence_noise_db": silence_noise_db,
        },
    }

    def check(name: str, passed: bool, detail: str = "") -> None:
        report["checks"].append(
            {"name": name, "status": "passed" if passed else "failed", "detail": detail}
        )

    def issue(code: str, severity: str, message: str, **details: object) -> None:
        report["issues"].append(
            {"code": code, "severity": severity, "message": message, "details": details}
        )

    if not path.is_file():
        check("file_exists", False, "The output file is missing.")
        issue("file_missing", "error", "The rendered video file is missing.")
        return report

    size_bytes = path.stat().st_size
    report["file"]["size_bytes"] = size_bytes
    if size_bytes <= 0:
        check("file_nonempty", False, "The output file is empty.")
        issue("file_empty", "error", "The rendered video file is empty.")
        return report
    check("file_nonempty", True, "The output file exists and is non-empty.")

    try:
        metadata = probe_media(path)
    except (FileNotFoundError, MediaToolError, OSError) as exc:
        check("ffprobe", False, str(exc)[:500])
        issue("container_probe_failed", "error", "FFprobe could not read the output container.")
        return report

    streams = metadata.get("streams", [])
    video_streams = [
        stream for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    if not video_streams:
        check("video_stream", False, "No video stream was found.")
        issue("video_stream_missing", "error", "The output does not contain a video stream.")
        return report
    check("video_stream", True, f"{len(video_streams)} video stream(s) detected.")

    video = video_streams[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    duration = _fraction(metadata.get("format", {}).get("duration")) or _fraction(
        video.get("duration")
    )
    report["metrics"] = {
        "duration_seconds": round(duration, 6) if duration is not None else None,
        "width": width or None,
        "height": height or None,
        "fps": round(fps, 6) if fps is not None else None,
        "video_codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"),
        "audio_stream_count": len(audio_streams),
        "audio_codecs": [stream.get("codec_name") for stream in audio_streams],
        "audio_sample_rates": [stream.get("sample_rate") for stream in audio_streams],
        "container": metadata.get("format", {}).get("format_name"),
    }

    try:
        report["file"]["sha256"] = _sha256(path)
        check("file_sha256", True, "SHA-256 was calculated for this exact output.")
    except OSError as exc:
        check("file_sha256", False, str(exc)[:300])
        issue("file_fingerprint_failed", "error", "The output file could not be fingerprinted.")

    if duration is None or duration <= 0:
        check("duration", False, "A positive media duration could not be determined.")
        issue("duration_invalid", "error", "The output duration is missing or invalid.")
    elif duration > 3600:
        check("duration_limit", False, "The current production profile is limited to one hour.")
        issue(
            "duration_limit_exceeded",
            "error",
            "The output exceeds the one-hour validation limit.",
        )
    else:
        check("duration", True, f"Detected duration: {duration:.3f} seconds.")
        if expected_duration_seconds is not None:
            tolerance = max(0.5, 2.0 / max(expected_fps or fps or 1.0, 1.0))
            matches = abs(duration - expected_duration_seconds) <= tolerance
            check(
                "expected_duration",
                matches,
                (
                    f"Expected {expected_duration_seconds:.3f}s; detected "
                    f"{duration:.3f}s; tolerance {tolerance:.3f}s."
                ),
            )
            if not matches:
                issue(
                    "duration_mismatch",
                    "error",
                    "The rendered duration differs from the saved timeline.",
                    expected_seconds=round(expected_duration_seconds, 6),
                    actual_seconds=round(duration, 6),
                    tolerance_seconds=round(tolerance, 6),
                )

    if width <= 0 or height <= 0:
        check("resolution", False, "Video dimensions are missing or invalid.")
        issue("resolution_invalid", "error", "Video resolution could not be verified.")
    elif expected_width is not None and expected_height is not None:
        matches = width == expected_width and height == expected_height
        check(
            "resolution",
            matches,
            f"Expected {expected_width}x{expected_height}; detected {width}x{height}.",
        )
        if not matches:
            issue(
                "resolution_mismatch",
                "error",
                "The output resolution does not match the requested delivery resolution.",
                expected=f"{expected_width}x{expected_height}",
                actual=f"{width}x{height}",
            )
    else:
        check("resolution", True, f"Detected resolution: {width}x{height}.")

    if fps is None:
        check("frame_rate", False, "FFprobe did not return a valid frame rate.")
        issue("frame_rate_invalid", "error", "The output frame rate could not be verified.")
    elif expected_fps is not None:
        tolerance = max(0.5, expected_fps * 0.02)
        matches = abs(fps - expected_fps) <= tolerance
        check(
            "frame_rate",
            matches,
            f"Expected {expected_fps:.3f} FPS; detected {fps:.3f} FPS.",
        )
        if not matches:
            issue(
                "frame_rate_mismatch",
                "error",
                "The output frame rate differs from the requested delivery frame rate.",
                expected_fps=expected_fps,
                actual_fps=round(fps, 6),
            )
    else:
        check("frame_rate", True, f"Detected frame rate: {fps:.3f} FPS.")

    has_audio = bool(audio_streams)
    if expected_audio is True and not has_audio:
        check("audio_stream", False, "The timeline requires audio, but the export has none.")
        issue(
            "audio_stream_missing",
            "error",
            "The final video is missing its required audio stream.",
        )
    else:
        check(
            "audio_stream",
            True,
            f"{len(audio_streams)} audio stream(s) detected."
            if has_audio else "No audio stream is required for this output.",
        )

    ffmpeg = require_binary("ffmpeg")
    decode_timeout = max(300, min(timeout_seconds, int((duration or 60) * 8)))
    try:
        _run_diagnostic(
            [
                ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-xerror",
                "-i", str(path), "-map", "0:v:0?", "-map", "0:a?",
                "-f", "null", "-",
            ],
            timeout_seconds=decode_timeout,
            cancel_check=cancel_check,
        )
        check(
            "full_decode",
            True,
            "FFmpeg decoded the selected video/audio streams without fatal errors.",
        )
    except MediaJobCancelled:
        raise
    except (MediaToolError, OSError) as exc:
        check("full_decode", False, str(exc)[:500])
        issue(
            "media_decode_failed",
            "error",
            "FFmpeg reported a decoding error in the rendered media.",
        )

    try:
        _, visual_log = _run_diagnostic(
            [
                ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
                "-i", str(path), "-map", "0:v:0",
                "-vf",
                f"blackdetect=d={black_min_duration_seconds}:pix_th=0.10,"
                f"freezedetect=n=-50dB:d={freeze_min_duration_seconds}",
                "-an", "-f", "null", "-",
            ],
            timeout_seconds=decode_timeout,
            cancel_check=cancel_check,
        )
        black_segments = []
        for line in visual_log.splitlines():
            match = re.search(
                rf"black_start:({_NUMBER}).*?black_end:({_NUMBER}).*?black_duration:({_NUMBER})",
                line,
            )
            if match:
                black_segments.append(
                    {
                        "start_seconds": float(match.group(1)),
                        "end_seconds": float(match.group(2)),
                        "duration_seconds": float(match.group(3)),
                    }
                )

        freeze_starts = [
            float(value) for value in re.findall(rf"freeze_start:\s*({_NUMBER})", visual_log)
        ]
        freeze_durations = [
            float(value) for value in re.findall(rf"freeze_duration:\s*({_NUMBER})", visual_log)
        ]
        freeze_ends = [
            float(value) for value in re.findall(rf"freeze_end:\s*({_NUMBER})", visual_log)
        ]
        freeze_segments = []
        for start, measured_duration, end in zip_longest(
            freeze_starts, freeze_durations, freeze_ends
        ):
            if start is None:
                continue
            actual_duration = measured_duration
            if actual_duration is None and end is not None:
                actual_duration = max(0.0, end - start)
            if actual_duration is None and duration is not None:
                actual_duration = max(0.0, duration - start)
            if actual_duration is not None and actual_duration >= freeze_min_duration_seconds:
                freeze_segments.append(
                    {"start_seconds": start, "duration_seconds": actual_duration}
                )

        check("visual_scan", True, "Black-frame and frozen-frame filters completed.")
        if black_segments:
            issue(
                "black_frames_detected",
                "error",
                (
                    f"Detected {len(black_segments)} black segment(s) at or above "
                    "the configured duration."
                ),
                segments=black_segments[:50],
            )
        else:
            check("black_frames", True, "No black segment exceeded the configured threshold.")
        if freeze_segments:
            issue(
                "frozen_frames_detected",
                "error",
                (
                    f"Detected {len(freeze_segments)} frozen segment(s) at or above "
                    "the configured duration."
                ),
                segments=freeze_segments[:50],
            )
        else:
            check("frozen_frames", True, "No frozen segment exceeded the configured threshold.")
    except MediaJobCancelled:
        raise
    except (MediaToolError, OSError) as exc:
        check("visual_scan", False, str(exc)[:500])
        issue("visual_scan_failed", "error", "Black/frozen frame analysis could not be completed.")

    if has_audio:
        try:
            _, audio_log = _run_diagnostic(
                [
                    ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
                    "-i", str(path), "-map", "0:a:0",
                    "-af",
                    f"silencedetect=noise={silence_noise_db}dB:d={silence_min_duration_seconds},"
                    "astats=metadata=1:reset=0",
                    "-vn", "-f", "null", "-",
                ],
                timeout_seconds=decode_timeout,
                cancel_check=cancel_check,
            )
            silence_starts = [
                float(value) for value in re.findall(rf"silence_start:\s*({_NUMBER})", audio_log)
            ]
            silence_ends = [
                float(value) for value in re.findall(rf"silence_end:\s*({_NUMBER})", audio_log)
            ]
            silence_segments = []
            for index, start in enumerate(silence_starts):
                end = silence_ends[index] if index < len(silence_ends) else duration
                if end is None:
                    continue
                silence_duration = max(0.0, end - start)
                if silence_duration >= silence_min_duration_seconds:
                    silence_segments.append(
                        {
                            "start_seconds": start,
                            "end_seconds": end,
                            "duration_seconds": silence_duration,
                        }
                    )
            peaks = [
                float(value)
                for value in re.findall(rf"Peak level dB:\s*(-?{_NUMBER})", audio_log)
            ]
            check("audio_scan", True, "Silence and peak-level analysis completed.")
            if silence_segments:
                issue(
                    "long_silence_detected",
                    "warning",
                    (
                        f"Detected {len(silence_segments)} prolonged silent segment(s); "
                        "human review is required."
                    ),
                    segments=silence_segments[:50],
                )
            else:
                check("audio_silence", True, "No prolonged digital-silence segment was detected.")
            if peaks and max(peaks) >= -0.1:
                issue(
                    "audio_peak_near_clipping",
                    "warning",
                    "The audio peak is near digital full scale; listen for clipping.",
                    peak_dbfs=max(peaks),
                )
            elif peaks:
                check("audio_peak", True, f"Measured peak level: {max(peaks):.2f} dBFS.")
        except MediaJobCancelled:
            raise
        except (MediaToolError, OSError) as exc:
            check("audio_scan", False, str(exc)[:500])
            issue(
                "audio_scan_failed",
                "error",
                "Audio silence/peak analysis could not be completed.",
            )

    errors = sum(1 for item in report["issues"] if item["severity"] == "error")
    warnings = sum(1 for item in report["issues"] if item["severity"] == "warning")
    report["status"] = "failed" if errors else "passed_with_warnings" if warnings else "passed"
    report["can_export"] = errors == 0
    report["summary"] = {
        "error_count": errors,
        "warning_count": warnings,
        "check_count": len(report["checks"]),
    }
    return report


def quality_report_path(project_id: str, report_key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", report_key):
        raise ValueError("Invalid quality-report key.")
    directory = project_dir(project_id) / "quality-reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{report_key}.json"


def persist_quality_report(project_id: str, report_key: str, report: dict) -> Path:
    path = quality_report_path(project_id, report_key)
    atomic_write_json(path, report)
    return path


def quality_report_matches_file(report: dict, media_file: str | Path) -> bool:
    path = Path(media_file)
    if not path.is_file() or not report.get("can_export"):
        return False
    expected = report.get("file", {}).get("sha256")
    expected_size = report.get("file", {}).get("size_bytes")
    try:
        return bool(expected) and path.stat().st_size == expected_size and _sha256(path) == expected
    except OSError:
        return False
