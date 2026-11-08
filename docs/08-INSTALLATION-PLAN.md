# Installation Plan

Do not install every ML package into one Python environment. Use separate pinned containers or environments for incompatible stacks.

## Layer 1 — Base services
- Docker Engine and Compose.
- PostgreSQL 16.
- Valkey.
- FastAPI control plane.
- CPU worker.
- FFmpeg/FFprobe.

## Layer 2 — Agent runtime
- Ollama with a pinned Qwen artifact.
- LangGraph with PostgreSQL-backed checkpoints.
- Strict Pydantic schemas and allowlisted tool gateway.
- CPU-first LLM or explicit GPU lease when using GPU inference.

## Layer 3 — Analysis workers
- PySceneDetect + OpenCV.
- WhisperX; evaluate Arabic dialects and aligner support.
- SAM 2.
- Grounding DINO.
- DeepFilterNet only after raw/clean A/B evaluation.
- OpenCLIP + FAISS only after basic media indexing works.

## Layer 4 — Generation and motion workers
- ComfyUI at a pinned commit.
- Wan 2.2 TI2V 5B at a pinned model revision.
- Blender in a separate image or controlled worker.
- Fixed, reviewed workflow JSON files.

## Layer 5 — Quality workers
- FFmpeg technical checks.
- VMAF for reference/codec comparisons.
- Real-ESRGAN and RIFE for selected shots only.
- Product Identity Lock tests using approved reference packs.

## Install acceptance record
For every worker, record image digest, Git commit, lockfile, weight revision, SHA-256, license evidence, NVIDIA driver/CUDA/PyTorch compatibility, peak VRAM/RAM, runtime and output quality.

Do not download model weights automatically during bootstrap.
