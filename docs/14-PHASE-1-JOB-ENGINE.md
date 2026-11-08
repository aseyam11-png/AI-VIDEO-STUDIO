# Phase 1 — Durable Project and Job Engine

## Scope

Phase 1 adds the durable control plane needed before AI agents and model workers are introduced. It does not add a live LLM, GPU inference, public multi-user access, or unlimited execution.

### Data ownership

- PostgreSQL is the authoritative store for projects, asset metadata, jobs, job events, and dispatch outbox records.
- Media bytes, generated artifacts, JSON timeline files, and model caches remain under the private `MEDIA_ROOT`; binary media is not stored in PostgreSQL.
- Valkey is a Celery broker/transport only. It is not the source of truth for job status or results.
- SQLite is the default for single-process local API development when `DATABASE_URL` is omitted. It is not the recommended deployment database.
- Alembic is the only schema migration mechanism. Application startup never calls `create_all`.

## Job lifecycle

Allowed statuses:

`queued → running → succeeded | failed | cancel_requested → cancelled`

A retry is a new dispatch generation on the same job record and is permitted only for failed/cancelled jobs while the configured attempt budget remains. Successful upstream jobs and their output artifacts remain available, so a pipeline controller can continue from the next stage without re-running earlier successful stages. If a worker disappears during one media operation, recovery reruns that operation from its beginning; FFmpeg/PySceneDetect jobs do not yet support restoring an in-process encoder/detector cursor. This is an idempotent operation retry, not true mid-operation checkpoint resume. Step-level checkpoint restore for long-running model workflows is a later milestone.

Progress is an integer checkpoint, not a fabricated FFmpeg percentage. `progress_stage`, `progress_message`, heartbeat timestamps, and append-only `job_events` explain current activity. A job reaches 100 only after the handler has verified its output.

## Reliability model

1. Job row, creation event, and outbox row are committed in one database transaction.
2. A Celery Beat dispatcher claims pending outbox rows with database row locks and publishes them.
3. If publishing fails, the outbox row is made eligible for a later retry. If the dispatcher crashes after publishing but before marking the row published, the message may be delivered twice.
4. Worker delivery is therefore at-least-once. Job claim checks both current status and dispatch generation under a database row lock; stale/duplicate deliveries are ignored.
5. Worker heartbeats are stored in PostgreSQL. A periodic recovery task requeues jobs whose heartbeat expires, or fails them when their attempt budget is exhausted.
6. Task handlers write outputs to temporary files and atomically promote verified results. Cancellation terminates FFmpeg processes. PySceneDetect cancellation is checked at operation boundaries, not inside the detector's internal frame loop.
7. External effects are not claimed to be exactly-once. Handlers must remain idempotent and verify output before reporting success.
8. A partial unique database index allows only one active job of the same operation type per asset. Asset-row locking serializes concurrent submissions; the database index is the final race-condition guard.

## Initial supported durable job types

- `media.proxy`
- `media.scenes`
- `media.timeline`
- `media.render`

Only validated payload fields are accepted. Arbitrary shell commands, filesystem paths, model URLs, and workflow definitions are not accepted in job payloads. A partial unique database index permits only one active job of a given operation type per asset. All initial jobs route to the CPU queue; no GPU scheduling is enabled in this phase.

## API

- `POST /v1/jobs` — create a durable media job; requires the `idempotency_key` JSON field.
- `GET /v1/jobs?project_id=...` — list jobs with bounded pagination and optional status filter.
- `GET /v1/jobs/{job_id}` — inspect state and progress checkpoints.
- `GET /v1/jobs/{job_id}/events` — read the event history.
- `POST /v1/jobs/{job_id}/cancel` — cancel a queued job immediately or request cooperative cancellation of a running job.
- `POST /v1/jobs/{job_id}/retry` — create a new dispatch generation for an eligible job.

The existing synchronous Phase 0.5 media endpoints remain available for compatibility. New UI integrations should submit durable jobs for long-running work.

## Local development without Docker

1. Install the project: `pip install -e ".[dev,media]"`.
2. Set `MEDIA_ROOT` to a private directory outside the repository.
3. Apply migrations: `python -m alembic upgrade head`.
4. Import any Phase 0.5 manifests once: `python -m app.cli import-legacy-manifests`.
5. Start the API: `uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000`.

