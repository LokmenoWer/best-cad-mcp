# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

`best-cad-mcp` is a Windows AutoCAD MCP server for agents that need to work with
real DWG projects. It exposes AutoCAD drawing, editing, inspection, metadata,
validation, export, visual grounding, and planning tools through the Model
Context Protocol, with a handle-first workflow designed for safe CAD automation.

[Chinese README](README.zh-CN.md)

## What It Does

Most CAD automation examples stop at drawing primitives. `best-cad-mcp` is built
for production-style agent workflows:

- inspect an existing drawing before changing it,
- scan entities, layers, blocks, topology, dimensions, and layouts into a local
  SQLite workspace database,
- explain and edit exact AutoCAD handles,
- build CAD-IR, semantic graphs, constraints, validation reports, and reusable
  resources,
- export clean and annotated views for visual review,
- ground VLM findings from pixels or overlay IDs back to candidate handles, and
- validate, dry-run, and explicitly execute multi-step CADPlans.

The server runs locally, talks to AutoCAD through Windows COM, and stores agent
metadata in the workspace instead of writing hidden helper geometry into the
DWG.

## Highlights

- **290+ MCP tools** for drawing, editing, layers, blocks, attributes, hatches,
  dimensions, tables, layouts, plotting, 3D solids, queries, metadata, and
  workflow guidance.
- **Handle-first editing**: scan first, query structured metadata, then edit the
  precise handles returned by AutoCAD.
- **CAD Understanding Layer** with CAD-IR, drawing summaries, semantic objects,
  semantic graphs, dimension binding, constraints, validation reports, and MCP
  resources.
- **Visual grounding** from exported view pixels, world coordinates, or overlay
  IDs back to likely AutoCAD handles.
- **Guarded CADPlan workflow** with validation, static dry-run, variables,
  `save_as` handle capture, dependencies, postconditions, transactional
  execution, and rollback attempts.
- **Workspace-scoped SQLite memory** for multi-drawing, multi-turn, and
  multi-thread sessions.
- **Model-private annotations** stored in SQLite, not in hidden DWG layers,
  XData, blocks, labels, or marks.
- **Prompt and skill assets** for repeatable drawing understanding, precise
  generation, visual review, repair, and modular assembly drawing standards.

## Requirements

- Windows
- AutoCAD 2020 or newer recommended
- Python 3.11 or newer
- An MCP-compatible client, such as Codex or Claude Code
- A local AutoCAD installation available through Windows COM automation

AutoCAD must be installed, licensed, and able to start on the same Windows
machine where the MCP server runs.

## Installation

Clone the repository and install dependencies:

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
```

Optional Python visual-review helpers can be installed with:

```powershell
python -m pip install -e ".[visual]"
```

Run the server from source:

```powershell
python -m src.server
```

Or run the installed console command:

```powershell
cad-mcp
```

The server is an MCP stdio process. In normal use, your MCP client starts it
automatically from its configuration.

## Runtime Preflight

After installation, run the doctor command:

```powershell
cad-mcp-doctor --check-autocad
```

The same check is also available as the MCP tool `check_runtime_environment`
before live CAD work:

```text
check_runtime_environment(check_autocad=true, require_visual_export=false)
```

The preflight reports required Windows/Python/package/workspace checks, optional
visual-review renderer availability, and live AutoCAD COM connectivity when
`check_autocad=true`. Agents should treat `ok=false` as a blocker and fix the
reported environment issue before drawing or editing.

For multi-step changes, CADPlan execution is fail-fast: if any bound tool raises,
returns `ok=false`/`success=false`, or returns a recognizable error message,
`execute_cad_plan` stops and attempts rollback instead of continuing with a
partial drawing.

Strict startup mode is available for deployments that should refuse to start
when required checks fail:

```powershell
$env:CAD_MCP_STRICT_PREFLIGHT = "1"
$env:CAD_MCP_PREFLIGHT_CHECK_AUTOCAD = "1"
$env:CAD_MCP_PREFLIGHT_REQUIRE_VISUAL = "0"
cad-mcp
```

`CAD_MCP_PREFLIGHT_REQUIRE_VISUAL=1` requires either a supported system renderer
such as ImageMagick/Inkscape/librsvg/Chrome/Edge or the optional Python visual
dependencies from `.[visual]`.

## MCP Client Configuration

Start the MCP client from the workspace whose metadata should be used. Runtime
data is stored under that workspace unless `CAD_MCP_WORKSPACE_ROOT` is set.

### Codex

This repository includes `.codex/config.toml` for project-scoped Codex usage.
After trusting the project, Codex can start the server from the checkout.

For a user-level Codex configuration after `pip install -e .`, add:

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "cad-mcp"
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

From a virtual environment checkout:

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
args = ["-m", "src.server"]
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

Keep raw or destructive tools on manual approval:

```toml
[mcp_servers.best-cad-mcp.tools.send_command]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.execute_cad_plan]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_entity]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_entities]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.erase_selection_entities]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_layer]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.purge_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.audit_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.save_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.close_drawing]
approval_mode = "prompt"
```

### Claude Code

This repository includes:

- `.mcp.json`, which registers the local stdio server as `best-cad-mcp`.
- `.claude/settings.json`, which enables the server and asks before raw or
  destructive tools.

The checked-in `.mcp.json` uses `CLAUDE_PROJECT_DIR`:

```json
{
  "mcpServers": {
    "best-cad-mcp": {
      "command": "python",
      "args": ["${CLAUDE_PROJECT_DIR:-.}/src/server.py"],
      "env": {
        "CAD_MCP_WORKSPACE_ROOT": "${CLAUDE_PROJECT_DIR:-.}",
        "PYTHONPATH": "${CLAUDE_PROJECT_DIR:-.}"
      }
    }
  }
}
```

If dependencies are installed only in the project virtual environment, change
`command` to:

```json
"C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
```

Use `claude mcp list` or `/mcp` inside Claude Code to confirm the server is
connected.

## First Workflows

### Inspect And Repair An Existing DWG

1. `open_drawing` when the user supplies a DWG path.
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`.
3. `build_drawing_ir`.
4. `summarize_drawing`.
5. `detect_semantic_objects(domain="mechanical")` or another suitable domain.
6. `bind_all_dimensions`, `extract_drawing_constraints`, and
   `check_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=True)` when visual evidence
   matters.
