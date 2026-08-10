# Recognize Components From Image

Use this prompt with `VisualSemanticContext/v1` from
`prepare_visual_semantic_context`.

## Task

Inspect the supplied normalized image and auxiliary artifacts. Return only JSON
that can be merged into `ImageDrawingSpec/v1.component_hypotheses`.

Do not force an exact part name when the drawing view is partial, sectioned,
cropped, or ambiguous. Prefer open-vocabulary labels such as
`flange_like_component`, `bushing_or_hub_like_component`,
`cover_like_component`, `bracket_like_component`, `shaft_like_component`, or a
clearer domain label when the evidence supports it.

Component naming must be supported by both visible shape cues and drawing
context such as view type, neighboring features, centerlines, hole patterns,
section hatching, dimensions, leaders, or BOM references. Familiar appearance
alone is insufficient. Keep geometry extraction separate from naming, and do
not infer true size, material, fit, tolerance, or manufacturing process unless
the supplied structured context explicitly supports it.

## Required Output

```json
{
  "component_hypotheses": [
    {
      "id": "hyp_1",
      "label": "flange_like_component",
      "confidence": 0.0,
      "view_type": "section_view",
      "evidence": [
        "visible evidence from the drawing"
      ],
      "missing_evidence": [
        "important evidence not visible in this view"
      ],
      "related_feature_ids": []
    }
  ],
  "uncertainties": []
}
```

## Evidence Rules

- Every hypothesis must cite visible drawing evidence.
- `pixel_bbox` is optional. Include it only when measured from the supplied
  image; never emit a placeholder or full-image bbox merely to fill the field.
- For coordinates measured from a tile, crop, or downscaled embedded image,
  copy that image's returned `source_ref_template` unchanged into the
  hypothesis so validation can rebase the coordinates correctly.
- Use `missing_evidence` for absent signals such as hidden bolt patterns,
  incomplete views, cropped geometry, unclear text, or section-only context.
- Use top-k hypotheses when several components fit the same evidence.
- Distinguish one component seen in multiple views from several repeated
  components. Use related feature IDs and missing evidence instead of silently
  merging or duplicating hypotheses.
- Keep geometry extraction separate from semantic naming. Do not invent CAD
  primitives, dimensions, materials, or manufacturing details.
- If no component-level label is defensible, return an empty
  `component_hypotheses` array and explain why in `uncertainties`.
