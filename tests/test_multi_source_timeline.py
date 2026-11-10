import io
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.routes import media as media_route
from app.core.config import settings
from app.main import app
from app.media import pipeline
from app.media.storage import create_project, ingest_file_stream


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    return create_project(
        {
            "name": "Multi-source timeline",
            "target_duration_seconds": 30,
            "brief": "A sequence assembled from project assets.",
        }
    )


def _video_asset(project_id, filename):
    manifest, path = ingest_file_stream(
        project_id,
        filename,
        io.BytesIO(b"synthetic-video-bytes"),
        max_bytes=1024,
    )
    return manifest, path


def _probe_with_audio(path):
    if Path(path).name == "silent.mp4":
        streams = [{"codec_type": "video", "width": 640, "height": 360}]
    else:
        streams = [
            {"codec_type": "video", "width": 640, "height": 360},
            {"codec_type": "audio", "sample_rate": "44100"},
        ]
    return {"format": {"duration": "5.0"}, "streams": streams}


def test_build_multi_source_timeline_writes_project_manifest_and_otio(tmp_path, monkeypatch):
    pytest.importorskip("opentimelineio")
    project = _project(tmp_path, monkeypatch)
    first_id = "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c001"
    second_id = "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c002"
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    monkeypatch.setattr(pipeline, "probe_media", _probe_with_audio)

    manifest, otio_path = pipeline.build_multi_source_timeline(
        project_id=project["id"],
        sources={first_id: first, second_id: second},
        clips=[
            {
                "clip_id": "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c101",
                "asset_id": first_id,
                "source_start_seconds": 1.0,
                "duration_seconds": 2.0,
            },
            {
                "clip_id": "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c102",
                "asset_id": second_id,
                "source_start_seconds": 0.5,
                "duration_seconds": 3.0,
            },
        ],
        fps=24,
    )

    assert otio_path.is_file()
    assert manifest["schema_version"] == 2
    assert manifest["project_id"] == project["id"]
    assert manifest["duration_seconds"] == 5.0
    assert [clip["asset_id"] for clip in manifest["clips"]] == [first_id, second_id]
    assert [clip["timeline_start_seconds"] for clip in manifest["clips"]] == [0.0, 2.0]
    assert (otio_path.parent / f"project-{project['id']}.json").is_file()


def test_build_multi_source_timeline_rejects_ranges_beyond_source(tmp_path, monkeypatch):
    pytest.importorskip("opentimelineio")
    project = _project(tmp_path, monkeypatch)
    asset_id = "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c003"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(pipeline, "probe_media", _probe_with_audio)

    with pytest.raises(ValueError, match="exceeds its source video duration"):
        pipeline.build_multi_source_timeline(
            project_id=project["id"],
            sources={asset_id: source},
            clips=[
                {
                    "asset_id": asset_id,
                    "source_start_seconds": 4.0,
                    "duration_seconds": 2.0,
                }
            ],
        )


def test_render_multi_source_timeline_handles_missing_audio_and_normalizes_inputs(
    tmp_path, monkeypatch
):
    first_id = "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c011"
    second_id = "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c012"
    first = tmp_path / "with-audio.mp4"
    second = tmp_path / "silent.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    destination = tmp_path / "exports" / "preview.mp4"
    captured = {}

    monkeypatch.setattr(pipeline, "probe_media", _probe_with_audio)
    monkeypatch.setattr(pipeline, "require_binary", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(command, timeout=3600, cancel_check=None):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"rendered-video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    result = pipeline.render_multi_source_timeline(
        sources={first_id: first, second_id: second},
        timeline_manifest={
            "schema_version": 2,
            "clips": [
                {
                    "asset_id": first_id,
                    "source_start_seconds": 0.0,
                    "duration_seconds": 2.0,
                },
                {
                    "asset_id": second_id,
                    "source_start_seconds": 1.0,
                    "duration_seconds": 2.0,
                },
            ],
        },
        destination=destination,
        width=640,
        height=360,
        fps=24,
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 2
    assert "[0:v:0]" in filter_graph and "[1:v:0]" in filter_graph
    assert "anullsrc=r=48000:cl=stereo" in filter_graph
    assert "concat=n=2:v=1:a=1" in filter_graph
    assert result["clip_count"] == 2
    assert destination.read_bytes() == b"rendered-video"


def test_project_timeline_api_saves_reads_and_renders_assets(
    tmp_path, monkeypatch
):
    pytest.importorskip("opentimelineio")
    project = _project(tmp_path, monkeypatch)
    first, _ = _video_asset(project["id"], "first.mp4")
    second, _ = _video_asset(project["id"], "second.mp4")
    monkeypatch.setattr(pipeline, "probe_media", _probe_with_audio)
    client = TestClient(app)

    payload = {
        "fps": 24,
        "clips": [
            {
                "asset_id": first["id"],
                "source_start_seconds": 0,
                "duration_seconds": 2,
            },
            {
                "asset_id": second["id"],
                "source_start_seconds": 1,
                "duration_seconds": 2,
            },
        ],
    }
    saved = client.put(f"/v1/projects/{project['id']}/timeline", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["schema_version"] == 2
    assert len(saved.json()["clips"]) == 2

    loaded = client.get(f"/v1/projects/{project['id']}/timeline")
    assert loaded.status_code == 200
    assert loaded.json()["clips"] == saved.json()["clips"]

    def fake_render(**kwargs):
        destination = kwargs["destination"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"project-preview")
        return {"filename": destination.name, "size_bytes": destination.stat().st_size}

    monkeypatch.setattr(media_route, "render_multi_source_timeline", fake_render)
    rendered = client.post(
        f"/v1/projects/{project['id']}/timeline/render",
        json={"width": 640, "height": 360, "fps": 24},
    )
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["download_url"].endswith("/timeline/rendered")

    download = client.get(f"/v1/projects/{project['id']}/timeline/rendered")
    assert download.status_code == 200
    assert download.content == b"project-preview"

from fastapi.testclient import TestClient


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for multi-source render integration test",
)
def test_render_multi_source_timeline_with_real_ffmpeg_and_mixed_audio(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None

    with_audio = tmp_path / "with-audio.mp4"
    silent = tmp_path / "silent.mp4"
    output = tmp_path / "exports" / "multi-source-preview.mp4"

    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24:duration=1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1.5",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(with_audio),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=1.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    result = pipeline.render_multi_source_timeline(
        sources={
            "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c021": with_audio,
            "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c022": silent,
        },
        timeline_manifest={
            "schema_version": 2,
            "clips": [
                {
                    "asset_id": "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c021",
                    "source_start_seconds": 0.25,
                    "duration_seconds": 1.0,
                },
                {
                    "asset_id": "3eac5d2d-3a45-4cc4-a3b3-3a7f0fd2c022",
                    "source_start_seconds": 0.25,
                    "duration_seconds": 1.0,
                },
            ],
        },
        destination=output,
        width=320,
        height=240,
        fps=24,
        crf=28,
    )

    assert output.is_file()
    assert result["size_bytes"] > 0
    assert result["clip_count"] == 2
    streams = result["metadata"]["streams"]
    assert any(stream.get("codec_type") == "video" for stream in streams)
    assert any(stream.get("codec_type") == "audio" for stream in streams)
    duration = float(result["metadata"]["format"]["duration"])
    assert 1.8 <= duration <= 2.2

