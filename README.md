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
