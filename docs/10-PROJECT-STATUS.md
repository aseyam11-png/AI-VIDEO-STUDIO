# Project Status

Last updated: 2026-11-08.

## Verified CI

### Phase 1 latest verified run
- GitHub Actions run: 95
- Run URL: https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42498886302
- Commit: `b88b7a81e0e31a3531a79a315276124d9cb29d00`
- Ruff: passed
- PostgreSQL migration: passed using a non-superuser migrator role
- Runtime privilege check: DML passed; schema DDL denied as intended
- FastAPI readiness, project creation, and project read: passed using the restricted runtime role
- Docker Compose configuration: passed
- Pytest: 29 passed in 3.68 seconds
- Full Compose integration: passed through PostgreSQL → transactional outbox → Valkey → control worker → CPU worker; Proxy, Scenes, Timeline, Render, MP4 download and FFprobe validation all succeeded.
- This CI run verifies the implementation on GitHub's Linux runner, not yet a full Compose lifecycle on the user's Windows machine, production deployment, or GPU benchmark.

### Phase 0.5 baseline run
- GitHub Actions run: 83
- Run URL: https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42487114504
- Ruff: passed
- Pytest: 16 passed
- End-to-end fixture: synthetic video generated with FFmpeg, uploaded through the API, probed, proxied, scene-detected, converted to OTIO, rendered, and downloaded.
- Manual Windows acceptance: local user verified project creation, upload, proxy, scenes, OTIO, MP4 render, and MP4 download.

## Phase 1 implementation branch

- Branch: `feat/phase-1-durable-job-engine`
- Base: `bcbcc941f4bf9967c16c5b9f6d67e4b810c2c8ef` (Phase 0.5 verified baseline)
- Main is unchanged; Phase 1 is under review on its feature branch.
- Added Alembic schema for projects, assets, jobs, job events, and transactional outbox records.
- Added PostgreSQL-backed metadata with filesystem media bytes retained under MEDIA_ROOT, plus an idempotent legacy manifest importer.
- Added job submit/list/detail/events/cancel/retry APIs, strict per-operation payload allowlists, request hashing, and idempotency conflict handling.
- Added transactional outbox dispatcher, dedicated Celery `control` and `cpu` queues/workers, heartbeat-based stale job recovery, cancellation checkpoints, and dispatch-generation fencing.
- Media sizes use PostgreSQL BIGINT to match the configured upload size range.
- Added synthetic background worker end-to-end test, PostgreSQL migration validation, runtime least-privilege role validation, and full Compose broker-backed media pipeline validation. Run 95 passed (29 tests plus all four media jobs in Compose).
- Progress is reported as stage checkpoints; actual encoder percentage is not yet measured.
- This branch remains a local/private development surface without production authentication or public deployment hardening.

## Component status

| Area | Status | Evidence / next action |
|---|---|---|
| GitHub repository | Public repository populated | Keep secrets and private media out |
| Architecture, roadmap, risk register | Drafted | Implement and attach acceptance evidence |
| Project creation | Implemented on Phase 1 branch | PostgreSQL record plus JSON compatibility manifest |
| Video/image upload | Implemented on Phase 1 branch | Filesystem bytes plus PostgreSQL metadata, extension allowlist, SHA-256 and FFprobe validation |
| Proxy generation | Implemented | FFmpeg H.264 proxy; covered by end-to-end test |
| Scene detection | Implemented | PySceneDetect ContentDetector; covered by end-to-end test |
| Arabic transcription | Optional adapter | WhisperX not installed by default; CPU-only in Phase 0.5 |
| Timeline | Implemented | OpenTimelineIO file plus JSON manifest |
| Preview render | Implemented | Cut-only render from one source asset using FFmpeg |
| Media end-to-end test | **Passed** | GitHub Actions run 83; plus local Windows manual test of project/upload/proxy/scenes/OTIO/render/download |
| FastAPI control plane | Phase 1 API on feature branch | Job API added; no production authentication yet |
| PostgreSQL / Alembic | Implemented on Phase 1 branch | Schema + readiness check; migration passed using a non-superuser migrator role; asset sizes are BIGINT |
| Runtime DB role | Implemented on Phase 1 branch | Runtime DML allowed; DDL denied in CI; FastAPI create/read verified |
| Valkey / Celery | Implemented on Phase 1 branch | Transactional outbox, control + CPU workers, scheduler, retry/recovery; full Compose media pipeline passed |
| Arabic RTL web UI | Visual scaffold | Media upload and live API integration not wired |
| GPU scheduler and isolated workers | Not implemented | Needed before CUDA transcription or generation |
| Qwen / LangGraph agents | Placeholder/design | No live LLM connected |
| Wan / ComfyUI / SAM 2 / Grounding DINO | Candidate only | Not installed or benchmarked |
| Product Identity Lock | Design requirement | Not implemented |
| CapCut handoff | Design only | Optional Windows-side adapter |
| Phase 1 local Compose acceptance | Pending on Windows | Run the feature branch in a separate clone; verify /ready, import old manifests, then submit a broker-backed media job |
| Production readiness | **Not ready** | Complete Phase 1+, authentication, operational recovery, security review, and full end-to-end acceptance |

