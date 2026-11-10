import io
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from app.api.routes import media as media_route
from app.core.config import settings
from app.main import app
from app.media import pipeline
from app.media.storage import create_project, ingest_file_stream
from fastapi.testclient import TestClient


def _project(tmp_path, monkeypatch, name="Visual effects project"):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    return create_project(
        {
            "name": name,
            "target_duration_seconds": 30,
            "brief": "Test project for transitions, titles and image overlays.",
        }
    )


def _asset(project_id, filename):
    return ingest_file_stream(
        project_id,
        filename,
        io.BytesIO(b"synthetic-media-bytes"),
        max_bytes=1024,
    )


def _probe(path):
    source = Path(path)
    if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        streams = [{"codec_type": "video", "width": 80, "height": 40}]
        duration = "0.04"
    else:
        streams = [{"codec_type": "video", "width": 320, "height": 240}]
        duration = "12.0"
    return {"format": {"duration": duration}, "streams": streams}


def _save_two_clip_timeline(client, project_id, video_id, clip_ids):
    return client.put(
        f"/v1/projects/{project_id}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": clip_ids[0],
                    "asset_id": video_id,
                    "source_start_seconds": 0,
                    "duration_seconds": 5,
                },
                {
                    "clip_id": clip_ids[1],
                    "asset_id": video_id,
                    "source_start_seconds": 5,
                    "duration_seconds": 5,
                },
            ],
        },
    )


def test_visual_effects_api_persists_and_reads_project_scoped_manifest(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "source.mp4")
    logo, _ = _asset(project["id"], "brand-logo.png")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    clip_ids = [str(uuid4()), str(uuid4())]
    timeline = _save_two_clip_timeline(client, project["id"], video["id"], clip_ids)
    assert timeline.status_code == 200, timeline.text

    payload = {
        "transitions": [
            {"after_clip_id": clip_ids[0], "style": "dip_to_black", "duration_seconds": 0.4}
        ],
        "titles": [
            {
                "text": "مرحبا في الاستديو",
                "start_seconds": 0.5,
                "duration_seconds": 3,
                "font_size": 42,
                "color": "#FFFFFF",
                "position": "center",
                "animation": "fade",
                "background": True,
            }
        ],
        "overlays": [
            {
                "asset_id": logo["id"],
                "start_seconds": 1,
                "duration_seconds": 4,
                "width_ratio": 0.25,
                "opacity": 0.8,
                "position": "top_right",
                "animation": "slide_up",
            }
        ],
    }
    saved = client.put(f"/v1/projects/{project['id']}/visual-effects", json=payload)
    assert saved.status_code == 200, saved.text
    manifest = saved.json()
    assert manifest["schema_version"] == 1
    assert manifest["transitions"][0]["after_clip_id"] == clip_ids[0]
    assert manifest["titles"][0]["text"] == "مرحبا في الاستديو"
    assert manifest["overlays"][0]["asset_id"] == logo["id"]

    loaded = client.get(f"/v1/projects/{project['id']}/visual-effects")
    assert loaded.status_code == 200
    assert loaded.json() == manifest


def test_visual_effects_reject_last_clip_boundary_and_out_of_range_title(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "source.mp4")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    clip_ids = [str(uuid4()), str(uuid4())]
    timeline = _save_two_clip_timeline(client, project["id"], video["id"], clip_ids)
    assert timeline.status_code == 200, timeline.text

    invalid_transition = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={
            "transitions": [
                {"after_clip_id": clip_ids[-1], "style": "dip_to_black", "duration_seconds": 0.3}
            ]
        },
    )
    assert invalid_transition.status_code == 422

    invalid_title = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={
            "titles": [
                {
                    "text": "Outside timeline",
                    "start_seconds": 9,
                    "duration_seconds": 2,
                }
            ]
        },
    )
    assert invalid_title.status_code == 422


def test_visual_effects_reject_foreign_and_non_image_assets(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "First project")
    other = _project(tmp_path, monkeypatch, "Other project")
    video, _ = _asset(project["id"], "source.mp4")
    other_logo, _ = _asset(other["id"], "other-logo.png")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    clip_ids = [str(uuid4()), str(uuid4())]
    timeline = _save_two_clip_timeline(client, project["id"], video["id"], clip_ids)
    assert timeline.status_code == 200, timeline.text

    foreign = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={
            "overlays": [
                {
                    "asset_id": other_logo["id"],
                    "start_seconds": 0,
                    "duration_seconds": 2,
                }
            ]
        },
    )
    assert foreign.status_code == 404

    not_image = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={"overlays": [{"asset_id": video["id"], "start_seconds": 0, "duration_seconds": 2}]},
    )
    assert not_image.status_code == 422


