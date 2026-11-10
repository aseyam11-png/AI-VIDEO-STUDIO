# AI Video Studio — Production Roadmap

**Baseline date:** 2026-11-10  
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
- Wan image-to-video v1 now has a separate durable multi-shot path on the Phase 6.7 feature branch: 2–8 generated shots, up to 10 seconds each, registered as project assets and assembled by FFmpeg. Real Wan/RIFE inference and target-GPU performance still require target-host acceptance.
- The deterministic renderer now supports sequential multi-source clips, Arabic timeline SRT, synchronized audio mixing, and the Phase 6.6 transitions/titles/image-overlay slice merged in PR #13. Real-time waveform editing, arbitrary keyframes, 3D compositing and reviewed Blender/ComfyUI motion-graphics templates remain future work.
- Product Identity Lock and automated creative/visual QA are not implemented.
- Authentication/deployment controls, backup/restore testing, operational monitoring and a full production acceptance run remain outstanding.
- Third-party code, model, checkpoint, custom-node and dataset licenses must be inventoried before distribution or commercial use.

## 3. Phase 4 — target-host model runtime acceptance

The read-only Windows inventory helper and its Windows-runner execution test are merged in [PR #6](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/6). The remaining entry gate is to run it on the actual target machine and use that report to choose runtime placement and model sizes.

Do not download every checkpoint at once. Use the following order:

1. Run `infra/scripts/windows/host-inventory.ps1` and record the host OS, CPU, RAM, GPU/VRAM, driver, free disk space, Docker and WSL versions. Use `-MediaPath` to identify the drive reserved for private media/model storage.
2. Then run `infra/scripts/windows/model-runtime-preflight.ps1` on Windows. Preserve both outputs as acceptance artifacts.
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

### Phase 6.1 — Multi-source sequential timeline (merged)

Merged in [PR #7](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/7). Adds project-scoped clip manifests, OpenTimelineIO serialization, validated trim/reorder/remove operations and FFmpeg MP4 previews. The renderer is one sequential video lane; overlapping tracks and effects remain outside 6.1.

### Phase 6.2 — Arabic timeline editor UI (merged)

Merged in [PR #8](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/8). Adds an RTL timeline workspace to the served /studio/ application. Users can add video assets from the selected project, edit source in-points and clip durations, reorder and remove clips, save the ordered manifest, render a preview and download the MP4.

See [Phase 6.2 — Arabic Timeline Editor](20-PHASE-6.2-ARABIC-TIMELINE-EDITOR.md).

### Phase 6.3 — Scene/shot selection (merged)

Merged in [PR #10](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/10). Adds saved scene-results reads and an RTL scene-selection workspace. Users can run ContentDetector against a selected project video, reload saved results, select individual scenes, and append the chosen source ranges as separate clips. This is deterministic scene-boundary detection; semantic AI ranking and transcript-aware recommendations are not claimed.

See [Phase 6.3 — Scene and Shot Selection](21-PHASE-6.3-SCENE-SHOT-SELECTION.md).

### Phase 6.4 — Arabic transcription and timeline SRT (merged)

Merged in [PR #11](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/11). Adds saved-transcription reads, explicit WhisperX transcription controls for Arabic/English with optional word alignment, and project-level SRT generation. Transcript segments are cropped to each clip's source in/out range and shifted to their positions on the assembled timeline. Timeline edits invalidate the saved SRT.

See [Phase 6.4 — Arabic Captions and Speech Sync](22-PHASE-6.4-ARABIC-CAPTIONS-SPEECH-SYNC.md). This produces a separate downloadable SRT; burned-in captions and embedded MP4 subtitle streams remain future work.

### Phase 6.5 — Audio mixing and synchronization (merged)

Merged in [PR #12](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/12). Adds project-scoped audio assets and saved Voice/Music/SFX tracks with source/timeline timing, per-clip and master gain, fades, optional FFmpeg afftdn noise reduction, source-video audio control and synchronized stereo AAC mixing. The PR passed Python/Ruff tests, the real FFmpeg audio-render integration test, Windows script checks and full Compose integration before merge.

See [Phase 6.5 — Audio Mixing and Sync](23-PHASE-6.5-AUDIO-MIXING-SYNC.md). This is not a waveform editor or real-time audio engine. The current noise reduction uses FFmpeg afftdn; it does not claim DeepFilterNet inference. Actual listening and browser acceptance on the target Windows host remain required.

### Phase 6.6 — Transitions, titles and motion graphics (merged)

Merged in [PR #13](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/13). Adds project-scoped transition/title/image-overlay manifests and deterministic FFmpeg rendering for dip-to-black and white-flash boundaries, Arabic-capable titles with fade/slide-up animation, and positioned image/logo overlays. The Arabic Studio includes controls to configure, save and export these effects; timeline edits invalidate the saved effects.

This is a deterministic FFmpeg editing slice, not an arbitrary keyframe editor or 3D compositor. Human render and browser acceptance on the target host remain open; reviewed Blender/ComfyUI templates are future work.

See [Phase 6.6 — Transitions, Titles and Motion Graphics](24-PHASE-6.6-TRANSITIONS-TITLES-MOTION.md).

### Phase 6.7 — Multi-shot generation and deterministic assembly (implementation branch)

PR #14 on `feat/phase-6.7-multishot-assembly` adds a durable `video.generate_sequence` job. After explicit approval, an image-reference workflow maps 2–8 CreativePlan scenes into individual Wan 2.2 TI2V jobs, optionally applies RIFE interpolation to each shot, validates and registers each shot as a project asset, then assembles the ordered clips into one MP4 with FFmpeg. The final asset is handed to the existing workflow engine for timeline and export steps.

The PR is open as a Draft and is not merged. CI run [42829261546](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/actions/runs/42829261546) passed all three jobs: Python checks, Windows script checks and Compose integration. The Compose test exercises the application/worker infrastructure; the video assembly test uses synthetic FFmpeg clips. Neither proves real Wan/RIFE model inference.

The same image reference is supplied to every shot. This does not guarantee frame-to-frame identity or exact product preservation; Phase 6.9 addresses product identity review. Real Wan/RIFE execution still requires the exported workflow registry, exact custom nodes/weights and target-GPU acceptance.

See [Phase 6.7 — Multi-Shot Generation and Assembly](25-PHASE-6.7-MULTISHOT-GENERATION-ASSEMBLY.md).

### Remaining Phase 6 increments

Deliver in independently testable increments:

- Video enhancement pipeline with frame extraction/reassembly only after output and storage validation (6.8).
- Product Identity Lock for product ads: reference preservation, logo/label checks and human review (6.9).
- Technical and visual QA: black/frozen frame detection, audio clipping/silence, duration/resolution/FPS checks, missing files and corrupt exports (6.10).

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
