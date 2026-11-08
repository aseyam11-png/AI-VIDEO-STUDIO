# Master Architecture — AI Video Studio 2.0

## Scope
Single user; private GPU cloud; Arabic-first conversational control; finished videos from 1 to 5 minutes; product commercials, lifestyle scenes, motion graphics, talking-head edits and restoration. Initial target is one NVIDIA GPU with 24 GB VRAM. This is a capacity target, not a guarantee that all models fit simultaneously.

## Logical layers
1. Studio UI — Arabic chat, media upload, storyboard review, timeline preview, job monitor and approvals.
2. Control API — projects, media assets, jobs, costs, approvals and audit events.
3. Executive Director — local multimodal LLM role that converts the brief into a validated creative plan.
4. Orchestrator — LangGraph state machine with four specialist supervisors: editing, audio, generation/motion and quality. Roles may share one LLM instance.
5. Tool Gateway — strict schemas, allowlists, project-scoped paths, timeouts, idempotency and audit logs. No arbitrary shell access.
6. Deterministic media engines — FFmpeg, FFprobe, OpenTimelineIO, PySceneDetect and audio/video algorithms.
7. Model workers — Ollama, ComfyUI/Wan, SAM 2, Grounding DINO, WhisperX, DeepFilterNet and Blender. Heavy GPU jobs are serialized initially.
8. Quality and delivery — technical, visual, product-identity and creative checks; versioned outputs; user approval before final export.

## Runtime responsibilities
- PostgreSQL is the durable source of truth for project, job and approval state.
- Valkey/Redis is a broker/cache, not the only record of job state.
- LangGraph checkpoints must be persisted in PostgreSQL and recovery must be tested.
- CPU tasks and GPU tasks use separate queues.
- One GPU lease prevents simultaneous heavy models exhausting 24 GB VRAM.
- Model workers run in isolated processes/containers with pinned dependencies.
- FFmpeg is the render engine; OpenTimelineIO represents and exchanges timeline structure.
- ComfyUI workflows are versioned templates; the agent fills validated parameters only.
- Product Identity Lock compares generated shots with the user's references. Critical logos and small markings need compositing or 3D references rather than blind text-to-video generation.

## Approval policy
Human approval is required before paid generation, destructive operations, external uploads and final export. Non-destructive analysis and proxy generation may run automatically within project quotas.

## Non-goals for the first release
- Multi-user SaaS or public upload service.
- Training foundation models from scratch.
- Running all models concurrently on one GPU.
- Depending on undocumented CapCut project formats.
