# Phase 6.5 — Audio mixing and synchronization

**Status:** merged into main via [PR #12](https://github.com/aseyam11-png/AI-VIDEO-STUDIO/pull/12). The PR passed Python/Ruff, real FFmpeg audio-render, Windows script and full Compose integration checks before merge. Target-host listening and browser review remain production acceptance tasks.

## Delivered scope

Phase 6.5 adds a project-scoped audio mix manifest and connects it to the existing multi-source video timeline renderer. Audio files stay in the project's private media directory; only their metadata and UUID references are saved in the project timeline folder.

Supported upload formats for audio assets:

- WAV, MP3, M4A, AAC, FLAC, OGG and OPUS.
- Video assets may also be selected as audio sources when FFprobe confirms that they contain an audio stream.
- Upload validation rejects an audio extension when the uploaded media does not contain an audio stream.

## Project API

- GET /v1/projects/{project_id}/audio-mix reads the saved mix, or returns safe default levels when no mix is saved.
- PUT /v1/projects/{project_id}/audio-mix validates and saves the project audio mix.
- POST /v1/projects/{project_id}/timeline/render uses the saved mix when producing the MP4 preview.
- PUT /v1/projects/{project_id}/timeline invalidates the previous mix and preview because clip order, source ranges or duration may have changed. The Studio retains its unsaved in-memory settings and saves them again against the updated timeline.

Audio mix manifest schema version 1 stores:

- source_audio_enabled and source_audio_denoise for the audio embedded in the video clips.
- Master levels for voice, music and sfx, in dB.
- Up to 100 project-scoped audio clips. Each clip stores its role, source in-point, source duration, timeline start, gain, fade-in, fade-out and optional denoise setting.

All source references are validated against assets belonging to the same project. The API verifies the selected source actually contains an audio stream, checks source ranges against FFprobe duration when available, and rejects mix clips extending beyond the saved video timeline. Master and clip gain are limited to -60 dB through +12 dB. Fades must be non-negative and cannot exceed the clip duration.

## Render behavior

The FFmpeg export path:

1. Builds the sequential video cut as before.
2. Keeps or mutes source-video audio according to the saved setting and applies the voice master level.
3. Optionally applies FFmpeg afftdn noise reduction to source audio or individual audio clips.
4. Trims each external audio source, applies its gain, fade-in/fade-out and timeline delay.
5. Mixes the source audio and independent tracks into one synchronized stereo output, then applies a peak limiter before AAC encoding.
6. Preserves the existing video encode settings and MP4 fast-start behavior.

The track roles provide separate master levels for voice, music and sound effects. The per-clip controls allow precise placement without requiring all tracks to begin at zero.

## Arabic Studio UI

The Timeline workspace now includes an audio mixer with:

- Upload for supported audio files.
- Source selection from project audio assets and videos with audio streams.
- Voice, Music and SFX role selection.
- Timeline start, source in-point and duration controls.
- Per-clip gain, fade-in, fade-out and noise-reduction switches.
- Master levels for each role.
- Source-video audio enable/disable and noise-reduction controls.
- Explicit save state and project-switch / page-unload warnings for unsaved audio changes.

The audio mix is applied when the user creates a new MP4 preview. The UI does not claim real-time audio playback or waveform editing.

## Acceptance tests

The new test module covers:

- Audio asset type registration and upload validation.
- Project scoping, source audio-stream validation, source bounds and timeline bounds.
- Saving and reading the mix manifest and invalidating stale previews.
- Invalidating a saved audio mix when the video timeline changes.
- FFmpeg filter construction for voice/music/SFX levels, delayed placement, fades, denoise, mix and limiter.
- Rendering a separate music track when original video audio is disabled.
- A real FFmpeg integration test that generates synthetic video and music sources and verifies the resulting MP4 streams and duration.
- Studio HTML and JavaScript wiring for the audio mixer.

GitHub CI is the acceptance gate for this branch. A passing mocked test does not replace the real FFmpeg integration test. Neither CI nor the current implementation certifies audio quality for every codec, input sample rate, channel layout or noisy recording on the target Windows host.

## Explicit limitations

- Rendering remains a synchronous preview operation; durable background audio/video export is a later workflow increment.
- The current noise reducer is FFmpeg afftdn, not a deep-learning denoiser and not a replacement for the optional DeepFilterNet model.
- There is no waveform visualization, live scrubbing, equalizer, compressor, side-chain ducking, room reverb, pitch/time stretching or multi-bus automation yet.
- Source-video audio and external tracks are mixed into one stereo AAC stream; stems are not exported separately.
- The editor does not generate music or sound effects. Users must supply licensed audio assets.
- Browser acceptance and listening review on the actual Windows host remain required before production acceptance.
