---
name: draw-assembly-diagrams
description: >-
  Create, inspect, understand, validate, repair, visually ground, copy from a
  single image, and export standards-aware AutoCAD mechanical and assembly
  drawings through best-cad-mcp. Use when working with DWG/DXF/PDF CAD
  deliverables, external drawing images, ImageDrawingSpec image tracing,
  CAD-IR, semantic graphs, constraints, dimension binding, validation reports,
  VLM-to-handle grounding, pixel/world mapping, model-private spatial
  annotations, prompt resources, safe CADPlan dry-runs/execution, BOMs,
  balloons/item numbers, sectioned or exploded assemblies, dimensions, blocks,
  hatches, layouts, and precise handle-based edits. Requires best-cad-mcp
  runtime preflight, scanned SQLite metadata when a DWG exists, explicit
  dry-run before planned modification, image-trace fidelity checks before
  executing copied drawings, modular assembly-standard references, and no
  standalone AutoCAD COM scripts.
---

# Draw Assembly Diagrams

Use this skill as the operating guide for best-cad-mcp. Treat AutoCAD as the
source of truth once a DWG exists, external images as hypotheses until converted
to validated `ImageDrawingSpec/v1`, SQLite as model-private CAD memory,
prompt/resources as reusable guidance, and CADPlan as the guarded path for
multi-step generation or repair.

## Reference Routing

Read only the references needed for the task:

- Assembly drawing, BOM, item-numbering, section, exploded view, or
  standards-compliance work: read `references/assembly/index.md`.
- When no project/company/national standard is specified, use
  `references/assembly/standards/generic-mechanical.md` as the default
  mechanical assembly standard module.
- `references/assembly-drawing-requirements.md` is a compatibility entrypoint;
  prefer the modular assembly references above for new work.
- Prompt content should come from repository prompt files through MCP prompt
  tools. Use `copy_drawing_from_image` for one-image tracing,
  `precise_draw_from_spec` for text/spec generation, `vlm_review_drawing` for
  visual review of an existing/exported CAD view, and `repair_drawing` for
  validation-led fixes.

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
  binding, constraints, validation, image trace preparation, VLM grounding,
  prompt reads, CADPlan validation, fidelity checks, and dry-runs must not
  modify the DWG.
- Editing requires a specific drawing/editing tool call or
  `execute_cad_plan(plan, allow_modify=True)` after validation and dry-run.
- Treat `send_command`, modal plotting, screen-pick tools, purge, erase,
  password, close, and global undo/redo as unsafe unless explicitly requested.
- Keep agent memory in SQLite resources, semantic objects, constraints, image
  trace records, VLM findings, or spatial annotations. Do not create hidden
  helper layers, XData, blocks, labels, or visible marks for memory.
- For one-image copy/tracing, the VLM call stays on the agent side. MCP
  prepares the image, validates the VLM JSON, compiles CADPlan, and enforces
  fidelity; it does not call a model provider directly.
- Never use `draw_raster_image` as a substitute for vectorizing/copying a
  drawing. Use it only when the user explicitly wants a reference underlay.

## Choose The Workflow

- Existing DWG inspection: scan, build CAD-IR, summarize, infer domain, detect
  semantics, bind dimensions, extract/check constraints, validate, then export
  mapped views when visual evidence matters.
- Existing DWG repair: inspect first, ground ambiguous visual observations,
  propose a validation or constraint repair plan, validate and dry-run, then
  execute only with modification permission.
- New precise drawing from text/spec: create/open a drawing, set layers/styles,
  load the needed assembly standard module, build a CADPlan, validate and
  dry-run, execute, rescan, validate, and export.
- One-image mechanical drawing trace/copy: use `prepare_image_trace`, ask an
  Agent-side VLM with `copy_drawing_from_image` for `ImageDrawingSpec/v1`, run
  `validate_image_drawing_spec`, `submit_image_drawing_spec`,
  `compile_image_spec_to_cad_plan`, and
  `validate_image_fidelity_contract`, then continue through CADPlan
  validation/dry-run/execution and visual diff.
