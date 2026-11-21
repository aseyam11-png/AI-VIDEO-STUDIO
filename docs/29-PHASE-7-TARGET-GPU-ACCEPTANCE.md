# Phase 7.1 — target Windows/GPU model acceptance procedure

**Repository:** aseyam11-png/AI-VIDEO-STUDIO  
**Script:** infra/scripts/windows/target-runtime-acceptance.ps1  
**Purpose:** run controlled, real local inference; this is not a CI substitute or a claim that the target host has already passed.

## Preparation

1. Run infra/scripts/windows/host-inventory.ps1 and retain the minimized output outside Git.
2. Run infra/scripts/windows/model-runtime-preflight.ps1. Resolve driver, free disk, Docker, Ollama, ComfyUI and Studio API readiness issues before downloading weights.
3. In the private ComfyUI environment, install only the reviewed custom-node revisions and exact model checkpoints required for the intended tests. Record their versions, SHA-256 hashes and license terms.
4. Export the API-format workflows from the running ComfyUI instance. Populate MEDIA_ROOT/workflows/registry.json with the exact node IDs, input names, required inputs and workflow IDs. Never copy node IDs from an unrelated UI workflow or guess them.
5. Confirm the Studio model status endpoint reports the selected workflow as registered, workflow_file_ready and bindings_valid.
6. Create a private project in the Studio, upload a short source video no longer than 10 seconds and upload one product reference image. Use media with permission to test.
7. Create a unique random Studio token in the private .env. Example PowerShell generation:

~~~powershell
$bytes = [byte[]]::new(48)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$token
~~~

Store it only in the local password manager and .env. Never add it to Git or to the acceptance evidence.

## Run the real tests

The script prompts for the API token using hidden input. It does not save the token. The individual inference switches deliberately require explicit selection.

Qwen Arabic planning:

~~~powershell
.\infra\scripts\windows\target-runtime-acceptance.ps1 -ProjectId "PROJECT_UUID" -RunQwenInference
~~~

Wan 2.2 multi-shot generation with SHA-256 product identity lock:

~~~powershell
.\infra\scripts\windows\target-runtime-acceptance.ps1 -ProjectId "PROJECT_UUID" -ProductImageAssetId "IMAGE_ASSET_UUID" -RunWan
~~~

RIFE and Real-ESRGAN on the generated Wan video from the same run:

~~~powershell
.\infra\scripts\windows\target-runtime-acceptance.ps1 -ProjectId "PROJECT_UUID" -ProductImageAssetId "IMAGE_ASSET_UUID" -RunWan -RunRIFE -RunRealESRGAN
~~~

RIFE/Real-ESRGAN on an existing project video instead:

~~~powershell
.\infra\scripts\windows\target-runtime-acceptance.ps1 -ProjectId "PROJECT_UUID" -VideoAssetId "VIDEO_ASSET_UUID" -RunRIFE -RunRealESRGAN
~~~

The script waits for each actual Studio job, fails on a failed/cancelled job, verifies that output asset IDs were registered, and saves a timestamped evidence JSON outside the repository. It records model status snapshots and the generated output asset IDs. It never marks a model operational just because an HTTP endpoint was reachable.

## Evidence review

For each run, record and inspect:

- exact model tag/checkpoint hash and applicable license;
- workflow ID, ComfyUI export revision and custom-node versions;
- source asset IDs and output asset ID;
- GPU name, total VRAM, peak VRAM sampled during inference, RAM and elapsed time;
- FFprobe metadata and Phase 6.10 report;
- real visual review of every shot, product silhouette, materials, logos and labels;
- real audio listening and synchronization review.

The script's initial design check does not by itself certify image quality, temporal quality, brand correctness or licensing. A successful HTTP response from Ollama/ComfyUI is not an inference pass. The actual completed job, registered output and human review are required.

## Acceptance ledger

| Model | Minimum passing evidence | Status before target run |
|---|---|---|
| Qwen3.5-9B Q4_K_M | Valid Arabic CreativePlan via /v1/director/plan, timing, GPU/VRAM observation | Not verified |
| Wan 2.2 TI2V 5B | Real 2-shot generation, output asset, identity manifest, visual review | Not verified |
| RIFE | Real interpolation job and output video, frame-rate/temporal review | Not verified |
| Real-ESRGAN | Real frame-by-frame enhancement, output asset, 2× dimensions, preserved audio and visual review | Not verified |

Do not run all models concurrently. The model queue is intentionally single-concurrency. If a model fails, preserve the failure report and job ID, fix the actual registry/weights/runtime issue, then run a new acceptance job with a new idempotency key.
