# VLM Review Drawing

1. Call `export_view_image_with_mapping` with `include_overlay=true`.
   Use `overlay_granularity="both"` and `overlay_style="som"` for dense
   review, and `include_tiles=true` for large drawings.
2. Give the clean image, overlay image, tile index when present, and sidecar
   JSON to the VLM.
3. Require VLM output as JSON with a top-level `findings` array. Each finding
   must include one of `overlay_id`, `bbox`, or `claimed_handles`, plus
   `issue_type`, `confidence`, `severity`, `evidence`, and optional
   `semantic_type`.
4. Call `validate_vlm_review_output` before trusting the VLM JSON.
5. Call `submit_vlm_review` to validate, ground, and persist findings. It calls
   `ground_vlm_overlay_id` for overlay IDs or `ground_vlm_region` for pixel
   bboxes and stores candidates in SQLite.
6. Call `get_vlm_findings` to inspect stored evidence and ambiguous grounding.
7. Call `fuse_vlm_findings_into_semantic_graph` when findings identify layout,
   annotation, BOM, title block, GD&T, surface roughness, or other semantic
   objects.
8. Call `analyze_engineering_drawing_stages` to produce layout segmentation,
   annotation detection, VLM parsing, and reconciliation JSON.
9. Call `evaluate_vlm_grounding` when a benchmark or expected handle set is
   available.
10. Call `promote_vlm_finding_to_validation_issue` only for findings that should
   enter validation/repair planning.
11. Call `explain_entity` for likely handles and inspect primitive candidates.
12. Call `propose_repair_plan` or `propose_constraint_repair_plan` for selected
    validation, constraint, or VLM issues.
13. Validate and dry-run any CADPlan before execution.

Do not draw helper geometry or labels into the DWG for VLM grounding.
Do not claim exact grounding when the snapshot returns limitations or low
confidence.
VLM findings are hypotheses until grounded to handles or primitives and reviewed.
