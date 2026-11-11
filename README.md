# AI Video Studio 2.0

**Arabic-first, single-user, agentic video-production studio.**

Describe a video in Arabic, provide source media and product references, approve a creative plan, then orchestrate analysis, selected AI generation, editing, motion graphics, audio processing, quality checks, and final export.

> **Current state (2026-11-11): Phases 0.5–6.9 are merged into `main`.** Phase 6.8 was merged in PR #16 and Phase 6.9 Product Identity Lock was merged in PR #18. Phase 6.10 Automatic Video Quality Gate is implemented on `feat/phase-6.10-video-quality-gate` and submitted for review. It performs full FFmpeg decode, stream/duration/resolution/FPS checks, black/frozen-frame detection and audio silence/peak analysis, then blocks downloads unless a passing report exists. Real target-GPU inference and target-Windows visual/audio acceptance remain open. The repository is still **not production-ready**: real model weights/workflows, target-host GPU inference, visual/audio review, browser acceptance, security/operations and full release acceptance remain open.

## Current feature status

- **Phase 6.6 — merged:** transitions, Arabic titles and image/logo overlays with deterministic FFmpeg rendering.
- **Phase 6.7 — merged:** approved 2–8-shot Wan generation and ordered MP4 assembly. Real Wan/RIFE inference is still unverified on the target GPU.
- **Phase 6.8 — merged:** per-frame Real-ESRGAN video enhancement up to 10 seconds / 300 frames. Real model inference and visual quality still need target-host acceptance.

See [Production Roadmap](docs/17-PRODUCTION-ROADMAP.md) and [Phase 6.8 — Bounded Video Enhancement](docs/26-PHASE-6.8-VIDEO-ENHANCEMENT.md).

## Current feature status

- **Phase 6.6 — merged:** transitions, Arabic titles and image/logo overlays with deterministic FFmpeg rendering.
- **Phase 6.7 — merged:** approved 2–8-shot Wan generation and ordered MP4 assembly. Real Wan/RIFE inference is unverified on the target GPU.
- **Phase 6.8 — merged:** bounded per-frame Real-ESRGAN video enhancement up to 10 seconds / 300 frames. Real model inference and visual quality still need target-host acceptance.
- **Phase 6.9 — merged:** SHA-256 verified product reference, per-shot identity constraints and five-check human review gate before timeline/export. The gate does not guarantee exact logo or pixel-level identity.
- **Phase 6.10 — under review:** automatic technical checks on final MP4 output with a passing-report download gate.

See [Production Roadmap](docs/17-PRODUCTION-ROADMAP.md), [Phase 6.8](docs/26-PHASE-6.8-VIDEO-ENHANCEMENT.md) and [Phase 6.9](docs/27-PHASE-6.9-PRODUCT-IDENTITY-LOCK.md).

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
3. **Executive Director** — Qwen through a private Ollama runtime can draft schema-validated Arabic/English creative plans.
4. **Workflow Engine** — PostgreSQL-backed sequential workflow runs invoke validated jobs after explicit user approval. A general branching DAG and autonomous editing/quality supervisors remain future work.
5. **Tool Gateway** — strict schemas, allowlists, project-scoped paths, timeouts, idempotency, and audit logs. No arbitrary shell access.
6. **Media engines** — FFmpeg/FFprobe, PySceneDetect, OpenTimelineIO, and audio/video algorithms.
7. **Model workers** — adapters and queue routing exist for WhisperX, Ollama, ComfyUI/Wan, SAM 2, Grounding DINO, DeepFilterNet and Blender. Runtime weights, exported workflows and real target-GPU acceptance are still external setup tasks. Heavy ComfyUI jobs are serialized through the models worker.
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

The current timeline renderer supports sequential cuts across **multiple source videos**, with saved project timelines, Arabic transcription/SRT and synchronized audio mixing. Merged Phase 6.6 adds transitions, Arabic titles, basic motion and image overlays; advanced template-driven Blender/ComfyUI motion graphics remain later work. Phase 6.7 adds an approved multi-shot generation path on its feature branch.

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