9. `ground_vlm_region` or `ground_vlm_overlay_id` for VLM findings.
10. `explain_entity(handle)` before editing.
11. Edit by handle or through a validated, dry-run CADPlan.
12. Rescan, validate, visually confirm, then save or export.

### Create A New Drawing

1. `create_new_drawing`.
2. Set layers, text styles, dimension styles, layout, and units.
3. Build a CADPlan with high-level operations, dependencies, `save_as`
   variables, and postconditions.
4. `validate_cad_plan`, then `dry_run_cad_plan`.
5. `execute_cad_plan(..., allow_modify=True)` only after modification is
   authorized.
6. `scan_all_entities`, `build_drawing_ir`, `validate_geometry`, and export a
   review image.
7. Save or export the final DWG/PDF/DXF/DWF deliverable.

## Core Concepts

### Workspace Database

Runtime metadata is stored by default at:

```text
<workspace>/.cad_mcp/workspace.db
```

The database scopes data by workspace, drawing, conversation, and thread. This
keeps identical handles in different drawings from colliding and lets parallel
agent sessions keep private annotations and query history separate.

SQL exposed through `execute_query` is read-only, scoped, and bounded. Use
public table names such as `cad_entities`; direct `main.<table>` access is
blocked so one workspace cannot bypass the scoped views. Result sets default to
1,000 rows, 5 seconds, and about 1 MB of JSON. Tune with the tool parameters or
`CAD_MCP_SQL_MAX_ROWS`, `CAD_MCP_SQL_TIMEOUT_MS`, and
`CAD_MCP_SQL_MAX_RESULT_BYTES`.

