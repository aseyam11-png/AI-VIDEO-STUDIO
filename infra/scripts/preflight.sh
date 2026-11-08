#!/usr/bin/env bash
set -euo pipefail

echo "== Host =="
uname -a

echo "== Docker =="
docker --version
docker compose version

echo "== Media tools =="
if command -v ffmpeg >/dev/null 2>&1; then ffmpeg -version | head -n 1; else echo "ffmpeg: not installed"; fi
if command -v ffprobe >/dev/null 2>&1; then ffprobe -version | head -n 1; else echo "ffprobe: not installed"; fi

echo "== NVIDIA =="
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else echo "nvidia-smi: not available"; fi

echo "Preflight complete. This script does not download model weights or start paid GPU resources."