Without a configured `DATABASE_URL`, the API uses a SQLite file under `MEDIA_ROOT`. Durable job submission is persisted, but jobs will remain queued until a Celery worker and broker are configured.

## Docker Compose stack

The Compose stack runs PostgreSQL, Valkey, an Alembic migration one-shot, the API, a CPU worker, and Celery Beat. Only the PostgreSQL service receives bootstrap/migrator/runtime credential material; API, worker, and scheduler receive only the restricted runtime URL, and the migration service receives only the migrator URL. PostgreSQL and Valkey do not publish host ports. The API is bound to `127.0.0.1:8000`. PostgreSQL uses a bootstrap superuser only during first-volume initialization, a separate non-superuser migration owner for Alembic, and a restricted runtime role for the API/workers. The runtime role receives DML privileges on tables and sequences, but no schema DDL rights. Generate strong local secrets and store them only in an ignored local `.env`; never commit `.env`. The `database-init/01-roles.sh` initialization script runs only when PostgreSQL initializes an empty volume.

PowerShell setup:

```powershell
Copy-Item .env.example .env
function New-UrlSafeSecret {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}
$bootstrap = New-UrlSafeSecret
$migrator = New-UrlSafeSecret
$runtime = New-UrlSafeSecret
$valkey = New-UrlSafeSecret
$envText = Get-Content .env -Raw
$envText = $envText.Replace('REPLACE_WITH_BOOTSTRAP_SECRET', $bootstrap)
$envText = $envText.Replace('REPLACE_WITH_MIGRATOR_SECRET', $migrator)
$envText = $envText.Replace('REPLACE_WITH_RUNTIME_SECRET', $runtime)
$envText = $envText.Replace('REPLACE_WITH_VALKEY_SECRET', $valkey)
$envText = $envText.Replace('MEDIA_HOST_PATH=./data', 'MEDIA_HOST_PATH=C:/Users/MSI/AI-Video-Studio-Data')
Set-Content -Path .env -Value $envText -NoNewline
docker compose up -d --build
docker compose exec api python -m app.cli import-legacy-manifests
docker compose ps
```

The four passwords are generated independently on your machine and are URL-safe. The runtime role cannot create tables or alter the schema; only the migrator role performs migrations. The example path preserves the Phase 0.5 media directory on the Windows host; change it to your actual private media path. Run the legacy import once after migrations; reruns are idempotent. Do not paste your `.env` contents or secrets into chat/issues. The role bootstrap runs only when Postgres creates a fresh data volume; do not delete an existing volume to rerun it.

Use `/health` for process health and `/ready` for database/migration readiness. Stop with `docker compose down`; add `-v` only when intentionally deleting persistent PostgreSQL/Valkey data. The bind-mounted media directory is not deleted by `down` or `down -v`.

## Backup and recovery

Back up PostgreSQL and the media volume together. The database stores paths and metadata that point to media bytes; restoring only one side can leave broken references. Do not run the legacy import command against an unverified media directory. The importer is idempotent and does not move or delete source files.

## Phase 1 acceptance criteria

- Alembic upgrade creates the schema on PostgreSQL and SQLite test databases.
- Project and asset metadata survives API process restarts because it is stored in the database.
- Duplicate job requests with the same project/idempotency key and identical canonical payload return the original job; reuse with different request data returns HTTP 409.
- A database partial unique index prevents concurrent active jobs of the same operation type on the same asset, including requests with different idempotency keys.
- Job row, creation event, and outbox row are atomic.
- Outbox failures can be retried; duplicate deliveries cannot claim the same generation twice.
- Job status, stage checkpoint, attempts, heartbeat, result/error, and ordered event history are inspectable.
- Queued cancellation, running cancellation request, failed/cancelled retry, exhausted-attempt rejection, active-operation uniqueness, and stale-worker recovery are tested.
- Retry/recovery currently reruns the deterministic media operation from its start; it is not true mid-operation resume.
- End-to-end worker integration creates a synthetic clip, uploads it, executes proxy/scenes/timeline/render jobs, and downloads the output.
- Main remains unchanged until the draft PR is reviewed and merged explicitly.
