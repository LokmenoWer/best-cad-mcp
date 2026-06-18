# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

`best-cad-mcp` is a Windows AutoCAD MCP server for agents that need to inspect,
reason about, modify, validate, and export real DWG drawings. It runs locally,
talks to AutoCAD through Windows COM, and exposes a handle-first CAD automation
workflow through the Model Context Protocol.

[Chinese README](README.zh-CN.md)

## Project Status

This project is in beta. The main architecture, command-line entry points,
workspace database, and core CAD workflows are in place, but the tool surface is
still evolving. Treat it as production-oriented infrastructure for controlled
local workflows, not as a fire-and-forget CAD robot.

## Why This Exists

Most CAD automation demos can draw a line, a circle, or a rectangle. Real agent
workflows need more:

- inspect an existing DWG before touching it,
- identify exact AutoCAD handles instead of guessing from labels or screenshots,
- keep drawing understanding, validation reports, and review context across
  turns,
- dry-run multi-step edits before they modify the drawing,
- export visual evidence and map VLM findings back to candidate entities, and
- keep private agent annotations out of the DWG itself.

`best-cad-mcp` is built around those requirements. The server combines broad
AutoCAD tool coverage with a local SQLite workspace database, CAD understanding
artifacts, visual grounding, prompt assets, and a guarded CADPlan execution
path.

## What It Provides

| Area | Capabilities |
| --- | --- |
| AutoCAD operations | Drawing primitives, editing, layers, blocks, attributes, dimensions, tables, hatches, layouts, plotting, view control, 3D solids, file export, queries, selection, and utility tools. |
| Handle-first inspection | Scan a drawing into SQLite, query structured metadata, explain entities, and edit by the handles returned by AutoCAD. |
| CAD understanding | CAD-IR, drawing summaries, semantic objects, semantic graphs, dimension binding, extracted constraints, validation reports, and MCP resources. |
| Visual review | Export clean view images, optional numeric overlays, and sidecar mapping data for pixel/world/entity grounding. |
| CADPlan | Validate, dry-run, and explicitly execute multi-step drawing or repair plans with variables, dependencies, captured handles, postconditions, transactional execution, and rollback attempts. |
| Agent memory | Store workspace context and model-private spatial annotations in SQLite instead of hiding helper geometry, XData, labels, or marks inside the DWG. |
| Prompt and skill assets | Prompt files for understanding, precise drawing, VLM review, and repair; assembly drawing skill references for standards-aware workflows. |

The server currently registers hundreds of MCP tool entry points. The intended
workflow is not to call random primitives until a drawing looks right; it is to
scan, understand, plan, modify by handle, validate, and visually confirm.

## Boundaries

`best-cad-mcp` does not include AutoCAD, replace an AutoCAD license, or provide
a cloud CAD renderer. It assumes AutoCAD is installed and can be automated on
the same Windows account that runs the MCP server.

The project also does not promise perfect geometric interpretation from a
screenshot. Visual grounding tools return candidates, confidence, and warnings;
agents should confirm important targets with `explain_entity` and structured
metadata before editing.

## Requirements

- Windows
- AutoCAD 2020 or newer recommended
- Python 3.11 or newer
- An MCP-compatible client such as Codex or Claude Code
- A local AutoCAD installation available through Windows COM automation

Optional visual-review helpers can use system renderers such as ImageMagick,
Inkscape, librsvg, Chrome, or Edge, or the Python dependencies installed through
the `visual` extra.

## Installation

### From Source

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Install optional visual-review dependencies:

```powershell
python -m pip install -e ".[visual]"
```

Install development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

### Published Package

For published releases, install the package directly:

```powershell
python -m pip install best-cad-mcp
```

The installed console commands are:

```powershell
cad-mcp
cad-mcp-doctor
```

The server is an MCP stdio process. In normal use, your MCP client starts it
from configuration rather than from an interactive terminal.

## Runtime Preflight

Before live CAD work, verify the runtime:

```powershell
cad-mcp-doctor --check-autocad
```

