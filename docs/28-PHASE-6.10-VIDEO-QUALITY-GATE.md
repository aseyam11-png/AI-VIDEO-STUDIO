# Phase 6.10 — Automatic Video Quality Gate

**Repository:** `aseyam11-png/AI-VIDEO-STUDIO`  
**Branch:** `feat/phase-6.10-video-quality-gate`  
**Status:** Implementation submitted for review; CI and real Windows output acceptance are required before merge.

## Checks

- File exists, is non-empty, and FFprobe can read it.
- Full FFmpeg decode using `-xerror` to expose damaged/truncated media.
- Actual duration compared with the expected timeline duration.
- Actual resolution and average FPS compared with requested render settings.
- Black frames via FFmpeg `blackdetect`, including segment start/end/duration.
- Frozen frames via FFmpeg `freezedetect`.
- Audio silence via FFmpeg `silencedetect` at -55 dBFS for at least one second.
- Audio peak/clipping risk via FFmpeg `volumedetect`.
- SHA-256 fingerprint of the exact file analysed.

## Export gate

Each render writes `<render>.quality.json` next to the MP4. Workflow advancement fails closed when a render result has no passing report. Both `/assets/{asset_id}/rendered` and `/timeline/rendered` reject downloads with HTTP 409 when the report is missing, failed, or its SHA-256 no longer matches the file. Reports are available from `/assets/{asset_id}/quality` and `/timeline/quality`.

Saving a changed timeline, audio mix, or visual-effects manifest invalidates the previous MP4 and its report. A new render removes old outputs before creating its replacement, preventing an earlier passing report from authorising a changed file.

## Threshold policy

- Black: warning from 0.5 seconds or 5% of duration; failure at 3 seconds or 25%.
- Frozen: warning from 0.8 seconds or 5%; failure at 2.5 seconds or 20%.
- Silence: warning from 2 seconds or 25%; failure when at least 95% of a two-second-or-longer video is silent.
- Audio peak: warning at -1 dBFS; failure at or above 0 dBFS.
- Duration tolerance: max(0.5 seconds, two frames).
- FPS tolerance: max(0.25 FPS, 0.5% of requested FPS).

Warnings remain visible and do not alone block export. These are technical heuristics: intentional black transitions, held shots and silence still require human review. The system does not judge composition, brand fidelity, Arabic typography, lip-sync, colour grading, story quality, or whether a detected segment was intentional. Phase 6.9's product-identity review remains separate.

## Verification

Synthetic FFmpeg fixtures cover normal output, black frames, frozen frames, silent audio, wrong dimensions, missing files and corrupt files. CI must confirm report creation for both renderers and fail-closed workflow/download behaviour. Target Windows acceptance still requires real Studio projects, real generated media, FFprobe evidence and human visual/audio review.

No model weights, ComfyUI workflow exports, user media or credentials are committed.
