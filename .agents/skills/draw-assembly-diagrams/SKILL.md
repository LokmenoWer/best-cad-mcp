---
name: draw-assembly-diagrams
description: >-
  Draw standards-aware mechanical assembly drawings, assembly diagrams, exploded views,
  sectioned assemblies, BOM/parts lists, balloons/item numbers, title blocks, assembly
  dimensions, and technical notes in AutoCAD through best-cad-mcp. Use when Codex needs
  to create, revise, inspect, verify, save, or export DWG/DXF/PDF/DWF/visual-review
  assembly drawings, including Chinese zhuangpeitu, mingxilan, xuhao, BOM, balloons,
  parts lists, model-private CAD annotations, visual verification, or exploded view
  requests. Enforces verified best-cad-mcp safety rules: use the existing AutoCAD
  session, prefer high-level MCP tools, avoid interactive/modal/destructive tools unless
  explicitly approved, verify by handles and exported view images when useful, and keep
  model-only spatial labels out of the drawing space.
---

# Draw Assembly Diagrams

Use this skill to create, revise, verify, and export assembly drawings through best-cad-mcp without disturbing the user's AutoCAD session. Work in small checked phases, keep handles, and prefer named MCP tools over raw commands.

Read `references/assembly-drawing-requirements.md` before creating a new assembly drawing, BOM, item-numbering scheme, sectioned view, exploded view, or standards-compliance claim.

## Safety Rules

- Use the existing best-cad-mcp AutoCAD connection. Do not start a new AutoCAD process.
- If AutoCAD is open only at the Start tab, `open_drawing` may open a DWG because it no longer requires an active document. If AutoCAD is unavailable or COM rejects `Documents.Open`, report that explicitly instead of pretending the drawing is open.
- Do not bypass MCP with standalone Python COM drawing scripts. If a needed operation is missing or broken, fix the MCP tool and then call it through MCP.
- Stop if AutoCAD becomes busy, rejects COM calls, or a tool times out. Confirm idle state before any retry.
- Avoid tools that need screen picks, open command prompts, modal dialogs, or destructive global state unless the user explicitly requested that exact action.
- Track handles for every meaningful entity, block, solid, dimension, hatch, table, balloon, viewport, and exported artifact.
- Verify after each phase with `scan_all_entities`, `get_entity_statistics`, targeted `execute_query`, `get_entity_properties`, topology tools, and `export_view_image` when visual confirmation would help.

## Vision Verification

Vision-capable models may inspect the current CAD state at any time:

1. Use `export_view_image(filepath=None, zoom_extents_first=False)` to export the current AutoCAD view as a model-facing review artifact. When no path is supplied, the MCP writes a timestamped WMF under `cad_visual_exports/`.
2. Use `zoom_extents_first=True` only when the whole drawing should be framed before export. Otherwise preserve the user's current view.
3. Treat the export as review output only. Do not create helper geometry, hidden layers, XData, blocks, or labels in the DWG just so the model can see or remember something.
4. If a raster PNG/JPG is required, say that this MCP's reliable COM image path is WMF; use `export_pdf` plus external rendering where available.
5. After visually inspecting an export, record important handles, regions, or assumptions in the response or with model-private spatial annotations.

## Model-Private Spatial Annotations

Use the Pointer-CAD idea of explicit references to geometry, but keep the references in MCP metadata rather than the user's drawing.

- Use `add_spatial_annotation` to label an entity handle, derived primitive key, point, bbox, area, view, or group with a model-only label such as `base plate`, `bolt-hole pattern`, `target face`, or `section cut region`.
- Use `list_spatial_annotations` to recover those labels after scans or long multi-step work.
- Use `clear_spatial_annotations` only to remove model-private context. It does not erase DWG entities.
- `scan_all_entities(clear_db=True)` preserves model-private annotations by default. Pass `clear_annotations=True` only when stale model memory should be discarded.
- Do not use visible text, nonplot layers, XData, extension dictionaries, groups, or blocks for model memory unless the user explicitly wants persistent CAD metadata in the DWG.

## Verified Boundaries

Default-safe tool families include drawing primitives, layers, filtered text search/replace, tables, blocks, most dimensions, hatches except gradient, query/database tools, noninteractive selections, views/layouts/viewports, solids, materials, UCS/named views, hyperlinks, XData, preferences, `export_pdf`/`export_dxf`/`export_dwf`, WMF `export_image`, and `export_view_image`.

Avoid by default:

- Lifecycle/security: `create_new_drawing`, `save_drawing`, `close_drawing`, `restart_mcp`, `set_drawing_password`. Use `open_drawing` only when opening a user-specified DWG is the task.
- Interactive or command-state-sensitive: `send_command`, `select_on_screen`, `break_entity`, `stretch_entities`, `lengthen_entity`, `align_entities`, `add_baseline_dimension`, `add_continue_dimension`, `undo`, `redo`.
- Modal plotting: `plot_to_device`, `plot_to_file`, `plot_preview`.
- Destructive global state: `purge_drawing`, `delete_selection_set`, `erase_selection_entities`.
- Preconditions/version-sensitive: `add_shape`, `set_entity_plot_style`, `unload_xref`, `reload_xref`, `hatch_set_gradient`.

