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
- A repair is complete only when structured before/after evidence and visual
  evidence agree. A successful edit tool response is not proof that the defect
  is fixed.

## Workflow: Observe -> Diagnose -> Plan -> Validate -> Execute -> Verify

0. Call `check_runtime_environment(check_autocad=true)` and stop on required
   blockers.
1. Establish a baseline before modification: inspect document/space/units,
   call `scan_all_entities`, `build_drawing_ir`, and `validate_geometry`, and
   use `render_drawing_view` or `export_view_image_with_mapping` when the issue
   is visual, spatial, or layout-related.
2. Call `bind_all_dimensions`, `extract_drawing_constraints`, and
   `check_drawing_constraints` when dimension or geometric intent matters.
3. Ground the exact target. Use issue IDs, semantic objects, constraints,
   handles, and `explain_entity`; keep ambiguous candidate handles separate.
4. For one simple edit with one known handle, call `explain_entity` and prepare
   the purpose-built handle edit tool and exact arguments, but do not modify yet.
   For multi-entity, multi-step, destructive, or ambiguous work,
   call `propose_repair_plan` for selected validation/VLM issue IDs or
   `propose_constraint_repair_plan` for violated constraints. Prefer the
   smallest repair that preserves surrounding semantics; keep broad acceptance
   criteria outside the plan and use executable postconditions for captured
   handles.
5. For a CADPlan repair, call `validate_cad_plan`, then `dry_run_cad_plan`.
6. If modification permission is not already granted by the request, obtain it.
   Execute a CADPlan with `allow_modify=true` and `transactional=true`; execute
   a direct edit only against the explained handle with the prepared tool and
   arguments. Ask again when ambiguity, destructive scope, or material scope
   expansion requires a new decision.
7. Rescan before trusting handles or cached metadata. Rebuild the relevant
   CAD-IR/semantic context, rebind dimensions, recheck constraints, and call
   `validate_geometry`.
8. Re-render the affected view and compare it with the baseline when visual
   evidence matters. Reconcile the visual result with exact handles,
   dimensions, constraints, and validation results.
9. If a confirmed residual issue is safely repairable, create a new validated
   and dry-run repair plan. Limit automatic verify/repair cycles to two unless
   the user explicitly asks to continue. Report unresolved issues, ambiguity,
   rollback state, and evidence instead of looping indefinitely.

Never modify the DWG during analysis, validation, grounding, or dry-run. Never
execute an ambiguous repair automatically. Read-only review renders and mapped
snapshots are allowed for verification. Do not save, purge, close, or produce a
deliverable export unless the user's request requires it.
