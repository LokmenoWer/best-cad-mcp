---
name: draw-assembly-diagrams
description: >-
  Draw standards-aware mechanical assembly drawings, assembly diagrams, exploded views,
  sectioned assemblies, BOM/parts lists, balloons/item numbers, title blocks, assembly
  dimensions, and technical notes in AutoCAD through best-cad-mcp. Use when Codex needs
  to create, revise, inspect, verify, save, or export DWG/DXF/PDF assembly drawings,
  including Chinese zhuangpeitu, mingxilan, xuhao, BOM, balloons, parts list, or exploded
  view requests. Enforces verified best-cad-mcp safety rules: no new AutoCAD process,
  high-level tools first, no interactive/modal/destructive tools unless explicitly approved,
  handle-based verification, and phase-by-phase CAD checks.
---

# Draw Assembly Diagrams

Use this skill to create or revise assembly drawings through best-cad-mcp without breaking the user's active AutoCAD session. Work in small verified phases. Prefer named MCP tools over raw AutoCAD commands.

Read `references/assembly-drawing-requirements.md` before creating a new assembly drawing, BOM, item numbering scheme, sectioned view, or standards-compliance claim.

## Non-Negotiable CAD Safety

- Never start a new AutoCAD process to solve the drawing task. Use the existing best-cad-mcp connection only. If direct debugging is unavoidable, connect with `GetActiveObject("AutoCAD.Application")`; never use `Dispatch` or `DispatchEx` from agent-side scripts.
- Do not bypass MCP with standalone Python COM drawing scripts. If a needed operation is missing, add or fix an MCP tool in `src/cad_controller.py` / `src/cad_tools/*`, then call it through MCP.
- Stop immediately if AutoCAD becomes busy, rejects COM calls, or a tool times out. Do not keep sending commands into a possibly active command prompt.
- Do not use tools that require follow-up screen picking, crossing windows, command prompts, modal dialogs, or destructive global state unless the user explicitly approves that exact risk.
- Keep handle registers. Every meaningful entity, block, solid, dimension, hatch, table, balloon, and viewport must be tracked by handle after creation.
- Verify after each phase with `scan_all_entities`, `get_entity_statistics`, targeted `execute_query`, `get_entity_properties`, topology tools, and visual zoom/highlight where useful.

## Verified Tool Boundaries

Use these boundaries from real AutoCAD-backed validation.

Safe default tools: drawing primitives, layers, text, tables, blocks, most dimensions, hatches except gradient, query/database tools, noninteractive selection tools, view tools, solids, materials, UCS/views, hyperlinks, xdata, preferences, `export_pdf`, `export_dxf`, `export_dwf`, and `export_image` when using WMF-style output.

Do not run by default:

- Lifecycle/state tools: `create_new_drawing`, `open_drawing`, `save_drawing`, `close_drawing`, `restart_mcp`, `set_drawing_password`.
- Interactive tools: `send_command`, `select_on_screen`, `break_entity`, `stretch_entities`, `lengthen_entity`, `align_entities`, `add_baseline_dimension`, `add_continue_dimension`.
- Modal plotting tools: `plot_to_device`, `plot_to_file`, `plot_preview`.
- Destructive tools: `purge_drawing`, `delete_selection_set`, `erase_selection_entities`.
- Command-state tools: `undo`, `redo`.
- Preconditions/version-sensitive tools: `add_shape`, `set_entity_plot_style`, `unload_xref`, `reload_xref`, `hatch_set_gradient`.

If the user asks for one of those tools, state the risk and use it only after the request is explicit. Prefer a safer MCP alternative when one exists.

## Mandatory Workflow

1. Discover: call `recommend_cad_tools(intent)` when tool choice is unclear. Call `get_tool_help(tool_name)` before unfamiliar or risky tools.
2. Inspect state: call `get_document_info`, `get_active_space_info`, and `get_variable("INSUNITS")`.
3. Existing DWG: run `scan_all_entities(clear_db=True)` before edits, then `get_entity_statistics`; use SQL/topology queries to find handles.
4. New drawing content: set layers, linetypes, text styles, dimension styles, title/BOM space, and layout assumptions before detailed geometry.
5. Draw by intent: choose specific high-level tools first. Avoid rebuilding rectangles, polygons, arrays, hatches, dimensions, leaders, blocks, or transforms from low-level primitives.
6. Record handles: maintain a component register with item number, part name, qty, material/spec, layer, geometry handles, balloon handles, and BOM row.
7. Verify each phase before continuing.

## Assembly Drawing Procedure

### 1. Plan the Sheet

Decide deliverable type: outline assembly, sectioned assembly, exploded view, pictorial assembly, installation sheet, repair sheet, or subassembly. Define units, sheet size, scale, projection method, view set, title block, BOM position, and assumptions.

Create/check common layers:

- `ASM-OUTLINE`
- `ASM-HIDDEN`
- `ASM-CENTER`
- `ASM-HATCH`
- `ASM-DIMS`
- `ASM-TEXT`
- `ASM-BOM`
- `ASM-BALLOON`
- `ASM-TITLE`

Use `create_layer`, `load_linetype`, `create_text_style`, `get_dimension_styles`, `copy_dimension_style`, and `set_current_dimension_style`. Draw with `color="bylayer"` unless there is a clear reason.

### 2. Build the Component Register

Before geometry, list every distinct part and subassembly:

- Item number
- Part number/code/reference
- Name/description
- Quantity per assembly
- Material/specification
- Standard/purchased/custom status
- Required drawing/detail reference
- Notes such as fit, torque, lubricant, adhesive, or finish

Every unique part gets one item number. Identical parts use the same item number. Do not create balloons before the register exists.

### 3. Draw Components and Reuse Geometry

Create components in small batches. Capture handles after each component or repeated family.

Preferred tools:

- Plates and rectangles: `draw_rectangle`.
- Nuts and regular forms: `draw_polygon`.
- Smooth profiles: `draw_spline`, or `draw_polyline` plus bulge tools.
- Rings, washers, gaskets: `draw_donut`.
- Repeated parts: `create_block`, `insert_block`, `insert_block_with_attributes`, `array_rectangular`, `array_polar`, `insert_minsert_block`.
- 3D parts: `draw_box`, `draw_cylinder`, `draw_torus`, `add_region`, `extrude_region`, `extrude_region_along_path`, `revolve_region`, `solid_boolean`, `check_interference`.
- Sections: `add_hatch`, `hatch_add_boundary`, `hatch_add_inner_loop`, `hatch_set_properties`. Do not use `hatch_set_gradient` in default work.
- Edits: `move_entity`, `rotate_entity`, `offset_entity`, `mirror_entity`, `trim_entity`, `extend_entity`, `fillet_entities`, `chamfer_entities` by handle.

### 4. Compose Assembly Views

Place parts in operating position first. Add only the views needed to explain relationships: front/top/right, section view, local detail, exploded view, or installation view.

For sectioned assemblies, hatch adjacent cut parts with different angles/spacing. Avoid sectioning standard fasteners, pins, shafts, and similar standard parts unless the user or standard requires it. Use hidden lines only when they clarify the assembly.

After each view, query by layer/type and inspect questionable handles with `get_entity_properties`, `get_entity_topology`, or `get_topology_summary`.

### 5. Add BOM, Balloons, and Leaders

Create the parts list with `add_table`; fill cells with `edit_table_cell`. Minimum columns: item, part/reference number, description/name, quantity, material/specification, and notes. For Chinese/GB-style deliverables, use item number, drawing/standard code, name, quantity, material, weight, and remarks where appropriate.

Use `add_mleader` for item callouts. If circular balloons are required and no dedicated balloon tool exists, draw a consistent circle/text/leader unit and group or block it when useful. Keep balloons outside part outlines, align them cleanly, avoid crossing leaders, and ensure every balloon number matches exactly one BOM row.

Before dimensioning, cross-check:

- Every BOM row has a visible part or subassembly representation.
- Every balloon item number exists in the BOM.
- Identical reused parts share one item number.
- BOM quantities match block/array/pattern counts.

### 6. Add Dimensions and Technical Requirements

Dimension only assembly-relevant information: interfaces, installation, inspection, motion, clearance, overall size, machining-after-assembly, service access, or critical fit.

Use real dimension entities: `add_linear_dimension`, `add_rotated_dimension`, `add_radial_dimension`, `add_diametric_dimension`, `add_angular_dimension`, `add_3point_angular_dimension`, and `add_qdim`. Avoid `add_baseline_dimension` and `add_continue_dimension` unless explicitly approved because they may depend on command-state behavior. Never write measured dimensions as plain text. Use `get_dimension_measurement` for important dimensions.

Add notes with `draw_mtext` or `add_mleader`: fit/clearance, torque, weld/bond/lubricant, alignment, test, inspection, installation sequence, and references. Mark uncertain values as `TBD` or "to confirm".

### 7. Layout and Export

Use layouts and viewports instead of scaling model geometry to paper:

1. `create_layout` or `set_active_layout`.
2. Add title block and frame.
3. `add_viewport`.
4. `set_viewport_properties(display_locked=True, custom_scale=...)`.
5. Check `get_plot_devices`, `get_plot_style_tables`, and `get_plot_configurations`.
6. `zoom_extents` and `audit_drawing`.
7. Export with `export_pdf`, `export_dxf`, `export_dwf`, or `export_image`. Prefer `export_pdf` for review deliverables. Avoid modal plot tools unless explicitly requested.

## Recovery Rules

- If a tool times out, assume AutoCAD may still be waiting for command input. Stop the batch and inspect idle state before any further call.
- If AutoCAD is busy but later becomes idle, retry the specific MCP tool once after confirming the document and handles are still valid.
- If a tool requires missing parameters or screen selection, do not ask AutoCAD to prompt. Generate complete arguments or fix the MCP wrapper.
- Clean up only temporary MCP artifacts such as selection sets with known `MCP_` prefixes. Do not purge, erase, close, or save over the user's drawing without explicit instruction.

## Anti-Patterns

- Drawing a full assembly in one unverified pass.
- Using `send_command` as the first choice.
- Letting AutoCAD ask for a pick point, crossing window, object selection, plot device, or file dialog.
- Drawing BOMs from loose lines and text instead of `add_table`.
- Drawing dimensions, leaders, hatches, arrays, blocks, or transforms manually when named tools exist.
- Editing an existing DWG without scanning and handle-based queries.
- Running direct Python COM scripts as the drawing workflow.
- Making balloons before the register and BOM exist.
- Giving identical parts different item numbers.
- Over-dimensioning an assembly with part-detail manufacturing dimensions.

## Completion Report

When finished, report the drawing file, active document/layout, view types, layers/styles, BOM columns, item count, key handles or handle-register location, verification calls, export path, skipped/risky tools avoided, and unresolved assumptions.
