import shutil
import subprocess

import pytest

from app.media.quality import analyze_video_quality


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and FFprobe are required for video quality checks",
)


def _make_video(
    path,
    *,
    video_filter="testsrc2=size=320x240:rate=24:duration=2",
    audio_filter="sine=frequency=660:sample_rate=48000:duration=2",
):
    command = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", video_filter,
    ]
    if audio_filter:
        command.extend(["-f", "lavfi", "-i", audio_filter, "-shortest"])
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio_filter:
        command.extend(["-c:a", "aac"])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def test_quality_report_accepts_valid_video_and_checks_delivery_metadata(tmp_path):
    video = tmp_path / "clean.mp4"
    _make_video(video)

    report = analyze_video_quality(
        video,
        expected_duration_seconds=2,
        expected_width=320,
        expected_height=240,
        expected_fps=24,
        expected_audio=True,
    )

    assert report["status"] in {"passed", "passed_with_warnings"}
    assert report["can_export"] is True
    assert report["file"]["sha256"]
    assert report["metrics"]["width"] == 320
    assert report["metrics"]["height"] == 240
    assert report["metrics"]["fps"] == pytest.approx(24, abs=0.5)
    assert not any(issue["severity"] == "error" for issue in report["issues"])


def test_quality_report_blocks_long_black_video(tmp_path):
    video = tmp_path / "black.mp4"
    _make_video(
        video,
        video_filter="color=c=black:s=320x240:r=24:d=2",
        audio_filter=None,
    )

    report = analyze_video_quality(video, expected_audio=False)

    assert report["can_export"] is False
    assert any(issue["code"] == "black_frames_detected" for issue in report["issues"])


def test_quality_report_warns_about_prolonged_silence(tmp_path):
    video = tmp_path / "silent-audio.mp4"
    _make_video(
        video,
        audio_filter="anullsrc=channel_layout=stereo:sample_rate=48000:duration=2",
    )

    report = analyze_video_quality(video, expected_audio=True)

    assert report["can_export"] is True
    assert any(issue["code"] == "long_silence_detected" for issue in report["issues"])


def test_quality_report_fails_closed_for_missing_or_corrupt_files(tmp_path):
    missing = analyze_video_quality(tmp_path / "missing.mp4")
    corrupt_file = tmp_path / "broken.mp4"
    corrupt_file.write_bytes(b"not an mp4 file")
    corrupt = analyze_video_quality(corrupt_file)

    assert missing["can_export"] is False
    assert any(issue["code"] == "file_missing" for issue in missing["issues"])
    assert corrupt["can_export"] is False
    assert any(issue["code"] == "container_probe_failed" for issue in corrupt["issues"])
