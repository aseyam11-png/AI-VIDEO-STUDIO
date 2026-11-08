import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.media import pipeline
from app.media.storage import create_project


def test_build_timeline_writes_otio_and_json(tmp_path, monkeypatch):
    pytest.importorskip("opentimelineio")
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    project = create_project(
        {"name": "Timeline test", "target_duration_seconds": 10, "brief": "test"}
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    asset_id = "f503ab55-36f8-48d3-a0ee-2a1d573cb5db"

    manifest, timeline_path = pipeline.build_timeline(
        project_id=project["id"],
        asset_id=asset_id,
        source=source,
        scenes=[
            {"id": "scene_1", "start_seconds": 0.0, "duration_seconds": 2.0},
            {"id": "scene_2", "start_seconds": 2.0, "duration_seconds": 3.0},
        ],
        fps=30,
    )

    assert timeline_path.is_file()
    assert manifest["asset_id"] == asset_id
    assert len(manifest["clips"]) == 2
    assert (timeline_path.parent / f"{asset_id}.json").is_file()


def test_render_timeline_interleaves_audio_video_filter_inputs(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    destination = tmp_path / "export.mp4"
    captured = {}

    metadata = {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080},
            {"codec_type": "audio", "sample_rate": "48000"},
        ]
    }
    monkeypatch.setattr(pipeline, "probe_media", lambda _path: metadata)
    monkeypatch.setattr(pipeline, "require_binary", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(command, timeout=3600):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    result = pipeline.render_timeline(
        source=source,
        timeline_manifest={
            "clips": [
                {"start_seconds": 0.0, "duration_seconds": 2.0},
                {"start_seconds": 5.0, "duration_seconds": 3.0},
            ]
        },
        destination=destination,
    )

    filter_graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1" in filter_graph
    assert result["filename"] == "export.mp4"
    assert destination.read_bytes() == b"rendered"


def test_render_rejects_empty_timeline(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 500"):
        pipeline.render_timeline(
            source=tmp_path / "missing.mp4",
            timeline_manifest={"clips": []},
            destination=tmp_path / "out.mp4",
        )