def test_timeline_render_passes_project_visual_effects_and_image_sources(
    tmp_path, monkeypatch
):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "source.mp4")
    logo, logo_path = _asset(project["id"], "logo.png")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        destination = kwargs["destination"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"rendered-video")
        return {
            "filename": destination.name,
            "size_bytes": destination.stat().st_size,
            "clip_count": 2,
            "transition_count": 0,
            "title_count": 0,
            "overlay_count": 1,
            "audio_clip_count": 0,
            "settings": {"width": 320, "height": 240, "fps": 24, "crf": 28},
            "metadata": {"format": {"duration": "10.0"}},
        }

    monkeypatch.setattr(media_route, "render_multi_source_timeline", fake_render)
    client = TestClient(app)
    clip_ids = [str(uuid4()), str(uuid4())]
    timeline = _save_two_clip_timeline(client, project["id"], video["id"], clip_ids)
    assert timeline.status_code == 200, timeline.text

    saved = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={
            "overlays": [
                {
                    "asset_id": logo["id"],
                    "start_seconds": 1,
                    "duration_seconds": 4,
                    "width_ratio": 0.25,
                    "opacity": 0.9,
                    "position": "top_right",
                    "animation": "slide_up",
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    rendered = client.post(
        f"/v1/projects/{project['id']}/timeline/render",
        json={"width": 320, "height": 240, "fps": 24, "crf": 28},
    )
    assert rendered.status_code == 200, rendered.text
    assert captured["visual_effects_manifest"]["overlays"][0]["asset_id"] == logo["id"]
    assert captured["overlay_sources"][logo["id"]] == logo_path
    assert rendered.json()["overlay_count"] == 1


def test_saving_new_timeline_invalidates_previous_visual_effects(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "source.mp4")
    logo, _ = _asset(project["id"], "logo.png")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    clip_ids = [str(uuid4()), str(uuid4())]
    timeline = _save_two_clip_timeline(client, project["id"], video["id"], clip_ids)
    assert timeline.status_code == 200, timeline.text

    saved = client.put(
        f"/v1/projects/{project['id']}/visual-effects",
        json={"overlays": [{"asset_id": logo["id"], "start_seconds": 0, "duration_seconds": 2}]},
    )
    assert saved.status_code == 200, saved.text

    changed = client.put(
        f"/v1/projects/{project['id']}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": video["id"],
                    "source_start_seconds": 0,
                    "duration_seconds": 4,
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    reloaded = client.get(f"/v1/projects/{project['id']}/visual-effects")
    assert reloaded.status_code == 200
    assert reloaded.json()["transitions"] == []
    assert reloaded.json()["titles"] == []
    assert reloaded.json()["overlays"] == []


@pytest.mark.skipif(
    not shutil.which("ffmpeg")
    or not Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").is_file(),
    reason="FFmpeg and DejaVu Sans are required for the visual effects integration test",
)
def test_real_ffmpeg_render_applies_transition_arabic_title_and_image_overlay(tmp_path):
    project_id = str(uuid4())
    video_id = str(uuid4())
    first_clip_id = str(uuid4())
    second_clip_id = str(uuid4())
    logo_id = str(uuid4())
    source = tmp_path / "source.mp4"
    logo = tmp_path / "logo.png"
    destination = tmp_path / "exports" / "visual-preview.mp4"

    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24:duration=3",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=80x40",
            "-frames:v",
            "1",
            str(logo),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    timeline = {
        "schema_version": 2,
        "project_id": project_id,
        "duration_seconds": 3,
        "clips": [
            {
                "clip_id": first_clip_id,
                "asset_id": video_id,
                "source_start_seconds": 0,
                "duration_seconds": 1.5,
            },
            {
                "clip_id": second_clip_id,
                "asset_id": video_id,
                "source_start_seconds": 1.5,
                "duration_seconds": 1.5,
            },
        ],
    }
    effects = {
        "schema_version": 1,
        "project_id": project_id,
        "transitions": [
            {"after_clip_id": first_clip_id, "style": "dip_to_black", "duration_seconds": 0.3}
        ],
        "titles": [
            {
                "title_id": str(uuid4()),
                "text": "مرحبا في الاستديو",
                "start_seconds": 0.2,
                "duration_seconds": 2.5,
                "font_size": 30,
                "color": "#FFFFFF",
                "position": "center",
                "animation": "fade",
                "background": True,
            }
        ],
        "overlays": [
            {
                "overlay_id": str(uuid4()),
                "asset_id": logo_id,
                "start_seconds": 0,
                "duration_seconds": 3,
                "width_ratio": 0.25,
                "opacity": 0.85,
                "position": "top_right",
                "animation": "slide_up",
            }
        ],
    }

    result = pipeline.render_multi_source_timeline(
        sources={video_id: source},
        timeline_manifest=timeline,
        destination=destination,
        width=320,
        height=240,
        fps=24,
        crf=28,
        visual_effects_manifest=effects,
        overlay_sources={logo_id: logo},
    )
    assert destination.is_file()
    assert result["transition_count"] == 1
    assert result["title_count"] == 1
    assert result["overlay_count"] == 1
    assert float(result["metadata"]["format"]["duration"]) == pytest.approx(3, abs=0.1)
