# Phase 2 — Model and specialist tool integrations

## Placement

| Role | Model/tool | Runtime |
|---|---|---|
| Executive director | Qwen3.5-9B | Ollama HTTP API |
| Speech transcription/alignment | WhisperX | Optional Python speech extra, CPU by default |
| Text-conditioned visual detection | Grounding DINO | Approved ComfyUI API workflow |
| Segmentation and object tracking | SAM 2 | Approved ComfyUI API workflow |
| Video shot generation | Wan 2.2 TI2V 5B | Approved ComfyUI API workflow |
| Audio denoising | DeepFilterNet | Explicitly provisioned local CLI/script and checkpoint directory |
| Image/frame upscaling | Real-ESRGAN | Approved ComfyUI API workflow |
| Frame interpolation | RIFE | Approved ComfyUI API workflow |
| Motion graphics/compositing | Blender | Background CLI using an admin-provisioned .blend template |
| Timeline and final delivery | OpenTimelineIO + FFmpeg | Existing deterministic media engine |

LangGraph remains the control-flow layer. The model proposes typed plans; the application validates them. No model receives arbitrary shell access or may submit a workflow outside the allowlist.

## Weight and dependency policy

- Application startup, image build, GitHub Actions and CI tests do not download model weights.
- Qwen is used only when the configured model already exists in the configured Ollama runtime.
- WhisperX is optional. A transcription job loads the selected small profile on explicit request; its cache stays under the private MEDIA_ROOT/model-cache/whisperx.
- DeepFilterNet requires an explicit script and an existing local checkpoint directory. The adapter passes that directory to the upstream CLI and never asks it to resolve a model name or download checkpoints.
- SAM 2, Grounding DINO, Wan 2.2, Real-ESRGAN and RIFE run through named ComfyUI workflows. Their checkpoints/custom nodes remain in the separately managed ComfyUI model store.
- Blender is an executable, not a neural model. Project templates are staged under projects/<project_id>/motion_templates/; arbitrary user-supplied script text is never executed.
- No checkpoints, model caches, generated media, .blend files or API credentials belong in Git.

## ComfyUI setup

1. Install ComfyUI and only the custom nodes required by the five approved workflows.
2. Mount the Studio media directory into ComfyUI at the same absolute path used by the API and worker-models. This is required for workflow source_path inputs.
3. Export each workflow using ComfyUI's File → Export (API) command.
4. Copy the exported JSON files and config/model-workflows.example.json to MEDIA_ROOT/workflows/.
5. Rename the example registry to registry.json, replace every REPLACE_NODE_ID and REPLACE_INPUT_NAME with the actual nodes/inputs from the exported workflow, and verify the workflow input/output nodes.
6. Set COMFYUI_BASE_URL to the private ComfyUI endpoint. Use a private network; do not expose ComfyUI's prompt or view endpoints publicly.

The workflow IDs are fixed in application code:
- groundingdino_detect_v1
- sam2_track_v1
- wan22_ti2v_5b_v1
- realesrgan_upscale_v1
- rife_interpolate_v1

The registry controls only which node inputs are configurable. It cannot add a new workflow ID or pass arbitrary command strings.

## Runtime configuration

Set these values in the private .env file:

- OLLAMA_BASE_URL and OLLAMA_MODEL for Qwen.
- COMFYUI_BASE_URL and optionally COMFYUI_API_KEY.
- MODEL_WORKFLOW_REGISTRY if the registry is not at MEDIA_ROOT/workflows/registry.json.
- DEEPFILTER_SCRIPT and DEEPFILTER_MODEL_DIR for DeepFilterNet.
- BLENDER_EXECUTABLE for Blender.

For Docker Desktop, a host-installed Ollama/ComfyUI runtime is commonly reachable via host.docker.internal; confirm the actual network path on the host before enabling jobs. The ComfyUI process must see the media root at the identical absolute path.

## API

