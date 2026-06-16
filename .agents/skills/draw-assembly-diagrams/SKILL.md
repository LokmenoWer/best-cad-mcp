---
name: draw-assembly-diagrams
description: >-
  Create, inspect, understand, validate, repair, and export standards-aware
  mechanical assembly drawings through best-cad-mcp. Use for AutoCAD DWG/DXF/PDF
  assembly deliverables, exploded or sectioned assemblies, BOM/parts lists,
  balloons/item numbers, VLM visual review, semantic CAD understanding, CAD-IR,
  constraints, validation reports, safe repair plans, and precise handle-based
  edits. Requires using the existing best-cad-mcp AutoCAD MCP tools, scanned
  SQLite metadata, CAD Understanding Layer, model-private annotations, and
  explicit dry-run before modification.
---

# Draw Assembly Diagrams

Use this skill to work on assembly drawings through best-cad-mcp as a CAD
understanding workflow, not only as a drawing-tool workflow. Preserve the user's
AutoCAD session, capture handles, ground visual observations, validate geometry,
and edit only through explicit MCP tools.

Read `references/assembly-drawing-requirements.md` before creating or checking
an assembly drawing, BOM, item-numbering scheme, sectioned view, exploded view,
or standards-compliance claim.

## Hard Boundaries

- Use the active best-cad-mcp AutoCAD connection. Do not launch a separate
  AutoCAD process.
- Do not bypass `src/cad_controller.py` or write standalone Python COM scripts.
  If an operation needs COM, use or fix a best-cad-mcp tool.
- Understanding tools, validation tools, grounding tools, resources, prompts,
  and dry-runs must not modify the DWG.
- Editing requires an explicit editing tool call or `execute_cad_plan` with
  `allow_modify=True`.
- Treat `send_command`, modal plotting, screen-pick tools, purge, erase,
  password, close, and global undo/redo as unsafe unless the user specifically
  requested that operation.
- Keep model-only labels in SQLite with spatial annotations or semantic objects;
  do not add helper labels, hidden layers, XData, blocks, or visible marks to
  the DWG for agent memory.

## Existing Drawing Understanding Workflow

Use this sequence before editing an existing assembly drawing:

1. `open_drawing` only when the user provides a DWG to open.
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`.
3. `build_drawing_ir`.
4. `summarize_drawing(level="normal")`; use `level="deep"` when a full IR is
   useful in the response.
5. `detect_semantic_objects(domain="mechanical")`.
6. `extract_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=True)` when visual review,
   VLM review, or grounding is useful.
9. `explain_entity(handle)` before precise edits to any ambiguous handle.

Use `scan_all_entities(topology_detail="full")` only when primitive/relation
topology is needed for selected geometry. The default summary topology is the
large-drawing-safe survey mode.

## CAD Understanding Tools

- `build_drawing_ir`: get a stable JSON CAD intermediate representation with
  native handles, layers, blocks, topology, semantic objects, constraints,
  validation, and view snapshots.
- `summarize_drawing`: get agent-oriented drawing/domain/entity/layer/block
  summaries and recommended next tools.
- `find_entities_by_description`: lexical/rule-based search over type, layer,
  block/text content, annotations, bbox position, and simple geometric terms.
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
  pattern, and dimension constraints. Unknown dimension binding must remain
  `status="unknown"`.
- `validate_geometry` / `get_validation_report`: produce structured issue
  reports with severity, handles, evidence, repair hints, and suggested tools.
- `propose_repair_plan`: create a non-executing repair plan from issue IDs.
- `list_cad_resources` / `get_cad_resource`: retrieve current summary, IR,
  topology, semantic graph, constraints, validation report, and tool guide.

## VLM Grounding Workflow

1. Call `export_view_image_with_mapping(include_overlay=True)`.
2. Give the clean export, overlay export, and sidecar JSON to the VLM.
3. Require VLM output with pixel bbox or overlay ID, issue type, confidence,
   evidence, and any claimed handle.
4. Call `ground_vlm_region(snapshot_id, bbox)` for each VLM bbox.
5. Call `explain_entity` on top candidates before proposing edits.
6. Convert confirmed issues into `propose_repair_plan` or a user-reviewed
   `CADPlan`.

The first view mapper is most reliable for top/plan views. For twisted, UCS, or
3D views, keep the warnings in the final reasoning and avoid claiming exact
grounding beyond the returned confidence.

## Safe Plan Workflow

Use a `CADPlan` for multi-step drawing or repair:

1. Build a plan with high-level operations and explicit args.
2. `validate_cad_plan`.
3. `dry_run_cad_plan`.
4. Ask for explicit modification permission when the user has not already
   granted it.
5. `execute_cad_plan(plan, allow_modify=True)`.
6. `scan_all_entities`.
7. `validate_geometry`.
8. `export_view_image_with_mapping` for visual confirmation.

Plans must fail validation for unknown operations. `send_command` is disallowed
by default. Dry-run is static and must not call AutoCAD.

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
- BOMs: create the parts list with `add_table`, fill with `edit_table_cell`,
  and ensure every balloon maps to one BOM row.
- Balloons/leaders: prefer `add_mleader`. If circular balloons are required,
  create one consistent circle/text/leader unit and block or group it.

## Verification Checklist

Before reporting completion:

- Document info, active space, and units inspected.
- Existing drawing scanned and summarized.
- CAD-IR built without leaking scoped internal keys.
- Semantic objects detected for mechanical assemblies.
- Constraints extracted and checked; uncertain dimensions called out.
- Validation report generated and important issues handled or reported.
- VLM/export grounding used when visual confirmation matters.
- All intended edits executed by handle and followed by rescan.
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
- Clean only known temporary MCP artifacts. Do not purge, erase, save over,
  close, or password-protect the user's drawing without explicit instruction.
