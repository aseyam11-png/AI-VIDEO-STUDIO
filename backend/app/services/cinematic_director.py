from app.schemas.models import CreativePlan, CreativeScene

_PROMPT_LIMIT = 4000

_OPENING = (
    "Create one continuous, photorealistic live-action-style video shot. "
    "Preserve natural textures, stable geometry, believable motion and coherent exposure."
)

_CLOSING = (
    "Single continuous take. Keep anatomy and object geometry stable; preserve temporal "
    "consistency, natural materials, coherent reflections, physically plausible motion and "
    "consistent light direction. No flicker, frame wobble or morphing. Do not invent text, "
    "subtitles or watermarks; any requested wording must remain unchanged."
)


def compile_scene_generation_prompt(
    plan: CreativePlan,
    scene: CreativeScene,
    *,
    product_identity_lock: bool = False,
) -> tuple[str, str]:
    """Compile a validated scene into bounded, provider-neutral generation instructions."""
    details = [
        f"Story context: {plan.logline}",
        f"Audience: {plan.audience}",
        f"Global visual style: {plan.visual_style}",
        f"Cinematic look: {plan.cinematic_look}" if plan.cinematic_look else "",
        f"Color palette: {', '.join(plan.color_palette)}",
        f"Scene {scene.index} purpose: {scene.purpose}",
        f"Visual description: {scene.visual_description}",
        f"Requested on-screen text: {scene.on_screen_text}" if scene.on_screen_text else "",
        f"Subject action: {scene.subject_action}" if scene.subject_action else "",
        f"Shot type: {scene.shot_type}",
        f"Camera movement: {scene.camera_movement}",
        (
            f"Camera optics: {scene.focal_length_mm}mm lens, "
            f"f/{scene.aperture_f_number:g}, {scene.depth_of_field} depth of field"
        ),
        f"Lighting design: {scene.lighting_design}" if scene.lighting_design else "",
        f"Composition: {scene.composition}" if scene.composition else "",
        f"Motion intensity: {scene.motion_intensity}",
        f"Continuity anchor: {scene.continuity_anchor}" if scene.continuity_anchor else "",
        f"Transition intent: {scene.transition_intent}" if scene.transition_intent else "",
        (
            "Global continuity rules: " + "; ".join(plan.continuity_rules)
            if plan.continuity_rules
            else ""
        ),
        (
            "Realism constraints: " + "; ".join(plan.realism_constraints)
            if plan.realism_constraints
            else ""
        ),
        f"Shot duration: {scene.duration_seconds} seconds",
    ]
    if product_identity_lock:
        # Identity is a hard constraint; put it ahead of user-authored descriptive fields
        # so prompt-budget truncation cannot silently remove the reference lock.
        details.insert(
            0,
            "Canonical product identity lock: preserve its exact silhouette, proportions, "
            "geometry, colors, materials, hardware, logo, typography, labels and packaging "
            "from the supplied reference image. Do not redesign, replace, morph, omit, add "
            "or invent product details. Simplify camera motion rather than changing the "
            "product.",
        )

    detail_text = "\n".join(part.strip() for part in details if part and part.strip())
    fixed_text = f"{_OPENING}\n\n{_CLOSING}"
    detail_budget = max(0, _PROMPT_LIMIT - len(fixed_text) - 2)
    detail_text = detail_text[:detail_budget].rstrip()
    prompt = f"{_OPENING}\n{detail_text}\n{_CLOSING}"

    negative_parts = [
        "low quality",
        "flicker",
        "temporal inconsistency",
        "frame wobble",
        "distorted geometry",
        "warped anatomy",
        "unnatural hands",
        "impossible physics",
        "rubbery motion",
        "inconsistent subject identity",
        "unstable reflections",
        "plastic-looking skin",
        "unintended text",
        "gibberish lettering",
        "subtitles",
        "watermarks",
    ]
    if product_identity_lock:
        negative_parts.extend(
            [
                "changed product identity",
                "altered logo",
                "misspelled label",
                "different product",
                "changed materials",
                "missing components",
                "extra product",
                "product morphing",
            ]
        )
    negative_prompt = ", ".join(negative_parts)
    return prompt, negative_prompt
