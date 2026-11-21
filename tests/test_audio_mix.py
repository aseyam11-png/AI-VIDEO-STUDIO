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


def _write_test_video(
    path: Path,
    *,
    duration: int,
    width: int,
    height: int,
    audio: bool,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for export quality integration tests.")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=24:duration={duration}",
    ]
    if audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=660:sample_rate=48000:duration={duration}",
                "-shortest",
            ]
        )
    else:
        command.append("-an")
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio:
        command.extend(["-c:a", "aac"])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    return create_project(
        {
            "name": "Audio mix project",
            "target_duration_seconds": 30,
            "brief": "Test project for synchronized audio mixing.",
        }
    )


def _asset(project_id, filename):
    manifest, path = ingest_file_stream(
        project_id,
        filename,
        io.BytesIO(b"synthetic-media-bytes"),
        max_bytes=1024,
    )
    return manifest, path


def _probe(path):
    name = Path(path).name
    streams = [{"codec_type": "audio", "sample_rate": "48000"}]
    if name.endswith(".mp4") or name == "video.mp4":
        streams.insert(0, {"codec_type": "video", "width": 320, "height": 240})
    return {"format": {"duration": "12.0"}, "streams": streams}


def _saved_timeline(client, project, video):
    return client.put(
        f"/v1/projects/{project['id']}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": video["id"],
                    "source_start_seconds": 0,
                    "duration_seconds": 10,
                }
            ],
        },
    )


