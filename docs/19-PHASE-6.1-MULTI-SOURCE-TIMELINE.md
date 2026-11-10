# Phase 6.1 — Multi-source sequential timeline

## Delivered scope

The first Phase 6 increment adds a project-level timeline that can assemble multiple uploaded video assets into one ordered sequence.

- `PUT /v1/projects/{project_id}/timeline` creates or replaces the current project timeline.
- `GET /v1/projects/{project_id}/timeline` reads the saved project timeline.
- `POST /v1/projects/{project_id}/timeline/render` renders an MP4 preview.
- `GET /v1/projects/{project_id}/timeline/rendered` downloads that preview.

Each clip references a video asset UUID belonging to the same project and contains a source start time and duration. The order of clips in the submitted array is the playback order. Replacing the ordered clip list supports the first editing operations: trimming source ranges, removing clips, and reordering clips. The API rejects non-video assets, missing/cross-project assets, invalid time ranges, source-range overruns, more than 100 clips, and timelines longer than one hour. An empty clip list may also be saved to clear all clips from the project timeline.

The renderer normalizes video size, frame rate, pixel format and audio format before concatenation. If only some source clips contain audio, it inserts silence for the remaining clips so the output stays synchronized. An OpenTimelineIO project file and JSON manifest are stored under the private project media directory; media bytes and generated exports are not committed to Git.

## Explicit limitations

This is a sequential, single-video-lane cut editor foundation. It does not yet provide overlapping tracks, transitions, crossfades, titles, subtitles, overlays, keyframes, audio mixing controls, or non-destructive collaborative editing. Rendering is currently a synchronous preview endpoint; long-running production exports should be moved behind the durable job/workflow engine before broad use. The existing per-asset Phase 0.5 timeline endpoints remain available and unchanged.

## Acceptance tests

The test suite covers:

- OpenTimelineIO serialization of two project-scoped source assets.
- Source-range validation.
- FFmpeg command construction for multiple inputs, including silent clips in a timeline that otherwise has audio.
- Project timeline save/read/render/download API flow using synthetic media fixtures.

These tests exercise application behavior and mocked FFmpeg execution. They do not certify actual rendering quality on the target Windows host.
