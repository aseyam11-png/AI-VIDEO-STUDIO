# AI Video Studio — Project Status

**Last updated:** 2026-11-09  
**Repository:** `aseyam11-png/AI-VIDEO-STUDIO`  
**Default branch:** `main`  
**Release state:** Not production-ready

## Current verified baseline

| Phase | Scope | Status |
|---|---|---|
| 0.5 | Project/media API, secure upload, proxy, scene detection, OTIO and simple FFmpeg export | Implemented; local Windows acceptance is recorded in the project history |
| 1 | PostgreSQL metadata, durable jobs/events, transactional outbox, Celery/Valkey and CPU worker recovery | Merged into `main` |
| 2 | Ollama and specialist model adapters, dedicated model/speech queues and unified Tools API | Merged into `main`; actual model runtimes and weights are not yet accepted |
| 3 | Durable sequential Workflow Engine, Qwen plan approval and job/asset handoff | Merged into `main` by PR #4 |

### Phase 3 CI evidence

- Pull request: [#4 — Durable AI workflow orchestration](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/4)
- Workflow run: [GitHub Actions #120](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42519328429)
- Tested commit: `91c62b87110f86e2bfd211a744039260c1f63ad5`
- `python-checks`: passed
- `compose-integration`: passed
- Merge commit: `29bc06bf334c329eb0396c0f821cc20b6eaaffe1`

This proves the tested Python and Linux Compose paths only. It does not prove target-Windows Compose acceptance, real Ollama/ComfyUI inference, GPU compatibility, output quality or a production deployment.

### Phase 1 CI evidence

- Workflow run: [GitHub Actions #95](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42498886302)
- PostgreSQL migration under a non-superuser migrator: passed.
- Runtime DML allowed and schema DDL denied: passed.
- FastAPI readiness and project create/read using the restricted runtime role: passed.
- Full Linux Compose media path: Outbox → Valkey → Celery → Proxy → Scenes → Timeline → Render → MP4 download/FFprobe: passed.
- Pytest: 29 passed.

### Phase 0.5 baseline

- Workflow run: [GitHub Actions #83](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42487114504)
- Pytest: 16 passed.
- Synthetic video upload, probing, proxy, scene detection, OTIO, render and download: passed.
- Manual Windows acceptance for the local Phase 0.5 flow is recorded in the prior project status.

## Component status

| Area | Current status | Remaining acceptance |
|---|---|---|
| Project and asset API | Implemented | Real user-media testing at configured size limits |
| Media validation and storage | Implemented | Windows host volume/permissions and disk exhaustion tests |
| Proxy and scene detection | Implemented | More codecs, frame rates and varied real media |
| Timeline and rendering | Cut-only, one-source implementation | Multi-source, layers, transitions, titles, subtitles and audio mix |
| PostgreSQL/Alembic | Implemented | Fresh production DB upgrade and restore drill |
| Durable jobs and outbox | Implemented | Target-host restart/failure/recovery acceptance |
| Model adapters and Tools API | Implemented | Real runtime provisioning and successful inference |
| Qwen/Ollama | Adapter and schema validation implemented | Install exact model, Arabic inference, schema/output review and GPU/RAM measurement |
| ComfyUI / Grounding DINO / SAM 2 / Wan / Real-ESRGAN / RIFE | Allowlisted adapters implemented | Actual API-exported workflow JSON, node/input registry, custom nodes, checkpoints and smoke jobs |
| WhisperX | Optional dedicated speech worker adapter | Enable worker, explicit model load and Arabic transcription test |
| DeepFilterNet | CPU adapter | Install explicit script/checkpoint and validate audio output |
| Blender | Reviewed-template adapter | Install executable/template and perform actual render |
| Workflow Engine | Durable sequential v1 merged | Real runtime chain; general branching DAG is future work |
| Wan multi-shot assembly | Not implemented | Multi-shot planning, generation, assembly and QA |
| Arabic RTL UI | Initial connected video workflow UI merged in PR #5 | Browser acceptance on the target Windows host remains required |
| Product Identity Lock | Not implemented | Product reference preservation and logo/label QA |
| Automated technical/creative QA | Not implemented | Video/audio validation plus human-reviewed quality suite |
| Security and operations | Local/private development posture | Authentication before remote access, secret/path isolation, monitoring, backups and restore |

## Current architecture boundaries

- PostgreSQL is the source of truth for projects, assets, jobs and workflow state.
- Media files, generated outputs, workflow exports and model caches remain under the private media volume, not in Git or database blobs.
- Valkey transports Celery work; it is not the authoritative job-state store.
- The Workflow Engine v1 executes a persisted ordered sequence. It is not a general-purpose branching DAG.
- Qwen proposes a typed plan; the application validates it and waits for explicit user approval before execution.
- ComfyUI workflows are fixed allowlisted IDs. Actual API workflow exports, node IDs, custom nodes and checkpoints are provisioned separately.
- The models worker has single-process concurrency. This serializes Studio-originated model jobs but is not a multi-GPU scheduler.
- Model adapters, runtime configuration and a passing CI test are not proof of actual model inference.
- The initial product target remains a private single-user installation. Do not expose the API, Ollama, ComfyUI, PostgreSQL or Valkey publicly.

## Immediate next steps

1. Run the Windows runtime preflight and record exact host/GPU/VRAM/driver/disk information.
2. Start one Ollama runtime and test Qwen Arabic plan generation with real weights.
3. Provision ComfyUI and configure each API workflow from an actual export; do not guess node IDs or checkpoint names.
4. Execute low-resolution real smoke jobs for the enabled models and record memory, time, logs and FFprobe results.
5. Run the merged Studio UI on the target Windows host and verify the upload → plan → approval → workflow → MP4 flow in a real browser.
6. Complete real-media quality tests, authentication/private-access review, backup/restore and restart/recovery acceptance.

See [docs/17-PRODUCTION-ROADMAP.md](17-PRODUCTION-ROADMAP.md) for phase exit gates and the first-release scope.
