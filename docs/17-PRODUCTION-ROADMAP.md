# AI Video Studio — Production Roadmap

**Baseline date:** 2026-11-09  
**Repository:** `aseyam11-png/AI-VIDEO-STUDIO`  
**Target release:** private, single-user production installation first. This roadmap does not claim a public multi-user SaaS release.

## 1. Current verified baseline

The following implementation phases are present on `main`:

| Phase | Scope | Repository state |
|---|---|---|
| 0.5 | Project/media API, secure upload, proxy, scene detection, OTIO and cut-only FFmpeg preview | Implemented; local Windows acceptance is recorded in project docs |
| 1 | PostgreSQL metadata, durable jobs/events, transactional outbox, Celery/Valkey, CPU worker and recovery | Merged |
| 2 | Model adapter contracts, Ollama integration, specialist model jobs and unified Tools API | Merged; real model runtimes/weights are not accepted |
| 3 | Durable sequential workflow runs/steps, plan approval, child-job chaining and generated-asset handoff | Merged by PR #4 |

Phase 3 CI run 120 passed on commit `91c62b8` before merge. Both `python-checks` and `compose-integration` succeeded. This is automated CI evidence, not target-machine GPU or model-inference evidence.

## 2. Not yet production-ready

The following gaps remain open:

- No accepted end-to-end test using the target machine's real Ollama model, ComfyUI server, exported workflows, custom nodes and checkpoints.
- No recorded GPU memory/performance benchmark for the selected hardware.
- The five ComfyUI workflow registry entries must be populated from actual API-exported workflow JSON; placeholder node IDs/input names must not be guessed.
- An initial connected Arabic RTL UI is merged in [PR #5](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/5). It covers project creation/history, video upload, Qwen plan review/approval, workflow monitoring, model-readiness indicators and MP4 preview/download. Browser acceptance on the target host remains open.
- The Workflow Engine v1 is sequential. It is not a general branching DAG or autonomous multi-agent editing director.
- Wan image-to-video workflow v1 is limited to one generated shot up to 10 seconds; multi-shot assembly is not implemented.
- Current deterministic timeline rendering is cut-only and single-source. Layered timelines, transitions, titles/subtitles, motion graphics, multi-source editing and audio mix automation remain incomplete.
- Product Identity Lock and automated creative/visual QA are not implemented.
- Authentication/deployment controls, backup/restore testing, operational monitoring and a full production acceptance run remain outstanding.
- Third-party code, model, checkpoint, custom-node and dataset licenses must be inventoried before distribution or commercial use.

## 3. Phase 4 — target-host model runtime acceptance

Do not download every checkpoint at once. Use the following order:

1. Record exact host OS, CPU, RAM, NVIDIA GPU model/VRAM, driver, CUDA visibility, free disk space and Docker/WSL2 versions.
2. Run `infra/scripts/windows/model-runtime-preflight.ps1` on Windows. Preserve the full output as an acceptance artifact.
3. Start one Ollama runtime only (host-installed Windows service or Compose profile, not both). Install the configured Qwen tag explicitly after confirming disk and memory budget.
4. Run the Arabic inference smoke test and verify the response matches the CreativePlan schema. Record model tag, quantization, load time, prompt duration, peak VRAM/RAM and whether GPU offload actually occurred.
5. Provision private ComfyUI with the selected custom-node revisions and exact checkpoint files. Keep its API on a private network.
6. Export API-format workflows for Grounding DINO, SAM 2, Wan 2.2 TI2V 5B, Real-ESRGAN and RIFE. Configure `MEDIA_ROOT/workflows/registry.json` using real node IDs and input names from those exports.
7. Verify that ComfyUI and Studio workers see the same media directory at the required path. Run `GET /v1/models/status`.
8. Run one low-resolution smoke job per enabled workflow. Probe every downloaded output with FFprobe where the result is video. Record logs, output IDs, duration, peak memory and failure mode.

**Phase 4 exit gate:** no model is labelled operational until its real inference succeeds on the target host and its exact artifact/license/runtime configuration is recorded.

## 4. Phase 5 — connect the Arabic Studio UI

Implement and verify this user path:

1. Create a project and show project history.
2. Upload video/image assets with progress, validation errors and size feedback.
3. Submit an Arabic brief to `POST /v1/workflows/from-brief`.
4. Display the Qwen plan and ordered workflow steps for review. Nothing should run before approval.
5. Let the user approve or cancel the workflow.
6. Poll/display workflow and child-job states, progress checkpoints, events and actionable errors.
7. Show generated assets, timeline/preview and final MP4 download.
8. Support retry only for eligible failed/cancelled jobs and show when a retry budget is exhausted.

Keep credentials out of browser bundles. The API currently binds to localhost in Compose; do not make it publicly reachable as a shortcut.

**Phase 5 exit gate:** a user can complete the full control-plane path from upload to approved workflow and download without manually calling API endpoints.

## 5. Phase 6 — editing and production features

Deliver in independently testable increments:

- Multi-source timeline model and editing operations.
- Scene/shot selection using actual scene, transcript and detection outputs.
- Subtitle/caption generation with Arabic RTL, timing and font tests.
- Audio mixing, voice/music levels, denoise and synchronization.
- Transitions, titles, overlays and motion graphics using reviewed Blender/ComfyUI templates.
- Multi-shot generation and deterministic assembly of generated clips.
- Video enhancement pipeline with frame extraction/reassembly only after output and storage validation.
- Product Identity Lock for product ads: reference preservation, logo/label checks and human review.
- Technical and visual QA: black/frozen frame detection, audio clipping/silence, duration/resolution/FPS checks, missing files and corrupt exports.

Do not claim "professional automatic editing" until these paths are exercised on varied real videos and reviewed output samples.

## 6. Phase 7 — security and operations

For the initial single-user installation:

- Keep the API bound to loopback or behind a private VPN/reverse proxy with authentication. Never expose Ollama, ComfyUI, PostgreSQL or Valkey publicly.
- Add/verify API authentication before allowing access beyond the trusted local host. Define session/token handling, CSRF/CORS behavior and secret rotation.
- Enforce project-scoped file access, safe output paths, upload limits, request timeouts and job/resource quotas.
- Keep media, model weights, workflow exports, logs with private content and credentials outside Git.
- Pin production container images and model/custom-node revisions; record software and artifact licenses.
- Add structured logs, health/readiness checks, disk/VRAM monitoring and actionable worker failure alerts.
- Test PostgreSQL and media-volume backup plus restore. Verify that database records and media assets remain consistent.
- Document upgrades, migration rollback/recovery, worker restart and stuck-job recovery.

## 7. Release acceptance matrix

| Gate | Evidence required | Release decision |
|---|---|---|
| Code/DB | Main CI green, reviewed migrations, clean upgrade on a fresh DB | Required |
| Runtime | Real Qwen and each enabled ComfyUI workflow succeeds on target host | Required for enabled models |
| UI | Upload → plan → approval → job monitor → preview/download | Required |
| Video quality | Real sample suite, FFprobe checks, human review and no broken outputs | Required |
| Identity/audio | Product/logo and Arabic speech/audio checks for supported use cases | Required for advertising workflow |
| Security | Private access, authentication where remotely reachable, secret/path isolation | Required |
| Operations | Backup/restore drill, restart/recovery and disk/worker monitoring | Required |

## 8. First production milestone

The first acceptable release should be deliberately narrow:

- One trusted user on a private Windows/Linux host.
- One video-input workflow: upload → Arabic plan → user approval → proxy/scenes → timeline → MP4 preview/export.
- Qwen enabled only after real host acceptance.
- Optional specialist models enabled one by one after their smoke tests.
- No claim of full autonomous editing, multi-shot Wan generation, or public multi-user service.

Once this narrow flow is stable, expand to generated shots, subtitles, audio enhancement, motion graphics and advanced editing.
