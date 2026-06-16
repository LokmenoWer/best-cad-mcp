# Understand Existing Drawing

1. Call `scan_all_entities`.
2. Call `build_drawing_ir`.
3. Call `detect_semantic_objects` with an appropriate domain or `generic`.
4. Call `extract_drawing_constraints`.
5. Call `validate_geometry`.
6. Call `summarize_drawing`.
7. Optionally call `export_view_image_with_mapping`.

Do not modify the DWG during understanding. Use structured handles, evidence,
confidence, constraints, validation issues, and recommended next tools for the
agent response.
