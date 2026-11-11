import shutil
import subprocess

import pytest

from app.integrations.ffmpeg import MediaToolError
from app.media.quality import analyse_video_quality


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and FFprobe are required for real media quality tests",
)


def _make_fixture(path, *, video_filter=None, audio_filter=None):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=3",
    ]
    if audio_filter is not None:
        command.extend(["-f", "lavfi", "-i", f"{audio_filter}:duration=3"])
    if video_filter:
        command.extend(["-vf", video_filter])
    if audio_filter is not None:
        command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(["-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(path)])
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def test_clean_video_passes_decode_and_technical_checks(tmp_path):
    path = tmp_path / "clean.mp4"
    _make_fixture(path, audio_filter="sine=frequency=440:sample_rate=48000")
    report = analyse_video_quality(
        path,
        expected_width=320,
        expected_height=240,
        expected_fps=24,
        expected_duration_seconds=3,
        expected_audio=True,
    )
    assert report["passed"] is True
    assert report["summary"]["failure_count"] == 0
    assert report["file"]["sha256"]
    assert {"decode", "resolution", "fps", "duration", "black_frames", "frozen_frames"} <= {
        item["id"] for item in report["checks"]
    }


def test_long_black_sequence_is_reported(tmp_path):
    path = tmp_path / "black.mp4"
    _make_fixture(
        path,
        video_filter="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill",
        audio_filter="sine=frequency=440:sample_rate=48000",
    )
    report = analyse_video_quality(
        path,
        expected_width=320,
        expected_height=240,
        expected_fps=24,
        expected_duration_seconds=3,
        expected_audio=True,
    )
    check = next(item for item in report["checks"] if item["id"] == "black_frames")
    assert check["status"] == "fail"
    assert report["passed"] is False


def test_frozen_sequence_is_detected(tmp_path):
    path = tmp_path / "frozen.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
        "-vf", "tpad=stop_mode=clone:stop_duration=2",
        "-map", "0:v:0", "-map", "1:a:0", "-t", "5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    report = analyse_video_quality(
        path,
        expected_width=320,
        expected_height=240,
        expected_fps=24,
        expected_duration_seconds=5,
        expected_audio=True,
    )
    check = next(item for item in report["checks"] if item["id"] == "frozen_frames")
    assert check["details"]["segment_count"] >= 1


def test_silent_audio_track_is_detected(tmp_path):
    path = tmp_path / "silent.mp4"
    _make_fixture(path, audio_filter="anullsrc=r=48000:cl=stereo")
    report = analyse_video_quality(
        path,
        expected_width=320,
        expected_height=240,
        expected_fps=24,
        expected_duration_seconds=3,
        expected_audio=True,
    )
    check = next(item for item in report["checks"] if item["id"] == "audio_silence")
    assert check["status"] in {"warning", "fail"}
    assert check["details"]["segment_count"] >= 1


def test_wrong_resolution_fails_the_gate(tmp_path):
    path = tmp_path / "wrong-resolution.mp4"
    _make_fixture(path, audio_filter="sine=frequency=440:sample_rate=48000")
    report = analyse_video_quality(
        path,
        expected_width=640,
        expected_height=360,
        expected_fps=24,
        expected_duration_seconds=3,
        expected_audio=True,
    )
    check = next(item for item in report["checks"] if item["id"] == "resolution")
    assert check["status"] == "fail"
    assert report["passed"] is False


def test_missing_and_corrupt_files_are_rejected(tmp_path):
    with pytest.raises(MediaToolError):
        analyse_video_quality(tmp_path / "missing.mp4")

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not an MP4 file")
    with pytest.raises(MediaToolError):
        analyse_video_quality(corrupt)
