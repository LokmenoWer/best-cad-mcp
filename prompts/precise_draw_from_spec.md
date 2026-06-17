# Precise Draw From Spec

1. Analyze the specification and produce a `CADPlan` with variables, `save_as`,
   dependencies, expectations, and postconditions when later steps need earlier
   handles.
2. Call `validate_cad_plan`.
3. Call `dry_run_cad_plan`.
4. Execute only after explicit modification permission by calling
   `execute_cad_plan` with `allow_modify=true` and `transactional=true`.
5. Call `scan_all_entities`.
6. Call `build_drawing_ir`.
7. Call `validate_geometry`.
8. Call `export_view_image_with_mapping(include_overlay=true)`.
9. Save or export only when requested.

Prefer high-level CAD tools over repeated primitives. Do not use `send_command`
inside plans unless explicitly approved as dangerous.
If execution fails, inspect `failed_step`, `completed_steps`, and
`rollback_status` before retrying.