def test_audio_uploads_are_registered_as_audio_assets(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    asset, path = _asset(project["id"], "voice.wav")
    assert asset["asset_type"] == "audio"
    assert path.suffix == ".wav"
    with pytest.raises(ValueError, match="Unsupported media extension"):
        ingest_file_stream(
            project["id"], "voice.exe", io.BytesIO(b"bad"), max_bytes=1024
        )


def test_audio_upload_endpoint_requires_an_audio_stream(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(media_route, "probe_media", lambda _path: _probe("voice.wav"))
    client = TestClient(app)
    response = client.post(
        f"/v1/projects/{project['id']}/assets",
        files={"file": ("voice.wav", b"audio-content", "audio/wav")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["asset_type"] == "audio"

    monkeypatch.setattr(
        media_route,
        "probe_media",
        lambda _path: {"format": {"duration": "3"}, "streams": [{"codec_type": "video"}]},
    )
    invalid = client.post(
        f"/v1/projects/{project['id']}/assets",
        files={"file": ("bad.wav", b"not-audio", "audio/wav")},
    )
    assert invalid.status_code == 422


def test_audio_mix_api_saves_levels_and_timing_and_invalidates_preview(
    tmp_path, monkeypatch
):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "video.mp4")
    voice, _ = _asset(project["id"], "voice.wav")
    music, _ = _asset(project["id"], "music.mp3")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    saved_timeline = _saved_timeline(client, project, video)
    assert saved_timeline.status_code == 200, saved_timeline.text

    response = client.put(
        f"/v1/projects/{project['id']}/audio-mix",
        json={
            "source_audio_enabled": False,
            "source_audio_denoise": False,
            "levels_db": {"voice": -3, "music": -8, "sfx": -5},
            "clips": [
                {
                    "asset_id": voice["id"],
                    "role": "voice",
                    "timeline_start_seconds": 1.5,
                    "source_start_seconds": 0,
                    "duration_seconds": 4,
                    "gain_db": 2,
                    "fade_in_seconds": 0.4,
                    "fade_out_seconds": 0.5,
                    "denoise": True,
                },
                {
                    "asset_id": music["id"],
                    "role": "music",
                    "timeline_start_seconds": 0,
                    "source_start_seconds": 1,
                    "duration_seconds": 10,
                    "gain_db": 0,
                    "fade_in_seconds": 1,
                    "fade_out_seconds": 1,
                    "denoise": False,
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["schema_version"] == 1
    assert manifest["levels_db"] == {"voice": -3, "music": -8, "sfx": -5}
    assert manifest["clips"][0]["timeline_start_seconds"] == 1.5
    assert manifest["clips"][0]["denoise"] is True

    loaded = client.get(f"/v1/projects/{project['id']}/audio-mix")
    assert loaded.status_code == 200
    assert loaded.json() == manifest

    preview = (
        media_route.project_dir(project["id"])
        / "exports"
        / f"project-{project['id']}-preview.mp4"
    )
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"stale-preview")
    cleared = client.put(
        f"/v1/projects/{project['id']}/audio-mix",
        json={"clips": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert not preview.exists()


def test_audio_mix_rejects_foreign_assets_invalid_ranges_and_non_audio_sources(
    tmp_path, monkeypatch
):
    project = _project(tmp_path, monkeypatch)
    other = create_project(
        {
            "name": "Foreign audio project",
            "target_duration_seconds": 30,
            "brief": "Separate project for scope validation.",
        }
    )
    video, _ = _asset(project["id"], "video.mp4")
    audio, _ = _asset(project["id"], "voice.wav")
    foreign_audio, _ = _asset(other["id"], "foreign.wav")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    assert _saved_timeline(client, project, video).status_code == 200

    def save_clip(asset_id, start=0, duration=3):
        return client.put(
            f"/v1/projects/{project['id']}/audio-mix",
            json={
                "clips": [
                    {
                        "asset_id": asset_id,
                        "role": "voice",
                        "timeline_start_seconds": start,
                        "source_start_seconds": 0,
                        "duration_seconds": duration,
                    }
                ]
            },
        )

    assert save_clip(foreign_audio["id"]).status_code == 404
    assert save_clip(audio["id"], start=9, duration=2).status_code == 422
    assert save_clip(audio["id"], start=0, duration=20).status_code == 422
    assert save_clip(video["id"]).status_code == 200

    def non_audio_probe(_path):
        return {
            "format": {"duration": "12"},
            "streams": [{"codec_type": "video", "width": 320, "height": 240}],
        }
    monkeypatch.setattr(media_route, "probe_media", non_audio_probe)
    response = save_clip(video["id"])
    assert response.status_code == 422


def test_render_pipeline_builds_voice_music_sync_denoise_and_fades(tmp_path, monkeypatch):
    video_id = str(uuid4())
    voice_id = str(uuid4())
    music_id = str(uuid4())
    video = tmp_path / "video.mp4"
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    for path in (video, voice, music):
        path.write_bytes(b"synthetic")

    probes = {
        "video.mp4": {
            "format": {"duration": "10"},
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio", "sample_rate": "48000"},
            ],
        },
        "voice.wav": _probe("voice.wav"),
        "music.wav": _probe("music.wav"),
        "output.partial.mp4": {
            "format": {"duration": "10"},
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio", "sample_rate": "48000"},
            ],
        },
    }
    monkeypatch.setattr(pipeline, "probe_media", lambda path: probes[Path(path).name])
    monkeypatch.setattr(pipeline, "require_binary", lambda _name: "/usr/bin/ffmpeg")
    captured = {}

    def fake_run(command, timeout=7200, cancel_check=None):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"rendered-video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    project_id = str(uuid4())
    result = pipeline.render_multi_source_timeline(
        sources={video_id: video},
        timeline_manifest={
            "schema_version": 2,
            "project_id": project_id,
            "clips": [
                {
                    "asset_id": video_id,
                    "source_start_seconds": 0,
                    "duration_seconds": 10,
                }
            ],
        },
        audio_mix_manifest={
            "schema_version": 1,
            "project_id": project_id,
            "source_audio_enabled": True,
            "source_audio_denoise": True,
            "levels_db": {"voice": -6, "music": -10, "sfx": -4},
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": voice_id,
                    "role": "voice",
                    "timeline_start_seconds": 1.25,
                    "source_start_seconds": 0.5,
                    "duration_seconds": 3,
                    "gain_db": 2,
                    "fade_in_seconds": 0.25,
                    "fade_out_seconds": 0.5,
                    "denoise": True,
                },
                {
                    "clip_id": str(uuid4()),
                    "asset_id": music_id,
                    "role": "music",
                    "timeline_start_seconds": 0,
                    "source_start_seconds": 0,
                    "duration_seconds": 8,
                    "gain_db": 0,
                    "fade_in_seconds": 1,
                    "fade_out_seconds": 1,
                    "denoise": False,
                },
            ],
        },
        audio_sources={voice_id: voice, music_id: music},
        destination=tmp_path / "output.mp4",
    )
    filter_graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert captured["command"].count("-i") == 3
    assert "afftdn=nr=12:nf=-30" in filter_graph
    assert "adelay=delays=1250|1250:all=1" in filter_graph
    assert "afade=t=in:st=0:d=0.250000" in filter_graph
    assert "afade=t=out:st=2.500000:d=0.500000" in filter_graph
    assert "amix=inputs=3" in filter_graph
    assert "alimiter=limit=0.95" in filter_graph
    assert "-map" in captured["command"] and "[a_final]" in captured["command"]
    assert result["audio_clip_count"] == 2



def test_audio_mix_api_rejects_audio_when_timeline_is_missing(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.put(
        f"/v1/projects/{project['id']}/audio-mix",
        json={"clips": []},
    )
    assert response.status_code == 404


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for audio mix export quality integration",
)
def test_audio_mix_api_accepts_video_audio_source_and_render_passes_manifest(
    tmp_path, monkeypatch
):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "video.mp4")
    voice, _ = _asset(project["id"], "voice.wav")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)
    assert _saved_timeline(client, project, video).status_code == 200
    mixed = client.put(
        f"/v1/projects/{project['id']}/audio-mix",
        json={
            "source_audio_enabled": False,
            "clips": [
                {
                    "asset_id": voice["id"],
                    "role": "voice",
                    "timeline_start_seconds": 0,
                    "source_start_seconds": 0,
                    "duration_seconds": 5,
                }
            ],
        },
    )
    assert mixed.status_code == 200, mixed.text
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        output = kwargs["destination"]
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_test_video(
            output,
            duration=10,
            width=640,
            height=360,
            audio=True,
        )
        return {
            "filename": output.name,
            "size_bytes": output.stat().st_size,
            "audio_clip_count": 1,
        }

    monkeypatch.setattr(media_route, "render_multi_source_timeline", fake_render)
    rendered = client.post(
        f"/v1/projects/{project['id']}/timeline/render",
        json={"width": 640, "height": 360, "fps": 24},
    )
    assert rendered.status_code == 200, rendered.text
    assert captured["audio_mix_manifest"]["clips"][0]["asset_id"] == voice["id"]
    assert voice["id"] in captured["audio_sources"]


def test_saving_a_new_video_timeline_invalidates_previous_audio_mix(
    tmp_path, monkeypatch
):
    project = _project(tmp_path, monkeypatch)
    video, _ = _asset(project["id"], "video.mp4")
    voice, _ = _asset(project["id"], "voice.wav")
    monkeypatch.setattr(media_route, "probe_media", _probe)
    monkeypatch.setattr(pipeline, "probe_media", _probe)
    client = TestClient(app)

    first_timeline = _saved_timeline(client, project, video)
    assert first_timeline.status_code == 200, first_timeline.text
    audio_mix = client.put(
        f"/v1/projects/{project['id']}/audio-mix",
        json={
            "clips": [
                {
                    "asset_id": voice["id"],
                    "role": "voice",
                    "timeline_start_seconds": 1,
                    "source_start_seconds": 0,
                    "duration_seconds": 3,
                }
            ]
        },
    )
    assert audio_mix.status_code == 200, audio_mix.text
    audio_path = (
        media_route.project_dir(project["id"])
        / "timelines"
        / f"project-{project['id']}-audio-mix.json"
    )
    assert audio_path.is_file()

    second_timeline = client.put(
        f"/v1/projects/{project['id']}/timeline",
        json={
            "fps": 24,
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": video["id"],
                    "source_start_seconds": 1,
                    "duration_seconds": 8,
                }
            ],
        },
    )
    assert second_timeline.status_code == 200, second_timeline.text
    assert not audio_path.exists()
    fresh = client.get(f"/v1/projects/{project['id']}/audio-mix")
    assert fresh.status_code == 200
    assert fresh.json()["clips"] == []
    assert fresh.json()["timeline_duration_seconds"] == 8


