# best-cad-mcp

Comprehensive AutoCAD MCP server for Windows. It exposes high-level tools for
drawing, editing, querying, annotation, layout, plotting, metadata, 3D solids,
and CAD workflow guidance through the Model Context Protocol.

## Features

- AutoCAD COM automation through a single controller layer.
- 250+ MCP tools across drawing primitives, editing, dimensions, blocks,
  hatches, views, layouts, files, selection sets, metadata, and utilities.
- SQLite-backed CAD metadata indexing for scan and query workflows.
- Tool-selection guidance that encourages high-level CAD operations instead of
  primitive-only drafting.
- Optional Codex agent skills under `.agents/` for CAD operations and mainland
  China construction drafting workflows.

## Requirements

- Windows
- AutoCAD 2020+ recommended
- Python 3.11+
- MCP-compatible client

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

Run the server:

```powershell
python src\server.py
```

Or install the package locally:

```powershell
pip install -e .
cad-mcp
```

## MCP Client Configuration

Example client configuration:

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

Use an absolute path for `src/server.py` in your local checkout.

## Runtime Files

The server can create runtime files such as `cad_mcp.log` and
`autocad_data.db` in the MCP client's working directory. These files are ignored
by Git and should not be committed.

## Development

Run the test suite:

```powershell
python -m pytest
```

The tests focus on interface coverage and module wiring. Some runtime behavior
requires a local AutoCAD installation.

## License

MIT. See [LICENSE](LICENSE).
