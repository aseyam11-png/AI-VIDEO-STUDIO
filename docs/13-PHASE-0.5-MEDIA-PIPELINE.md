# Phase 0.5 — Deterministic Media Pipeline

## Scope
This phase builds a working media path without AI agents or GPU generation:

1. Create a project.
2. Upload a supported video or still image.
3. Inspect the media with FFprobe.
4. Create a video proxy.
5. Detect candidate scene boundaries.
6. Optionally transcribe Arabic/English speech locally with WhisperX.
7. Generate an OpenTimelineIO timeline and JSON manifest.
8. Render a cut-only MP4 preview with FFmpeg.

## Supported uploads
Video: MP4, MOV, MKV, WebM, M4V.
Images: JPG/JPEG, PNG, WebP.

Upload extension is allowlisted. FFprobe must successfully identify media streams. Images are stored as product/reference assets; proxy, scene detection and timeline endpoints currently apply to video assets only.

## Storage
Phase 0.5 stores project and asset manifests as JSON under MEDIA_ROOT/projects/<project-uuid>/. Media bytes, proxies, scenes, transcripts, OTIO timelines and exports stay on the private media volume, never in Git.

The default Docker media volume is persistent. Set MEDIA_ROOT to a directory or volume that is not inside the Git checkout. The repository ignores local data/, but that is not a substitute for keeping user data outside the repository.

## API sequence

### 1. Create a project
POST /v1/projects

JSON example:

    {
      "name": "Product advertisement",
      "target_duration_seconds": 60,
      "target_resolution": "1920x1080",
      "target_fps": 30,
      "language": "ar",
      "brief": "A 60-second luxury product video"
    }

The response includes a project UUID.

### 2. Upload source media
POST /v1/projects/{project_id}/assets

Multipart field: file.

The server:
- validates project UUID and extension allowlist;
- streams bytes to a temporary file while computing SHA-256 and enforcing the configured size limit;
- atomically publishes the source file;
- runs FFprobe and rejects unrecognized or invalid media;
- writes an asset JSON manifest.

### 3. Create a proxy
POST /v1/projects/{project_id}/assets/{asset_id}/proxy

Optional JSON: {"max_width":1280,"fps":30}.

The proxy is H.264 MP4 with AAC audio when the source has audio. The original is retained.

### 4. Detect scenes
POST /v1/projects/{project_id}/assets/{asset_id}/scenes

Optional JSON: {"threshold":27.0,"min_scene_len":15}.

PySceneDetect ContentDetector produces candidate boundaries. These are algorithmic suggestions and may need manual correction; rapid motion, flash frames and dissolves can affect detection.

### 5. Transcribe speech (optional)
POST /v1/projects/{project_id}/assets/{asset_id}/transcription

JSON: {"language":"ar","model_name":"small","device":"cpu","align":false}.

Install WhisperX separately in a compatible speech-worker environment. The first explicit request may download model artifacts into MEDIA_ROOT/models/whisperx. This is local inference, not a paid transcription API. No API keys are required for the default public model. Arabic alignment support and dialect accuracy must be evaluated on real samples. The endpoint is CPU-only in this phase.

### 6. Create timeline
POST /v1/projects/{project_id}/assets/{asset_id}/timeline

JSON: {"fps":30}.

Writes:
- timelines/<asset-id>.otio
- timelines/<asset-id>.json

The initial timeline contains scene clips from one source asset. It is an editable interchange representation, not a multi-track NLE or final compositing system.

### 7. Render preview
POST /v1/projects/{project_id}/assets/{asset_id}/render

JSON example: {"width":1920,"height":1080,"fps":30,"crf":20}.

Produces a cut-only MP4 preview at exports/<asset-id>-preview.mp4. The first renderer supports ordered cuts from one source video. Transitions, titles, overlays, B-roll, captions, multiple source clips, sound design and motion graphics are future work.

## API endpoints and access control
The Compose API is published to host 127.0.0.1:8000 only. It has no production authentication in Phase 0.5. Do not expose it to the Internet. Use local access or a private VPN plus a reverse proxy with authentication before remote use.

## Tests
- Unit tests for safe storage, path IDs, SHA-256, size limits and timeline serialization.
- Render command test verifies interleaved audio/video filter pads for FFmpeg concat.
- End-to-end test generates a short synthetic source via FFmpeg and calls upload, proxy, scene detection, timeline, render and download endpoints.
- CI installs FFmpeg and the media extra. WhisperX model downloads are deliberately excluded from CI.

## Known limitations
- Manifests are filesystem JSON, not PostgreSQL records yet.
- HTTP requests wait for media processing; durable background jobs and live progress belong to Phase 1.
- The UI is not yet wired to these endpoints.
- Scene detection is not semantic AI.
- WhisperX is optional and uninstalled by default.
- No CUDA/GPU work is exposed in Phase 0.5.
- The one-source cut renderer is not a complete nonlinear editor.
