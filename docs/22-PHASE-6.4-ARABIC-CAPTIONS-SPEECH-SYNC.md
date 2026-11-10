# Phase 6.4 — Arabic Captions and Speech Synchronization

## Purpose

Connect the existing optional WhisperX transcription adapter to the Arabic Studio and generate subtitles for the assembled project timeline.

## User capabilities

- Select the active video source and choose Arabic or English speech.
- Explicitly request WhisperX transcription using the configured `small` profile on CPU.
- Optionally request word alignment; the UI reports whether alignment completed, was unavailable, or was not requested.
- Reload a previously saved transcription for the selected project asset.
- Generate one project-level SRT file from all video clips currently in the timeline.
- Download the SRT file and use it in a downstream editor/player.

## Timeline timing

For each timeline clip, the generator intersects saved source transcription segments with the clip's source in-point and out-point. It removes segments outside the selected source range, clips partially overlapping segment timestamps to the selected range, and shifts the resulting cue times by the clip's accumulated position in the project timeline. Reusing a source video at multiple timeline positions correctly produces captions at each occurrence.

The subtitle file is UTF-8 with a byte-order mark for Arabic compatibility. The output status records languages and the best alignment state reported by the source transcription files.

## API

- `POST /v1/projects/{project_id}/assets/{asset_id}/transcription` runs WhisperX and persists segment results.
- `GET /v1/projects/{project_id}/assets/{asset_id}/transcription` reads a saved transcription.
- `POST /v1/projects/{project_id}/timeline/subtitles` creates the project SRT. By default it permits segment-level timestamps even when optional word alignment is unavailable.
- `GET /v1/projects/{project_id}/timeline/subtitles/manifest` reads subtitle metadata.
- `GET /v1/projects/{project_id}/timeline/subtitles` downloads the SRT file.

## Validation and boundaries

- Every source video used by the saved timeline must have a valid saved transcription before project subtitles can be generated.
- The generator validates the project/asset scope, clip ranges and transcript segment times; it caps output at 20,000 cues.
- When the timeline changes, the previous SRT and its manifest are deleted so a stale subtitle file cannot be mistaken for the new edit.
- The SRT is a separate deliverable. This increment does not burn captions into the MP4 or mux a soft-subtitle track into the MP4.
- Segment-level captions are supported; word-level timing is only reported when WhisperX alignment completes for all source transcripts.

## Acceptance

- Tests cover source-range cropping, timeline offset calculation across multiple source assets, saved transcription reads, missing transcription errors and stale subtitle invalidation after timeline edits.
- Studio contract tests verify transcription and subtitle controls are present.
- CI runs Ruff, the Python suite and Studio JavaScript syntax validation.
- Real WhisperX model installation, Arabic output review, audio-language accuracy and playback in external players remain target-host acceptance tasks.
