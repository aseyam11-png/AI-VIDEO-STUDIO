from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

GenerationStrategy = Literal["auto", "local_wan", "seedance2_5"]
LOCAL_PROVIDER = "local_wan"
REMOTE_PROVIDER = "seedance2_5"
SEEDANCE_25_720P_USD_PER_SECOND = Decimal("0.30")
SEEDANCE_25_MINIMUM_USD = Decimal("0.80")
SEEDANCE_25_MIN_DURATION_SECONDS = 4
SEEDANCE_25_MAX_DURATION_SECONDS = 10


class GenerationRoutingError(ValueError):
    pass


def estimate_seedance25_cost_usd(duration_seconds: int) -> Decimal:
    if not SEEDANCE_25_MIN_DURATION_SECONDS <= duration_seconds <= SEEDANCE_25_MAX_DURATION_SECONDS:
        raise GenerationRoutingError(
            "Seedance 2.5 routing in this Studio supports shots from 4 to 10 seconds."
        )
    estimated = max(
        SEEDANCE_25_MINIMUM_USD,
        Decimal(duration_seconds) * SEEDANCE_25_720P_USD_PER_SECOND,
    )
    return estimated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def route_generation_shots(
    shots: list[dict],
    *,
    strategy: GenerationStrategy,
    allow_external_processing: bool,
    runway_available: bool,
    max_cost_usd: float,
) -> list[dict]:
    """Build a deterministic per-shot provider plan before a worker can submit jobs."""
    if strategy not in {"auto", "local_wan", "seedance2_5"}:
        raise GenerationRoutingError("Unknown video generation provider strategy.")
    if not shots:
        raise GenerationRoutingError("At least one generation shot is required.")

    budget = Decimal(str(max_cost_usd)).quantize(Decimal("0.01"))
    if budget < SEEDANCE_25_MINIMUM_USD:
        raise GenerationRoutingError(
            "The Seedance per-workflow budget is below its minimum charge."
        )

    if strategy == "seedance2_5":
        if not allow_external_processing:
            raise GenerationRoutingError(
                "Seedance 2.5 requires explicit permission to send the reference image "
                "to Runway Dev."
            )
        if not runway_available:
            raise GenerationRoutingError(
                "Runway is not enabled or RUNWAY_API_KEY is missing on the API/worker services."
            )
        total = Decimal("0.00")
        routes = []
        for shot in shots:
            cost = estimate_seedance25_cost_usd(int(shot["duration_seconds"]))
            total += cost
            routes.append({
                "provider": REMOTE_PROVIDER,
                "estimated_cost_usd": float(cost),
                "routing_reason": (
                    "Explicit Seedance 2.5 selection with external-processing consent."
                ),
            })
        if total > budget:
            raise GenerationRoutingError(
                f"Estimated Seedance cost {total:.2f} USD exceeds the per-workflow cap "
                f"of {budget:.2f} USD."
            )
        return routes

    routes = []
    remaining = budget
    for shot in shots:
        if strategy == "local_wan":
            routes.append({
                "provider": LOCAL_PROVIDER,
                "estimated_cost_usd": 0.0,
                "routing_reason": "Local Wan was explicitly selected.",
            })
            continue
        if not allow_external_processing:
            routes.append({
                "provider": LOCAL_PROVIDER,
                "estimated_cost_usd": 0.0,
                "routing_reason": "External processing was not authorized; using local Wan.",
            })
            continue
        if not runway_available:
            routes.append({
                "provider": LOCAL_PROVIDER,
                "estimated_cost_usd": 0.0,
                "routing_reason": "Runway is not configured; using local Wan.",
            })
            continue

        duration = int(shot["duration_seconds"])
        if not SEEDANCE_25_MIN_DURATION_SECONDS <= duration <= SEEDANCE_25_MAX_DURATION_SECONDS:
            routes.append({
                "provider": LOCAL_PROVIDER,
                "estimated_cost_usd": 0.0,
                "routing_reason": (
                    "Shot duration is outside the 4–10 second external-routing range."
                ),
            })
            continue

        cost = estimate_seedance25_cost_usd(duration)
        if cost > remaining:
            routes.append({
                "provider": LOCAL_PROVIDER,
                "estimated_cost_usd": 0.0,
                "routing_reason": "The per-workflow external cost cap has been reached.",
            })
            continue
        remaining -= cost
        routes.append({
            "provider": REMOTE_PROVIDER,
            "estimated_cost_usd": float(cost),
            "routing_reason": (
                "Auto selected Seedance 2.5 within the authorized per-workflow cost cap."
            ),
        })
    return routes
