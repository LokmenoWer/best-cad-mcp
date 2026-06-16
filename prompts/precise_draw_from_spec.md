# Precise Draw From Spec

1. Analyze the specification and produce a `CADPlan`.
2. Call `validate_cad_plan`.
3. Call `dry_run_cad_plan`.
4. Execute only after explicit modification permission by calling
   `execute_cad_plan` with `allow_modify=true`.
5. Call `scan_all_entities`.
6. Call `validate_geometry`.
7. Call `export_view_image_with_mapping`.
8. Save or export only when requested.

Prefer high-level CAD tools over repeated primitives. Do not use `send_command`
inside plans unless explicitly approved as dangerous.
