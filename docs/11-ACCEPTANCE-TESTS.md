# Acceptance Test Plan

No release is production-ready until these tests pass on the selected GPU host.

## A. Media ingest
- Accept common MP4/MOV inputs; reject invalid or oversized files.
- Record source SHA-256, streams, time base, frame rate, rotation and duration.
- Create proxies without modifying original files.
- Test VFR sources and rotated phone footage; verify timestamp mapping and audio sync.

## B. Arabic audio
- Test Modern Standard Arabic, Palestinian dialect and mixed Arabic/English.
- Measure word/character error on a reviewed reference set.
- Verify timestamps around pauses, speaker changes and overlapping speech.
- Compare raw and denoised transcription; never overwrite the source audio.

## C. Scene and visual understanding
- Test hard cuts, dissolves, flash frames, rapid motion and static shots.
- Validate object detections on multiple frames before accepting a semantic label.
- Test SAM 2 tracking through short occlusions and reinitialization.
- Preserve evidence: frame timestamp, detector label, confidence and source asset ID.

## D. Timeline and render
- Generate an OpenTimelineIO timeline from source assets and transcript.
- Verify source in/out points, frame-accurate placement, transitions and caption safe areas.
- Compare preview and final render against the same canonical timeline.
- Verify no accidental gaps, black frames, clipped audio or sync drift.

## E. AI generation
- Benchmark one fixed 5-second 720p Wan 2.2 TI2V 5B workflow.
- Record exact commit, workflow JSON hash, weight revision, seed, parameters, peak VRAM, host RAM and elapsed time.
- Run three consecutive generations and one forced worker restart.
- Reject if OOM, repeated corruption or budget limits are hit.
- Do not assume the generated product exactly matches the source reference.

## F. Product Identity Lock
- Use a product reference pack with multiple views and critical detail regions.
- Check logo, dial/label, shape, color and accessory consistency.
- Flag any critical mismatch for compositing, another method or user review.
- Do not auto-approve a product shot solely from a language model's opinion.

## G. Reliability, security and cost
- Kill a worker during a job; resume from last validated checkpoint.
- Retry the same job ID; verify idempotency and no duplicate output.
- Fill a test volume to the configured threshold; ensure preflight blocks new jobs.
- Attempt an unapproved generation, external upload and delete; all must be blocked.
- Verify no model/database/broker ports are public.
- Verify per-project GPU seconds and estimated cost are recorded.

## Release acceptance videos
1. One-minute Arabic talking-head edit.
2. One-minute product commercial with product references and generated lifestyle shot.
3. Five-minute mixed-source edit with audio, captions, motion graphics and final QA.

Record wall-clock time, GPU-seconds, retries, peak VRAM/RAM, storage used, quality findings and final human acceptance for each run.
