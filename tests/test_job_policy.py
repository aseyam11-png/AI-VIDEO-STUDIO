import pytest

from app.services.job_policy import validate_generation_request


def test_generation_requires_approval():
    with pytest.raises(PermissionError):
        validate_generation_request(
            {"duration_seconds": 5, "workflow_id": "wan22_ti2v_5b_v1"}, approved=False
        )


def test_generation_rejects_unregistered_workflow():
    with pytest.raises(ValueError):
        validate_generation_request(
            {"duration_seconds": 5, "workflow_id": "arbitrary_workflow"}, approved=True
        )


def test_generation_rejects_long_single_shot():
    with pytest.raises(ValueError):
        validate_generation_request(
            {"duration_seconds": 30, "workflow_id": "wan22_ti2v_5b_v1"}, approved=True
        )


def test_generation_accepts_approved_short_shot():
    validate_generation_request(
        {"duration_seconds": 5, "workflow_id": "wan22_ti2v_5b_v1"}, approved=True
    )
