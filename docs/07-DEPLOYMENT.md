# Deployment Notes

## Development
1. Install Docker Engine and Docker Compose plugin.
2. Copy .env.example to .env and set unique strong secrets.
3. Run docker compose up --build.
4. Check http://127.0.0.1:8000/health.

## GPU cloud deployment
- Verify provider, region, instance SKU, GPU, driver, CUDA, Docker NVIDIA runtime, VRAM, RAM, CPU and storage.
- Put the API behind a private VPN and authentication.
- Do not publish PostgreSQL, Valkey, Ollama, ComfyUI, MCP or worker ports.
- Use separate pinned images for ComfyUI/Wan, SAM 2 and WhisperX.
- Enable one GPU worker initially with a lease/lock.
- Keep persistent project data separate from ephemeral compute.
- Keep independent backups and test restore.
- Shut down GPU after queues are empty and an idle grace period expires.
- Configure hard per-project GPU-second and spend limits.

## Preflight
nvidia-smi
ffmpeg -version
ffprobe -version
docker compose version
python --version

Model weights are not downloaded automatically by the bootstrap.
