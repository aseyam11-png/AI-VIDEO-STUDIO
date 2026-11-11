from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.integrations.ffmpeg import MediaToolError, probe_media, require_binary


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _frame_rate(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", maxsplit=1)
            divisor = float(denominator)
            return float(numerator) / divisor if divisor else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _decode_and_analyse(
    path: Path,
    *,
    has_audio: bool,
    cancel_check: Callable[[], bool] | None,
    timeout_seconds: int = 7200,
) -> tuple[int, str]:
    ffmpeg = require_binary("ffmpeg")
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-50dB:d=1.0",
    ]
    if has_audio:
        command.extend(
            [
                "-map",
                "0:a?",
                "-af",
                "silencedetect=n=-55dB:d=1.0,volumedetect",
            ]
        )
    command.extend(["-f", "null", "-"])

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
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
            raise RuntimeError("Video quality analysis was cancelled.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            _, stderr = process.communicate()
            raise MediaToolError(
                f"Video quality analysis timed out after {timeout_seconds} seconds. "
                f"{stderr[-2000:]}"
            )
        try:
            _, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    return process.returncode or 0, stderr


def _segments(log: str, kind: str, duration_seconds: float) -> list[dict[str, float]]:
    if kind == "freeze":
        pattern = r"freezedetect\.freeze_(start|end|duration):\s*(-?\d+(?:\.\d+)?)"
    else:
        pattern = rf"{kind}_(start|end|duration):\s*(-?\d+(?:\.\d+)?)"

    segments: list[dict[str, float]] = []
    pending: dict[str, float] | None = None
    for match in re.finditer(pattern, log):
        key = match.group(1)
        value = float(match.group(2))
        if key == "start":
            if pending is not None:
                start = pending["start"]
                end = min(duration_seconds, pending.get("end", duration_seconds))
                segments.append(
                    {
                        "start_seconds": max(0.0, start),
                        "end_seconds": max(start, end),
                        "duration_seconds": max(0.0, pending.get("duration", end - start)),
                    }
                )
            pending = {"start": value}
            continue
        if pending is None:
            continue
        pending[key] = value
        if key == "end":
            start = pending["start"]
            end = max(start, value)
            segments.append(
                {
                    "start_seconds": max(0.0, start),
                    "end_seconds": min(duration_seconds, end),
                    "duration_seconds": max(0.0, min(end, duration_seconds) - start),
                }
            )
            pending = None

    if pending is not None:
        start = pending["start"]
        end = max(start, duration_seconds)
        segments.append(
            {
                "start_seconds": max(0.0, start),
                "end_seconds": end,
                "duration_seconds": max(0.0, end - start),
            }
        )
    return [item for item in segments if item["duration_seconds"] > 0.01]


def _check(
    checks: list[dict[str, Any]],
    identifier: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": identifier,
            "status": status,
            "message": message,
            "details": details or {},
        }
    )


