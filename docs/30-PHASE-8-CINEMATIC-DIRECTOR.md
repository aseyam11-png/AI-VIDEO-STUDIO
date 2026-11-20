# Phase 8.0 — Cinematic Director Contract

## Purpose

Move the director from broad style descriptions to validated, shot-level cinematography and continuity instructions. This is a foundation for objective visual-quality work; it is not a claim that the current Wan model has reached Higgsfield-level quality.

## What changed

- The CreativePlan can now describe a global cinematic look, cross-shot continuity rules and realism constraints.
- Each scene can specify focal length, aperture, depth of field, lighting design, composition, subject action, motion intensity, continuity anchor and transition intent.
- Legacy plans remain valid: all new fields are optional with conservative defaults.
- Qwen is instructed to select plausible camera settings, coherent light direction and physically achievable movement rather than repeating generic terms such as "cinematic" or "ultra-realistic".
- A shared prompt compiler applies the same rules to single-shot and multi-shot generation. It uses a bounded 4,000-character prompt and produces a consistent negative prompt.
- Product Identity Lock adds its reference-preservation constraints to both the positive and negative prompts.

## Important limits

- The current video-generation workflow still uses the configured Wan 2.2 ComfyUI workflow and its existing base generation dimensions. This change does not add a frontier hosted model, native 4K generation, new weights, or multi-provider routing.
- Camera metadata in a prompt is a conditioning instruction, not a guarantee that the model obeys physical optics.
- A valid CreativePlan or a passing FFmpeg technical report does not prove photorealism, temporal consistency, correct physics, identity preservation or viewer indistinguishability.
- Text, logos, hands, reflections, contact physics and multi-shot identity still require visual review.
- No model is labelled at parity with Higgsfield until blind, repeatable comparisons on matched briefs pass the visual-quality acceptance rubric.

## Validation

The accompanying tests verify backward compatibility, schema bounds, prompt content and size limits, Product Identity Lock preservation directions, and use of the same compiler in single-shot and multi-shot paths.

## Exit criteria for the next quality increment

1. Capture a fixed, licensed benchmark set of briefs and reference assets across people, products, fashion, interiors, vehicles, motion and multi-shot continuity.
2. Generate matched samples through this Studio and the selected Higgsfield baselines with settings and model versions recorded.
3. Blind the outputs and score photorealism, temporal consistency, physics, identity consistency, cinematography, composition and audio separately.
4. Select and integrate additional generation providers only after cost, rights, latency, data handling and reproducibility have been reviewed.
5. Do not claim parity based on upscaling, metadata checks, synthetic CI fixtures or prompt quality alone.
