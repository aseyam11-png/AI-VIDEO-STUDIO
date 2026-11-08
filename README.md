# AI Video Studio 2.0

**Arabic-first, single-user, agentic video-production studio.**

Describe a video in Arabic, provide source media and product references, approve a creative plan, then orchestrate analysis, selected AI generation, editing, motion graphics, audio processing, quality checks, and final export.

> **Current state: Phase 0.5 media pipeline implementation.** The repository contains an initial deterministic media API and tests. It is not yet a complete video editor or production-ready service. Model weights are not included, WhisperX is optional, GPU inference is not enabled, and the web UI is not yet connected to the media API.

## Product scope

- One user, protected by local-only access or a private VPN.
- Finished videos from 1 to 5 minutes.
- Arabic-first interaction and mixed Arabic/English media.
- Product commercials, lifestyle scenes, motion graphics, talking-head edits, and restoration.
- Initial GPU target: one NVIDIA GPU with 24 GB VRAM (RTX 3090 candidate; not yet benchmarked).
- No paid model API is required by the base design. External services are disabled by default and must be explicitly approved.

## Architecture

1. **Studio UI** — Arabic chat, media upload, storyboard approval, job monitor, preview, and project history.
2. **Control API** — FastAPI project/media endpoints and explicit approval policies.
3. **Executive Director** — future local multimodal LLM role that converts the brief into validated production plans.
4. **Orchestrator** — future LangGraph state machine with editing, audio, generation/motion, and quality supervisors.
5. **Tool Gateway** — strict schemas, allowlists, project-scoped paths, timeouts, idempotency, and audit logs. No arbitrary shell access.
6. **Media engines** — FFmpeg/FFprobe, PySceneDetect, OpenTimelineIO, and audio/video algorithms.
7. **Model workers** — optional local WhisperX first; later Ollama, ComfyUI/Wan, SAM 2, Grounding DINO, DeepFilterNet and Blender. Heavy GPU jobs will be serialized initially.
8. **Quality and delivery** — technical, visual, product-identity, and creative checks; versioned outputs; user approval before final export.

## Phase 0.5 API

Create a project:

    POST /v1/projects

Upload a video or image:

    POST /v1/projects/{project_id}/assets

List assets:

    GET /v1/projects/{project_id}/assets

Inspect an asset:

    GET /v1/projects/{project_id}/assets/{asset_id}

Create a low-resolution editing proxy:

    POST /v1/projects/{project_id}/assets/{asset_id}/proxy

Detect candidate scenes:

    POST /v1/projects/{project_id}/assets/{asset_id}/scenes

Run **local CPU WhisperX transcription** (optional extra; first invocation may download model weights into the media volume):

    POST /v1/projects/{project_id}/assets/{asset_id}/transcription

Build both an OpenTimelineIO file and a JSON timeline manifest:

    POST /v1/projects/{project_id}/assets/{asset_id}/timeline

Render a simple cut-only MP4 preview from the timeline:

    POST /v1/projects/{project_id}/assets/{asset_id}/render

Download the preview:

    GET /v1/projects/{project_id}/assets/{asset_id}/rendered

The current timeline renderer handles ordered cuts from **one source video**. It does not yet support multi-source editing, overlays, transitions, subtitles, motion graphics, or AI-generated shots. Those are later phases.

## Key design decisions

- PostgreSQL is planned as the durable source of truth in Phase 1. Phase 0.5 uses atomic JSON manifests on the persistent media volume.
- Valkey/Redis is a broker/cache, not the only record of job state.
- LangGraph checkpoints must be persisted and tested; in-memory checkpoints are not for production.
- CPU and GPU tasks will use separate queues. The initial 24 GB profile permits one heavy GPU job at a time.
- FFmpeg is the deterministic render engine. OpenTimelineIO is the timeline interchange model, not a renderer.
- ComfyUI workflows will be reviewed, versioned templates. Agents may fill validated parameters only.
- Product Identity Lock is a first-class future subsystem. Generated video can alter product details; logos and small markings require reference compositing or 3D assets and QA.
- CapCut is an optional Windows-side handoff. Core rendering must remain independent of undocumented CapCut project formats.
- Never commit API keys, environment files, user media, model weights, caches, generated assets, or credentials.

## Quick start (local control-plane scaffold)

Requirements: Python 3.11, FFmpeg/FFprobe, and the media extra.

1. Create a virtual environment.
2. Install development and media dependencies: pip install -e ".[dev,media]".
3. Copy .env.example to .env only if you need to override settings; replace placeholders with unique secrets.
4. Set MEDIA_ROOT to a private directory outside the Git checkout.
5. Run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000.
6. Open http://127.0.0.1:8000/docs.

Docker development:

1. Copy .env.example to .env and replace both placeholder secrets.
2. Run docker compose config.
3. Run docker compose up --build.
4. Check http://127.0.0.1:8000/health.

The API port is bound to localhost in Compose. Do not expose it publicly. On a cloud host, place access behind a private VPN and authentication before remote use.

## Optional Arabic transcription

WhisperX is not installed in the base image because it brings a large ML dependency stack. Install and validate it in a separate speech-worker environment. The API transcription endpoint is CPU-only in Phase 0.5. Model weights are downloaded only when the transcription endpoint is explicitly called; no paid API key is used.

## Current implementation status

| Component | Status |
|---|---|
| Project creation and JSON manifests | Implemented in Phase 0.5 |
| Secure extension-filtered upload + SHA-256 | Implemented; FFprobe validates media streams |
| Proxy generation | Implemented with FFmpeg |
| Scene detection | Implemented with PySceneDetect media extra |
| Local Arabic transcription | Optional WhisperX adapter; dependency/model not installed |
| OpenTimelineIO timeline generation | Implemented with media extra |
| Simple cut-only FFmpeg preview render | Implemented for one source asset |
| Automated media integration test | Added; CI result must pass |
| PostgreSQL-backed job state | Planned; not yet wired |
| Durable queue, GPU scheduler and isolated workers | Not implemented |
| LangGraph live LLM and agents | Placeholder only |
| Wan/ComfyUI/SAM 2 production inference | Not installed or benchmarked |
| Product Identity Lock | Design requirement; not implemented |
| Arabic RTL web UI | Visual scaffold; not connected to API |
| Production readiness | **Not ready** |

## Public repository notice

This repository is public. Keep it free of secrets and private media. A public repository does not automatically grant an open-source license to its own code. The project-level license is intentionally unselected; third-party code, weights, datasets, and extensions retain their separate licenses. See docs/05-LICENSE-REVIEW.md and docs/12-PUBLIC-REPOSITORY-CHECKLIST.md.

## Next step

Run and pass the Phase 0.5 media integration test in GitHub Actions, then connect the Arabic UI to the project/media API. Do not provision paid GPU time or download large generation weights until the exact hardware, artifact licenses, and benchmark budget are verified.