def analyse_video_quality(
    video_path: str | Path,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    expected_duration_seconds: float | None = None,
    expected_audio: bool | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Decode and inspect the finished video before the file can be downloaded."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise MediaToolError("Quality analysis requires an existing, non-empty video file.")

    metadata = probe_media(path)
    streams = metadata.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    format_data = metadata.get("format", {})
    actual_duration = _number(format_data.get("duration"))
    actual_width = int(video_stream.get("width", 0)) if video_stream else None
    actual_height = int(video_stream.get("height", 0)) if video_stream else None
    actual_fps = (
        _frame_rate(video_stream.get("avg_frame_rate"))
        or _frame_rate(video_stream.get("r_frame_rate"))
        if video_stream
        else None
    )
    checks: list[dict[str, Any]] = []
    findings: dict[str, list[dict[str, float]]] = {
        "black_segments": [],
        "frozen_segments": [],
        "silence_segments": [],
    }

    _check(
        checks,
        "file",
        "pass",
        "The rendered file exists and is non-empty.",
        {"size_bytes": path.stat().st_size},
    )
    if video_stream is None:
        _check(checks, "video_stream", "fail", "No video stream was found.")
    else:
        _check(checks, "video_stream", "pass", "A video stream is present.")

    if actual_duration is None or actual_duration <= 0:
        _check(checks, "duration", "fail", "FFprobe could not determine a valid duration.")
    elif expected_duration_seconds is not None:
        tolerance = max(0.5, 2.0 / max(expected_fps or 24.0, 1.0))
        difference = abs(actual_duration - expected_duration_seconds)
        matches = difference <= tolerance
        _check(
            checks,
            "duration",
            "pass" if matches else "fail",
            "Duration matches the saved timeline."
            if matches
            else "Duration differs from the saved timeline.",
            {
                "expected_seconds": round(expected_duration_seconds, 6),
                "actual_seconds": round(actual_duration, 6),
                "tolerance_seconds": round(tolerance, 6),
            },
        )
    else:
        _check(
            checks,
            "duration",
            "pass",
            "Video duration is readable.",
            {"actual_seconds": round(actual_duration, 6)},
        )

    if video_stream is not None:
        if expected_width is not None and expected_height is not None:
            matches = actual_width == expected_width and actual_height == expected_height
            _check(
                checks,
                "resolution",
                "pass" if matches else "fail",
                "Resolution matches the requested export."
                if matches
                else "Resolution differs from the requested export.",
                {
                    "expected": f"{expected_width}x{expected_height}",
                    "actual": f"{actual_width}x{actual_height}",
                },
            )
        else:
            _check(
                checks,
                "resolution",
                "pass",
                "Video dimensions are readable.",
                {"actual": f"{actual_width}x{actual_height}"},
            )

        if expected_fps is not None:
            tolerance = max(0.25, expected_fps * 0.005)
            matches = actual_fps is not None and abs(actual_fps - expected_fps) <= tolerance
            _check(
                checks,
                "fps",
                "pass" if matches else "fail",
                "Frame rate matches the requested export."
                if matches
                else "Frame rate differs from the requested export.",
                {
                    "expected": expected_fps,
                    "actual": round(actual_fps, 6) if actual_fps is not None else None,
                    "tolerance": round(tolerance, 6),
                },
            )
        else:
            _check(
                checks,
                "fps",
                "pass" if actual_fps is not None else "warning",
                "Frame rate is readable." if actual_fps is not None else "Frame rate is unavailable.",
                {"actual": actual_fps},
            )

    has_audio = bool(audio_streams)
    if expected_audio is True and not has_audio:
        _check(
            checks,
            "audio_stream",
            "fail",
            "The export requires audio, but no audio stream was found.",
        )
    elif has_audio:
        _check(
            checks,
            "audio_stream",
            "pass",
            "At least one audio stream is present.",
            {
                "stream_count": len(audio_streams),
                "sample_rates": sorted(
                    {
                        str(stream.get("sample_rate"))
                        for stream in audio_streams
                        if stream.get("sample_rate")
                    }
                ),
            },
        )
    else:
        _check(
            checks,
            "audio_stream",
            "not_applicable",
            "No audio stream was expected for this export.",
        )

    analysis_log = ""
    if video_stream is not None:
        try:
            return_code, analysis_log = _decode_and_analyse(
                path,
                has_audio=has_audio,
                cancel_check=cancel_check,
            )
            _check(
                checks,
                "decode",
                "pass" if return_code == 0 else "fail",
                "FFmpeg decoded the complete export."
                if return_code == 0
                else "FFmpeg reported a media decode error.",
                {"return_code": return_code},
            )
            findings["black_segments"] = _segments(
                analysis_log, "black", actual_duration or 0.0
            )
            findings["frozen_segments"] = _segments(
                analysis_log, "freeze", actual_duration or 0.0
            )
            if has_audio:
                findings["silence_segments"] = _segments(
                    analysis_log, "silence", actual_duration or 0.0
                )
        except (MediaToolError, RuntimeError) as exc:
            _check(
                checks,
                "decode",
                "fail",
                "Full media analysis could not be completed.",
                {"error": str(exc)[:500]},
            )

    duration = actual_duration or 0.0
    black_durations = [item["duration_seconds"] for item in findings["black_segments"]]
    black_total = sum(black_durations)
    black_longest = max(black_durations, default=0.0)
    black_ratio = black_total / duration if duration > 0 else 0.0
    if black_total >= max(3.0, duration * 0.25):
        status = "fail"
        message = "Black frames exceed the severe-duration threshold."
    elif black_longest >= 0.5 or black_ratio >= 0.05:
        status = "warning"
        message = "Black segments were detected and should be visually reviewed."
    else:
        status = "pass"
        message = "No prolonged black-frame segment was detected."
    _check(
        checks,
        "black_frames",
        status,
        message,
        {
            "segment_count": len(findings["black_segments"]),
            "total_seconds": round(black_total, 4),
            "longest_seconds": round(black_longest, 4),
            "duration_ratio": round(black_ratio, 4),
            "segments": findings["black_segments"][:100],
        },
    )

    freeze_durations = [item["duration_seconds"] for item in findings["frozen_segments"]]
    freeze_total = sum(freeze_durations)
    freeze_longest = max(freeze_durations, default=0.0)
    freeze_ratio = freeze_total / duration if duration > 0 else 0.0
    if freeze_total >= max(2.5, duration * 0.20):
        status = "fail"
        message = "Frozen frames exceed the severe-duration threshold."
    elif freeze_longest >= 0.8 or freeze_ratio >= 0.05:
        status = "warning"
        message = "A frozen-frame segment was detected and should be reviewed."
    else:
        status = "pass"
        message = "No prolonged frozen-frame segment was detected."
    _check(
        checks,
        "frozen_frames",
        status,
        message,
        {
            "segment_count": len(findings["frozen_segments"]),
            "total_seconds": round(freeze_total, 4),
            "longest_seconds": round(freeze_longest, 4),
            "duration_ratio": round(freeze_ratio, 4),
            "segments": findings["frozen_segments"][:100],
        },
    )

    if has_audio:
        silence_durations = [
            item["duration_seconds"] for item in findings["silence_segments"]
        ]
        silence_total = sum(silence_durations)
        silence_longest = max(silence_durations, default=0.0)
        silence_ratio = silence_total / duration if duration > 0 else 0.0
        if duration >= 2.0 and silence_ratio >= 0.95:
            status = "fail"
            message = "The audio track is silent for nearly the entire export."
        elif silence_longest >= 2.0 or silence_ratio >= 0.25:
            status = "warning"
            message = "Long or repeated silent sections were detected."
        else:
            status = "pass"
            message = "No prolonged audio-silence issue was detected."
        _check(
            checks,
            "audio_silence",
            status,
            message,
            {
                "segment_count": len(findings["silence_segments"]),
                "total_seconds": round(silence_total, 4),
                "longest_seconds": round(silence_longest, 4),
                "duration_ratio": round(silence_ratio, 4),
                "segments": findings["silence_segments"][:100],
                "threshold": "-55 dBFS for at least one second",
            },
        )

        peak_match = re.search(
            r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB",
            analysis_log,
            flags=re.IGNORECASE,
        )
        peak = _number(peak_match.group(1)) if peak_match else None
        if peak is None:
            status = "warning"
            message = "Peak audio level could not be measured."
        elif peak >= 0.0:
            status = "fail"
            message = "Audio reached or exceeded 0 dBFS; clipping is likely."
        elif peak >= -1.0:
            status = "warning"
            message = "Audio peak is close to 0 dBFS; review for clipping."
        else:
            status = "pass"
            message = "Audio peak is below the clipping-risk threshold."
        _check(
            checks,
            "audio_peak",
            status,
            message,
            {
                "max_volume_dbfs": peak,
                "warning_threshold_dbfs": -1.0,
                "failure_threshold_dbfs": 0.0,
            },
        )
    else:
        _check(
            checks,
            "audio_silence",
            "not_applicable",
            "Silence analysis was skipped because there is no audio stream.",
        )
        _check(
            checks,
            "audio_peak",
            "not_applicable",
            "Peak analysis was skipped because there is no audio stream.",
        )

    failures = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {
        "schema_version": 1,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "file": {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _digest(path),
        },
        "expected": {
            "width": expected_width,
            "height": expected_height,
            "fps": expected_fps,
            "duration_seconds": expected_duration_seconds,
            "audio": expected_audio,
        },
        "actual": {
            "duration_seconds": actual_duration,
            "width": actual_width,
            "height": actual_height,
            "fps": actual_fps,
            "video_codec": video_stream.get("codec_name") if video_stream else None,
            "audio_codecs": sorted(
                {
                    str(stream.get("codec_name"))
                    for stream in audio_streams
                    if stream.get("codec_name")
                }
            ),
            "format_name": format_data.get("format_name"),
        },
        "findings": findings,
        "checks": checks,
        "summary": {
            "passed": not failures,
            "score": max(0, 100 - 25 * len(failures) - 5 * len(warnings)),
            "failure_count": len(failures),
            "warning_count": len(warnings),
            "check_count": len(checks),
            "failed_checks": [item["id"] for item in failures],
            "warning_checks": [item["id"] for item in warnings],
        },
        "passed": not failures,
    }
