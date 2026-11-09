# Windows host inventory for AI Video Studio

Run this before installing model weights or provisioning ComfyUI. The script is read-only: it inspects the machine and does not install drivers, change configuration, start services or download weights.

From PowerShell at the repository root:

```powershell
.\infra\scripts\windows\host-inventory.ps1
```

To identify the drive intended for private media/model storage (the script records the drive letter and free space, not the full path):

```powershell
.\infra\scripts\windows\host-inventory.ps1 -MediaPath "D:\AI-Video-Studio-Data"
```

To save a shareable JSON report to the Desktop:

```powershell
.\infra\scripts\windows\host-inventory.ps1 -MediaPath "D:\AI-Video-Studio-Data" -OutputPath "$env:USERPROFILE\Desktop\ai-video-studio-host-inventory.json"
```

## What the report contains

- Windows edition/version/build and architecture.
- CPU model, core count and logical processor count.
- Total and currently free RAM.
- Windows GPU names and driver versions.
- NVIDIA GPU model, total VRAM and driver version from `nvidia-smi`, when available.
- Fixed-drive capacity/free space and the free space on the specified media drive.
- Docker client/server versions, Docker Compose version and WSL status.

The report intentionally excludes serial numbers, network addresses, usernames, environment variables and credentials. The `Win32_VideoController.AdapterRAM` field is not treated as authoritative VRAM because it can be capped; use the `nvidia-smi` line for NVIDIA hardware.

## How we use the result

1. Confirm the actual GPU model and VRAM before selecting the ComfyUI/Wan checkpoints.
2. Confirm RAM and free disk space before downloading Qwen or any visual model weights.
3. Choose one Ollama deployment only: a Windows host service or the private Compose profile. Do not run both against the same port without an intentional configuration.
4. Choose a ComfyUI runtime whose process and the Studio workers can read the same private media directory.
5. After inventory, run the runtime preflight:

```powershell
.\infra\scripts\windows\model-runtime-preflight.ps1
```

After Qwen is installed, run the Arabic inference check:

```powershell
.\infra\scripts\windows\model-runtime-preflight.ps1 -RunArabicInferenceSmokeTest
```

Do not interpret a successful inventory as GPU inference acceptance. That requires real model runs, output validation and measured memory/performance.