Useful workspace tools:

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`
- `get_database_maintenance_status`
- `maintain_database`
- `clear_understanding_cache`
- `get_legacy_database_status`

### CAD Understanding Layer

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
the workspace database.

`scan_all_entities(clear_db=True)` clears stale semantic objects, constraints,
validation reports, and view snapshots for the active thread by default. Pass
`clear_understanding=False` only when you intentionally want to keep cached
understanding artifacts across a rescan.

Key tools include:

- `build_drawing_ir` and `export_drawing_ir`
- `summarize_drawing`
- `find_entities_by_description`
- `explain_entity`
- `detect_semantic_objects`, `get_semantic_graph`, and `find_semantic_objects`
- `bind_dimension_to_geometry` and `bind_all_dimensions`
- `extract_drawing_constraints`, `check_drawing_constraints`, and
  `get_drawing_constraints`
- `validate_geometry` and `get_validation_report`
- `propose_repair_plan` and `propose_constraint_repair_plan`
- `list_cad_resources` and `get_cad_resource`

### CADPlan

CADPlan is the guarded path for multi-step drawing or repair. It is best for
changes that touch multiple entities, need reviewable intent, or should be
dry-run before execution.

```json
{
  "plan_id": "mounting-plate",
  "description": "Draw a plate with four mounting holes",
  "units": "mm",
  "risk_level": "low",
  "requires_confirmation": true,
  "variables": {"origin": [0, 0, 0]},
  "steps": [
    {
      "step_id": "plate",
      "op": "draw_rectangle",
      "args": {"corner1": "$origin", "corner2": [120, 80, 0], "layer": "M-PART"},
      "writes": true,
      "save_as": "$plate",
      "postconditions": [{"type": "exists", "target": "$plate"}]
    }
  ],
  "constraints": [
    {"type": "distance", "expected": 120.0}
  ]
}
```

Executable CADPlan operations include common drawing, editing, layer,
dimension, hatch, and block operations. Valid CAD actions that are not yet bound
inside CADPlan can still be called through their direct MCP tools.

`send_command`, SQL mutation, purge, and audit are disallowed by default in
CADPlan validation.

### Visual Grounding

`export_view_image_with_mapping(include_overlay=True)` creates:

- a clean view export,
- an optional overlay image with numeric IDs,
- a sidecar JSON file with view parameters, visible handles, pixel boxes, and
  mapping data.

Use `ground_vlm_region(snapshot_id, bbox)` for VLM pixel boxes and
`ground_vlm_overlay_id(snapshot_id, overlay_id)` for overlay IDs. Call
`explain_entity` on top candidates before editing.

Top/plan modelspace views are the most reliable. Twisted, UCS, 3D, and complex
layout viewport cases return warnings or lower confidence when exact grounding
is not available.

### Prompts And Assembly Skills

The `prompts/` directory contains MCP prompt source files for:

- understanding existing drawings,
- precise drawing from a specification,
- VLM drawing review,
- repair planning.

The `.agents/skills/draw-assembly-diagrams` skill provides agent-facing
assembly drawing workflows. Assembly rules are modular:

- `references/assembly/index.md` selects the applicable standard module.
- `references/assembly/standards/generic-mechanical.md` is the default
  mechanical assembly module.
- Additional ASME, ISO, GB, or company modules can be added without rewriting
  the main skill.

## Safety Model

- Scan and understand before editing.
- Prefer high-level CAD tools over raw primitives.
- Use handles returned by AutoCAD; do not guess edit targets from text alone.
- Keep destructive tools and raw `send_command` behind manual approval.
- Use CADPlan validation and dry-run before multi-step edits.
- Store agent memory in SQLite, not in hidden DWG entities.
- Rescan and validate after changes.

## Runtime Files

The server may create these files in the active workspace:

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

They are runtime artifacts and should not be committed.

The active database is `.cad_mcp/workspace.db`. If a retired root-level
`autocad_data.db` exists from older versions, `check_runtime_environment` and
`get_legacy_database_status` report it as a warning. Archive or delete it after
confirming no old MCP process still uses it.

Logs are UTF-8 and rotate by size. Configure with `CAD_MCP_LOG_PATH`,
`CAD_MCP_LOG_MAX_BYTES`, `CAD_MCP_LOG_BACKUP_COUNT`, `CAD_MCP_LOG_LEVEL`, and
`CAD_MCP_MCP_LOG_LEVEL`. Each log line includes workspace, drawing, and thread
IDs for correlation.

## Repository Layout

```text
src/
  server.py                 MCP tool, prompt, and resource definitions
  cad_controller.py         AutoCAD COM bridge
  cad_database.py           SQLite persistence
  cad_tools/                Tool implementations grouped by CAD domain
  cad_understanding/        CAD-IR, semantics, constraints, validation, grounding
prompts/                    Prompt sources loaded by MCP prompt functions
scripts/                    Verification and smoke-test scripts
tests/                      Unit tests that mock COM-dependent behavior
.agents/skills/             Agent skill guidance and assembly standards
.codex/                     Project-scoped Codex MCP config
.claude/ and .mcp.json      Claude Code MCP config
```

## Development

Install development dependencies:

```powershell
python -m pip install -e .[dev]
```

Run unit tests:

```powershell
python -m pytest
```

Run the AutoCAD MCP tool smoke verifier:

```powershell
python scripts\verify_autocad_mcp_tools.py
```

Run the CAD understanding workflow smoke benchmark:

```powershell
python scripts\verify_cad_understanding_workflow.py
```

Unit tests mock COM-dependent behavior and do not require AutoCAD. Smoke
verification requires a local AutoCAD COM session.

## Troubleshooting

- **The server starts but tools fail**: make sure AutoCAD is installed,
  licensed, and able to open normally on the same Windows account.
- **The MCP client cannot import `src`**: set the server `cwd` to the repository
  root or set `PYTHONPATH` to the repository root.
- **Workspace data appears in the wrong folder**: start the MCP client from the
  intended workspace or set `CAD_MCP_WORKSPACE_ROOT`.
- **A drawing operation needs a tool that is missing**: add the behavior as a
  best-cad-mcp tool instead of using an agent-side COM script.
- **Visual grounding is uncertain**: inspect warnings, use overlay IDs where
  possible, and confirm candidate handles with `explain_entity`.

## Contributing

Contributions are welcome. Good changes usually include:

- a focused tool implementation in `src/cad_tools/` or
  `src/cad_understanding/`,
- an MCP wrapper in `src/server.py` when a new capability should be exposed,
- tests that do not require AutoCAD for ordinary CI,
- docs or prompt updates when workflows change.

Please avoid committing runtime artifacts such as `.cad_mcp/`, logs, exported
review images, local databases, virtual environments, or AutoCAD smoke-test
outputs.

## Acknowledgements

The model-private annotation and pointer-style workflow is conceptually
informed by the public Pointer-CAD project and paper:
https://github.com/Snitro/Pointer-CAD

No Pointer-CAD source code is copied into this repository.

## License

MIT. See [LICENSE](LICENSE).
