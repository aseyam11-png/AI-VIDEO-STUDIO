# Phase 6.7 — Multi-Shot Generation and Deterministic Assembly

**Status:** implementation on stacked feature branch; not merged or target-GPU accepted  
**Branch:** feat/phase-6.7-multishot-assembly  
**Prerequisite:** Phase 6.6 visual-effects branch

## Implemented design

- A new durable job type, video.generate_sequence, runs on the serialized models queue.
- The workflow plan maps 2–8 approved creative scenes into separate Wan 2.2 TI2V 5B shot requests.
- Each shot is limited to 10 seconds; the complete sequence is limited to 80 seconds.
- The same project-owned image reference is supplied to every shot. Prompts are built from the approved scene purpose, visual description, shot type, camera movement, on-screen direction and project style.
- Every generated shot must return a decodable video stream and have sufficient duration before it is registered as a project-scoped video asset.
- Optional RIFE interpolation is performed per shot before assembly.
- FFmpeg assembles the registered shots in approved scene order into a single MP4 at the project's target resolution and FPS.
- The final MP4 is registered as a generated project asset and returned as output_asset_id. The durable workflow engine therefore passes that asset to its existing timeline and render steps.
- A sequence.json manifest records the shot order, source reference, shot asset IDs, durations, interpolation factor and final output asset.

## Limits

- The first version supports image-to-video only; source-video editing remains a separate path.
- Each shot uses the same image reference. The previous shot is not used as a conditioning frame for the next shot.
- Shot boundaries are hard cuts in this increment; Phase 6.6 transitions can be applied afterward in the visual-effects editor.
- Audio tracks are not synthesized by this job. The project audio mixer can add supplied voice/music/SFX after assembly.
- A successful synthetic FFmpeg test proves deterministic assembly and project asset handling only. It does not prove that Wan 2.2 or RIFE inference works on the target GPU.
- Model workflow JSON, bindings, custom nodes, weights, memory budget and real output quality must be verified on the target host.
- The sequence renderer does not guarantee product identity across AI-generated shots. Product reference lock and human review are in Phase 6.9.

## API and job contract

The job is submitted through the existing durable jobs API with job_type video.generate_sequence and a project image asset_id. The payload contains approved=true, 2–8 shot prompts, each shot duration, generation size, final output dimensions, FPS, CRF and interpolate_factor (1 or 2). Unknown fields are rejected.

Job output includes output_asset_id and output_asset metadata for the final assembled MP4, shot asset IDs and a project-relative sequence manifest path. The workflow engine uses output_asset_id to route the following media.timeline step to the new video rather than the original image.

## Verification requirements

- Payload tests: approval gate, 2–8 count and per-shot duration limits.
- Workflow planning test: multi-scene CreativePlan maps to one sequence job followed by timeline and render jobs.
- Real FFmpeg integration test with two synthetic generated clips: both clip assets are registered and one ordered MP4 is rendered.
- API/worker CI must pass Ruff, all Python tests, JavaScript checks, Windows scripts and Compose integration.
- Target-host acceptance must execute Wan 2.2 on the actual GPU, optionally run RIFE, inspect every shot, check total duration/resolution/FPS, review product identity and listen to the final audio after mixing.

Passing CI is necessary but not model-inference or production acceptance.