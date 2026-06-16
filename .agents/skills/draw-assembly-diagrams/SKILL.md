---
name: draw-assembly-diagrams
description: >-
  Create, inspect, understand, validate, repair, annotate, visually ground, and
  export standards-aware AutoCAD assembly drawings through best-cad-mcp. Use
  when working with DWG/DXF/PDF CAD deliverables, existing drawing
  understanding, CAD-IR, semantic objects, constraints, validation reports,
  VLM-to-handle grounding, model-private spatial annotations, safe CADPlan
  dry-runs/execution, BOMs, balloons/item numbers, sectioned or exploded
  assemblies, dimensions, blocks, hatches, layouts, and precise handle-based
  edits. Requires using best-cad-mcp tools, scanned SQLite metadata, explicit
  dry-run before planned modification, and no standalone AutoCAD COM scripts.
---

# Draw Assembly Diagrams

Use this skill as an operating guide for best-cad-mcp. Treat AutoCAD as the
source of truth, handles as the edit targets, SQLite as the agent's private CAD
memory, and CADPlan as the guarded path for multi-step modification.

Read `references/assembly-drawing-requirements.md` before creating or checking
an assembly drawing, BOM, item-numbering scheme, sectioned view, exploded view,
or standards-compliance claim.

## Hard Boundaries

- Use the active best-cad-mcp AutoCAD connection. Do not launch a separate
  AutoCAD process.
- Do not write standalone Python, VBA, LISP, or pywin32 COM scripts to modify
  drawings. If a COM behavior is missing, add or fix a best-cad-mcp tool.
- Understanding, scan, query, semantic detection, constraints, validation,
  VLM grounding, resource reads, prompt reads, and dry-runs must not modify the
  DWG.
- Editing requires a specific editing/drawing tool call or
  `execute_cad_plan(plan, allow_modify=True)` after validation and dry-run.
- Treat `send_command`, modal plotting, screen-pick tools, purge, erase,
  password, close, and global undo/redo as unsafe unless the user specifically
  requested that operation.
- Keep agent memory in SQLite with spatial annotations, semantic objects, or
  resources. Do not add hidden helper layers, XData, blocks, labels, or visible
  marks to the DWG for model memory.

## Choose The Workflow

- Existing DWG inspection: scan, build IR, summarize, detect semantics, extract
  constraints, validate, optionally export mapped view.
- Existing DWG repair: inspect first, ground ambiguous observations, propose a
  repair plan, validate and dry-run, then execute only with modification
  permission.
- New precise drawing: create/open drawing, set layers/styles/layout context,
  build a CADPlan from high-level operations, validate and dry-run, execute,
  rescan, validate, and export.
- One-off edit with a known handle: call `explain_entity(handle)`, run the
  specific handle-based edit tool, rescan or query the affected handle, then
  validate if geometry changed.
- Visual/VLM review: export a mapped view with overlay, ground pixel regions to
  handles, explain candidates, then repair by plan or direct edit.

## Existing Drawing Understanding

Use this sequence before editing an existing drawing:

