# best-cad-mcp

Comprehensive AutoCAD MCP server for Windows. It exposes high-level tools for
drawing, editing, querying, annotation, layout, plotting, metadata, 3D solids,
and CAD workflow guidance through the Model Context Protocol.

## Features

- AutoCAD COM automation through a single controller layer.
- 260+ specialized MCP tools across drawing primitives, editing, dimensions, blocks,
  hatches, views, layouts, files, selection sets, metadata, and utilities.
- SQLite-backed CAD metadata indexing for scan and query workflows.
- Model-private spatial annotations for AI labels and pointer-style references
  that stay in SQLite and do not create visible DWG geometry, layers, or XData.
- Vision-model verification through `export_view_image`, which exports the
  current AutoCAD view as a review artifact without modifying the drawing.
- `open_drawing` can open a DWG even when AutoCAD is only showing the Start tab
  and no drawing document is active.
- Built-in tool-selection guidance that prefers high-level CAD operations over
  primitive-only drafting.
- Optional Codex agent skills under `.agents/` for assembly drawing workflows.

## Requirements

- Windows
- AutoCAD 2020+ recommended
- Python 3.11+
- MCP-compatible client
- An AutoCAD installation that is accessible through Windows COM automation

Python dependencies:

```powershell
pip install -r requirements.txt
```

## Quick Start

Clone the repository and install dependencies:

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

Or install the package locally:

```powershell
pip install -e .
cad-mcp
```

## MCP Client Configuration

After `pip install -e .`, the preferred client configuration can use the
console script:

```json
{
  "mcpServers": {
    "CAD": {
      "command": "cad-mcp"
    }
  }
}
```

You can also run the server from the source checkout:

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

Use an absolute path for `src/server.py` in your local checkout. Run the MCP
client from a working directory where it is acceptable for runtime files to be
created.

## Runtime Files

The server can create runtime files such as `cad_mcp.log` and
`autocad_data.db` in the MCP client's working directory. These files are ignored
by Git and should not be committed.

Visual review exports created by `export_view_image` default to
`cad_visual_exports/` in the MCP working directory. These are review artifacts
for the model or user and are not written into the DWG.

## Model-Private CAD Context

Run `scan_all_entities` to index drawing geometry into SQLite. The scan now
keeps model-private spatial annotations by default, so an AI model can label
important handles, primitives, points, bounding boxes, views, or groups across
multi-step work:

- `add_spatial_annotation` stores a hidden label such as `base plate`,
  `bolt-hole pattern`, or `target face`.
- `list_spatial_annotations` retrieves those labels for later reasoning.
- `clear_spatial_annotations` removes only the SQLite annotations and never
  erases AutoCAD entities.

Pass `clear_annotations=true` to `scan_all_entities` only when stale model
context should be discarded. The annotation system is inspired by pointer-based
CAD workflows: references are explicit and queryable, but they do not pollute
the user's drawing space.

## Visual Verification

Vision-capable models can call `export_view_image` whenever seeing the current
CAD view would reduce ambiguity. The reliable AutoCAD COM image path is WMF; if
a PNG or JPG is required, export PDF and render it externally.

## Development

Run the test suite:

```powershell
python -m pytest
```

The tests focus on interface coverage and module wiring. Some runtime behavior
requires a local AutoCAD installation.

For a real AutoCAD smoke run against registered MCP tools, use:

```powershell
python scripts\verify_autocad_mcp_tools.py
```

The verifier uses the existing AutoCAD COM session and skips risky, modal, or
interactive tools by default.

## License

MIT. See [LICENSE](LICENSE).
