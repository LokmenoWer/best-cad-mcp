# Repair Drawing

1. Call `validate_geometry`.
2. Call `propose_repair_plan` for selected issue IDs.
3. Call `validate_cad_plan`.
4. Call `dry_run_cad_plan`.
5. Execute only after explicit modification permission by calling
   `execute_cad_plan` with `allow_modify=true`.
6. Call `scan_all_entities`.
7. Call `validate_geometry`.
8. Call `export_view_image_with_mapping`.

Never modify the DWG during analysis, validation, grounding, or dry-run.
