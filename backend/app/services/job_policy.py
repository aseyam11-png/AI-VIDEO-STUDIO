from dataclasses import dataclass


@dataclass(frozen=True)
class JobPolicy:
    """Hard limits enforced by code, never delegated to an LLM."""

    max_generation_retries_per_shot: int = 2
    max_parallel_gpu_jobs: int = 1
    approval_required_for_generation: bool = True
    approval_required_for_export: bool = True
    approval_required_for_delete: bool = True


def validate_generation_request(payload: dict, *, approved: bool) -> None:
    if not approved:
        raise PermissionError("Generation requires explicit user approval.")
    duration = payload.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0 or duration > 10:
        raise ValueError("A single generation shot must be between 0 and 10 seconds in the initial profile.")
    workflow_id = payload.get("workflow_id")
    if workflow_id not in {"wan22_ti2v_5b_v1"}:
        raise ValueError("Unknown or unapproved workflow_id.")
