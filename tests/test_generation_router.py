import pytest
from app.services.generation_router import (
    GenerationRoutingError,
    estimate_seedance25_cost_usd,
    route_generation_shots,
)


def _shots(*durations: int) -> list[dict]:
    return [
        {
            "prompt": f"Shot {index}",
            "negative_prompt": "",
            "duration_seconds": duration,
        }
        for index, duration in enumerate(durations, start=1)
    ]


def test_auto_never_sends_reference_externally_without_explicit_consent():
    routes = route_generation_shots(
        _shots(4, 6),
        strategy="auto",
        allow_external_processing=False,
        runway_available=True,
        max_cost_usd=3.0,
    )

    assert [item["provider"] for item in routes] == ["local_wan", "local_wan"]
    assert all(item["estimated_cost_usd"] == 0 for item in routes)


def test_auto_uses_local_wan_when_runway_is_not_configured():
    routes = route_generation_shots(
        _shots(4, 8),
        strategy="auto",
        allow_external_processing=True,
        runway_available=False,
        max_cost_usd=3.0,
    )

    assert all(item["provider"] == "local_wan" for item in routes)


def test_auto_routes_eligible_shots_until_the_per_workflow_cap_is_reached():
    routes = route_generation_shots(
        _shots(4, 4, 4, 3),
        strategy="auto",
        allow_external_processing=True,
        runway_available=True,
        max_cost_usd=3.0,
    )

    assert [item["provider"] for item in routes] == [
        "seedance2_5",
        "seedance2_5",
        "local_wan",
        "local_wan",
    ]
    assert sum(item["estimated_cost_usd"] for item in routes) == pytest.approx(2.4)
    assert "cap" in routes[2]["routing_reason"]


def test_explicit_seedance_requires_consent_configuration_and_budget():
    with pytest.raises(GenerationRoutingError, match="explicit permission"):
        route_generation_shots(
            _shots(4),
            strategy="seedance2_5",
            allow_external_processing=False,
            runway_available=True,
            max_cost_usd=3.0,
        )

    with pytest.raises(GenerationRoutingError, match="not enabled"):
        route_generation_shots(
            _shots(4),
            strategy="seedance2_5",
            allow_external_processing=True,
            runway_available=False,
            max_cost_usd=3.0,
        )

    with pytest.raises(GenerationRoutingError, match="exceeds"):
        route_generation_shots(
            _shots(10, 4),
            strategy="seedance2_5",
            allow_external_processing=True,
            runway_available=True,
            max_cost_usd=3.0,
        )


def test_external_duration_is_bounded_and_price_estimate_uses_720p_rate():
    assert float(estimate_seedance25_cost_usd(4)) == pytest.approx(1.2)
    assert float(estimate_seedance25_cost_usd(10)) == pytest.approx(3.0)

    with pytest.raises(GenerationRoutingError, match="4 to 10"):
        estimate_seedance25_cost_usd(3)


def test_local_wan_strategy_ignores_external_consent_and_never_spends():
    routes = route_generation_shots(
        _shots(4, 10),
        strategy="local_wan",
        allow_external_processing=True,
        runway_available=True,
        max_cost_usd=3.0,
    )

    assert [item["provider"] for item in routes] == ["local_wan", "local_wan"]
    assert sum(item["estimated_cost_usd"] for item in routes) == 0


def test_sequence_normalization_recomputes_provider_fields_on_replay(monkeypatch):
    from app.core.config import settings
    from app.schemas.jobs import JobType
    from app.services.jobs import normalize_payload

    monkeypatch.setattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")
    monkeypatch.setattr(settings, "runway_enabled", False)
    monkeypatch.setattr(settings, "runway_api_key", None)
    payload = {
        "shots": [
            {"prompt": "Shot one", "negative_prompt": "", "duration_seconds": 4},
            {"prompt": "Shot two", "negative_prompt": "", "duration_seconds": 6},
        ],
        "generation_provider": "auto",
        "allow_external_processing": False,
        "approved": True,
    }

    normalized = normalize_payload(JobType.VIDEO_GENERATE_SEQUENCE, payload)
    replayed = normalize_payload(JobType.VIDEO_GENERATE_SEQUENCE, normalized)

    assert [shot["provider"] for shot in replayed["shots"]] == [
        "local_wan",
        "local_wan",
    ]
    assert all(shot["estimated_cost_usd"] == 0 for shot in replayed["shots"])


def test_explicit_seedance_sequence_fails_closed_without_worker_credentials(monkeypatch):
    from app.core.config import settings
    from app.schemas.jobs import JobType
    from app.services.jobs import JobRequestError, normalize_payload

    monkeypatch.setattr(settings, "runway_enabled", False)
    monkeypatch.setattr(settings, "runway_api_key", None)
    payload = {
        "shots": [
            {"prompt": "Shot one", "negative_prompt": "", "duration_seconds": 4},
            {"prompt": "Shot two", "negative_prompt": "", "duration_seconds": 4},
        ],
        "generation_provider": "seedance2_5",
        "allow_external_processing": True,
        "approved": True,
    }

    with pytest.raises(JobRequestError, match="not enabled"):
        normalize_payload(JobType.VIDEO_GENERATE_SEQUENCE, payload)


def test_explicit_seedance_sequence_is_budgeted_before_it_can_be_queued(monkeypatch):
    from app.core.config import settings
    from app.schemas.jobs import JobType
    from app.services.jobs import JobRequestError, normalize_payload

    monkeypatch.setattr(settings, "runway_enabled", True)
    monkeypatch.setattr(settings, "runway_api_key", "test-key")
    monkeypatch.setattr(settings, "runway_max_usd_per_workflow", 3.0)
    monkeypatch.setattr(settings, "comfyui_base_url", None)
    payload = {
        "shots": [
            {"prompt": "Shot one", "negative_prompt": "", "duration_seconds": 10},
            {"prompt": "Shot two", "negative_prompt": "", "duration_seconds": 4},
        ],
        "generation_provider": "seedance2_5",
        "allow_external_processing": True,
        "approved": True,
    }

    with pytest.raises(JobRequestError, match="exceeds the per-workflow cap"):
        normalize_payload(JobType.VIDEO_GENERATE_SEQUENCE, payload)
