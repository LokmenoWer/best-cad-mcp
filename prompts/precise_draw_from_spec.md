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
- Apply explicit requirements by precedence: user/project/company/customer
  requirements, then the named national/industry standard, then documented
  drawing values and existing styles. Stop and clarify material conflicts;
  never resolve them from visual impression alone.

## Acceptance Criteria Before Planning

Translate the specification into explicit, testable criteria before writing a
CADPlan:

- drawing units, WCS/UCS and active space, model-space true-size geometry,
  paper/viewport/annotation scales, layers, linetypes, text style, dimension
  style, and applicable drafting standard;
- required views, semantic object/component families, repeated-part strategy,
  and critical handles or handle relationships;
- dimensions, geometric constraints, BOM/balloon mappings, section hatches,
  title-block fields, and other required annotations;
- structured validation postconditions and visual layout expectations.

## Workflow: Observe -> Plan -> Validate -> Execute -> Verify

0. Call `check_runtime_environment(check_autocad=true)` and stop on required
   blockers.
1. Inspect or establish `get_document_info`, `get_active_space_info`, units,
   layers, and styles. If editing an existing drawing, call
   `scan_all_entities` and capture a visual baseline with `render_drawing_view`
   when appearance or layout matters.
2. Call `recommend_cad_tools(intent)` with the full drawing intent. Prefer, in
   order: existing handles/styles/approved blocks; purpose-built semantic CAD
   tools; blocks, arrays, tables, dimensions, hatches, regions, and solids;
   simple primitives only for genuinely simple one-off geometry;
   `send_command` only as an explicitly approved last resort.
3. Decompose the deliverable into semantic objects or a component register.
   For assemblies, choose the applicable user/project/company/national
   standard before generic mechanical practice, and design BOM rows and item
   balloons together.
4. Produce a bounded `CADPlan` with explicit units, layers, variables,
   `save_as`, dependencies, supported step-level `expect` metadata, and
   executable postconditions. Keep the broader acceptance criteria outside the
   plan for verification after rescan. Split large or high-risk work into
   independently verifiable semantic phases. Use one transaction per phase
   unless the user requires all-or-nothing execution.
5. Call `validate_cad_plan`, then `dry_run_cad_plan`. Resolve unknown
   operations, unsafe calls, missing bindings, and failed fidelity conditions
   before execution.
6. If modification permission is not already granted by the request, obtain it.
   Then call `execute_cad_plan` with `allow_modify=true` and
   `transactional=true`. Ask again only when ambiguity, destructive scope, or
   material scope expansion requires a new decision.
7. Verify structurally: call `scan_all_entities`, `build_drawing_ir`,
   `bind_all_dimensions`, `extract_drawing_constraints`,
   `check_drawing_constraints`, and `validate_geometry` as applicable to the
   acceptance criteria.
8. Verify visually: use `render_drawing_view` when the model must inspect the
   result, or `export_view_image_with_mapping(include_overlay=true)` when
   handle/pixel mapping is needed. Compare the result with the visual baseline
   or source specification.
9. Never accept a committed modification batch as complete from the tool's
   success response alone.
   Compare the verified result with every acceptance criterion. If a confirmed
   mismatch is safely repairable, create a new validated and dry-run repair
   plan. Limit automatic verify/repair cycles to two unless the user explicitly
   asks to continue; otherwise report the remaining issue and evidence.
10. Create read-only review renders/mapped snapshots whenever verification
    needs them. Save the DWG or produce deliverable exports only when requested.

Do not use `send_command` inside plans unless explicitly approved as dangerous.
If execution fails, inspect `failed_step`, `completed_steps`, and
`rollback_status`, then rescan the current state. Rebuild the affected plan and
run validation and dry-run again; never blindly replay old steps or stale
handles.