def test_audio_mix_pipeline_can_render_music_without_original_video_audio(
    tmp_path, monkeypatch
):
    video_id, music_id = str(uuid4()), str(uuid4())
    video, music = tmp_path / "video.mp4", tmp_path / "music.wav"
    video.write_bytes(b"video")
    music.write_bytes(b"music")
    monkeypatch.setattr(
        pipeline,
        "probe_media",
        lambda path: (
            {
                "format": {"duration": "10"},
                "streams": [{"codec_type": "video"}],
            }
            if Path(path).name == "video.mp4"
            else _probe(path)
        ),
    )
    monkeypatch.setattr(pipeline, "require_binary", lambda _name: "/usr/bin/ffmpeg")
    captured = {}

    def fake_run(command, timeout=7200, cancel_check=None):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    project_id = str(uuid4())
    result = pipeline.render_multi_source_timeline(
        sources={video_id: video},
        timeline_manifest={
            "schema_version": 2,
            "project_id": project_id,
            "clips": [
                {"asset_id": video_id, "source_start_seconds": 0, "duration_seconds": 10}
            ],
        },
        audio_mix_manifest={
            "schema_version": 1,
            "project_id": project_id,
            "source_audio_enabled": False,
            "levels_db": {"voice": 0, "music": -5, "sfx": 0},
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": music_id,
                    "role": "music",
                    "timeline_start_seconds": 1,
                    "source_start_seconds": 0,
                    "duration_seconds": 5,
                    "gain_db": 0,
                    "fade_in_seconds": 0,
                    "fade_out_seconds": 0,
                    "denoise": False,
                }
            ],
        },
        audio_sources={music_id: music},
        destination=tmp_path / "music-only.mp4",
    )
    filter_graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "concat=n=1:v=1:a=0[vout]" in filter_graph
    assert "adelay=delays=1000|1000:all=1" in filter_graph
    assert "-an" not in captured["command"]
    assert result["audio_clip_count"] == 1


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg is required for the real audio-mix export test",
)
def test_audio_mix_real_ffmpeg_places_and_mixes_external_music(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    video = tmp_path / "video.mp4"
    music = tmp_path / "music.wav"
    output = tmp_path / "exports" / "mixed.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=3",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(video),
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2.5",
            "-c:a", "pcm_s16le", str(music),
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    video_id, music_id = str(uuid4()), str(uuid4())
    project_id = str(uuid4())
    result = pipeline.render_multi_source_timeline(
        sources={video_id: video},
        timeline_manifest={
            "schema_version": 2,
            "project_id": project_id,
            "clips": [
                {"asset_id": video_id, "source_start_seconds": 0, "duration_seconds": 3}
            ],
        },
        audio_mix_manifest={
            "schema_version": 1,
            "project_id": project_id,
            "source_audio_enabled": True,
            "levels_db": {"voice": -8, "music": -12, "sfx": -6},
            "clips": [
                {
                    "clip_id": str(uuid4()),
                    "asset_id": music_id,
                    "role": "music",
                    "timeline_start_seconds": 0.5,
                    "source_start_seconds": 0.25,
                    "duration_seconds": 2,
                    "gain_db": 0,
                    "fade_in_seconds": 0.2,
                    "fade_out_seconds": 0.2,
                    "denoise": False,
                }
            ],
        },
        audio_sources={music_id: music},
        destination=output,
        width=320,
        height=240,
        fps=24,
        crf=28,
    )
    assert output.is_file()
    assert result["audio_clip_count"] == 1
    streams = result["metadata"]["streams"]
    assert any(stream.get("codec_type") == "video" for stream in streams)
    assert any(stream.get("codec_type") == "audio" for stream in streams)
    assert 2.8 <= float(result["metadata"]["format"]["duration"]) <= 3.2
