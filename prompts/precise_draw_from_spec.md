# Precise Draw From Spec

## Fidelity Contract

- Preserve the requested CAD semantics. Repeated components become blocks or
  arrays; measurements become associative dimensions; BOMs, schedules, and
  part lists become CAD tables; sections use hatch/section conventions; 3D
  intent uses regions, solids, and boolean operations when applicable.
- Do not simplify an assembly, engineering sheet, section/detail view,
  exploded view, title block, or tabular annotation into generic rectangles,
  loose lines, or plain text.
- If the exposed MCP tools cannot preserve a requested feature, state the gap
  and ask for guidance instead of silently drawing a lower-fidelity substitute.

## Workflow

1. Call `recommend_cad_tools(intent)` with the full drawing intent when the
   specification has repeated parts, annotations, dimensions, tables, sections,
   3D intent, or other complex features.
2. Analyze the specification and produce a `CADPlan` with variables, `save_as`,
   dependencies, expectations, and postconditions when later steps need earlier
   handles. The plan must name layers, object families, repeated-component
   strategy, annotation/dimension/table strategy, and validation postconditions.
3. Call `validate_cad_plan`.
4. Call `dry_run_cad_plan`.
5. Execute only after explicit modification permission by calling
   `execute_cad_plan` with `allow_modify=true` and `transactional=true`.
6. Call `scan_all_entities`.
7. Call `build_drawing_ir`.
8. Call `validate_geometry`.
9. Call `export_view_image_with_mapping(include_overlay=true)`.
10. Save or export only when requested.

Prefer high-level CAD tools over repeated primitives. Do not use `send_command`
inside plans unless explicitly approved as dangerous.
If execution fails, inspect `failed_step`, `completed_steps`, and
`rollback_status` before retrying.