- GET /v1/models/status reports configured runtimes, workflow readiness, optional dependency presence and model availability.
- POST /v1/director/plan asks the configured Qwen model for a schema-validated Arabic/English draft production plan. The graph ends at awaiting_approval; it does not generate or render anything.
- Submit model jobs through the existing POST /v1/jobs API using these job types: speech.transcribe, audio.enhance, vision.detect, vision.track, video.generate, video.upscale, video.interpolate, and motion.blender.

The models queue is served by a separate worker-models process with concurrency one. GPU-intensive model inference runs in the external ComfyUI runtime, not inside the API or CPU worker. This serializes Studio-originated ComfyUI requests, but other clients using the same ComfyUI server are outside that queue's control.

ComfyUI exposes a global interrupt endpoint. The Studio deliberately does not call it, because that could interrupt another ComfyUI client. A cancelled Studio job may therefore leave its remote ComfyUI execution running until the workflow finishes.

## Blender templates

Place a reviewed .blend file at:
MEDIA_ROOT/projects/<project_id>/motion_templates/<template_id>.blend

Submit motion.blender with the template ID and approved frame/output settings. The runner calls Blender in background mode with the bundled render script. It does not run arbitrary scripts from job payloads.

## Verification boundaries

CI tests the adapters with synthetic data and mocked model services. It does not install checkpoints or claim inference quality, GPU compatibility, or model licensing approval. A model becomes operational only after its runtime, exact workflow, dependencies, weights, license artifacts and hardware benchmark are verified on the target host.


## Enable only the components you intend to use

### Qwen3.5-9B (one Ollama model)

- Use only the explicit Ollama artifact qwen3.5:9b-q4_K_M. The current Ollama library lists this quantization at about 6.6 GB; do not pull Q8/BF16/MLX variants or larger Qwen sizes for this integration.
- Install it manually in the Ollama model store on the private host. The Studio never invokes ollama pull.
- Set OLLAMA_BASE_URL to the private Ollama endpoint and OLLAMA_MODEL=qwen3.5:9b-q4_K_M.
- For Docker Desktop on Windows, http://host.docker.internal:11434 is a common host endpoint, but test network reachability before enabling it.
- The director endpoint is unavailable when Ollama or the exact tag is missing.

### WhisperX

To enable the optional speech worker:
1. Set SPEECH_WORKER_ENABLED=true.
2. Start the optional worker with the speech Compose profile: docker compose --profile speech up -d --build worker-speech.

The Python dependency is installed only in the dedicated worker-speech image, not the API, deterministic CPU worker or model worker. The small profile is locked as the only ASR checkpoint in this deployment. Its model cache is created under MEDIA_ROOT/model-cache/whisperx only when an explicit speech.transcribe job is run. This first transcription may download that one selected speech model. Word-level alignment is off by default; enabling align=true may download the Arabic wav2vec alignment model into the same private cache. No speech checkpoint is included in any image or CI.

### DeepFilterNet

- Install the upstream DeepFilterNet CLI/script separately and provision only the checkpoint you intend to use.
- Set DEEPFILTER_SCRIPT and DEEPFILTER_MODEL_DIR to paths visible inside both API and CPU-worker containers; prefer paths under the private MEDIA_ROOT mount.
- The adapter does not pass a model name to DeepFilterNet and does not trigger its default pretrained-model download.
- Submit audio.enhance only after both paths exist.

### ComfyUI workflows

The Studio does not ship ComfyUI, custom nodes, or model checkpoints inside its API image. Deploy one private ComfyUI runtime and mount the same host media directory inside it at the exact path /srv/video-studio/data. Configure ComfyUI with --input-directory=/srv/video-studio/data so its standard LoadImage/video-loader nodes can select project assets. The Studio sends a project-scoped relative path under this input root, not an arbitrary absolute host path. A Windows host-side ComfyUI process that cannot see this container path is not a supported arrangement.

