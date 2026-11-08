# Implementation Roadmap

## Phase 0 — Verification
- Confirm the actual GPU SKU, VRAM, CPU, RAM, storage, region and provider pricing.
- Verify CUDA, driver, PyTorch and Docker GPU runtime compatibility.
- Review the exact code, weights, datasets, extensions and commercial-use terms for each selected model.
- Record model revisions, SHA-256 hashes and benchmark inputs.
- Do not provision paid compute or download large weights before budget approval.

Exit criteria: hardware profile, budget cap, pinned candidates and license manifest approved.

## Phase 0.5 — Deterministic media pipeline (no agents)
- Media upload and FFprobe inspection.
- Safe proxy generation and VFR-to-CFR handling while preserving source time mapping.
- Arabic transcription baseline and scene detection.
- OpenTimelineIO timeline generation.
- FFmpeg preview/export and technical checks.

Exit criteria: a real Arabic source video produces a valid, editable timeline with synchronized audio.

## Phase 1 — Durable control plane
- PostgreSQL project/job/asset state.
- Valkey queue and separate CPU/GPU workers.
- Idempotency keys, GPU lease, watchdog, retry limits and checkpoints.
- Disk quota, per-project GPU-seconds and cost accounting.

Exit criteria: controlled worker crash and server restart do not duplicate or lose completed stages.

## Phase 2 — Executive Director
- Local Qwen integration through Ollama.
- LangGraph orchestration with typed JSON schemas.
- Four specialist supervisors sharing the base model where appropriate.
- Approval gates and decision log.

Exit criteria: Arabic brief becomes a validated creative brief and storyboard; no tool executes from free-form model text.

## Phase 3 — Smart editing and audio
- Transcript-based editing with phrase/sentence boundaries.
- Shot ranking and semantic B-roll retrieval.
- Audio cleanup A/B checks, music ducking and loudness checks.
- Timeline versioning and edit decision records.

Exit criteria: produce a reviewable timeline from user media.

## Phase 4 — Generation and motion
- Pinned ComfyUI deployment and approved Wan 2.2 TI2V 5B workflow.
- Blender motion templates and product compositing.
- Product Identity Lock with image references and QA.
- Limit generation retries and spend.

Exit criteria: successful fixed 5-second 720p benchmark and product-ad test.

## Phase 5 — Quality and delivery
- Technical, visual, product-identity and creative QA.
- Selective Real-ESRGAN/RIFE use after A/B comparison.
- Final preview, approval and export.
- CapCut handoff as optional Windows adapter.

Exit criteria: 1-, 3- and 5-minute end-to-end acceptance runs with recorded time, cost and VRAM.

## Release rule
The scaffold is not production-ready until every phase's exit criteria passes on the selected server.
