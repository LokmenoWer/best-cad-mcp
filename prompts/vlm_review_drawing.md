# VLM Review Drawing

Default persisted prompt contract: `vlm_review_drawing/v3`.

## Fidelity Contract

- Use VLM review to preserve complex visible semantics, not to replace CAD
  evidence. Views, section/detail markers, BOMs, title blocks, GD&T, surface
  finish symbols, dimensions, hatches, and repeated components must remain
  grounded to handles, primitives, or explicit uncertainty.
- A VLM finding is a hypothesis until validated, grounded, persisted, and
  reviewed. Do not promote it to a repair issue just because it is visually
  plausible.
- Do not draw helper geometry, labels, arrows, or temporary marks into the DWG
  for VLM grounding.

## Workflow

1. Call `export_view_image_with_mapping` with `include_overlay=true`.
   For dense point/line drawings, run semantic detection first and use
   `overlay_granularity="adaptive"` with `overlay_style="som"`: sparse views
   retain entity marks, while crowded views prefer multi-handle semantic shape
   marks instead of covering every stroke with labels. Use `"both"` only for a
   focused local region that truly needs primitive marks, and
   `include_tiles=true` for large drawings.
2. Check `snapshot.vlm_ready` in the result before proceeding:
   - If `vlm_ready=true`: use `snapshot.vlm_image_path` (PNG/JPEG) as the
     image to send to the VLM. Also send the `overlay_image_path` overlay
     and the `overlay_items_path` sidecar JSON.
   - If `vlm_ready=false`: WMF→PNG conversion failed because ImageMagick,
     wand, and Inkscape are all unavailable. You cannot perform VLM visual
     review in this state. Report the issue to the user and suggest installing
     one of those tools before retrying.
   Do not pass `.wmf` or `.svg` files to the VLM — they are not raster images
   and will be rejected or silently misread by all major VLM APIs.
3. Give the PNG clean image, PNG overlay image, tile index when present, and
   sidecar JSON to the VLM.
   When a dense region needs a local pass, call
   `get_snapshot_image(snapshot_id, tile_id="T...")`. For any finding measured
   in the returned embedded image, echo that image's `source_ref_template`
   exactly. It records the observed image dimensions and the validated
   observed-to-source/global transforms, including any automatic downscaling.
   The validator checks the transform and rebases the bbox to snapshot-global
   pixels; never label observed/downscaled numbers as original tile-local or
   snapshot-global coordinates.
4. Require VLM output as JSON with a top-level `findings` array. Each finding
   must include one of `overlay_id`, `bbox`, or `claimed_handles`, plus
   `issue_type`, `confidence`, `severity`, `evidence`, and optional
   `semantic_type`. `claimed_handles` is one exact visual group: include every
   handle belonging to that shape and do not put mutually exclusive alternatives
   in the same list. Bbox coordinates and confidence must be finite JSON numbers;
   a bbox with no spatially supported CAD candidate must remain ungrounded (or
   ambiguous when it conflicts with claimed handles).
5. Call `validate_vlm_review_output` before trusting the VLM JSON.
6. Call `submit_vlm_review` to validate, ground, reconcile, and persist findings.
   It evaluates every supplied localization source instead of allowing an
   `overlay_id` to suppress bbox or claimed-handle evidence. An entity overlay
   can corroborate membership in a larger semantic shape; it is not proof that
   one member is the complete object. For region results, inspect
   `shape_candidates` and `recommended_candidate` before collapsing a visible
   closed profile/component to one entity; a correct shape may contain several
   LINE or curve handles. Ambiguity compares distinct canonical handle groups,
   not duplicate entity/semantic representations. It is independent of the
   display `top_k`; if `selection.ambiguous=true`, keep the finding uncertain.
   LINE/POLYLINE localization uses the authored pixel path, semantic profiles
   use their actual contour, and an optional `semantic_type` affects a decision
   only when an exact spatially supported semantic candidate exists. Member
   edges cannot satisfy a complete-shape intent by themselves. A contour whose
   whole projected footprint is below one pixel must remain ungrounded because
   the reviewed raster cannot distinguish it reliably.
7. Call `get_vlm_findings` to inspect stored evidence, source agreements, and
   conflicts. Incomplete claimed groups and conflicting localization sources
   fail closed as ambiguous and must not be fused or promoted.
8. Call `fuse_vlm_findings_into_semantic_graph` when findings identify layout,
   annotation, BOM, title block, GD&T, surface roughness, or other semantic
   objects.
9. Call `analyze_engineering_drawing_stages` to produce layout segmentation,
   annotation detection, VLM parsing, and reconciliation JSON.
10. Call `evaluate_vlm_grounding` when a benchmark or expected handle set is
   available. Supply one complete multi-handle shape as
   `expected_handle_group` (`expected_handles` remains a compatibility alias).
   For a genuinely ambiguous observation, supply mutually valid decisions as
   `expected_alternative_groups=[[...], [...]]`; never flatten those alternatives
   into one shape. Use `expected_equivalent_handle_groups` when several complete
   CAD groups are semantically interchangeable rather than visually ambiguous.
   Set `ground_truth_exhaustive=false` for sampled annotations. When the case
   tests a decision, also provide
   `expected_status="grounded"|"ambiguous"`. Inspect exact-group, alternative
   coverage, and ambiguity metrics instead of accepting a one-handle overlap.
11. Call `promote_vlm_finding_to_validation_issue` only for findings that should
   enter validation/repair planning.
12. Call `explain_entity` for likely handles and inspect primitive candidates.
13. Call `propose_repair_plan` or `propose_constraint_repair_plan` for selected
    validation, constraint, or VLM issues.
14. Validate and dry-run any CADPlan before execution.

Do not draw helper geometry or labels into the DWG for VLM grounding.
Do not claim exact grounding when the snapshot returns limitations or low
confidence.
VLM findings are hypotheses until grounded to handles or primitives and reviewed.
