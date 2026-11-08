# AI Video Studio 2.0

**Arabic-first, single-user, agentic video-production studio.**

Describe a video in Arabic, provide source media and product references, approve a creative plan, then orchestrate analysis, selected AI generation, editing, motion graphics, audio processing, quality checks, and final export.

> **Current state: Phase 1 implementation in a review branch.** Phase 0.5 media processing passed local acceptance on Windows. Phase 1 introduces database-backed metadata and durable background jobs; it is not yet merged to main or production-ready. No model weights are included, WhisperX is optional, GPU inference is not enabled, and the web UI is not yet connected to the API.

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

## Phase 1 durable job API

Submit a validated background media job:

    POST /v1/jobs

List and inspect jobs:

    GET /v1/jobs?project_id={project_id}
    GET /v1/jobs/{job_id}
    GET /v1/jobs/{job_id}/events

Request cancellation or retry an eligible job:

    POST /v1/jobs/{job_id}/cancel
    POST /v1/jobs/{job_id}/retry

Supported initial types are `media.proxy`, `media.scenes`, `media.timeline`, and `media.render`. Payloads are allowlisted and validated by job type. Submit requests carry a project-scoped `idempotency_key`; repeating the same request returns the original job, while reusing the key with different request content returns HTTP 409.

Job rows, job events, and outbox messages are written in one database transaction. Celery workers consume messages via Valkey; PostgreSQL remains the authoritative source for status, progress checkpoints, attempts, heartbeats, results, and errors. Delivery is at-least-once, with database generation checks fencing stale/duplicate deliveries. Progress is an honest stage checkpoint, not a fabricated percentage from FFmpeg.

See [Phase 1 Job Engine](docs/14-PHASE-1-JOB-ENGINE.md) for lifecycle, reliability guarantees, migrations, legacy import, and run instructions.

## Key design decisions

- PostgreSQL is the durable source of truth when configured; SQLite under MEDIA_ROOT supports a single-process local development fallback. Alembic owns schema changes; application startup never creates tables automatically.
- Phase 0.5 projects/assets are imported idempotently from JSON manifests using `python -m app.cli import-legacy-manifests`. Media bytes remain on the filesystem.
- Valkey is a Celery transport, not the source of truth for job state or results.
- The transactional outbox prevents the database-commit/message-publish gap; workers must still tolerate at-least-once delivery.
- LangGraph checkpoints must be persisted and tested; in-memory checkpoints are not for production.
- Phase 1 currently routes deterministic media jobs to the CPU queue. GPU queues and GPU scheduling are deferred until model workers are designed and benchmarked.
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

Docker development (Phase 1):

1. Copy .env.example to .env.
2. Generate two different URL-safe random secrets for POSTGRES_PASSWORD and VALKEY_PASSWORD. Set MEDIA_HOST_PATH to a private host directory outside the Git checkout; for example, the existing Windows media directory.
3. Run `docker compose up -d --build`. Compose starts PostgreSQL and Valkey, applies Alembic migrations, then starts the API, CPU worker, and scheduler.
4. Import existing Phase 0.5 manifests once with `docker compose exec api python -m app.cli import-legacy-manifests`. The importer is idempotent and does not move media bytes.
5. Check `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/ready`.

PostgreSQL and Valkey have no published host ports. The API is bound to localhost only. See [Phase 1 Job Engine](docs/14-PHASE-1-JOB-ENGINE.md) for API examples, local SQLite mode, backup and recovery.

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
| Automated media integration test | **Passed in GitHub Actions** |
| PostgreSQL-backed project/asset/job metadata | Implemented on Phase 1 branch; Alembic migration and legacy import added |
| Durable job API, transactional outbox, Celery CPU worker and recovery | Implemented on Phase 1 branch; CI acceptance pending |
| GPU scheduler and isolated GPU workers | Not implemented |
| LangGraph live LLM and agents | Placeholder only |
| Wan/ComfyUI/SAM 2 production inference | Not installed or benchmarked |
| Product Identity Lock | Design requirement; not implemented |
| Arabic RTL web UI | Visual scaffold; not connected to API |
| Phase 1 merge / PostgreSQL CI verification | Pending draft PR review and CI |
| Production readiness | **Not ready** |

## Public repository notice

This repository is public. Keep it free of secrets and private media. A public repository does not automatically grant an open-source license to its own code. The project-level license is intentionally unselected; third-party code, weights, datasets, and extensions retain their separate licenses. See docs/05-LICENSE-REVIEW.md and docs/12-PUBLIC-REPOSITORY-CHECKLIST.md.

## Latest verification

GitHub Actions run 83 passed on 2026-11-08: Ruff succeeded and pytest reported 16 passed, including a synthetic FFmpeg end-to-end media test. This validates the CI Linux environment only; deployment on a GPU server and testing with user-supplied media remain outstanding.

## Next step

Complete Phase 1 review and acceptance on its feature branch, then connect the Arabic UI to the durable job API. Do not provision paid GPU time or download large generation weights until the exact hardware, artifact licenses, and benchmark budget are verified.