- One-off edit with a known handle: call `explain_entity(handle)`, run the
  precise handle-based edit tool, rescan/query the handle, then validate if
  geometry changed.
- Visual/VLM review of an existing CAD view: export a mapped clean image plus
  overlay and sidecar JSON, ground pixel bboxes or overlay IDs to handles,
  explain candidates, then repair by plan or direct handle edit.

## One-Image Mechanical Trace

Use this sequence whenever the user provides one external image and asks to
copy, trace, recreate, or convert it to CAD:

1. `prepare_image_trace(image_path, domain="mechanical")`.
2. Give the normalized image and tile index to the Agent-side VLM using the
   `copy_drawing_from_image` prompt.
3. Require strict `ImageDrawingSpec/v1` JSON with top-level
   `schema_version`, `domain`, `units`, `calibration_candidates`, `features`,
   `geometry`, `annotations`, `relations`, `tables`, and `uncertainties`.
4. Require every item to include `kind`, `confidence`, `evidence`, and either
   `pixel_bbox` or `pixel_geometry`.
5. `validate_image_drawing_spec(spec, image_id)`.
6. `submit_image_drawing_spec(image_id, spec, source_model=..., prompt_version=...)`.
7. `compile_image_spec_to_cad_plan(image_id=..., units="mm",
   scale_mode="dimension_first")`.
8. `validate_image_fidelity_contract(spec, cad_plan)`.
9. `validate_cad_plan(plan)`.
10. `dry_run_cad_plan(plan)`.
11. Ask for explicit modification permission when the user has not already
    granted it.
12. `execute_cad_plan(plan, allow_modify=True, transactional=True)`.
13. `scan_all_entities`, `build_drawing_ir`, and `validate_geometry`.
14. `export_view_image_with_mapping(include_overlay=True,
    overlay_granularity="both")`.
15. Compare the original image and generated export with the VLM. Convert
    confirmed misses into a repair CADPlan. Limit automatic repair loops to two
    passes unless the user explicitly asks to keep iterating.

If no reliable dimension calibration is available, use the default scale
warning from the image-trace tools and do not claim true engineering scale.

## Image Fidelity Rules

The image trace path is feature-preserving, not approximate sketching:

- A `chamfered_rectangle` must not compile to a plain `draw_rectangle`; it must
  preserve chamfer vertices or use a chamfer operation.
- A `filleted_rectangle` must preserve radii or arc segments; it must not
  become a sharp-corner polyline or rectangle.
- Holes, counterbores, slots, grooves, steps, centerlines, hatches, dimensions,
  leaders, tables, title blocks, BOMs, and patterns must be represented as
  semantic CAD objects where possible.
- Repeated holes or parts must preserve the pattern relationship through
  `pattern` evidence and, when executable, `array_rectangular`,
  `array_polar`, blocks, or equivalent CADPlan structure.
- Dimensions must use real dimension tools such as `add_linear_dimension`,
  `add_radial_dimension`, or `add_diametric_dimension`; never fake dimensions
  with text and lines.
- Unclear, blurred, cropped, or ambiguous features must remain in
  `uncertainties`. Do not silently guess or simplify them to make a plan pass.
- If `validate_image_fidelity_contract` fails, do not execute the CADPlan.
  Repair the spec/plan or ask the user for confirmation about the ambiguous
  feature.

## Existing Drawing Understanding

Use this sequence before editing an existing drawing:

1. `open_drawing` only when the user provides a DWG path to open.
2. `scan_all_entities(clear_db=True, detail_level="minimal",
   topology_detail="summary")`.
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

- Image trace: `prepare_image_trace`, `validate_image_drawing_spec`,
  `submit_image_drawing_spec`, `compile_image_spec_to_cad_plan`, and
  `validate_image_fidelity_contract`.
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

Use a `CADPlan` for multi-step generation, image tracing, or repair,
especially when more than one entity changes.

Required sequence:

0. Confirm runtime readiness with `check_runtime_environment`; run
   `cad-mcp-doctor --check-autocad` outside MCP when diagnosing installation or
   COM availability.
