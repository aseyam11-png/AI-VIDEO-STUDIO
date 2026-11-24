# Phase 8.1 — Multi-provider video generation routing

## Scope

This phase adds an optional per-shot provider route for approved multi-shot image-to-video workflows. Wan 2.2 remains the default local path. Seedance 2.5 through Runway Dev is optional and disabled unless configured server-side.

## Consent and privacy

- The Workflow API accepts `generation_provider`: `auto`, `local_wan` or `seedance2_5`.
- External processing requires a separate explicit `allow_external_processing=true` consent. The UI checkbox is off by default.
- `auto` without consent always selects local Wan. With consent and Runway configured, eligible 4–10 second shots may route to Seedance 2.5.
- An explicit Seedance selection fails closed if consent, server configuration or cost limits are absent.
- Single-shot workflows remain local Wan in this increment.
- The Runway API key is injected only into the `worker-models` container. The API receives only the enablement flag and cost cap; the browser receives only the routing choice and consent boolean.
- Reference images are uploaded with Runway's ephemeral upload flow. The Studio downloads completed outputs into the project's private media directory and never exposes the temporary signed output URL.
- Runway uploads and output downloads accept only Runway-published S3/Runway-owned hosts and the documented CloudFront distribution hosts; arbitrary HTTPS hosts and arbitrary CloudFront distributions are rejected.

## Cost guard

The initial remote route is Seedance 2.5 at 720p. The planner uses the current published 0.30 USD/second price and 0.80 USD minimum per generation as an estimate. A 3.00 USD hard cap applies to one workflow. Auto routing sends shots to remote only when the estimated cost still fits; the remaining shots use local Wan.

This is a **per-workflow cap**, not a monthly spending cap or cumulative billing ledger. Actual provider invoices remain authoritative. This phase does not claim a monthly budget guarantee.

## Idempotency and recovery

- Before the paid generation POST, the worker atomically writes a task record with a SHA-256 fingerprint of the source image, prompt, negative prompt, duration and model.
- A cross-process per-shot lock serializes task-record inspection, submission, polling and output download across workers sharing the project media volume. A process crash releases the OS lock automatically.
- After a successful POST, the provider task ID is persisted before polling.
- If a transport failure leaves the POST outcome ambiguous, the task is marked `submission_unknown`; retries do not submit again automatically. The operator must inspect the Runway dashboard.
- If a worker is recovered after a task ID is known, retry polls the same task and downloads its output rather than generating again.
- Auto fallback to local Wan is permitted only for an upload failure before the billable generation task is submitted. An ambiguous task submission never falls back.

## Compatibility

- Existing requests default to local Wan and no external consent.
- No database migration is required; workflow/job payload options are JSON.
- Local Wan and RIFE continue to use the current ComfyUI workflow registry and local worker.
- A local ComfyUI configuration is required whenever any shot is routed locally or RIFE interpolation is enabled.

## Acceptance limits

- CI uses mocked provider requests. It does not create Runway tasks, upload project media externally or prove visual output quality.
- No real Runway key is included or configured by this PR.
- Provider quality parity with Higgsfield is not established. It requires matched prompts, model/version pinning, blind visual review and separate scoring for realism, temporal consistency, physics, identity and audio.
- 8.1 does not add a cumulative monthly cost ledger or automatic cost reconciliation against invoices.
