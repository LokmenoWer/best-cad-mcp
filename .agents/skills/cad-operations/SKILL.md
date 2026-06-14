---
name: cad-operations
description: Operate AutoCAD through the updated best-cad-mcp server using its full 256-tool surface instead of primitive-only drafting. Use whenever Codex needs to create, open, inspect, query, edit, draw, dimension, annotate, hatch, block, xref, plot, export, model in 3D, manage metadata, or produce mainland China constructible CAD/DWG/DXF drawings through this MCP. Routes every CAD intent to the highest-level purpose-built tool, including Chinese construction layers/fonts when required.
---

# CAD Operations (best-cad-mcp)

Use this skill as the primary operating manual for AutoCAD work in this repo. The server exposes
256 registered tools across drawing, editing, querying, annotation, 3D, plotting, metadata, and
construction-document workflows.

## The Prime Directive

Before composing geometry by hand, ask: "Is there a tool for this exact CAD intent?"

Use the named tool first. `draw_line`, `draw_circle`, `draw_polyline`, and `draw_point` are basic
geometry tools, not substitutes for rectangles, walls, arrays, dimensions, hatches, tables, blocks,
viewports, or 3D solids.

If the next step is not obvious, call or consult:

- `recommend_cad_tools(intent)` for natural-language routing.
- `get_tool_help(tool_name)` for tool-specific help.
- `references/TOOL-MAP.md` for the current grouped catalog.

## Mandatory Workflow

1. **Open or create**: Start with `create_new_drawing` or `open_drawing`. Use `get_document_info`, `get_active_space_info`, and `get_variable` for units/state.
2. **Survey existing drawings first**: Run `scan_all_entities`, then `get_entity_statistics`. Query with `execute_query`; use `get_entity_topology` or `get_topology_summary` for geometric reasoning.
3. **Plan standards before drawing**: Create layers, load linetypes, choose text and dimension styles, and set output/layout assumptions. For mainland China construction drawings, read `references/CN-CONSTRUCTION.md`.
4. **Draw with high-level tools**: Use shape, hatch, block, array, dimension, table, viewport, and solid tools directly. Do not fake those objects with lines and text.
5. **Edit by handle**: Capture every returned handle. Use `move_entity`, `rotate_entity`, `offset_entity`, `trim_entity`, `extend_entity`, `set_entity_properties`, and related edit tools. Do not delete-and-redraw to simulate edits.
6. **Annotate as CAD objects**: Dimensions use `add_*_dimension` or `add_qdim`; leaders use `add_mleader`; schedules use `add_table`; materials use `add_hatch`.
7. **Use reuse mechanisms**: Repeated content becomes a block, block attribute, MInsert, rectangular array, or polar array.
8. **Verify**: Re-scan after meaningful edits, check layer/type counts, run `zoom_extents`, inspect properties, run `audit_drawing` when appropriate, and verify plot/export state.
9. **Save/export**: Use `save_drawing`, `export_pdf`, `export_dxf`, `plot_to_file`, or `plot_to_device` only after the model and layout checks pass.

## Quick Routing

| Intent | Use |
|---|---|
| Discover current capabilities | `recommend_cad_tools`, `get_tool_help`, `references/TOOL-MAP.md` |
| Inspect existing DWG | `scan_all_entities`, `get_entity_statistics`, `execute_query`, `highlight_query_results` |
| Rectangle/square | `draw_rectangle`, not four `draw_line` calls |
| Regular polygon | `draw_polygon`, not N line segments |
| Wall/parallel route | `draw_mline`, not two offset lines |
| Smooth curve | `draw_spline`, not many short segments |
| Polyline arc/taper | `polyline_set_bulge`, `polyline_set_width` |
| Repeated objects | `create_block` plus `insert_block`, `array_rectangular`, `array_polar`, or `insert_minsert_block` |
| Hatch/material fill | `add_hatch` plus `hatch_add_boundary`; never hand-draw hatch strokes |
| Dimensioning | `add_linear_dimension`, `add_qdim`, `add_baseline_dimension`, `add_continue_dimension`, etc. |
| Callouts and notes | `add_mleader`, `draw_mtext`, `add_table` |
| 3D primitive/profile | `draw_box`, `draw_cylinder`, `add_region`, `extrude_region`, `revolve_region`, `solid_boolean` |
| Layout, viewport, plotting | `create_layout`, `add_viewport`, `set_viewport_properties`, `export_pdf`, `plot_to_file` |
| Metadata and review | `set_xdata`, `get_xdata`, `add_hyperlink`, `create_snapshot`, `get_file_dependencies` |

## Strong Anti-Patterns

- Drawing rectangles, tables, hatches, dimensions, arrows, or repeated symbols from raw lines.
- Repeatedly calling `copy_entity` in a loop where `array_rectangular`, `array_polar`, or `insert_minsert_block` fits.
- Writing measured dimension values as text without creating dimension entities.
- Drawing 3D objects as wireframe cages when solid tools exist.
- Coloring objects individually instead of assigning them to a styled layer.
- Editing existing drawings without scanning and querying handles first.
- Using `send_command` before checking the MCP catalog.
- Claiming construction compliance without standards, project assumptions, and "to confirm" items.

## Reference Routing

- `references/TOOL-MAP.md`: read when selecting a tool, checking new server capabilities, or avoiding primitive misuse.
- `references/WORKFLOWS.md`: read for end-to-end patterns: existing drawing edits, construction plans, MEP routing, mechanical parts, hatch, 3D, and plotting.
- `references/CN-CONSTRUCTION.md`: read whenever the user asks for Chinese layers, mainland China standards, constructible drawings, construction details, architectural/structural/MEP/fire drawings, or local-font-first output.

## Escape Hatches

Use `send_command` only when no registered MCP tool can express the operation and the raw AutoCAD command is known. Prefer `recommend_cad_tools` and `get_tool_help` first.

Compose primitives only after confirming no dedicated tool fits. If the composed result will be reused, immediately turn it into a block with `create_block`.

## Completion Standard

Report the drawing opened/created, major layers/styles used, key handles or query strategy, verification steps, saved/exported files, and unresolved assumptions. For construction drawings, also report code/standard assumptions and all "待确认" items.
