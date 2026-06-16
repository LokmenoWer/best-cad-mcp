# best-cad-mcp

Comprehensive Windows AutoCAD MCP server for agents that need to work on real
drawings, not just call a handful of primitive drawing commands.

[中文说明](README.zh-CN.md)

## Why This MCP Is Different

Many CAD MCP servers stop at simple tools such as `draw_line`, `draw_circle`,
or raw command forwarding. `best-cad-mcp` is built for multi-step CAD work:

- 260+ purpose-built AutoCAD tools for drawing, editing, dimensions, blocks,
  hatches, layouts, plotting, metadata, 3D solids, and workflow guidance.
- Handle-based editing workflows: scan first, query structured metadata, then
  edit the exact entities returned by AutoCAD.
- Workspace-aware SQLite metadata storage for multi-drawing, multi-turn, and
  multi-thread agent sessions.
- Scoped SQL views: `execute_query("SELECT * FROM cad_entities")` returns only
  the active workspace/drawing/thread context while still exposing native
  AutoCAD handles.
- Model-private spatial annotations for pointer-style references such as
  important parts, points, faces, bounding boxes, or semantic regions. These
  annotations stay in SQLite and never pollute the DWG with helper layers,
  XData, or visible labels.
- Derived topology tables for points, lines, curves, surfaces, solids, and
  relationships, so agents can reason over geometry instead of parsing raw COM
  fields repeatedly.
- Visual verification through `export_view_image`, which writes a review
  artifact without modifying the drawing.
- Built-in tool-selection guidance that steers agents toward high-level CAD
  operations instead of rebuilding rectangles, arrays, dimensions, blocks, or
  hatches from primitives.

## Workspace Database Architecture

Runtime metadata is stored by default in:

```text
<workspace>/.cad_mcp/workspace.db
```

The database tracks four scopes:

- `workspace`: the project directory shared by one or more agent sessions.
- `drawing`: each DWG has its own entity/layer/block/query scope, so identical
  AutoCAD handles in different drawings do not collide.
- `conversation`: multi-turn context for a client session.
- `thread`: parallel agent threads can share the same workspace database while
  keeping model-private annotations and query history isolated.

Internally, physical SQLite keys are scoped. Externally, MCP tools and scoped
SQL views still return native AutoCAD handles and names. This keeps existing
handle-based workflows compatible while making the database safe for multiple
drawings and threads.