1. `open_drawing` only when the user provides a DWG path to open.
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`.
3. `build_drawing_ir`.
4. `summarize_drawing(level="normal")`; use `level="deep"` when full IR detail
   is needed in the response.
5. `detect_semantic_objects(domain="mechanical")` for assemblies; use
   `generic`, `architecture`, or `electrical` when evidence points elsewhere.
6. `extract_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=True)` when visual review,
   VLM review, or grounding is useful.
9. `explain_entity(handle)` before precise edits to ambiguous or important
   handles.

Use `scan_all_entities(topology_detail="full")` only when primitive/relation
topology is needed for selected geometry. The default summary topology is the
large-drawing-safe survey mode.

## CAD Understanding Tools

- `build_drawing_ir`: return a stable CAD intermediate representation with
  native handles, layers, blocks, topology, semantic objects, constraints,
  validation, and view snapshots.
- `summarize_drawing`: return drawing/domain/entity/layer/block summaries and
  recommended next tools.
- `find_entities_by_description`: search type, layer, block/text content,
  annotations, bbox position, and simple geometric terms.
- `explain_entity`: inspect one handle, related topology, nearby entities,
  annotations, dimensions, and semantic guess.
- `analyze_drawing_intent`: infer mechanical, architecture, electrical,
  structural, or generic domain from evidence.
- `detect_semantic_objects`: write rule-based semantic objects to SQLite only.
- `get_semantic_graph` / `find_semantic_objects`: recover semantic object IDs,
  handles, evidence, confidence, and relations.
- `extract_drawing_constraints`, `check_drawing_constraints`,
  `get_drawing_constraints`: manage radius, diameter, distance, parallel,
  perpendicular, concentric, coincident endpoint, closed profile, repeated
  pattern, and scanned dimension constraints. Keep uncertain dimension binding
  as `status="unknown"`.
- `validate_geometry` / `get_validation_report`: produce structured issues
  with severity, handles, evidence, repair hints, and suggested tools.
- `propose_repair_plan`: create a non-executing plan from validation issue IDs.
- `list_cad_resources` / `get_cad_resource`: retrieve current summary, IR,
  topology, semantic graph, constraints, validation report, and tool guide.

## CADPlan Workflow

Use a `CADPlan` for multi-step generation or repair, especially when more than
one entity will be changed.

Required sequence:

1. Build a plan with high-level operations and explicit args.
2. `validate_cad_plan(plan)`.
3. `dry_run_cad_plan(plan)`.
4. Ask for explicit modification permission when the user has not already
   granted it.
5. `execute_cad_plan(plan, allow_modify=True)`.
6. `scan_all_entities`.
7. `validate_geometry`.
8. `export_view_image_with_mapping` for visual confirmation when layout or
   geometry matters.

Plan shape:

```json
{
  "plan_id": "short-stable-id",
  "description": "what this plan changes",
  "units": "drawing_units",
  "risk_level": "low|medium|high",
  "requires_confirmation": true,
  "steps": [
    {
      "step_id": "s1",
      "op": "draw_rectangle",
      "args": {"corner1": [0, 0, 0], "corner2": [100, 50, 0], "layer": "M-PART"},
      "writes": true,
      "depends_on": []
    }
  ],
  "constraints": [
    {"type": "distance", "handles": ["H1", "H2"], "expected": 25.0}
  ]
}
```

Currently executable CADPlan operations are:

```text
draw_line, draw_circle, draw_rectangle, draw_polyline, draw_polygon,
draw_text, draw_mtext, move_entity, rotate_entity, copy_entity,
delete_entity, delete_entities, scale_entity, mirror_entity, offset_entity,
array_rectangular, array_polar, set_entity_properties, create_layer,
set_current_layer, add_linear_dimension, add_radial_dimension,
add_diametric_dimension, add_hatch, hatch_add_boundary, create_block,
insert_block
```

Use direct MCP tools for valid CAD operations that are not yet in the CADPlan
executor, such as `draw_donut`, `draw_spline`, `draw_box`, `draw_cylinder`,
`solid_boolean`, `trim_entity`, `extend_entity`, `fillet_entities`,
`chamfer_entities`, `add_table`, `edit_table_cell`, `add_mleader`, and layout
or plotting tools.

Plan rules:

- Unknown operations must fail validation.
- `send_command`, SQL mutation, purge, and audit are disallowed by default.
- Dry-run is static and must not call AutoCAD.
- Execution must route through existing safe MCP tool implementations.
- After execution, rescan before relying on new handles, topology, semantics, or
  validation results.

## VLM Grounding

1. Call `export_view_image_with_mapping(include_overlay=True)`.
2. Give the clean export, overlay export, and sidecar JSON to the VLM.
3. Require VLM output with pixel bbox or overlay ID, issue type, confidence,
   evidence, and any claimed handle.
4. Call `ground_vlm_region(snapshot_id, bbox)` for each VLM bbox.
5. Call `explain_entity` on top candidates before proposing edits.
6. Convert confirmed issues into `propose_repair_plan`, a CADPlan, or a direct
   handle-based edit.

The first view mapper is most reliable for top/plan views. For twisted, UCS, or
3D views, keep warnings in the reasoning and avoid claiming exact grounding
beyond returned confidence.

## Spatial Annotations And Resources

- Use `add_spatial_annotation` for model-private labels, remembered points,
  part names, face/edge hints, or VLM-derived observations. These annotations
  belong in SQLite only.
- Use `list_spatial_annotations` before relying on remembered labels from
  earlier turns.
- Use `clear_spatial_annotations` only for annotations known to be temporary.
- Use `list_cad_resources` to discover current CAD-IR, summary, topology,
  semantic graph, constraints, validation report, and tool guide resources.
- Use `get_cad_resource(uri)` instead of rebuilding expensive context when a
  current resource already exists.

## Assembly Drafting Rules

- Plates and rectangular parts: `draw_rectangle`.
- Regular nuts/forms: `draw_polygon`.
- Washers/gaskets/rings: `draw_donut`.
- Repeated parts: `create_block`, `insert_block`, `array_rectangular`,
  `array_polar`, or `insert_minsert_block`.
- 3D forms: `draw_box`, `draw_cylinder`, `draw_torus`, `add_region`,
  `extrude_region`, `revolve_region`, `solid_boolean`.
- Sections: `add_hatch`, `hatch_add_boundary`, `hatch_add_inner_loop`,
  `hatch_set_properties`; avoid gradient hatches by default.
- Edits by handle: `move_entity`, `rotate_entity`, `offset_entity`,
  `mirror_entity`, `trim_entity`, `extend_entity`, `fillet_entities`,
  `chamfer_entities`.
- Dimensions: use real dimension entities such as `add_linear_dimension`,
  `add_radial_dimension`, `add_diametric_dimension`, `add_angular_dimension`,
  and `add_qdim`; never fake dimensions with text and lines.
- BOMs: create the parts list with `add_table`, fill it with
  `edit_table_cell`, and ensure every balloon maps to one BOM row.
- Balloons/leaders: prefer `add_mleader`. If circular balloons are required,
  create one consistent circle/text/leader unit and block or group it.

## Verification Checklist

Before reporting completion:

- Document info, active space, and units inspected.
- Existing drawing scanned and summarized when applicable.
- CAD-IR built without leaking scoped internal keys.
- Semantic objects detected for mechanical assemblies when applicable.
- Constraints extracted and checked; uncertain dimensions called out.
- Validation report generated and important issues handled or reported.
- VLM/export grounding used when visual confirmation matters.
- Intended edits executed by handle or CADPlan and followed by rescan.
- BOM quantities match visible repeated parts, blocks, arrays, or patterns.
- Balloons match BOM item numbers.
- Final export path, layout, key handles, validation evidence, and unresolved
  assumptions are included in the response.

## Recovery

- If AutoCAD is busy or COM rejects calls, stop the batch, wait for idle, and
  retry only the failed MCP tool once.
- If a tool prompts for input, stop and supply complete arguments or fix the
  wrapper.
- If VLM grounding is ambiguous, keep multiple candidate handles and ask for
  confirmation before editing.
- If CADPlan lacks an operation binding, use a direct MCP tool or extend the
  plan executor with tests before relying on it.
- Clean only known temporary MCP artifacts. Do not purge, erase, save over,
  close, or password-protect the user's drawing without explicit instruction.
