# VLM Review Drawing

1. Call `export_view_image_with_mapping` with `include_overlay=true`.
2. Give the clean image, overlay image, and sidecar JSON to the VLM.
3. Require VLM output as JSON containing overlay IDs, handles, pixel bboxes,
   issue type, confidence, and evidence.
4. Call `ground_vlm_overlay_id` when the VLM references an overlay ID, or
   `ground_vlm_region` for each pixel bbox.
5. Call `map_pixel_region_to_world_bbox` when region-level world extents are
   needed.
6. Call `explain_entity` for likely handles and inspect primitive candidates.
7. Call `propose_repair_plan` or `propose_constraint_repair_plan` for selected
   validation, constraint, or VLM issues.
8. Validate and dry-run any CADPlan before execution.

Do not draw helper geometry or labels into the DWG for VLM grounding.
Do not claim exact grounding when the snapshot returns limitations or low
confidence.