Useful context tools:

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`

## Requirements

- Windows
- AutoCAD 2020+ recommended
- Python 3.11+
- MCP-compatible client
- AutoCAD accessible through Windows COM automation

Install Python dependencies:

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

Run the server directly:

```powershell
python src\server.py
```

Or install the console script:

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

Run the MCP client from the workspace directory you want to use for runtime
metadata. You can also set `CAD_MCP_WORKSPACE_ROOT`, `CAD_MCP_WORKSPACE_ID`,
`CAD_MCP_CONVERSATION_ID`, `CAD_MCP_THREAD_ID`, or drawing-specific environment
variables before launch.

## Typical Agent Workflow

1. Open or create a drawing with `open_drawing` or `create_new_drawing`.
2. Run `scan_all_entities` on existing drawings. The default scan is large-drawing
   friendly (`detail_level="minimal"`) and still writes topology summaries
   (`topology_detail="summary"`).
3. Use `get_entity_statistics`, `execute_query`, `get_entity_topology`, or
   `get_topology_summary` to understand geometry.
   Use `scan_all_entities(topology_detail="full")` when primitive/relation
   topology is needed for detailed geometric reasoning.
4. Edit by handle with purpose-built tools such as `move_entity`,
   `array_rectangular`, `fillet_polyline`, `add_qdim`, `insert_block`,
   `add_hatch`, or `solid_boolean`.
5. Store model-only context with `add_spatial_annotation` when the agent needs
   to remember a part, region, edge-like primitive, point, or target face.
6. Use `export_view_image` when visual verification is useful.
7. Save or export the final DWG/PDF/DXF/DWF as needed.

## CAD Understanding Layer

`best-cad-mcp` now includes an additive CAD Understanding Layer on top of the
existing AutoCAD COM controller and scanned SQLite metadata. New understanding
tools return structured `ToolResult` dictionaries:

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

Understanding, search, validation, grounding, and dry-run tools do not modify
the DWG. Semantic objects, constraints, validation reports, and view snapshots
are stored in SQLite using the same workspace/drawing/conversation/thread
scope as the existing metadata.

## CAD-IR Schema

Use `build_drawing_ir` to build a JSON CAD intermediate representation with:

- Drawing metadata: `drawing_id`, name, path, units, extents, counts.
- `entities`: native AutoCAD handles, object/type/layer/color/linetype,
  visibility, bounding boxes, geometry, properties, topology refs, semantic
  tags, source, and confidence.
- `layers` and `blocks`: scoped layer and block summaries.
- `topology`: primitive, relation, and summary rows derived from scanned
  geometry.
- `semantic_objects`, `semantic_relations`, `constraints`, `validation`, and
  `views`: cached understanding outputs.

The IR exposes native handles for agent use and avoids leaking scoped internal
SQLite keys.

## Semantic Graph And Constraints

`detect_semantic_objects` is deterministic and rule-based in this first
version. It can identify closed profiles, circle features or holes, repeated
circle patterns, hatches or section regions, dimension annotations, text
annotations, block instances, and domain-specific candidates for mechanical,
architecture, and electrical drawings.

`extract_drawing_constraints` writes constraints to SQLite, including radius,
diameter, line distance, parallel, perpendicular, concentric, coincident
endpoint, closed profile, repeated pattern count, and scanned dimension
constraints. Dimension constraints remain `status="unknown"` when binding the
dimension entity to measured geometry is ambiguous.

## Validation Report

`validate_geometry` creates a structured report with issue IDs, severity,
handles, evidence, repair hints, and suggested tools. Initial checks cover
zero-length lines, duplicate entities, tiny endpoint gaps, unclosed polylines,
overlapping lines, missing dimension candidates, dimension mismatches,
empty layers, out-of-extents geometry, and empty or unresolved blocks.

`propose_repair_plan` returns a plan only; it does not execute changes.

## VLM Visual Grounding Workflow

`export_view_image_with_mapping` extends the existing visual export workflow by
writing a sidecar JSON mapping between view pixels, world coordinates, visible
handles, and entity screen bounding boxes. It can also create an overlay review
artifact with numeric IDs mapped back to handles.

Use `ground_vlm_region(snapshot_id, bbox)` when a vision model reports a pixel
region. The tool ranks likely AutoCAD handles using overlap and distance
against the mapped entity screen boxes.

Current mapping is most reliable for top/plan views. Rotated, twisted, UCS, and
3D views are handled as first-version approximations with warnings.

## Safe CAD Plan Workflow

The plan DSL supports `validate_cad_plan`, `dry_run_cad_plan`, and
`execute_cad_plan`.

- Unknown operations fail validation.
- `send_command` is disallowed by default.
- Dry-run never calls AutoCAD and never modifies the DWG.
- Execution refuses to run unless `allow_modify=True`.
- Execution routes through known existing MCP tool implementations.

## Recommended Understanding Workflows

For an existing DWG:

1. `open_drawing`
2. `scan_all_entities`
3. `build_drawing_ir`
4. `summarize_drawing`
5. `detect_semantic_objects`
6. `extract_drawing_constraints`
7. `validate_geometry`
8. `export_view_image_with_mapping`
9. `ground_vlm_region` if using a VLM
10. `propose_repair_plan`
11. `dry_run_cad_plan`
12. `execute_cad_plan` only with explicit modification permission

For generating a new drawing:

1. `create_new_drawing`
2. Create a `CADPlan`
3. `validate_cad_plan`
4. `dry_run_cad_plan`
5. `execute_cad_plan` with `allow_modify=True`
6. `scan_all_entities`
7. `validate_geometry`
8. `export_view_image_with_mapping`
9. Save or export

Example mechanical review:

1. `build_drawing_ir(rescan=True)`
2. `summarize_drawing(level="deep")`
3. `detect_semantic_objects(domain="mechanical")`
4. `extract_drawing_constraints`
5. `validate_geometry`
6. `export_view_image_with_mapping(include_overlay=True)`
7. VLM returns a bbox or overlay ID for a wrong hole
8. `ground_vlm_region`
9. `explain_entity`
10. `propose_repair_plan`
11. `dry_run_cad_plan`
12. `execute_cad_plan(..., allow_modify=True)`
13. `validate_geometry`

## Limitations

- Semantic detection is rule-based; no embedding search is implemented yet.
- View mapping is initially best for plan/top views.
- Dimension binding may be uncertain and should be interpreted through
  confidence, evidence, and `status`.
- Understanding tools operate on scanned metadata, so call `scan_all_entities`
  after external edits or plan execution.

## Runtime Files

The server may create these files under the active workspace:

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

These are runtime artifacts and should not be committed.

## Development

Run the test suite:

```powershell
python -m pytest
```

Run the AutoCAD smoke verifier against registered MCP tools:

```powershell
python scripts\verify_autocad_mcp_tools.py
```

The unit tests run without AutoCAD by mocking COM-dependent modules. Runtime
smoke verification requires a local AutoCAD COM session.

## Acknowledgements

The model-private annotation and pointer-style workflow is conceptually
informed by the public Pointer-CAD project and paper:
https://github.com/Snitro/Pointer-CAD

No Pointer-CAD source code is copied into this repository.

## License

MIT. See [LICENSE](LICENSE).
