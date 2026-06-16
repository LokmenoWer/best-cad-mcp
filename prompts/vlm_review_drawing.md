# VLM Review Drawing

1. Call `export_view_image_with_mapping` with `include_overlay=true`.
2. Give the clean image, overlay image, and sidecar JSON to the VLM.
3. Require VLM output as JSON containing overlay IDs, handles, pixel bboxes,
   issue type, confidence, and evidence.
4. Call `ground_vlm_region` for each pixel bbox.
5. Call `explain_entity` for likely handles.
6. Call `propose_repair_plan` for validation or VLM issues.

Do not draw helper geometry or labels into the DWG for VLM grounding.