The same check is available as an MCP tool:

```text
check_runtime_environment(check_autocad=true, require_visual_export=false)
```

The preflight reports Windows, Python, package availability, workspace
writability, optional visual renderer support, and AutoCAD COM connectivity
when `check_autocad=true`. Treat `ok=false` as a blocker before drawing or
editing.

Strict startup mode is available for deployments that should refuse to start
when required checks fail:

```powershell
$env:CAD_MCP_STRICT_PREFLIGHT = "1"
$env:CAD_MCP_PREFLIGHT_CHECK_AUTOCAD = "1"
$env:CAD_MCP_PREFLIGHT_REQUIRE_VISUAL = "0"
cad-mcp
```

Set `CAD_MCP_PREFLIGHT_REQUIRE_VISUAL=1` when visual export support is a hard
runtime requirement.

## MCP Client Configuration

Start the MCP client from the workspace whose CAD metadata should be used.
Runtime data is stored under that workspace unless `CAD_MCP_WORKSPACE_ROOT` is
set.

### Codex

This repository includes `.codex/config.toml` for project-scoped Codex usage.
After trusting the project, Codex can start the local checkout.

User-level configuration after `pip install -e .` or package installation:

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "cad-mcp"
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

Configuration that runs from a checkout virtual environment:

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

Keep raw or destructive tools interactive:

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

If dependencies live only in the project virtual environment, change `command`
to:

```json
"C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
```

Use `claude mcp list` or `/mcp` inside Claude Code to confirm that the server
is connected.

## Recommended Workflows

### Inspect Or Repair An Existing DWG

1. `check_runtime_environment(check_autocad=true)`.
2. `open_drawing` when the user supplies a DWG path.
3. `scan_all_entities(clear_db=true, detail_level="minimal", topology_detail="summary")`.
4. `build_drawing_ir`, then `summarize_drawing`.
5. `detect_semantic_objects(domain="mechanical")` or another suitable domain.
6. `bind_all_dimensions`, `extract_drawing_constraints`, and `check_drawing_constraints`.
7. `validate_geometry`.
8. `export_view_image_with_mapping(include_overlay=true)` when visual evidence matters.
9. `ground_vlm_region` or `ground_vlm_overlay_id` for VLM findings.
10. `explain_entity(handle)` before editing.
11. Edit by handle or through a validated, dry-run CADPlan.
12. Rescan, validate, visually confirm, then save or export.

### Create A New Drawing

1. `check_runtime_environment(check_autocad=true)`.
2. `create_new_drawing`.
3. Set units, layers, text styles, dimension styles, layout, and view state.
4. Build a CADPlan with high-level operations, dependencies, `save_as`
   variables, and postconditions.
5. `validate_cad_plan`, then `dry_run_cad_plan`.
6. `execute_cad_plan(..., allow_modify=true)` only after modification is authorized.
7. `scan_all_entities`, `build_drawing_ir`, `validate_geometry`, and export a review image.
8. Save or export the final DWG, PDF, DXF, or DWF deliverable.

### Review With Vision

1. `export_view_image_with_mapping(include_overlay=true)`.
2. Review the clean image, overlay image, and sidecar mapping JSON.
3. Use `ground_vlm_overlay_id` for overlay IDs or `ground_vlm_region` for pixel boxes.
4. Confirm candidates with `explain_entity`.
5. Use `propose_repair_plan` or `propose_constraint_repair_plan` for selected issues.

## Core Concepts

### Workspace Database

Runtime metadata is stored by default at:

```text
<workspace>/.cad_mcp/workspace.db
```

The database scopes data by workspace, drawing, conversation, and thread. This
keeps identical handles in different drawings from colliding and lets parallel
agent sessions keep private annotations and query history separate.

`execute_query` is read-only, scoped, and bounded. Use public table names such
as `cad_entities`; direct `main.<table>` access is blocked so one workspace
cannot bypass scoped views. Result sets default to 1,000 rows, 5 seconds, and
about 1 MB of JSON. Tune with tool parameters or:

