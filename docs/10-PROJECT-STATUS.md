# Project Status

Last updated: 2026-11-08.

| Area | Status | Evidence / next action |
|---|---|---|
| GitHub repository | Public repository populated | Keep secrets and private media out |
| Architecture, roadmap, risk register | Drafted | Implement and attach acceptance evidence |
| Project creation | Implemented | Atomic JSON manifest on private media volume |
| Video/image upload | Implemented | Extension allowlist, size cap, SHA-256 and FFprobe validation |
| Proxy generation | Implemented | FFmpeg H.264 proxy; verify with integration test |
| Scene detection | Implemented | PySceneDetect ContentDetector; tune threshold per content |
| Arabic transcription | Optional adapter | WhisperX not installed by default; CPU-only in Phase 0.5 |
| Timeline | Implemented | OpenTimelineIO file plus JSON manifest |
| Preview render | Implemented | Cut-only render from one source asset using FFmpeg |
| Media end-to-end test | Added | Must pass CI before Phase 0.5 acceptance |
| FastAPI control plane | Active scaffold | No production authentication yet |
| PostgreSQL / Valkey | Compose declared | Phase 0.5 filesystem manifests; database integration still planned |
| Arabic RTL web UI | Visual scaffold | Media upload and live API integration not wired |
| GPU scheduler and isolated workers | Not implemented | Needed before CUDA transcription or generation |
| Qwen / LangGraph agents | Placeholder/design | No live LLM connected |
| Wan / ComfyUI / SAM 2 / Grounding DINO | Candidate only | Not installed or benchmarked |
| Product Identity Lock | Design requirement | Not implemented |
| CapCut handoff | Design only | Optional Windows-side adapter |
| Production readiness | **Not ready** | Complete Phase 1+ and full end-to-end acceptance |

The Phase 0.5 API is a local/private development surface. Do not expose it to the public Internet; authentication and private-network deployment are required before remote use.
