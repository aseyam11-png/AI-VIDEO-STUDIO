# Risk Register

This register is a living engineering control, not proof that every mitigation has been implemented or tested.

| ID | Risk | Required mitigation | Acceptance evidence |
|---|---|---|---|
| GPU-01 | CUDA out of memory | One heavy GPU job at a time; process isolation; model unload; worker restart on leak | Three repeated benchmark runs without OOM |
| GPU-02 | VRAM fragmentation | Pin PyTorch; measure allocator behavior; test expandable segments where supported | Peak/reserved VRAM telemetry |
| GPU-03 | Wan exceeds 24 GB | CPU offload, tiled/low-memory settings, short shots, validated workflow | Fixed 5-second 720p benchmark |
| GPU-04 | Qwen competes with generation | CPU-first LLM or explicit GPU lease | No overlapping heavy GPU leases |
| GPU-05 | Worker hangs | Heartbeat, timeout, cancellation and process supervisor | Forced timeout recovers the GPU |
| GPU-06 | RAM exhaustion during offload | Verify host RAM; set memory limits; monitor swap and OOM events | 10 consecutive generation jobs |
| MODEL-01 | License restricts usage | Review code, weights, dataset, extensions and intended use separately | Signed license manifest |
| MODEL-02 | Upstream becomes unavailable | Pin commit and artifact hash; mirror where allowed | Reproducible reinstall |
| MODEL-03 | Weights are corrupted | SHA-256 verification and atomic download | Checksum match |
| MODEL-04 | Remote code executes unexpectedly | Code review, isolated container, no broad host mounts | Security review passed |
| DEP-01 | PyTorch/CUDA conflicts | Separate pinned worker images | Image build and smoke tests |
| DEP-02 | Dependency upgrade breaks behavior | Lockfiles and regression fixtures | CI passes before update |
| DEP-03 | FFmpeg codec/license differences | Record build configuration and linked libraries | ffmpeg -buildconf captured |
| AUDIO-01 | Arabic dialect transcription errors | Test representative clips, glossary and human correction | Word error review on Arabic corpus |
| AUDIO-02 | False words in silence | VAD as a candidate filter; preserve original audio and timestamps | Silence fixture has no hallucinated captions |
| AUDIO-03 | Alignment drift | Language-specific aligner evaluation and offset checks | Word timing fixture passes |
| AUDIO-04 | Noise reduction damages speech | A/B compare raw and cleaned audio; cleanup optional | Human intelligibility comparison |
| VISION-01 | Incorrect scene boundaries | Adaptive threshold and dissolve detection; allow manual correction | Scene-boundary fixture |
| VISION-02 | False object detections | Confidence thresholds and temporal confirmation | Precision review on sample set |
| VISION-03 | Target lost during occlusion | Re-seed tracker and validate mask stability | Occlusion fixture |
| VISION-04 | VFR/rotation causes sync errors | Preserve source time map; normalize proxies only | Audio/video sync fixture |
| GEN-01 | Face/hand artifacts | Prefer suitable framing, controlled motion, QA and limited retries | Human visual acceptance |
| GEN-02 | Product identity drift | Product reference pack, tracking, compositing/3D and identity checks | Logo/dial/shape comparison |
| GEN-03 | Distorted generated text | Render titles in deterministic graphics engine, not inside video generation | Exact text match |
| GEN-04 | Non-repeatable generation | Record seed, workflow, model hash and parameters; seed is not a full determinism guarantee | Provenance manifest |
| EDIT-01 | Cut inside a word | Transcript-aware boundaries, handles and audio crossfades | Speech continuity review |
| EDIT-02 | Timeline/render mismatch | Same canonical timeline and renderer settings for preview/export | Frame-accurate comparison |
| EDIT-03 | Broken source references | Stable asset IDs and project-relative references | Project relink test |
| EDIT-04 | B-roll semantically wrong | Similarity threshold plus relevance check; ask user if uncertain | Human review |
| AUDIO-05 | Music masks dialogue | Speech-aware ducking and loudness checks | Dialogue intelligibility test |
| AUDIO-06 | Clipping in source | Detect and warn; do not pretend lost peaks can be restored | Clipping report |
| QA-01 | VMAF misused as creative score | Use for codec/reference comparisons only; separate visual/creative QA | QA report separates score types |
| QA-02 | Upscaling flickers | A/B test temporal behavior and process selected shots only | Flicker review |
| AGENT-01 | Hallucinated tool or parameter | Strict Pydantic schemas and allowlisted tools | Invalid call rejected |
| AGENT-02 | Infinite agent loop | Step, time, retry and cost limits | Loop limit fixture |
| AGENT-03 | Prompt injection in media | Treat OCR/subtitles/metadata as untrusted data | Injection test rejected |
| AGENT-04 | Conflicting decisions | Persistent decision log and typed project state | Restart preserves decisions |
| REL-01 | Duplicate work after retry | Idempotency keys and atomic artifact publication | Duplicate delivery test |
| REL-02 | Redis loses task | PostgreSQL remains source of truth; reconcile outbox and queue | Queue loss recovery test |
| REL-03 | Worker dies silently | Heartbeat, timeout and retry policy | Worker kill test |
| REL-04 | Partial output is mistaken as complete | Write temp files then atomic rename; validate artifact | Corrupt partial output rejected |
| STORAGE-01 | Disk fills mid-render | Preflight plus continuous disk monitoring and quotas | Low-disk test |
| STORAGE-02 | Server removal loses media | Separate persistent volume and external backup | Restore drill |
| SECURITY-01 | Service ports exposed | VPN, firewall, private network and authentication | External port scan |
| SECURITY-02 | Secrets committed | Secret scan, .gitignore and rotation procedure | CI secret scan |
| SECURITY-03 | Agent performs destructive action | Tool allowlist and human approval | Delete operation blocked |
| COST-01 | Regeneration exceeds budget | Per-project cap, retry cap, spend estimates and approval | Budget cap test |
| COST-02 | GPU runs idle | Queue-aware shutdown with grace period | Idle shutdown test |
| UX-01 | User cannot tell job state | Live job events, clear errors and time/cost estimates | UI acceptance test |
| UX-02 | Too many approval prompts | Risk-based approval levels | Approval policy tests |

## Operational rules

- Never treat a mitigation as complete until its acceptance evidence is recorded.
- PostgreSQL is the durable source of truth; Redis/Valkey is not.
- Heavy GPU jobs are serialized on the 24 GB profile.
- Every generation has an approved workflow ID, cost cap, timeout and retry limit.
- Preserve source media and timeline versions; destructive edits are prohibited.
- Reassess this register after each production incident and major dependency update.
