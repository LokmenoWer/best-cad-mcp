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

## Required Output

```json
{
  "component_hypotheses": [
    {
      "id": "hyp_1",
      "label": "flange_like_component",
      "confidence": 0.0,
      "pixel_bbox": [0, 0, 1, 1],
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
- Use `missing_evidence` for absent signals such as hidden bolt patterns,
  incomplete views, cropped geometry, unclear text, or section-only context.
- Use top-k hypotheses when several components fit the same evidence.
- Keep geometry extraction separate from semantic naming. Do not invent CAD
  primitives, dimensions, materials, or manufacturing details.
- If no component-level label is defensible, return an empty
  `component_hypotheses` array and explain why in `uncertainties`.
