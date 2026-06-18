# Repair Drawing

## Fidelity Contract

- Repair by handle and preserve existing drawing intent wherever possible:
  layers, blocks, arrays, hatches, dimensions, tables, title blocks, and view
  relationships should survive unless the selected issue requires changing
  them.
- Do not delete and redraw complex geometry as a shortcut. If replacement is
  unavoidable, the CADPlan must state what is being replaced, why, and how
  postconditions prove fidelity.
- Ambiguous repairs must present alternatives or require confirmation; never
  choose a lower-fidelity simplification just because it is easier to execute.

## Workflow

1. Call `validate_geometry`.
2. Call `extract_drawing_constraints`, `bind_all_dimensions`, and
   `check_drawing_constraints` when dimension or geometric intent matters.
3. Call `propose_repair_plan` for validation issue IDs or
   `propose_constraint_repair_plan` for violated constraints.
4. Call `validate_cad_plan`.
5. Call `dry_run_cad_plan`.
6. Execute only after explicit modification permission by calling
   `execute_cad_plan` with `allow_modify=true` and `transactional=true`.
7. Call `scan_all_entities`.
8. Call `validate_geometry`.
9. Call `export_view_image_with_mapping`.
10. For dense engineering drawings, call `build_drawing_ir` and compare the
    relevant semantic objects, dimensions, constraints, and validation issues
    before and after the repair.

Never modify the DWG during analysis, validation, grounding, or dry-run.
Never execute a repair automatically; ambiguous issues should return
alternatives or require user confirmation.