Large drawing rules:

- Use `isolate_layer` and `unisolate_layers`; do not hand-roll full layer-table loops.
- Use `find_text` and `replace_text`; they use filtered text selection. Do not scan all `ModelSpace.Item(i)` entries for text.
- `select_all` may return a handle sample instead of creating a huge global selection set. Use area/window/crossing/query tools for precise bulk operations.
- Use `get_xrefs` for xref listing instead of filtering `get_all_blocks`.
- For paper-space work, create or obtain a real viewport handle with `add_viewport` or `get_viewports` before `set_viewport_properties`.
- Prefer `export_pdf` for print review, `export_view_image` for visual-model inspection, `export_dxf` for exchange, and `export_dwf` only when a DWF deliverable is needed.

## Workflow

1. Clarify deliverable: outline assembly, sectioned assembly, exploded view, installation sheet, repair sheet, or subassembly.
2. Inspect state: `get_document_info`, `get_active_space_info`, `get_variable("INSUNITS")`; for existing DWGs, run `scan_all_entities(clear_db=True)` and `get_entity_statistics`.
3. For unclear existing drawings, combine SQL/topology inspection with `export_view_image`; then add model-private labels for important handles or regions.
4. Plan sheet: units, sheet size, scale, projection method, view set, title block, BOM location, layers, text style, dimension style, and assumptions.
5. Build a component register before geometry: item number, part code, name, quantity, material/spec, standard/purchased/custom status, drawing/detail reference, and notes.
6. Draw in batches with high-level tools, record handles, then verify before continuing.
7. Add BOM, balloons, leaders, dimensions, hatches, and notes only after the component register and views are stable.
8. Layout with viewports, lock viewport display, audit, export, and report verification evidence.

## Assembly Tool Choices

- Plates and rectangular parts: `draw_rectangle`; regular nuts/forms: `draw_polygon`; washers/gaskets: `draw_donut`.
- Profiles: `draw_polyline`, bulge/width tools, or `draw_spline`.
- Repeated parts: `create_block`, `insert_block`, `insert_block_with_attributes`, `array_rectangular`, `array_polar`, `insert_minsert_block`.
- 3D forms: `draw_box`, `draw_cylinder`, `draw_torus`, `add_region`, `extrude_region`, `extrude_region_along_path`, `revolve_region`, `solid_boolean`, `check_interference`.
- Sections: `add_hatch`, `hatch_add_boundary`, `hatch_add_inner_loop`, `hatch_set_properties`; do not use gradient hatches by default.
- Edits by handle: `move_entity`, `rotate_entity`, `offset_entity`, `mirror_entity`, `trim_entity`, `extend_entity`, `fillet_entities`, `chamfer_entities`.
- Dimensions: use dimension entities, not plain text. Prefer `add_linear_dimension`, `add_rotated_dimension`, `add_radial_dimension`, `add_diametric_dimension`, `add_angular_dimension`, `add_3point_angular_dimension`, and `add_qdim`.

## BOM And Balloons

Create the parts list with `add_table`; fill it with `edit_table_cell`. Minimum columns: item, part/reference number, description/name, quantity, material/specification, and notes. For Chinese/GB-style deliverables, use item number, drawing/standard code, name, quantity, material, weight, and remarks where appropriate.

Use `add_mleader` for item callouts. If circular balloons are required and no dedicated balloon tool exists, draw one consistent circle/text/leader unit and group or block it. Keep leaders outside part outlines where possible, avoid crossings, and ensure every balloon number maps to exactly one BOM row.

Before dimensioning, verify:

- Every BOM row has a visible part or subassembly representation.
- Every balloon item number exists in the BOM.
- Identical reused parts share one item number.
- BOM quantities match block/array/pattern counts.

## Layout And Export

Use layouts and viewports instead of scaling model geometry to paper:

1. `create_layout` or `set_active_layout`.
2. Draw or insert title block and frame.
3. `add_viewport`.
4. `set_viewport_properties(display_locked=True, custom_scale=...)`.
5. Check `get_plot_devices`, `get_plot_style_tables`, and `get_plot_configurations`.
6. `zoom_extents`, `audit_drawing`, and `export_view_image` when a visual model should confirm the sheet.
7. Export with `export_pdf`, `export_dxf`, `export_dwf`, or WMF `export_image`.

## Recovery

- If a tool times out, assume AutoCAD may still be waiting or plotting. Stop the batch, check idle state, and retry only the failed MCP tool once.
- If COM calls are rejected, wait for idle and confirm document/handles before retrying.
- If a wrapper asks AutoCAD to prompt for input, stop and supply complete arguments or fix the wrapper.
- Clean up only temporary MCP artifacts with known `MCP_` prefixes. Do not purge, erase, close, save over, or password-protect the user's drawing without explicit instruction.

## Completion Report

Report the active document/layout, drawing/export paths, view types, layers/styles, BOM columns, item count, key handles or handle-register location, model-private spatial annotations used, visual export paths inspected, verification calls, skipped/risky tools avoided, and unresolved assumptions.
