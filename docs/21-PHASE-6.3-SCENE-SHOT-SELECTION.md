# Phase 6.3 — Scene and Shot Selection

## Purpose

Expose the existing PySceneDetect ContentDetector outputs in the Arabic Studio and connect scene selection to the Phase 6.2 multi-source timeline editor.

## Delivered scope

- Run scene detection for the video currently selected as a timeline source.
- Configure ContentDetector threshold (1–100) and minimum scene length (1–1000 frames).
- Persist scene results using the existing project/asset-scoped `scenes.json` record.
- Load previously detected scenes without repeating scene analysis.
- Select individual scenes and append them to the timeline as separate clips in scene order.
- Validate source ranges, the 100-clip limit and 60-minute timeline limit before adding a selection.
- Keep scene detection and scene selection separate from the creative plan / workflow approval path.

## API

- `POST /v1/projects/{project_id}/assets/{asset_id}/scenes` runs scene detection and saves the manifest.
- `GET /v1/projects/{project_id}/assets/{asset_id}/scenes` reads the saved manifest, returning 404 until detection has run for that project asset.

## Boundaries

This is deterministic scene-boundary detection and manual shot selection. It does not yet rank scenes using semantic understanding, transcripts, Grounding DINO or SAM 2; nor does it automatically select a final edit. Those capabilities require their own reviewed increments and model acceptance.

## Acceptance

- Tests verify that detected scene manifests can be loaded and are scoped to the correct project asset.
- UI tests verify scene detection and selection controls are present.
- CI validates Python quality/tests and the Studio JavaScript syntax.
- Detection on varied real source footage and user-reviewed scene selection remain target-host acceptance tasks.
