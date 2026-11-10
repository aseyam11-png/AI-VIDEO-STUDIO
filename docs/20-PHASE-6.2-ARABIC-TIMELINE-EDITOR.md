# Phase 6.2 — Arabic Timeline Editor

## Purpose

Connect the project-scoped multi-source timeline API from Phase 6.1 to the served Arabic RTL Studio at `/studio/`. This increment provides a usable sequential cut editor without claiming a layered nonlinear editor.

## User capabilities

- Select any uploaded video asset belonging to the current project as a source clip.
- Add multiple clips to the current project timeline.
- Set each clip's source in-point and playback duration.
- Move clips earlier/later in the ordered sequence or remove them.
- See clip count and total sequence duration.
- Save the ordered clip list and FPS to the project timeline manifest.
- Render an MP4 preview and play or download the result.
- Load a saved timeline when reopening the project.
- Warn before leaving a project or page with unsaved timeline edits.

## API integration

The browser uses only same-origin project-scoped endpoints:

- `GET /v1/projects/{project_id}/timeline`
- `PUT /v1/projects/{project_id}/timeline`
- `POST /v1/projects/{project_id}/timeline/render`
- `GET /v1/projects/{project_id}/timeline/rendered`

No media files are placed in browser storage or committed to Git. Timeline content remains server-side.

## Validation and boundaries

- Source selection is restricted to video assets returned for the active project.
- The UI checks source in-point and duration against FFprobe duration metadata when available.
- The editor enforces the current API limits of 100 clips and 60 minutes; the backend remains authoritative.
- A change to a clip or FPS marks the timeline dirty. Rendering first saves dirty edits.
- An empty saved timeline is supported, so removing the last clip can be persisted.
- Rendering is synchronous in this increment and uses the 6.1 FFmpeg sequential renderer.
- This is one sequential video lane. It does not implement overlapping tracks, transitions, text, subtitles, keyframes or audio mixing controls.

## Acceptance

- FastAPI serves the new editor markup and static assets.
- UI contract tests assert the editor controls and API integration are present.
- Timeline API tests cover save, read, render, download and clearing the timeline.
- CI runs the Python suite, Ruff and `node --check frontend/app.js`.
- Browser workflow, actual uploaded media and Windows-host MP4 playback/output quality still require target-host acceptance.
