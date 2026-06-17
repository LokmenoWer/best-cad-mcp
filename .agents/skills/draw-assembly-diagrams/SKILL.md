---
name: draw-assembly-diagrams
description: >-
  Create, inspect, understand, validate, repair, annotate, visually ground, and
  export standards-aware AutoCAD assembly drawings through best-cad-mcp. Use
  when working with DWG/DXF/PDF CAD deliverables, CAD-IR, semantic graphs,
  constraints, dimension binding, validation reports, VLM-to-handle grounding,
  pixel/world mapping, model-private spatial annotations, prompt resources,
  safe CADPlan dry-runs/execution, BOMs, balloons/item numbers, sectioned or
  exploded assemblies, dimensions, blocks, hatches, layouts, and precise
  handle-based edits. Requires best-cad-mcp runtime preflight, scanned SQLite
  metadata, explicit dry-run before planned modification, modular
  assembly-standard references, and no standalone AutoCAD COM scripts.
---

# Draw Assembly Diagrams

Use this skill as the operating guide for best-cad-mcp. Treat AutoCAD as the
source of truth, native handles as edit targets, SQLite as model-private CAD
memory, prompt/resources as reusable guidance, and CADPlan as the guarded path
for multi-step changes.

## Reference Routing

Read only the references needed for the task:

- Assembly drawing, BOM, item-numbering, section, exploded view, or
  standards-compliance work: read `references/assembly/index.md`.
- When no project/company/national standard is specified, use
  `references/assembly/standards/generic-mechanical.md` as the default assembly
  standard module.
- `references/assembly-drawing-requirements.md` is a compatibility entrypoint;
  prefer the modular assembly references above for new work.
- Prompt content may come from repository prompt files through MCP prompt tools;
  prefer prompt resources over duplicating long instructions in chat.

## Hard Boundaries

- Before live CAD work, call
  `check_runtime_environment(check_autocad=True)`. Treat `ok=false` as a
  blocker. For strict deployments, expect `CAD_MCP_STRICT_PREFLIGHT=1` to make
  the server refuse startup when required checks fail.
- Use the active best-cad-mcp AutoCAD connection. Do not launch another AutoCAD
  process.
- Do not write standalone Python, VBA, LISP, or pywin32 COM scripts to modify
  drawings. Add or fix a best-cad-mcp tool when COM behavior is missing.
- Understanding, scans, CAD-IR/resource reads, semantic detection, dimension
  binding, constraints, validation, VLM grounding, prompt reads, and dry-runs
  must not modify the DWG.
- Editing requires a specific drawing/editing tool call or
  `execute_cad_plan(plan, allow_modify=True)` after validation and dry-run.
- Treat `send_command`, modal plotting, screen-pick tools, purge, erase,
  password, close, and global undo/redo as unsafe unless explicitly requested.
- Keep agent memory in SQLite resources, semantic objects, constraints, or
  spatial annotations. Do not create hidden helper layers, XData, blocks,
  labels, or visible marks for memory.

## Choose The Workflow

- Existing DWG inspection: scan, build CAD-IR, summarize, infer domain, detect
  semantics, bind dimensions, extract/check constraints, validate, then export
  mapped views when visual evidence matters.
- Existing DWG repair: inspect first, ground ambiguous visual observations,
  propose a validation or constraint repair plan, validate and dry-run, then
  execute only with modification permission.
- New precise drawing: create/open a drawing, set layers/styles/layout context,
  load the needed assembly standard module, build a CADPlan, validate and
  dry-run, execute, rescan, validate, and export.
- One-off edit with a known handle: call `explain_entity(handle)`, run the
  precise handle-based edit tool, rescan/query the handle, then validate if
  geometry changed.
- Visual/VLM review: export a mapped clean image plus overlay and sidecar JSON,
  ground pixel bboxes or overlay IDs to handles, explain candidates, then
  repair by plan or direct handle edit.

## Existing Drawing Understanding

Use this sequence before editing an existing drawing:

1. `open_drawing` only when the user provides a DWG path to open.
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`.
3. `build_drawing_ir`.
4. `summarize_drawing(level="normal")`; use `level="deep"` only when full IR
   detail is needed.
5. `analyze_drawing_intent` and `detect_semantic_objects(domain="mechanical")`
   for assemblies; choose another domain only when evidence supports it.
6. `bind_all_dimensions`, then `extract_drawing_constraints` and
   `check_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=True)` when visual review or
   grounding is useful.
9. `explain_entity(handle)` before editing ambiguous or important handles.

Use `scan_all_entities(topology_detail="full")` only for selected geometry that
needs primitive/relation topology. Summary topology is the safe default for
large drawings.

## Core Tool Groups

- CAD-IR/resources: `build_drawing_ir`, `summarize_drawing`,
  `list_cad_resources`, and `get_cad_resource`.
- Search/explain: `find_entities_by_description`, `explain_entity`,
  `get_visible_entities_in_view`, and document/entity statistics tools.
- Semantics: `analyze_drawing_intent`, `detect_semantic_objects`,
  `get_semantic_graph`, and `find_semantic_objects`.
- Dimensions and constraints: `bind_dimension_to_geometry`,
  `bind_all_dimensions`, `infer_geometric_constraints`,
  `extract_drawing_constraints`, `check_drawing_constraints`,
  `get_drawing_constraints`, and `propose_constraint_repair_plan`.
- Validation and repair: `validate_geometry`, `get_validation_report`, and
  `propose_repair_plan`.
- View grounding: `export_view_image_with_mapping`, `map_pixel_to_world`,
  `map_pixel_region_to_world_bbox`, `ground_vlm_region`, and
  `ground_vlm_overlay_id`.
- Model-private memory: `add_spatial_annotation`, `list_spatial_annotations`,
  and `clear_spatial_annotations`.

## CADPlan Workflow

Use a `CADPlan` for multi-step generation or repair, especially when more than
one entity changes.

Required sequence:

0. Confirm runtime readiness with `check_runtime_environment`; run
   `cad-mcp-doctor --check-autocad` outside MCP when diagnosing installation or
   COM availability.
1. Build a plan with high-level operations, explicit args, variables when
   needed, dependencies, and postconditions for critical handles.
2. `validate_cad_plan(plan)`.
3. `dry_run_cad_plan(plan)`.
4. Ask for explicit modification permission when the user has not already
   granted it.
5. `execute_cad_plan(plan, allow_modify=True)`.
6. `scan_all_entities`.
7. `validate_geometry`.
8. `export_view_image_with_mapping` when layout or geometry needs visual proof.

Plan shape:

```json
{
  "plan_id": "short-stable-id",
  "description": "what this plan changes",
  "units": "drawing_units",
  "risk_level": "low|medium|high",
  "requires_confirmation": true,
  "variables": {"origin": [0, 0, 0]},
  "steps": [
    {
      "step_id": "outer",
      "op": "draw_circle",
      "args": {"center": "$origin", "radius": 25, "layer": "M-PART"},
      "writes": true,
      "save_as": "$outer_circle",
      "depends_on": [],
      "postconditions": [{"type": "exists", "target": "$outer_circle"}]
    }
  ],
  "constraints": [
    {"type": "concentric", "handles": ["$outer_circle", "$inner_circle"]}
  ]
}
```

Executable CADPlan operations include:

```text
draw_line, draw_circle, draw_rectangle, draw_polyline, draw_polygon,
draw_text, draw_mtext, move_entity, rotate_entity, copy_entity,
delete_entity, delete_entities, scale_entity, mirror_entity, offset_entity,
array_rectangular, array_polar, set_entity_properties, create_layer,
set_current_layer, add_linear_dimension, add_radial_dimension,
add_diametric_dimension, add_hatch, hatch_add_boundary, create_block,
insert_block, set_dimension_text_override
```

Use direct MCP tools for valid CAD operations that are not yet executable in
CADPlan, such as `draw_donut`, `draw_spline`, `draw_box`, `draw_cylinder`,
`solid_boolean`, `trim_entity`, `extend_entity`, `fillet_entities`,
`chamfer_entities`, `add_table`, `edit_table_cell`, `add_mleader`, layout
tools, plotting/export tools, and save/open tools.

Plan rules:

- Unknown operations must fail validation.
- `send_command`, SQL mutation, purge, and audit are disallowed by default.
- Dry-run is static and must not call AutoCAD.
- Execution is fail-fast: any bound tool exception, `ok=false`,
  `success=false`, or recognizable error result must stop the plan and trigger
  rollback when enabled. Do not continue from a partial plan.
- Execution must route through safe MCP tool implementations and transactional
  undo grouping where available.
- `save_as` captures created handles for later `$variable` references.
- Postconditions should protect critical generated or edited handles.
- After execution, rescan before relying on new handles, topology, semantics,
  constraints, or validation results.

## VLM Grounding

1. Call `export_view_image_with_mapping(include_overlay=True)`.
2. Give the clean export, overlay export, and sidecar JSON to the VLM.
3. Require VLM output with pixel bbox or overlay ID, issue type, confidence,
   evidence, and any claimed handle.
4. Call `ground_vlm_overlay_id(snapshot_id, overlay_id)` for overlay IDs or
   `ground_vlm_region(snapshot_id, bbox)` for each VLM bbox.
5. Call `map_pixel_region_to_world_bbox(snapshot_id, bbox)` when world extents
   are needed for selection or repair planning.
6. Call `explain_entity` on top candidates before proposing edits.
7. Convert confirmed issues into `propose_repair_plan`,
   `propose_constraint_repair_plan`, a CADPlan, or a direct handle-based edit.

Top/plan views are the most reliable for exact grounding. For twisted, UCS, or
3D views, carry returned warnings forward and avoid stronger precision claims
than confidence supports.

## Assembly Drafting

Do not keep assembly-standard rules in this SKILL file. Load
`references/assembly/index.md`, select the applicable standard module, and then
use best-cad-mcp tools for the actual geometry, dimensions, BOM, balloons,
sections, hatches, blocks, arrays, and exports.

When standards conflict, apply the user's project/company/customer/national
standard first, then the selected module, then generic mechanical practice.
State unresolved assumptions in the response and in notes only when the drawing
deliverable needs visible assumptions.

## Verification Checklist

Before reporting completion:

- Runtime preflight passed, or any preflight blocker is explicitly reported.
- Document info, active space, and units inspected.
- Existing drawing scanned and summarized when applicable.
- CAD-IR built without leaking scoped internal keys.
- Semantic objects detected for mechanical assemblies when applicable.
- Dimensions bound; constraints extracted/checked; uncertain dimension binding
  called out.
- Validation report generated and important issues handled or reported.
- VLM/export grounding used when visual confirmation matters.
- Intended edits executed by handle or CADPlan and followed by rescan.
- Assembly standard module loaded for BOM, balloons, dimensions, sections, and
  notes.
- BOM quantities match visible repeated parts, blocks, arrays, or patterns.
- Balloons match BOM item numbers.
- Final export path, layout, key handles, validation evidence, and unresolved
  assumptions are included in the response.

## Recovery

- If AutoCAD is busy or COM rejects calls, stop the batch, wait for idle, and
  retry only the failed MCP tool once.
- If tools are missing or visual export/rendering is unavailable, run
  `check_runtime_environment` or `cad-mcp-doctor --check-autocad` and report the
  blocker instead of improvising external tools.
- If a tool prompts for input, stop and supply complete arguments or fix the
  wrapper.
- If VLM grounding is ambiguous, keep multiple candidate handles and ask for
  confirmation before editing.
- If CADPlan lacks an operation binding, use a direct MCP tool or extend the
  plan executor with tests before relying on it.
- Clean only known temporary MCP artifacts. Do not purge, erase, save over,
  close, or password-protect the user's drawing without explicit instruction.
