# Phase 6.6 — Transitions, Titles and Motion Graphics

**Status:** merged into `main` in [PR #13](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/13); target-host browser and visual acceptance remain open  
**Implementation branch:** `feat/phase-6.6-transitions-titles-motion`  
**Base:** Phase 6.5 audio-mixing merge on main

## Scope implemented in this increment

- Project-scoped versioned visual-effects manifest, separate from the video timeline and audio-mix manifest.
- Boundary transitions that preserve the timeline's total duration and existing audio timing: dip to black and white flash.
- Arabic-capable titles rendered by FFmpeg drawtext using UTF-8 text files: top, center or bottom placement; font size and text color; optional translucent dark box; none, fade-in/fade-out or slide-up entrance.
- Image/logo overlays from images already uploaded to the same project: PNG, JPG/JPEG and WEBP assets; position, size ratio, opacity, timeline start/duration and basic slide-up motion.
- Arabic Studio controls for creating, listing, removing, saving and exporting transitions, titles and overlays.
- Timeline edits invalidate the previous visual-effects manifest and preview. The UI retains current in-memory changes as dirty until effects are saved against the newly saved timeline.
- DejaVu Sans is installed in the container and CI image so Arabic-title rendering has a known font file.

## API

GET /v1/projects/{project_id}/visual-effects

Returns the saved project effects or an empty schema-v1 manifest for a saved timeline.

PUT /v1/projects/{project_id}/visual-effects

Saves the complete manifest: up to 99 transitions, 20 titles and 20 image overlays.

The API checks clip boundaries, duration overlap, title/overlay timing, enum values, identifier uniqueness and project-scoped image assets. Image references are resolved through the project's asset registry; caller-supplied filesystem paths are never accepted.

## Render behavior

The timeline remains a sequential, single-video-lane edit. The two supported transition styles are implemented as paired outgoing/incoming fades at a cut, rather than overlapping clips; this preserves the existing timeline duration and audio synchronization contract.

Titles are composited with FFmpeg drawtext and use a text file rather than embedding user text in a filter expression. Image/logo assets are looped as still inputs and composited at their configured position and visibility interval. Output remains H.264 video with the existing synchronized AAC audio mix when audio is present.

## Limits — not claimed as complete

- No arbitrary keyframe editor, bezier curves, 3D camera rig, particle system, complex motion-graphics compositor or per-frame property panel.
- No real-time preview/scrubbing; visual effects are applied during MP4 rendering.
- No automatic Blender or ComfyUI template generation in this increment. Existing integrations remain allowlisted/template-based and need real target-host runtime setup before acceptance.
- No multi-layer video tracks or clip overlap. Transition effects use cut-edge fades.
- Font fallback is system-installed DejaVu Sans/Arial/Segoe UI; typography and Arabic shaping still need visual review with real text samples on the target host.
- Human browser review and rendered-sample review remain required.

## Verification plan

The branch adds API tests for project-scoped persistence, valid transition boundaries, title timing, rejection of foreign/non-image assets, and invalidation after timeline edits. A real FFmpeg integration test creates a short synthetic video and logo image, then renders a transition, Arabic title and animated image overlay and probes the resulting MP4.

Acceptance requires Python tests and Ruff clean, JavaScript syntax check clean, Windows script checks clean, Compose integration clean, real FFmpeg visual-effects integration passing in CI, and browser preview/human visual inspection of Arabic title rendering on the target host.

Passing CI is necessary, not sufficient, for a production-ready motion-graphics claim.