1. Build or compile a plan with high-level operations, explicit args,
   variables when needed, dependencies, and postconditions for critical
   handles. For image tracing, compile from validated `ImageDrawingSpec/v1`.
2. For image tracing, run `validate_image_fidelity_contract(spec, plan)` before
   normal CADPlan validation.
3. `validate_cad_plan(plan)`.
4. `dry_run_cad_plan(plan)`.
5. Ask for explicit modification permission when the user has not already
   granted it.
6. `execute_cad_plan(plan, allow_modify=True, transactional=True)`.
7. `scan_all_entities`.
8. `validate_geometry`.
9. `export_view_image_with_mapping` when layout or geometry needs visual proof.

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
      "args": {"center_x": 0, "center_y": 0, "radius": 25, "layer": "M-PART"},
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
draw_line, draw_arc, draw_circle, draw_donut, draw_ellipse, draw_rectangle,
draw_polyline, draw_polygon, draw_spline, draw_text, draw_mtext,
move_entity, rotate_entity, copy_entity, delete_entity, delete_entities,
scale_entity, mirror_entity, offset_entity, array_rectangular, array_polar,
set_entity_properties, create_layer, set_current_layer, add_mleader,
add_table, edit_table_cell, add_linear_dimension, add_radial_dimension,
add_diametric_dimension, add_hatch, hatch_add_boundary, chamfer_polyline,
fillet_polyline, create_block, insert_block, set_dimension_text_override
```

Use direct MCP tools for valid CAD operations that are not yet executable in
CADPlan, such as advanced 3D solids, boolean operations, trimming/extending
where plan bindings are insufficient, layout tools, plotting/export tools, and
save/open tools.

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
  constraints, validation results, or visual exports.

## VLM Review And Grounding

Use this only for reviewing an existing/exported CAD view, not for the initial
external image trace:

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
- For image trace, `prepare_image_trace`, VLM `ImageDrawingSpec/v1`,
  `validate_image_drawing_spec`, `submit_image_drawing_spec`,
  `compile_image_spec_to_cad_plan`, and `validate_image_fidelity_contract`
  completed or any blocker is explicitly reported.
- Document info, active space, and units inspected when a DWG exists or is
  created.
- Existing drawing scanned and summarized when applicable.
- CAD-IR built without leaking scoped internal keys.
- Semantic objects detected for mechanical assemblies when applicable.
- Dimensions bound; constraints extracted/checked; uncertain dimension binding
  called out.
- Validation report generated and important issues handled or reported.
- VLM/export grounding used when visual confirmation matters.
- Intended edits executed by handle or CADPlan and followed by rescan.
- Image trace output visually diffed against the original image after
  execution when visual fidelity is the deliverable.
- Assembly standard module loaded for BOM, balloons, dimensions, sections, and
  notes.
- BOM quantities match visible repeated parts, blocks, arrays, or patterns.
- Balloons match BOM item numbers.
- Final export path, layout, key handles, validation evidence, fidelity
  warnings, and unresolved assumptions are included in the response.

## Recovery

- If AutoCAD is busy or COM rejects calls, stop the batch, wait for idle, and
  retry only the failed MCP tool once.
- If tools are missing or visual export/rendering is unavailable, run
  `check_runtime_environment` or `cad-mcp-doctor --check-autocad` and report the
  blocker instead of improvising external tools.
- If a tool prompts for input, stop and supply complete arguments or fix the
  wrapper.
- If image trace validation or fidelity fails, do not draw a lower-fidelity
  substitute. Fix the `ImageDrawingSpec`, add uncertainty, or ask the user to
  confirm the ambiguous feature.
- If VLM grounding is ambiguous, keep multiple candidate handles and ask for
  confirmation before editing.
- If CADPlan lacks an operation binding, use a direct MCP tool or extend the
  plan executor with tests before relying on it.
- Clean only known temporary MCP artifacts. Do not purge, erase, save over,
  close, or password-protect the user's drawing without explicit instruction.
