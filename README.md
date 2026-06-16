# best-cad-mcp

`best-cad-mcp` is a Windows AutoCAD MCP server for agents that need to work on
real drawings: scan them, understand them, edit by handle, validate geometry,
ground visual findings, and export deliverables.

[中文说明](README.zh-CN.md)

## Highlights

- 260+ specialized AutoCAD tools for drawing, editing, layers, blocks,
  attributes, hatches, dimensions, tables, layouts, plotting, 3D solids,
  metadata, and workflow guidance.
- Handle-first workflows: scan the drawing, query structured metadata, then edit
  the exact AutoCAD handles returned by the scan.
- Workspace-scoped SQLite metadata for multi-drawing, multi-turn, and
  multi-thread agent sessions.
- CAD Understanding Layer with CAD-IR, semantic objects, constraints, validation
  reports, and resource endpoints.
- VLM grounding from exported view pixels or overlay IDs back to likely
  AutoCAD handles.
- Guarded CADPlan validation, static dry-run, and explicit execution gates for
  multi-step drawing or repair.
- Model-private spatial annotations stored in SQLite, not in hidden DWG layers,
  helper labels, XData, or blocks.
- Built-in tool recommendations so agents choose high-level CAD operations
  instead of rebuilding rectangles, arrays, dimensions, hatches, or blocks from
  primitives.

## Requirements

- Windows
- AutoCAD 2020+ recommended
- Python 3.11+
- An MCP-compatible client
- AutoCAD available through Windows COM automation

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Quick Start

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run from source:

```powershell
python src\server.py
```

Or install the console command:

```powershell
pip install -e .
cad-mcp
```

## MCP Client Configuration

After `pip install -e .`:

```json
{
  "mcpServers": {
    "CAD": {
      "command": "cad-mcp"
    }
  }
}
```

From a source checkout:

```json
{
  "mcpServers": {
    "CAD": {
      "command": "python",
      "args": ["C:/path/to/best-cad-mcp/src/server.py"]
    }
  }
}
```

Start the MCP client from the workspace directory whose runtime metadata should
be used. You can also set `CAD_MCP_WORKSPACE_ROOT`, `CAD_MCP_WORKSPACE_ID`,
`CAD_MCP_CONVERSATION_ID`, `CAD_MCP_THREAD_ID`, and drawing-specific
environment variables before launch.

## Workspace Database

Runtime metadata is stored by default at:

```text
<workspace>/.cad_mcp/workspace.db
```

The database scopes metadata by:

- `workspace`: the shared project directory.
- `drawing`: each DWG keeps separate entity, layer, block, topology, view, and
  query data so identical handles in different drawings do not collide.
- `conversation`: one multi-turn client session.
- `thread`: parallel agent threads with isolated private annotations and query
  history.

MCP tools and scoped SQL views return native AutoCAD handles and names even when
the physical SQLite keys are internally scoped.

Useful context tools:

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`

## Agent Workflow

For an existing DWG:

1. `open_drawing` when the user provides a path.
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`.
3. `build_drawing_ir`.
4. `summarize_drawing`.
5. `detect_semantic_objects(domain="mechanical")` or another suitable domain.
6. `extract_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=True)` when visual review is
   useful.
9. `explain_entity(handle)` before precise edits.
10. Edit by handle or through a validated and dry-run CADPlan.
11. Rescan, validate, visually confirm, then save or export.

For a new drawing:

1. `create_new_drawing`.
2. Create layers, text styles, dimension styles, and layout context as needed.
3. Build a CADPlan from high-level operations.
4. `validate_cad_plan`, then `dry_run_cad_plan`.
5. `execute_cad_plan(..., allow_modify=True)` only after modification is
   authorized.
6. `scan_all_entities`, `validate_geometry`, and export a review image.
7. Save or export the final DWG/PDF/DXF/DWF.

## CAD Understanding Layer

Understanding tools return structured `ToolResult` dictionaries:

```json
{
  "ok": true,
  "message": "",
  "data": {},
  "handles": [],
  "warnings": [],
  "next_tools": []
}
```

Read-only understanding tools do not modify the DWG. Semantic objects,
constraints, validation reports, view snapshots, and VLM mappings are stored in
the workspace SQLite database.

Key tools:

- `build_drawing_ir`: build a JSON CAD intermediate representation with native
  handles, entities, layers, blocks, topology, semantics, constraints,
  validation, and views.
- `summarize_drawing`: summarize drawing intent, entity mix, layers, blocks,
  warnings, and suggested next tools.
- `find_entities_by_description`: find handles by type, layer, text, block
  content, annotation, bbox position, or simple geometry words.
- `explain_entity`: inspect one handle with nearby entities, topology,
  dimensions, annotations, and semantic guess.
- `detect_semantic_objects`: write rule-based semantic objects to SQLite.
- `get_semantic_graph` / `find_semantic_objects`: inspect semantic IDs,
  handles, evidence, confidence, and relations.
- `extract_drawing_constraints`, `check_drawing_constraints`,
  `get_drawing_constraints`: manage measured and inferred constraints.
