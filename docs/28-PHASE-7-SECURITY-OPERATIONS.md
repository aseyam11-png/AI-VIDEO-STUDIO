# Phase 6.10 and Phase 7 — quality gate and production operations

Repository: aseyam11-png/AI-VIDEO-STUDIO  
Implementation branch: feat/phase-6.10-quality-gate-phase-7-hardening  
Status: implementation under CI and review; not a production acceptance certificate.

## Phase 6.10 — technical video/audio quality gate

The Studio analyzes the exact rendered MP4 before returning a download link. The report is project-scoped, saved as JSON, and bound to the file size and SHA-256 digest. The download route refuses a missing, failed, or stale report.

### Automated checks

- File existence, non-zero size, FFprobe container/stream inspection and SHA-256.
- Full FFmpeg decode of the selected video and audio streams with fatal decoding errors enabled.
- Duration against the saved timeline, requested resolution and frame rate.
- Black segments via blackdetect (default threshold: 0.5 seconds).
- Frozen segments via freezedetect (default threshold: 2 seconds).
- Prolonged digital silence via silencedetect (default threshold: 1 second at -50 dB).
- Near-full-scale peak detection using astats (warning threshold: -0.1 dBFS).
- Missing audio is blocking when the saved source/timeline requires audio.

### Export policy

| Finding | Result |
|---|---|
| Missing/empty/corrupt file, failed decode, missing video stream | Block |
| Wrong requested duration, resolution or FPS | Block |
| Black/frozen segment at or above the configured threshold | Block |
| Missing required audio stream | Block |
| Long silence or near-clipping peak | Warning; export may proceed after human listening review |
| All checks pass with no issues | Allow |

A failed report is returned to the Arabic Studio UI for diagnosis. Warnings remain visible with the report. This is deterministic technical QA: it does not understand Arabic speech semantics, musical licensing, aesthetic quality, or whether the product shown is the intended one. Those remain human-review requirements.

## Phase 7 — security and operations baseline

### API authentication

- Set a unique random STUDIO_API_TOKEN of at least 32 characters in the private .env file for a production installation.
- Production /v1/* API requests fail closed with HTTP 503 if the token is absent or too short; requests otherwise require Authorization: Bearer token.
- The Studio browser stores the token in per-tab sessionStorage, never in a JavaScript bundle or localStorage.
- Protected video, image and subtitle downloads are fetched with the bearer header and exposed through temporary browser object URLs; long-lived API tokens are not placed in URLs.
- /health and /ready remain unauthenticated for local probes. Keep the API bound to 127.0.0.1 or behind a private authenticated VPN/reverse proxy.
- FastAPI docs/OpenAPI are disabled in production.
- Response headers include nosniff, frame denial, referrer policy, permissions policy and a same-origin CSP; HSTS is added when the app is accessed over HTTPS.
- No cookie-based session is introduced. Keep API access same-origin; do not add permissive CORS.
- Compose exposes PostgreSQL and Valkey only inside the private Compose network. Ollama stays on loopback; ComfyUI remains private.

### Secret and data handling

- Use separate database bootstrap, migration and runtime credentials, Valkey credentials, ComfyUI credentials and the Studio API token.
- Keep .env, user media, workflow exports, model caches, checkpoints and logs containing private data outside Git and Docker build contexts.
- Apply restrictive Windows ACLs or Linux permissions to the environment file, media root and backup storage.
- Rotate a leaked token immediately and restart the API. Never place tokens in issue descriptions or source files.

### Consistent backup

Run on the Windows host:

~~~powershell
.\infra\scripts\windows\backup-studio.ps1 -OutputDirectory "E:\Studio-Backups"
~~~

The helper stops active API and worker writers, creates a PostgreSQL custom-format dump, archives the private media directory, and writes SHA-256 hashes for both artifacts. The services that were running are restarted in the finally block. Store the dump, media archive and manifest together on encrypted storage outside the repository.

A successful backup command is not a restore test. Keep at least one independently stored copy.

### Restore drill

Restore into a separate validation database and an empty media directory. This does not overwrite the configured application database or stop the running Studio:

~~~powershell
.\infra\scripts\windows\restore-studio.ps1 -DatabaseDump "E:\Studio-Backups\studio-YYYYMMDD-HHMMSSZ.dump" -MediaArchive "E:\Studio-Backups\studio-media-YYYYMMDD-HHMMSSZ.tar.gz" -Sha256Manifest "E:\Studio-Backups\studio-YYYYMMDD-HHMMSSZ.sha256.txt" -RestoreMediaDirectory "E:\AI-Video-Studio-Restore" -RestoreDatabaseName "video_studio_restore_20261111" -ConfirmRestore
~~~

The restore helper verifies both SHA-256 values, validates the PostgreSQL archive inside the database container, rejects unsafe archive paths, creates a new separate restore database, and extracts media only to a new/empty directory outside the repository. It does not overwrite the configured application database and does not stop the running Studio. Check the restored database tables and media file integrity before considering any cutover. A production cutover requires separate database ownership/privilege checks and an explicit environment change; this helper does not perform that switch.

### Operations

- /health is liveness; /ready validates database connectivity and current Alembic head.
- Use docker compose ps, docker compose logs --tail=200 api worker-cpu worker-models worker-control scheduler, nvidia-smi, disk-space monitoring and the worker job-event API.
- Compose healthcheck is attached to /ready.
- External alert delivery, dashboards, automatic retention, off-site key management, and a documented RPO/RTO are still outstanding.

## Real model runtime acceptance — the target Windows/GPU is still required

Cloud CI cannot access the owner's GPU, local Ollama model cache, private ComfyUI workflows or checkpoint files. This branch does not claim real inference.

| Model | Required evidence |
|---|---|
| Qwen3.5-9B Q4_K_M | Arabic CreativePlan produced through /v1/director/plan, load/inference time, GPU offload and VRAM/RAM observation |
| Wan 2.2 TI2V 5B | API-exported workflow, exact custom nodes/checkpoint hashes/licenses, multi-shot job success, output asset, FFprobe and visual review |
| Real-ESRGAN | Actual per-frame upscale job, before/after sample, output asset, dimensions, audio preservation and GPU memory |
| RIFE | Actual interpolation job/output, frame cadence/temporal review and GPU memory |
| WhisperX / DeepFilterNet when enabled | Real Arabic recording transcription/listening or denoise comparison, exact model/checkpoint and RAM/timing |

### Required target-host process

1. Run infra/scripts/windows/host-inventory.ps1; keep its minimized output outside Git.
2. Run infra/scripts/windows/model-runtime-preflight.ps1. A reachable service is not proof of inference.
3. Export API-format workflows from the installed ComfyUI and compare node IDs/input bindings with its live /object_info catalog. Do not guess node IDs.
4. Record exact model/checkpoint versions, SHA-256 hashes, license terms and custom-node revisions. Keep ComfyUI private.
5. Run the explicit Windows helper documented in docs/29-PHASE-7-TARGET-GPU-ACCEPTANCE.md. It prompts for the bearer token without persisting it, waits for actual job completion, records asset/job IDs, and samples nvidia-smi during inference.
6. Review all output frames in the Arabic Studio on Windows. Listen to audio; inspect black/frozen/silence findings; inspect logos, labels, materials and shot-to-shot consistency.
7. Accept each model independently only after its own real job, registered output, technical report and human review succeed.

## Release gate

The Studio is not production-ready until the code and migrations pass CI, real model acceptance is recorded for every enabled runtime, the browser workflow is accepted on Windows, authentication and private networking are configured, quality reports and human review pass on real footage, and an isolated database/media restore drill is completed.
