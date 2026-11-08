# AI Video Studio 2.0

**Arabic-first, single-user, agentic video-production studio.**

The goal is to let one user describe a video in Arabic, provide source media and product references, approve a creative plan, then orchestrate analysis, selected AI generation, editing, motion graphics, audio processing, quality checks, and final export.

> **Status: architecture scaffold — not production-ready.** Model weights are not included. GPU inference, ComfyUI/Wan workflows, complete timeline rendering, durable production jobs, and end-to-end acceptance tests must be implemented and benchmarked before production use.

## Product scope

- One user, with access protected by a private VPN and authentication.
- Finished videos from 1 to 5 minutes.
- Arabic-first interaction, with support for mixed Arabic/English speech.
- Product commercials, lifestyle scenes, motion graphics, talking-head edits, and restoration.
- Initial GPU target: one NVIDIA GPU with 24 GB VRAM (RTX 3090 candidate).
- No paid model API is required by the base design. External services must be optional, disabled by default, and explicitly approved.

## Architecture

1. **Studio UI** — Arabic chat, media upload, storyboard approval, job monitor, preview, and project history.
2. **Control API** — FastAPI, project/media/job state, approval gates, and audit events.
3. **Executive Director** — local multimodal LLM role; converts the brief into validated production plans.
4. **Orchestrator** — LangGraph state machine with four specialist supervisors: editing, audio, generation/motion, and quality. Roles may reuse the same LLM instance.
5. **Tool Gateway** — strict schemas, allowlists, project-scoped paths, timeouts, idempotency, and audit logs. No arbitrary shell access for agents.
6. **Media engines** — FFmpeg/FFprobe, OpenTimelineIO, PySceneDetect, audio/video algorithms, Blender.
7. **Model workers** — Ollama, ComfyUI/Wan, SAM 2, Grounding DINO, WhisperX, DeepFilterNet. Heavy GPU jobs are serialized initially.
8. **Quality and delivery** — technical, visual, product-identity, and creative checks; versioned outputs; user approval before final export.

## Key design decisions

- PostgreSQL is the durable source of truth. Redis-compatible Valkey is only a broker/cache.
- LangGraph checkpoints must be persisted and tested; in-memory checkpoints are not for production.
- CPU and GPU jobs use separate queues. A GPU lease allows one heavy GPU job at a time on the initial 24 GB profile.
- FFmpeg is the deterministic render engine. OpenTimelineIO is the timeline interchange model, not a renderer.
- ComfyUI workflows are reviewed, versioned templates. Agents may fill validated parameters only.
- Product Identity Lock is a first-class subsystem. Generative video can alter small product details; logos, dial markings, model numbers, and other critical features require reference compositing and QA.
- CapCut is an optional Windows-side handoff. Core rendering must remain independent of undocumented CapCut project formats.
- Never commit API keys, environment files, user media, model weights, caches, generated assets, or credentials.

## Repository layout

    apps/studio-web/        Next.js Arabic RTL studio UI scaffold
    backend/app/            FastAPI control plane, schemas, orchestration
    workers/                Isolated CPU/GPU workers (phased implementation)
    config/                 Model/tool registry and runtime policies
    docs/                   Architecture, roadmap, risks, licenses, deployment
    infra/                  Compose and operational scripts
    tests/                  Unit, integration, GPU benchmark, acceptance tests

## Current implementation status

| Component | Status |
|---|---|
| Architecture, roadmap, risk register | Drafted |
| FastAPI health/capabilities endpoints | Scaffold |
| LangGraph workflow | Placeholder nodes only |
| Generation approval policy | Initial code and tests |
| PostgreSQL / Valkey Compose services | Declared; deployment not verified |
| Arabic RTL web UI | Visual scaffold; API not wired |
| GPU scheduler and isolated workers | Not implemented |
| WhisperX / SAM 2 / Wan / ComfyUI inference | Not installed or benchmarked |
| Product Identity Lock | Design requirement; not implemented |
| CapCut handoff | Design only |
| Production readiness | **Not ready** |

## Public repository notice

This repository is currently public. Keep it free of secrets and private media. A public repository does not automatically grant an open-source license to its own code. The project-level license is intentionally unselected; third-party code, weights, datasets, and extensions retain their separate licenses. See docs/05-LICENSE-REVIEW.md.

## Next step

Start with Phase 0 and Phase 0.5 in docs/02-IMPLEMENTATION-ROADMAP.md. Do not provision paid GPU time or download large model weights until the hardware, exact model artifacts, licenses, and budget have been verified.
