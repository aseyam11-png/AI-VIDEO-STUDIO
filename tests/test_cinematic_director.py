from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.schemas.models import CreativePlan
from app.schemas.workflows import WorkflowCreateRequest
from app.services import workflow_engine
from app.services.cinematic_director import compile_scene_generation_prompt
from pydantic import ValidationError


def _plan(*, scene_count: int = 1, duration: int = 4) -> CreativePlan:
    scenes = []
    remaining = duration
    for index in range(1, scene_count + 1):
        scene_duration = remaining if index == scene_count else duration // scene_count
        remaining -= scene_duration
        scenes.append(
            {
                "index": index,
                "duration_seconds": scene_duration,
                "purpose": f"Scene {index} purpose",
                "visual_description": f"Detailed scene {index} description",
                "on_screen_text": "أصالة التفاصيل",
                "shot_type": "medium close-up",
                "camera_movement": "slow controlled dolly-in",
                "subject_action": "the subject moves naturally and deliberately",
                "focal_length_mm": 85,
                "aperture_f_number": 2.8,
                "depth_of_field": "shallow",
                "lighting_design": "soft key light from camera left with a subtle rim light",
                "composition": "subject placed on the right third with clean negative space",
                "motion_intensity": "subtle",
                "continuity_anchor": "same wardrobe, hairstyle and background direction",
                "transition_intent": "match cut on the subject's hand movement",
            }
        )
    return CreativePlan.model_validate(
        {
            "title": "Cinematic benchmark",
            "logline": "A premium, realistic product story with a clear visual progression.",
            "target_duration_seconds": duration,
            "audience": "Viewers of premium commercial films",
            "visual_style": "Naturalistic high-end commercial cinematography",
            "color_palette": ["charcoal", "warm amber", "neutral skin tones"],
            "audio_direction": "Restrained sound design with realistic room tone",
            "cinematic_look": (
                "soft highlight roll-off, controlled contrast and natural skin texture"
            ),
            "continuity_rules": [
                "Keep the same product silhouette, color and material in every shot.",
                "Maintain the same key-light direction and environment geometry.",
            ],
            "realism_constraints": [
                "Use physically plausible object movement and contact.",
                "Avoid waxy skin, warped reflections and artificial edge halos.",
            ],
            "scenes": scenes,
        }
    )


def _request(plan: CreativePlan) -> WorkflowCreateRequest:
    return WorkflowCreateRequest(
        project_id=uuid4(),
        asset_id=uuid4(),
        idempotency_key="cinematic-director-test-0001",
        brief="Create a realistic cinematic sequence with coherent camera language.",
        language="en",
        creative_plan=plan,
        generate_video=True,
    )


def test_legacy_plan_payload_defaults_to_compatible_cinematic_values():
    legacy = _plan().model_dump()
    legacy.pop("cinematic_look")
    legacy.pop("continuity_rules")
    legacy.pop("realism_constraints")
    for field in (
        "focal_length_mm",
        "aperture_f_number",
        "depth_of_field",
        "lighting_design",
        "composition",
        "subject_action",
        "motion_intensity",
        "continuity_anchor",
        "transition_intent",
    ):
        legacy["scenes"][0].pop(field)

    parsed = CreativePlan.model_validate(legacy)
    assert parsed.cinematic_look == ""
    assert parsed.continuity_rules == []
    assert parsed.realism_constraints == []
    assert parsed.scenes[0].focal_length_mm == 50
    assert parsed.scenes[0].aperture_f_number == 4.0
    assert parsed.scenes[0].depth_of_field == "moderate"


def test_cinematic_schema_rejects_implausible_camera_values_and_unknown_modes():
    payload = _plan().model_dump()
    payload["scenes"][0]["focal_length_mm"] = 500
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(payload)

    payload = _plan().model_dump()
    payload["scenes"][0]["motion_intensity"] = "teleporting"
    with pytest.raises(ValidationError):
        CreativePlan.model_validate(payload)


def test_compiler_includes_optics_lighting_composition_and_continuity():
    plan = _plan()
    prompt, negative = compile_scene_generation_prompt(plan, plan.scenes[0])

    assert "85mm lens" in prompt
    assert "f/2.8" in prompt
    assert "Lighting design:" in prompt
    assert "Composition:" in prompt
    assert "Global continuity rules:" in prompt
    assert "Requested on-screen text: أصالة التفاصيل" in prompt
    assert "Realism constraints:" in prompt
    assert "physically plausible motion" in prompt
    assert "flicker" in negative
    assert len(prompt) <= 4000


def test_product_identity_lock_is_applied_to_positive_and_negative_prompts():
    plan = _plan()
    prompt, negative = compile_scene_generation_prompt(
        plan,
        plan.scenes[0],
        product_identity_lock=True,
    )

    assert "Canonical product identity lock:" in prompt
    assert "preserve its exact silhouette" in prompt
    assert "altered logo" in negative
    assert "product morphing" in negative


def test_prompt_budget_is_enforced_for_long_but_valid_directions():
    payload = _plan().model_dump()
    payload["cinematic_look"] = "cinematic " * 45
    payload["continuity_rules"] = ["maintain continuity " * 14 for _ in range(8)]
    payload["realism_constraints"] = ["preserve realism " * 14 for _ in range(8)]
    payload["scenes"][0]["visual_description"] = "highly detailed " * 60
    payload["scenes"][0]["lighting_design"] = "lighting direction " * 20
    payload["scenes"][0]["composition"] = "careful composition " * 18
    long_plan = CreativePlan.model_validate(payload)
    prompt, negative = compile_scene_generation_prompt(
        long_plan,
        long_plan.scenes[0],
        product_identity_lock=True,
    )
    assert len(prompt) <= 4000
    assert prompt.endswith("unchanged.")
    assert "Canonical product identity lock:" in prompt
    assert "preserve its exact silhouette" in prompt
    assert "altered logo" in negative


def test_single_and_multi_shot_workflows_use_the_same_compiler(monkeypatch):
    calls = []

    def fake_compiler(plan, scene, *, product_identity_lock=False):
        calls.append((scene.index, product_identity_lock))
        return f"compiled-shot-{scene.index}", "shared-negative-prompt"

    monkeypatch.setattr(
        workflow_engine,
        "compile_scene_generation_prompt",
        fake_compiler,
    )
    project = SimpleNamespace(target_fps=24, target_resolution="640x360")

    single_request = _request(_plan())
    single_steps = workflow_engine._step_specs("image", single_request, project)
    single_payload = single_steps[0][1]
    assert single_payload["prompt"] == "compiled-shot-1"
    assert single_payload["negative_prompt"] == "shared-negative-prompt"

    multi_request = _request(_plan(scene_count=2))
    multi_steps = workflow_engine._step_specs("image", multi_request, project)
    multi_shots = multi_steps[0][1]["shots"]
    assert [shot["prompt"] for shot in multi_shots] == [
        "compiled-shot-1",
        "compiled-shot-2",
    ]
    assert all(
        shot["negative_prompt"] == "shared-negative-prompt"
        for shot in multi_shots
    )
    assert calls == [(1, False), (1, False), (2, False)]
