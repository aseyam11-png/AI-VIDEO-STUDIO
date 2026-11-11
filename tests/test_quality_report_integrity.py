import shutil
import subprocess

import pytest
from app.media.quality import analyze_video_quality, quality_report_matches_file


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and FFprobe are required for video quality checks",
)


def _make_video(path, filter_value):
    command = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", filter_value,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def test_freezedetect_blocks_a_static_video(tmp_path):
    path = tmp_path / "freeze.mp4"
    _make_video(path, "color=c=red:s=320x240:r=24:d=3")

    report = analyze_video_quality(path, expected_audio=False)

    assert report["can_export"] is False
    assert any(issue["code"] == "frozen_frames_detected" for issue in report["issues"])


def test_report_does_not_authorize_modified_file_bytes(tmp_path):
    path = tmp_path / "moving.mp4"
    _make_video(path, "testsrc2=size=320x240:rate=24:duration=2")

    report = analyze_video_quality(path, expected_audio=False)
    assert report["can_export"] is True
    assert quality_report_matches_file(report, path) is True

    with path.open("ab") as stream:
        stream.write(b"tampered-after-quality-check")

    assert quality_report_matches_file(report, path) is False


def test_missing_required_audio_stream_blocks_export(tmp_path):
    path = tmp_path / "video-without-audio.mp4"
    _make_video(path, "testsrc2=size=320x240:rate=24:duration=2")

    report = analyze_video_quality(path, expected_audio=True)

    assert report["can_export"] is False
    assert any(issue["code"] == "audio_stream_missing" for issue in report["issues"])