- `validate_geometry` / `get_validation_report`: report issues with severity,
  handles, evidence, repair hints, and suggested tools.
- `propose_repair_plan`: create a non-executing repair plan.
- `list_cad_resources` / `get_cad_resource`: reuse current CAD-IR, summaries,
  topology, semantic graph, constraints, validation report, and tool guide.

## CADPlan

CADPlan is the guarded path for multi-step drawing or repair. It is especially
useful when a change touches multiple entities or needs a dry-runable plan.

Required sequence:

1. Build the plan.
2. `validate_cad_plan(plan)`.
3. `dry_run_cad_plan(plan)`.
4. Get explicit modification permission when needed.
5. `execute_cad_plan(plan, allow_modify=True)`.
6. Rescan, validate, and visually confirm.

Plan shape:

```json
{
  "plan_id": "mounting-plate",
  "description": "Draw a plate with four mounting holes",
  "units": "mm",
  "risk_level": "low",
  "requires_confirmation": true,
  "steps": [
    {
      "step_id": "layer",
      "op": "create_layer",
      "args": {"name": "M-PART", "color": 1},
      "writes": true
    },
    {
      "step_id": "outline",
      "op": "draw_rectangle",
      "args": {"corner1": [0, 0, 0], "corner2": [120, 80, 0], "layer": "M-PART"},
      "writes": true,
      "depends_on": ["layer"]
    }
  ],
  "constraints": [
    {"type": "distance", "expected": 120.0}
  ]
}
```

Executable CADPlan operations currently include:

```text
draw_line, draw_circle, draw_rectangle, draw_polyline, draw_polygon,
draw_text, draw_mtext, move_entity, rotate_entity, copy_entity,
delete_entity, delete_entities, scale_entity, mirror_entity, offset_entity,
array_rectangular, array_polar, set_entity_properties, create_layer,
set_current_layer, add_linear_dimension, add_radial_dimension,
add_diametric_dimension, add_hatch, hatch_add_boundary, create_block,
insert_block
```

Use direct MCP tools for operations that are valid CAD actions but not yet bound
inside the CADPlan executor, such as `draw_donut`, `draw_box`, `solid_boolean`,
`trim_entity`, `extend_entity`, `fillet_entities`, `chamfer_entities`,
`add_table`, `edit_table_cell`, `add_mleader`, layout tools, and plotting tools.

`send_command`, SQL mutation, purge, and audit are disallowed by default in
CADPlan validation.

## Visual Grounding

`export_view_image_with_mapping(include_overlay=True)` creates:

- a clean view export,
- an optional overlay image with numeric IDs,
- a sidecar JSON mapping pixels, view parameters, visible handles, and entity
  screen boxes.

When a VLM reports a pixel bbox, call `ground_vlm_region(snapshot_id, bbox)` to
rank likely handles by overlap and distance. Then call `explain_entity` on the
best candidates before editing.

The first mapper is most reliable for top/plan views. Twisted, UCS, and 3D
views return warnings and should be treated as approximate.

## Tool Selection Guidance

Prefer the named high-level tool when one exists:

- Rectangles, polygons, donuts, splines, multilines, arrays, blocks, hatches,
  dimensions, leaders, tables, fillets, chamfers, trims, offsets, and 3D solids
  should use their specific tools.
- Use `draw_line`, `draw_circle`, and `draw_polyline` only for simple geometry
  or when no more specific tool fits.
- Use `send_command` only as a last resort and only when the user explicitly
  accepts the risk.
- Use `create_text_style` for text style setup. It supports TrueType typefaces
  through AutoCAD `SetFont` and SHX/TTF/OTF/TTC font files through `FontFile`.

## Assembly Drawing Guidance

Use `.agents/skills/draw-assembly-diagrams` for agent-facing assembly workflows.
That skill covers:

- assembly drawing requirements,
- BOM and item-numbering rules,
- CADPlan generation and repair,
- VLM grounding,
- handle-based editing,
- final validation and export checklists.

For mechanical assemblies, prefer:

- `draw_rectangle` for plates and rectangular parts,
- `draw_polygon` for regular forms,
- `draw_donut` for washers/gaskets/rings,
- blocks and arrays for repeated parts,
- true dimension entities for dimensions,
- `add_table` and `edit_table_cell` for BOMs,
- `add_mleader` or consistent balloon blocks for callouts.

## Runtime Files

The server may create:

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

These are runtime artifacts and should not be committed.

## Development

Run unit tests:

```powershell
python -m pytest
```

Run the AutoCAD smoke verifier against registered MCP tools:

```powershell
python scripts\verify_autocad_mcp_tools.py
```

Unit tests mock COM-dependent modules and do not require AutoCAD. Runtime smoke
verification requires a local AutoCAD COM session.

## Acknowledgements

The model-private annotation and pointer-style workflow is conceptually
informed by the public Pointer-CAD project and paper:
https://github.com/Snitro/Pointer-CAD

No Pointer-CAD source code is copied into this repository.

## License

MIT. See [LICENSE](LICENSE).
