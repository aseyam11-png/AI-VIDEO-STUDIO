# Phase 3 — Durable AI workflows and local runtime acceptance

## Workflow behavior

The Workflow API turns a reviewed production plan into a durable sequence of Phase 1 jobs. Each run and each step is persisted in PostgreSQL. Job creation, job event, and transactional outbox row for the next step are committed in one database transaction.

The first version supports these safe paths. SAM 2 tracking can be inserted for video inputs when a tracking prompt is supplied. Real-ESRGAN remains callable through the unified tools API for images/selected frames; automatic video frame extraction and reassembly are deliberately not claimed yet.

- Video asset: optional Grounding DINO detection, optional DeepFilterNet audio enhancement, optional WhisperX transcription, FFmpeg proxy, scene detection, OTIO timeline, FFmpeg MP4 export.
- Image asset: approved Wan 2.2 TI2V 5B generation and optional RIFE interpolation, followed by OTIO timeline and FFmpeg export. This is a short-shot path; full multi-scene Wan shot assembly is not included in workflow v1.

Generated video output from Wan, RIFE, Real-ESRGAN video output, and DeepFilterNet's cleaned video is registered as a project Asset using a stable job-derived ID. The next workflow step receives that new asset ID. The engine does not rely on in-memory task return state for handoff.

A failure stops the run. Retrying the child job re-opens its workflow step. Cancellation stops the active job and prevents subsequent steps from being queued.

## Arabic brief to MP4

1. Create a draft plan and ordered workflow:

    POST /v1/workflows/from-brief

    {
      "project_id": "PROJECT_UUID",
      "asset_id": "VIDEO_ASSET_UUID",
      "brief": "أنشئ إعلانًا سينمائيًا قصيرًا، اكتشف المشاهد المهمة، ثم صدّر MP4.",
      "language": "ar",
      "target_duration_seconds": 30,
      "transcribe_audio": false,
      "enhance_audio": false,
      "generate_video": false,
  "tracking_prompt": "the main product"
    }

2. Inspect the Qwen plan and ordered steps in the response. The workflow status remains awaiting_approval. No Job or Outbox entry is created before approval.

3. Approve only after review:

    POST /v1/workflows/WORKFLOW_UUID/approve

4. Poll the durable run:

    GET /v1/workflows/WORKFLOW_UUID

5. Inspect any child Job or event stream:

    GET /v1/jobs/JOB_UUID
    GET /v1/jobs/JOB_UUID/events

6. When the workflow reports succeeded, its final render Job result contains the MP4 download URL.

For a previously reviewed plan, use POST /v1/workflows with a creative_plan object rather than calling Qwen again.

## Start Ollama without automatic weight downloads

The repository includes an optional Ollama Compose profile. It does not pull any model weights:

    docker compose --profile ollama up -d ollama

Set OLLAMA_BASE_URL to http://ollama:11434 in the private .env file, then install the selected model explicitly:

    docker compose --profile ollama exec ollama ollama pull qwen3.5:9b-q4_K_M
    docker compose --profile ollama exec ollama ollama list

The selected Qwen 3.5 9B Q4_K_M registry artifact is approximately 6.6 GB (model metadata reports 8.95B parameters). Actual memory requirements exceed the artifact download size and depend on context size, concurrency and GPU offload. This Studio currently requests 8K context for director planning.

For Ollama installed directly on Windows, do not start the Compose Ollama service. Set OLLAMA_BASE_URL to http://host.docker.internal:11434 instead. The API container must be recreated after changing .env.

Set OLLAMA_IMAGE to a tested image tag or digest for deployment; the latest tag in the example is a development convenience, not a production pin.

## NVIDIA GPU on Linux Docker Engine

The optional compose.gpu.yaml grants the Ollama container access to NVIDIA devices. Before using it, install a supported NVIDIA driver and NVIDIA Container Toolkit on the Linux host. Configure Docker and restart it:

    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker

Then start the Ollama service with the GPU override:

    docker compose -f compose.yaml -f compose.gpu.yaml --profile ollama up -d ollama

Windows Docker Desktop has a separate host-driver and WSL2 GPU path. If the Ollama container does not expose the GPU, run Ollama as a Windows host service and use host.docker.internal. The included Windows preflight checks nvidia-smi and the local runtime; it does not change host drivers or Docker settings.

## ComfyUI and specialist weights

ComfyUI remains a separately installed runtime; the Studio repository does not invent a ComfyUI image, custom-node revisions, node IDs or checkpoints. The ComfyUI process must be able to read the same media workspace the Studio writes.

For a Docker-hosted ComfyUI service, attach it to the private Studio network and mount the same MEDIA_HOST_PATH at /srv/video-studio/data. Start ComfyUI with an input directory rooted there. This allows the fixed project-scoped source paths sent by the adapter to resolve inside ComfyUI.

For every supported workflow:

1. Install the exact required custom nodes and checkpoints separately.
2. Export an API-format workflow from ComfyUI.
3. Put its JSON file and registry.json under MEDIA_ROOT/workflows.
4. Replace placeholder node IDs and input names with actual IDs from that export.
5. Keep only the five allowlisted workflow IDs: groundingdino_detect_v1, sam2_track_v1, wan22_ti2v_5b_v1, realesrgan_upscale_v1, rife_interpolate_v1.
6. Set COMFYUI_BASE_URL to an address reachable from both API and worker-models.
7. Check GET /v1/models/status, then run a real low-resolution test job for each configured workflow.

The status endpoint checks reachability, workflow bindings, ComfyUI node classes and static model choices. It explicitly reports inference_verified=false until an actual successful model job has been recorded. No ComfyUI weights or custom nodes are downloaded by Docker build, application startup or CI.

## Run runtime preflight

From PowerShell at the repository root:

    .\infra\scripts\windows\model-runtime-preflight.ps1

After installing the selected Qwen artifact, run a small Arabic inference request:

    .\infra\scripts\windows\model-runtime-preflight.ps1 -RunArabicInferenceSmokeTest

Linux:

    bash infra/scripts/model-runtime-preflight.sh
    RUN_ARABIC_INFERENCE_SMOKE_TEST=true bash infra/scripts/model-runtime-preflight.sh

The scripts check host GPU visibility, Docker, Studio readiness, Ollama model presence and ComfyUI device reporting. They do not install software, download model weights or start GPU jobs.

## Acceptance checklist

- [ ] Qwen exact tag is installed and returns an Arabic response.
- [ ] Ollama shows GPU offload on the intended host when GPU use is expected.
- [ ] ComfyUI /system_stats reports a usable device.
- [ ] Every ComfyUI registry entry points to a real API-exported workflow.
- [ ] Grounding DINO smoke job completes and its result is probed.
- [ ] SAM 2 smoke job completes on a short video.
- [ ] Wan 2.2 TI2V 5B creates a short MP4 from an uploaded reference image.
- [ ] RIFE consumes the generated Asset and creates another valid video Asset.
- [ ] OTIO/FFmpeg render exports an MP4 that downloads and passes ffprobe.

GitHub CI can verify migrations, durable job chaining, mocked generated-Asset handoff, and CPU-based FFmpeg export. It cannot access the user's private Windows GPU, installed ComfyUI custom nodes, private checkpoints or local Ollama weights. Those hardware and real-inference checks must be run on the target host before production acceptance.


### Idempotency

Both workflow creation endpoints require a project-scoped `idempotency_key`. A replay with the same normalized request returns the existing workflow, including the original Qwen plan for `/from-brief`; reuse with a different request returns HTTP 409. This avoids calling Qwen twice when a client retries a request after a network timeout.
