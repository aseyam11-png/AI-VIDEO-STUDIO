# Phase 6.9 — Product Identity Lock

**Repository:** `aseyam11-png/AI-VIDEO-STUDIO`  
**Branch:** `feat/phase-6.9-product-identity-lock`  
**Status:** Merged in PR #18 (2026-11-10); real target-host Wan acceptance remains open.

## Goal

Keep one uploaded product image as the canonical visual reference while generating an image-to-video advertisement, then require a human identity decision before timeline construction and export.

The feature reduces avoidable identity drift through reference integrity checks, repeated prompt constraints, a review manifest and an explicit approval gate. It does not claim a generative model can guarantee exact logo, text, geometry or product preservation.

## User path

1. Upload/select a video for normal editing or a PNG/JPG/WEBP image for image-to-video generation.
2. The Wan 2.2 option is available only when its ComfyUI workflow is configured. RIFE is independently gated by its workflow readiness.
3. Enable **قفل هوية المنتج**. The Studio sends the selected image asset's SHA-256 reference to the workflow; the workflow rejects non-image sources or missing fingerprints.
4. After the initial workflow approval, the serialized model worker verifies that the source path matches the selected project-scoped image asset, that its stored fingerprint matches the workflow and that a streaming SHA-256 recomputation still matches the file.
5. The same verified image path is sent to every Wan shot. Per-shot prompts explicitly prioritize the exact product silhouette, proportions, colors, materials, components, logo, labels and packaging; negative prompts discourage redesign, morphing, missing or invented details.
6. The generated shots are registered and assembled. The workflow then enters `awaiting_identity_review`; the next `media.timeline` step and `media.render` step remain planned and have no job IDs.
7. The Arabic Studio displays the original reference image beside the generated video and a five-item checklist:
   - silhouette and proportions;
   - colors and materials;
   - logo and labels;
   - components and packaging;
   - consistency across shots.
8. The server accepts approval only when all five checks are explicitly true. Approval queues the next timeline job using the registered generated video asset. Rejection records the notes, marks the workflow failed and cancels the planned timeline/export steps.

## Integrity and state

- Identity reference is an existing image asset owned by the selected project.
- The approved fingerprint is SHA-256 over the file bytes; identity processing fails before any Wan request if the file or path changed.
- The identity decision, reviewer notes, timestamp, reference/output IDs, fingerprint and checklist are stored with the workflow options.
- A separate `awaiting_identity_review` workflow state is persisted through Alembic revision `0003_product_identity_review`.
- The status constraint migration uses Alembic batch mode so it is testable on SQLite and PostgreSQL.
- A project-scoped asset download endpoint is used to present the reference and generated result within the Studio origin.
- Repeated identical review decisions are idempotent. A different decision after approval/rejection is rejected.

## What this feature does not prove

- Prompt conditioning and repeated image reference are not pixel-level identity enforcement.
- CI uses synthetic video and mocked ComfyUI execution; it does not establish real Wan inference, temporal product fidelity or GPU performance.
- The five checklist items are human review confirmations, not automated logo OCR or computer-vision similarity scores.
- Real workflow JSON/bindings, custom-node revisions, checkpoint hashes/licenses and target-GPU smoke tests remain part of Phase 4 runtime acceptance.
- A human should inspect the full generated sequence, especially logo/label close-ups and transitions between shots, before approval.

## Automated test coverage

- rejects identity-lock payloads without a SHA-256 fingerprint;
- verifies reference integrity before dispatching Wan and confirms tampering prevents any Wan call;
- verifies locked prompts are propagated for every shot and the sequence manifest records the reference fingerprint;
- verifies the workflow pauses before timeline/export;
- verifies incomplete checklist approval returns 422;
- verifies complete approval queues the timeline using the generated output asset;
- verifies rejection cancels planned timeline and render steps;
- verifies the review preview endpoint returns the registered project video.

## Remaining acceptance

- CI must pass on the Phase 6.9 PR head.
- Provision the real Wan workflow and run identity-locked ads on the target GPU.
- Inspect a varied real product-image sample set for logo, text, materials, shape and cross-shot consistency.
- Complete Arabic browser acceptance for image upload, generation, preview, approve/reject and resumed export.