The Phase 0.5 API is a local/private development surface. Do not expose it to the public Internet; authentication and private-network deployment are required before remote use.


## Phase 2 — specialist model/tool adapters (2026-11-08)

- Implementation branch: feat/phase-2-model-integrations
- Base: Phase 1 verified commit 0f0ae09baee4a2122b20f5e1e487eed443e3481b
- This branch is stacked on the Phase 1 feature branch. Neither PR is merged into main.

### Adapter and orchestration work

| Component | Integration added | Runtime activation still required |
|---|---|---|
| Qwen3.5-9B | Ollama REST adapter, schema-constrained creative plan, LangGraph director endpoint | One manually installed qwen3.5:9b model in private Ollama |
| WhisperX | Dedicated optional speech queue and worker image | speech profile, speech flag, selected small profile on explicit transcription |
| Grounding DINO | Allowlisted ComfyUI workflow adapter and vision.detect job | Actual API-exported workflow, required custom node and checkpoints |
| SAM 2 | Allowlisted ComfyUI workflow adapter and vision.track job | Actual API-exported workflow, required custom node and checkpoints |
| Wan2.2 TI2V-5B | Allowlisted generation workflow, 1–10 second validation and approval gate | Actual workflow, only its required model artifacts, GPU benchmark |
| DeepFilterNet | CPU CLI adapter, explicit checkpoint directory, new-file audio remux | Installed script and checkpoint directory |
| Real-ESRGAN | Allowlisted ComfyUI upscaling workflow and video.upscale job | Actual API workflow, custom node and required checkpoint |
| RIFE | Allowlisted ComfyUI interpolation workflow and video.interpolate job | Actual API workflow, custom node and required checkpoint |
| Blender | Dedicated model-queue adapter and reviewed .blend template renderer | Blender executable and project template |

### New interfaces and routing

- GET /v1/models/status: reports external runtime and workflow configuration without downloading weights.
- POST /v1/director/plan: produces a typed Arabic/English draft plan; it stops at awaiting_approval.
- Added job types: speech.transcribe, audio.enhance, vision.detect, vision.track, video.generate, video.upscale, video.interpolate, motion.blender.
- Heavy ComfyUI/Blender jobs use the separate models queue with one worker process. WhisperX and DeepFilterNet use the CPU queue.
- Generation and Blender render require an explicit approval field. Wan is capped at 10 seconds per initial shot.
- ComfyUI workflows are fixed IDs plus private node/input binding configuration. Arbitrary workflow IDs, arbitrary Blender scripts and agent shell access are not allowed.

### Weight policy and verification boundary

- No neural weights, model cache, ComfyUI custom nodes, Blender project templates or generated media are committed to Git.
- Docker builds and GitHub Actions do not download neural weights.
- Qwen is not pulled automatically. WhisperX weights are loaded only after an explicit transcription job. DeepFilterNet requires an existing checkpoint folder. ComfyUI must already contain the precise artifacts required by exported workflows.
- CI tests adapter contracts, strict job schemas, approval gates and workflow input binding using synthetic workflow data. It does not run actual model inference or certify output quality, GPU fit, runtime licensing or hardware performance.
- A ComfyUI integration is not operational until the five actual API workflows, custom-node revisions and required checkpoints are installed and pass an end-to-end job on the target GPU.
- A Phase 2 CI run must pass before its PR can be considered for merge. Production readiness remains unverified.
