# Phase 6.8 — Bounded video enhancement

**Repository:** `aseyam11-png/AI-VIDEO-STUDIO`  
**Branch:** `feat/phase-6.8-video-enhancement-sync`  
**Status:** Implementation submitted for review; CI and target-host inference acceptance remain open.

## Goal

Provide a durable video-enhancement operation that upscales each frame with the approved Real-ESRGAN ComfyUI workflow, then rebuilds a verified MP4 while retaining the source audio streams.

This is a bounded processing path, not a claim that every video can be enhanced at arbitrary duration, resolution or frame rate.

## User path

1. Select a project video in the Arabic Studio.
2. The enhancement option is enabled only when the Real-ESRGAN workflow is configured and the asset has a verified duration no longer than 10 seconds.
3. The user checks **تحسين وضوح الفيديو** and submits the plan. The workflow still requires explicit approval before jobs run.
4. The workflow engine creates a durable `video.enhance` job on the serialized models queue.
5. FFprobe validates the source. FFmpeg extracts a constant-FPS PNG sequence.
6. The worker sends each frame to the approved `realesrgan_upscale_v1` ComfyUI workflow, sequentially. It validates that each returned image is inside that frame's output directory and has the expected dimensions.
7. FFmpeg rebuilds the MP4, maps all source audio streams and re-encodes them as AAC, then verifies output duration, dimensions and audio-stream presence.
8. The completed video is registered as a project-scoped asset. An `enhancement.json` manifest records source/output dimensions, FPS, scale, frame count and the registered output asset ID. Intermediate frame and ComfyUI artifacts are removed after successful registration.

## Enforced bounds

- Duration: 1–10 seconds per job.
- Extracted frames: maximum 300.
- Requested extraction FPS: 15, 24 or 30 FPS; the actual output rate is capped by the source and may be any integer from 1–30 FPS.
- Model scale: 2× or 4×.
- Output dimensions: maximum 7680×4320.
- Aggregate extracted pixel budget: 650,000,000 pixel-frames.
- Aggregate extracted PNG bytes and aggregate enhanced-frame bytes: each bounded by `MAX_MODEL_OUTPUT_BYTES` (default 2 GiB).
- Final MP4 registration also enforces the configured generated-output byte limit.
- Every enhancement operation requires explicit workflow/user approval; no model weights are downloaded automatically.

A source that exceeds any bound is rejected instead of silently truncated or rendered with unbounded resource use.

## Technical implementation

- `JobType.VIDEO_ENHANCE` / public tool ID `video.enhance`.
- Strict payload schema: scale, FPS, maximum duration, CRF and approval flag.
- Model-worker routing and cancellation integration with the existing durable job engine.
- FFprobe/FFmpeg extraction and assembly helpers in `backend/app/media/pipeline.py`.
- Per-frame ComfyUI execution and project asset registration in `backend/app/services/model_jobs.py`.
- Workflow planning uses the saved FFprobe metadata; generated image-to-video workflows may append enhancement only when their planned output is within the same 10-second bound.
- Arabic Studio toggle submits `upscale_output`; it is disabled when workflow readiness or source duration is not acceptable.

## Test evidence and limitations

The automated tests use synthetic FFmpeg-generated clips and a mocked ComfyUI client. They verify bounded extraction, sequential frame dispatch, output path/dimension validation, project-asset registration and MP4 reassembly with audio streams. This does **not** prove real Real-ESRGAN inference, visual quality or GPU performance.

Before enabling this feature in a release, provision and record the real API-exported workflow JSON, ComfyUI node IDs/input bindings, custom-node revisions, checkpoint hashes/licenses and shared media path. Then run a real smoke test on the target host and review the result visually and audibly.

## Remaining acceptance

- CI must pass on the synchronized feature PR.
- Real Real-ESRGAN inference and GPU memory/time measurements must be recorded on the target machine.
- Browser acceptance of the Arabic toggle and job progress must be performed on the target host.
- Review output quality, every audio stream, synchronization and file cleanup on several real sample videos.