Supported initial types are `media.proxy`, `media.scenes`, `media.timeline`, and `media.render`. Payloads are allowlisted and validated by job type. Submit requests carry a project-scoped `idempotency_key`; repeating the same request returns the original job, while reusing the key with different request content returns HTTP 409. A partial unique database index also prevents concurrent active jobs of the same operation type on the same asset.

Job rows, job events, and outbox messages are written in one database transaction. Celery workers consume messages via Valkey; PostgreSQL remains the authoritative source for status, progress checkpoints, attempts, heartbeats, results, and errors. Separate Celery `control` and `cpu` queues keep scheduling/recovery tasks away from media-processing workers. Delivery is at-least-once, with database generation checks fencing stale/duplicate deliveries. Progress is an honest stage checkpoint, not a fabricated percentage from FFmpeg.

See [Phase 1 Job Engine](docs/14-PHASE-1-JOB-ENGINE.md) for lifecycle, reliability guarantees, migrations, legacy import, and run instructions.

## Key design decisions

- PostgreSQL is the durable source of truth when configured; SQLite under MEDIA_ROOT supports a single-process local development fallback. Alembic owns schema changes; application startup never creates tables automatically.
- Phase 0.5 projects/assets are imported idempotently from JSON manifests using `python -m app.cli import-legacy-manifests`. Media bytes remain on the filesystem.
- Valkey is a Celery transport, not the source of truth for job state or results.
- The transactional outbox prevents the database-commit/message-publish gap; workers must still tolerate at-least-once delivery.
- The current Phase 3 Workflow Engine persists ordered steps in PostgreSQL and advances them through the durable Job/Outbox engine. It is sequential in v1, not a general-purpose branching DAG.
- Deterministic media jobs use the CPU queue; model jobs use a separate single-concurrency models queue. This serializes Studio-originated model work but is not a multi-GPU scheduler.
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
6. Open the Arabic Studio at http://127.0.0.1:8000/studio/ (API documentation remains at http://127.0.0.1:8000/docs).

For target-machine specifications before model setup, see [Windows Host Inventory](docs/18-WINDOWS-HOST-INVENTORY.md).

Docker Compose local development:

1. Copy .env.example to .env.
2. Generate four independent URL-safe secrets for the PostgreSQL bootstrap, migration, runtime, and Valkey roles. Set MEDIA_HOST_PATH to a private host directory outside the Git checkout; for example, the existing Windows media directory.
3. Run `docker compose up -d --build`. Compose starts PostgreSQL and Valkey, applies Alembic migrations, then starts the API, CPU media worker, control worker, and scheduler.
4. Import existing Phase 0.5 manifests once with `docker compose exec api python -m app.cli import-legacy-manifests`. The importer is idempotent and does not move media bytes.
5. Check `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/ready`.

PostgreSQL and Valkey have no published host ports. The API is bound to localhost only. See [Phase 1 Job Engine](docs/14-PHASE-1-JOB-ENGINE.md), [Phase 2 Model Integrations](docs/15-MODEL-INTEGRATIONS.md), [Phase 3 Workflows](docs/16-WORKFLOW-ORCHESTRATION.md), [Phase 6.5 Audio Mixing](docs/23-PHASE-6.5-AUDIO-MIXING-SYNC.md), and the [Production Roadmap](docs/17-PRODUCTION-ROADMAP.md).

The API port is bound to localhost in Compose. Do not expose it publicly. On a cloud host, place access behind a private VPN and authentication before remote use.

## Optional Arabic transcription

WhisperX is not installed in the base image because it brings a large ML dependency stack. Install and validate it in a separate speech-worker environment. The API transcription endpoint is CPU-only in Phase 0.5. Model weights are downloaded only when the transcription endpoint is explicitly called; no paid API key is used.

## Current implementation status