Install only the custom nodes and checkpoint files required by the five allowlisted API workflows. For Wan, use the ComfyUI Wan2.2 TI2V 5B workflow only; the initial integration does not require the 14B T2V/I2V variants. The TI2V workflow can handle text/image-to-video, but the first Studio API requires an uploaded image reference asset so it can pass a project-scoped source path.

Export each workflow using ComfyUI's API export (not its UI-only workflow JSON). The example registry has placeholder node IDs by design. The status endpoint validates the exported workflow file, configured node/input bindings, installed ComfyUI node types and listed static model choices. Dynamic project filenames/prompts are validated when a job is submitted. This is a configuration preflight, not proof of inference quality; an actual smoke job on the target GPU remains required. Replace placeholders using the actual exported workflow; do not guess node identifiers.

- Set COMFYUI_BASE_URL to the private endpoint (for example, a private Docker-network URL).
- The ComfyUI service should not publish its prompt/history/view endpoints to the public internet.
- worker-models consumes the models queue with concurrency one. This serializes Studio-originated heavy jobs; it cannot serialize other users of the same ComfyUI server.
- A cancellation request waits for the remote ComfyUI prompt to finish before the Studio releases its single model worker. The global ComfyUI interrupt endpoint is intentionally not used because it can interrupt unrelated prompts.

### Blender

- Blender is software, not a model checkpoint.
- Set INSTALL_BLENDER=1 to install the Debian Blender package in the dedicated worker-models image, then set BLENDER_EXECUTABLE=/usr/bin/blender.
- Set INSTALL_BLENDER=0 when motion graphics are not needed, keeping the image smaller.
- Place only reviewed .blend templates under each project's motion_templates directory. The worker executes the bundled render script, never arbitrary script text from a job.

### Real-ESRGAN and RIFE

These run only through the dedicated ComfyUI workflows. Do not install duplicate Python copies of these models in the Studio or CPU-worker image. Provision only the checkpoint files and custom nodes referenced by the exact API workflow you export.

## First-release weight inventory

The Studio repository and CI images contain no model weights. At runtime, the intended minimal inventory is:
- One Ollama tag: qwen3.5:9b-q4_K_M.
- One WhisperX ASR profile: small, cached only after an explicit transcription request. The Arabic word-alignment checkpoint is optional and is downloaded only when alignment is explicitly enabled.
- The exact required checkpoint files referenced by the five selected ComfyUI workflows; no other Wan, SAM2, Grounding DINO, Real-ESRGAN or RIFE variants.
- One explicit DeepFilterNet checkpoint directory only when audio enhancement is enabled.
- Blender templates are project assets, not neural weights.

The exact ComfyUI files are not pinned by this repository because they depend on the exported workflow node graph and the chosen compatible custom-node revisions. Do not mark a model ready until the workflow, checkpoint revision, license record and target-GPU benchmark have been recorded.


## Unified AI Tool Gateway

The integration branch exposes one discovery and dispatch contract:

- `GET /v1/tools` returns the allowlisted tool catalog, per-tool payload schemas, execution queue and runtime-readiness notes.
- `POST /v1/tools/execute` accepts a discriminated `tool_id`. `director.plan` returns a Qwen draft plan. All other registered operations are submitted as durable jobs through the existing Phase 1 job service.
- Job status and event history remain available through `GET /v1/jobs/{job_id}` and `GET /v1/jobs/{job_id}/events`.
- The gateway does not grant models shell access, arbitrary workflow IDs, arbitrary Blender scripts or unscoped filesystem access.
- Idempotency, payload validation, queue isolation, retry and cancellation continue to be enforced by the existing job engine.

This is the first unified invocation layer, not a claim that a multi-stage edit graph is already executing end-to-end. The current job model represents one operation per durable job; it does not yet persist parent/child dependencies or register one job's output as the next job's input asset. A later workflow-orchestration stage must add durable step dependencies and artifact lineage before advertising automatic model-to-model chaining. CI verifies the gateway contract and mocked adapters; real inference still requires the private runtimes, exported ComfyUI workflows, exact model weights and target-host smoke tests.
