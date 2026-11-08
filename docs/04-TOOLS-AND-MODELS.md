# Tools and Models Registry Guide

The canonical machine-readable candidate list is config/models.yaml and config/tool-registry.yaml. An entry marked candidate is not installed, benchmarked or approved for commercial use.

## Control plane
- FastAPI: project/job API.
- PostgreSQL: durable project and job state.
- Valkey: broker/cache.
- LangGraph: stateful workflow orchestration.
- Ollama: local LLM runtime.
- MCP: typed tool integration protocol.

## Media understanding
- FFprobe/FFmpeg: metadata, proxy generation, transcode and render.
- PySceneDetect: candidate scene boundaries.
- WhisperX: speech transcription, alignment and diarization integration.
- SAM 2: object segmentation and tracking.
- Grounding DINO: text-conditioned object detection.
- OpenCLIP: semantic image embeddings; FAISS may index those embeddings.
- MediaPipe: optional face/hand/pose landmarks.

## Generation and motion
- ComfyUI: versioned generation workflows.
- Wan 2.2 TI2V 5B: candidate text/image-to-video model; benchmark on the exact GPU.
- Blender: 2D/3D animation, compositing and camera movement.
- LivePortrait and AnimateDiff: optional later experiments subject to artifact-license review.

## Audio
- DeepFilterNet: optional speech-noise suppression with A/B comparison.
- librosa: audio feature extraction and beat analysis.
- Demucs: optional; upstream repository is archived, so use only a maintained and license-reviewed fork if needed.

## Restoration and QA
- Real-ESRGAN: selected-frame restoration/upscaling.
- RIFE: selected-shot frame interpolation.
- VMAF: perceptual comparison against a reference or for codec evaluation, not a general creative-quality score.
- Custom QA: black frames, clipping, sync, captions, product consistency and scene relevance.

## Timeline and delivery
- OpenTimelineIO: canonical timeline interchange representation.
- FFmpeg: deterministic preview and final render.
- CapCut: optional local Windows handoff only; undocumented project formats must not be a critical dependency.

## Selection rules
1. Pin source commit and model-weight revision.
2. Record SHA-256 for downloaded weights.
3. Review code, weights, datasets, plugins and dependencies separately.
4. Benchmark peak VRAM, host RAM, runtime and quality on the actual server.
5. Keep optional models disabled until their use case is demonstrated.