| Component | Status |
|---|---|
| Projects, manifests, secure media upload and FFprobe validation | Implemented |
| Proxy, scene detection, OTIO timeline and single-source cut-only MP4 export | Implemented; more editing features remain |
| PostgreSQL metadata, migrations and legacy manifest import | Merged into `main` (Phase 1) |
| Durable jobs, events, transactional outbox, Celery/Valkey and recovery | Merged into `main` (Phase 1) |
| Specialist model adapters and unified Tools API | Merged into `main` (Phase 2); real runtimes/weights not accepted |
| Qwen Arabic/English plan generation through Ollama | Adapter implemented; actual model installation/inference not verified on target host |
| ComfyUI workflows (Grounding DINO, SAM 2, Wan, Real-ESRGAN, RIFE) | Adapters implemented; API workflow exports, nodes and checkpoints still require provisioning |
| Durable sequential Workflow Engine | Merged into `main` (Phase 3); GitHub Actions run 120 passed before merge |
| General branching DAG / autonomous editing and QA agents | Not implemented |
| Target GPU scheduling and performance benchmarks | Not accepted; model queue is single-concurrency |
| Arabic RTL Studio UI | Initial connected video workflow UI merged in PR #5; target-host/browser acceptance pending |
| Product Identity Lock and automated creative/visual QA | Not implemented |
| Production authentication, deployment hardening and restore drill | Not accepted |
| Production readiness | **Not ready** |

## Public repository notice

This repository is public. Keep it free of secrets and private media. A public repository does not automatically grant an open-source license to its own code. The project-level license is intentionally unselected; third-party code, weights, datasets, and extensions retain their separate licenses. See docs/05-LICENSE-REVIEW.md and docs/12-PUBLIC-REPOSITORY-CHECKLIST.md.

## Latest verification

- Phase 0.5 media processing passed local Windows acceptance according to the project test record.
- Phase 1 CI run 95 passed, including PostgreSQL least-privilege checks and a full Linux Docker Compose path through Outbox → Valkey → Celery → Proxy → Scenes → Timeline → Render.
- Phase 3 GitHub Actions run 120 passed on commit `91c62b8` before PR #4 was merged: `python-checks` and `compose-integration` both succeeded.
- These results validate the automated Linux CI path and the recorded local Phase 0.5 check. They do **not** prove real Ollama/ComfyUI inference, target-Windows Compose behavior, GPU compatibility, output quality, authentication, or production deployment.

## Next step

The connected Arabic UI is merged. The next step is to run the [Windows Host Inventory](docs/18-WINDOWS-HOST-INVENTORY.md), then provision and smoke-test Ollama/Qwen and ComfyUI. Complete real-media, GPU, security, backup and restore acceptance before calling the studio production-ready. Do not download large weights until the actual GPU, disk budget and artifact licenses have been checked.


## Phase 2 specialist model integrations

Phase 2 is merged into `main` and adds adapters and job routing for the specialist stack:

- Qwen3.5-9B through a private Ollama runtime for Arabic creative planning.
- WhisperX on the optional, dedicated speech worker.
- Grounding DINO, SAM 2, Wan2.2 TI2V-5B, Real-ESRGAN and RIFE through five fixed ComfyUI API workflow IDs.
- DeepFilterNet through an explicitly provisioned CPU CLI/checkpoint.
- Blender through a reviewed project template and background rendering adapter.

The adapters do not bundle or automatically download model weights. The ComfyUI API workflows, required custom nodes/checkpoints, Ollama model, optional WhisperX speech extra/model cache, DeepFilterNet checkpoint and Blender templates must be provisioned outside Git. See docs/15-MODEL-INTEGRATIONS.md.

Phase 2 is implemented in `main`, but it is not a production release. CI verifies schemas and adapter contracts; real inference, model quality, GPU compatibility and artifact licensing require runtime-specific acceptance.


## Phase 3 — durable AI workflows

The durable workflow API is documented in docs/16-WORKFLOW-ORCHESTRATION.md. It stores the ordered steps in PostgreSQL and chains normal jobs through the transactional outbox. Runs stop in awaiting_approval until a user approves the Qwen plan. Generated videos are registered as project assets and passed to subsequent steps.

Ollama is available as an optional Compose profile. Model weights and ComfyUI workflows/checkpoints are provisioned explicitly on the target machine; no model weights are downloaded by application startup, CI or the default Docker Compose stack.