- `CAD_MCP_SQL_MAX_ROWS`
- `CAD_MCP_SQL_TIMEOUT_MS`
- `CAD_MCP_SQL_MAX_RESULT_BYTES`

Useful workspace tools:

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`
- `get_database_maintenance_status`
- `maintain_database`
- `clear_understanding_cache`
- `get_legacy_database_status`

### ToolResult

CAD understanding tools return structured `ToolResult` dictionaries:

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

`scan_all_entities(clear_db=true)` clears stale semantic objects, constraints,
validation reports, and view snapshots for the active thread by default. Pass
`clear_understanding=false` only when cached understanding artifacts should
survive a rescan.

Key understanding tools include:

- `build_drawing_ir` and `export_drawing_ir`
- `summarize_drawing`
- `find_entities_by_description`
- `explain_entity`
- `detect_semantic_objects`, `get_semantic_graph`, and `find_semantic_objects`
- `bind_dimension_to_geometry` and `bind_all_dimensions`
- `extract_drawing_constraints`, `check_drawing_constraints`, and `get_drawing_constraints`
- `validate_geometry` and `get_validation_report`
- `propose_repair_plan` and `propose_constraint_repair_plan`
- `list_cad_resources` and `get_cad_resource`

`build_drawing_ir` returns CAD-IR v2 by default. The top-level shape is stable
for agents:

```json
{
  "schema_version": "cad-ir/v2",
  "generated_at": "...",
  "manifest": {
    "profile": "agent",
    "sections": ["overview", "entities", "layers"],
    "counts": {},
    "limits": {},
    "warnings": []
  },
  "drawing": {
    "name": "active.dwg",
    "path": "",
    "units": "unknown",
    "extents": {},
    "counts": {}
  },
  "quality": {
    "scan_state": "scanned",
    "coverage": {},
    "issues": [],
    "recommended_next_tools": []
  },
  "sections": {}
}
```

Use `sections` to request only the needed payloads:
`overview`, `entities`, `layers`, `blocks`, `topology`, `semantics`,
`constraints`, `validation`, `views`, and `quality`. The default entity index is
compact and includes handles, entity type, layer, bbox, semantic tags, topology
availability, and constraint/validation flags. Pass `include_raw=true` only when
full decoded geometry and properties are needed. `entity_limit` defaults to
`1000`; truncation is reported in both `manifest.warnings` and `quality.issues`.

The CAD resource layer also exposes focused v2 slices:
`cad://drawing/current/ir/overview` and
`cad://drawing/current/ir/entities`.

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
  "variables": {
    "origin": [0, 0, 0]
  },
  "steps": [
    {
      "step_id": "plate",
      "op": "draw_rectangle",
      "args": {
        "corner1": "$origin",
        "corner2": [120, 80, 0],
        "layer": "M-PART"
      },
      "writes": true,
      "save_as": "$plate",
      "postconditions": [
        {"type": "exists", "target": "$plate"}
      ]
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

CADPlan validation disallows raw `send_command`, SQL mutation, purge, and audit
operations by default. During execution, a bound tool failure, `ok=false`,
`success=false`, or recognizable error text stops the plan and triggers rollback
attempts when rollback is enabled.

### Visual Grounding

`export_view_image_with_mapping(include_overlay=true)` creates:

- a clean view export,
- an optional overlay image with numeric IDs, and
- a sidecar JSON file with view parameters, visible handles, pixel boxes, and
  mapping data.

For dense drawings, pass `overlay_granularity="both"` to include primitive
overlay IDs such as `E001.P02`, `overlay_style="som"` for Set-of-Mark-style
labels, and `include_tiles=true` to emit a tile index for large-view review.

Use `ground_vlm_region(snapshot_id, bbox)` for VLM pixel boxes and
`ground_vlm_overlay_id(snapshot_id, overlay_id)` for overlay IDs. Call
`explain_entity` on top candidates before editing.

Structured VLM review output can be validated and persisted with
`validate_vlm_review_output`, `submit_vlm_review`, and `get_vlm_findings`.
Confirmed findings can be fused into the semantic graph with
`fuse_vlm_findings_into_semantic_graph` or promoted into validation reports with
`promote_vlm_finding_to_validation_issue`. `analyze_engineering_drawing_stages`
returns a staged engineering-drawing interpretation JSON covering layout
segmentation, annotation detection, VLM parsing, and reconciliation. CAD-IR also
exposes a `vlm_findings` section, and resources include
`cad://drawing/current/vlm-findings` and
`cad://drawing/current/engineering-interpretation`.

Top/plan modelspace views are the most reliable. Twisted views, UCS changes, 3D
views, and complex layout viewport cases can return warnings or lower
confidence.

### Prompts And Assembly Skills

The `prompts/` directory contains MCP prompt source files for:

- understanding existing drawings,
- precise drawing from a specification,
- VLM drawing review, and
- repair planning.

The `.agents/skills/draw-assembly-diagrams` skill provides agent-facing
assembly drawing workflows. Assembly rules are modular:

- `references/assembly/index.md` selects the applicable standard module.
- `references/assembly/standards/generic-mechanical.md` is the default
  mechanical assembly module.
- Additional ASME, ISO, GB, or company modules can be added without rewriting
  the main skill.

## Safety Model

- Run preflight before live CAD work.
- Scan and understand before editing.
- Prefer named high-level CAD tools over reconstructed low-level primitives.
- Use AutoCAD handles returned by scan/query tools; do not infer edit targets
  from prose alone.
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

Logs are UTF-8 and rotate by size. Configure with:

- `CAD_MCP_LOG_PATH`
- `CAD_MCP_LOG_MAX_BYTES`
- `CAD_MCP_LOG_BACKUP_COUNT`
- `CAD_MCP_LOG_LEVEL`
- `CAD_MCP_MCP_LOG_LEVEL`

Each log line includes workspace, drawing, and thread IDs for correlation.

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
server.json                 MCP registry metadata
```

## Development

Run unit tests that do not require AutoCAD:

```powershell
python -m pytest -q -m "not autocad_com"
```

Run the full test suite available in the current environment:

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

The release workflow builds distributions, runs non-AutoCAD tests on Windows,
validates `server.json`, publishes to PyPI, and publishes MCP registry metadata
for tagged releases.

## Troubleshooting

- **The server starts but tools fail**: make sure AutoCAD is installed,
  licensed, and able to open normally on the same Windows account.
- **The MCP client cannot import `src`**: set the server `cwd` to the repository
  root or set `PYTHONPATH` to the repository root.
- **Workspace data appears in the wrong folder**: start the MCP client from the
  intended workspace or set `CAD_MCP_WORKSPACE_ROOT`.
- **Visual export is unavailable**: install the `visual` extra or a supported
  renderer such as ImageMagick, Inkscape, librsvg, Chrome, or Edge.
- **A drawing operation needs a missing tool**: add the behavior as a
  `best-cad-mcp` tool instead of relying on an agent-side temporary COM script.
- **Visual grounding is uncertain**: inspect warnings, use overlay IDs where
  possible, and confirm candidate handles with `explain_entity`.

## Contributing

Contributions are welcome. Good changes usually include:

- a focused tool implementation in `src/cad_tools/` or
  `src/cad_understanding/`,
- an MCP wrapper in `src/server.py` when a new capability should be exposed,
- tests that do not require AutoCAD for ordinary CI,
- documentation or prompt updates when workflows change, and
- no committed runtime artifacts.

Avoid committing `.cad_mcp/`, logs, exported review images, local databases,
virtual environments, build outputs, or AutoCAD smoke-test artifacts.

## Acknowledgements

The model-private annotation and pointer-style workflow is conceptually
informed by the public Pointer-CAD project and paper:
https://github.com/Snitro/Pointer-CAD

No Pointer-CAD source code is copied into this repository.

## License

MIT. See [LICENSE](LICENSE).
