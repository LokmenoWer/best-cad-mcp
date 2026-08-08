# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

[![PyPI](https://img.shields.io/pypi/v/best-cad-mcp?color=3775A9)](https://pypi.org/project/best-cad-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/best-cad-mcp)](https://pypi.org/project/best-cad-mcp/)
![Platform](https://img.shields.io/badge/platform-Windows-0078D4)
[![License](https://img.shields.io/badge/license-MIT-2ea44f)](https://github.com/LokmenoWer/best-cad-mcp/blob/master/LICENSE)

**A local, handle-first MCP server for agents that work with real AutoCAD drawings.**

Inspect a DWG, reason over structured geometry, plan guarded edits, validate the
result, and export visual evidence without hiding agent state inside the drawing.

[简体中文](https://github.com/LokmenoWer/best-cad-mcp/blob/master/README.zh-CN.md) · [Install](#quick-start) · [Workflow](#the-guarded-workflow) · [Tool profiles](#tool-profiles) · [Safety](#safety-model)

![A mounting plate created from a validated CADPlan and exported from AutoCAD](https://raw.githubusercontent.com/LokmenoWer/best-cad-mcp/master/docs/images/readme-hero.png)

*A real AutoCAD export produced through best-cad-mcp: structured tools, semantic
scan, validation, and visual verification in one workflow.*

> [!IMPORTANT]
> best-cad-mcp is beta software. It is designed for controlled local workflows
> where an operator can review plans and evidence, not unattended changes to
> valuable production drawings.

## Why best-cad-mcp

Most CAD automation stops at drawing primitives. Useful agent workflows also
need to know exactly **what** they are changing, **why** a target was selected,
and **whether** the result is correct.

| Handle-first control | Drawing understanding | Evidence before trust |
| --- | --- | --- |
| Scan real AutoCAD handles, query exact entities, and edit those handles instead of guessing from labels or pixels. | Build CAD-IR, semantic objects and graphs, dimension bindings, constraints, and validation reports in a local workspace. | Validate and dry-run CADPlans, execute explicitly, rescan, and compare structured and visual results. |

The server runs on the same Windows account as AutoCAD and communicates over
MCP stdio. AutoCAD remains the source of truth; SQLite stores model-private
context, scan results, and review artifacts alongside the workspace.

## Quick start

### Requirements

- Windows
- AutoCAD 2020 or newer recommended, installed and licensed
- AutoCAD and the MCP client running as the same Windows user
- Python 3.11 or newer
- An MCP-compatible local client

### Install the package

```powershell
python -m pip install --upgrade best-cad-mcp
cad-mcp-doctor --check-autocad
```

For rendered overlays and visual-review helpers:

```powershell
python -m pip install --upgrade "best-cad-mcp[visual]"
cad-mcp-doctor --check-autocad --require-visual-export
```

Keep AutoCAD open, then configure your MCP client to launch `cad-mcp`.

### Codex

Codex supports both global `~/.codex/config.toml` and trusted,
project-scoped `.codex/config.toml` files. This minimal installed-package
configuration uses the curated `core` tool profile:

```toml
[mcp_servers.best-cad-mcp]
command = "cad-mcp"
cwd = 'C:\CAD\your-project'
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "writes"

[mcp_servers.best-cad-mcp.env]
CAD_MCP_TOOL_PROFILE = "core"
CAD_MCP_WORKSPACE_ROOT = 'C:\CAD\your-project'
```

Restart Codex after editing the file, then inspect the connected server with
`/mcp`. See the
[official Codex MCP configuration guide](https://developers.openai.com/codex/mcp)
for configuration scopes and current options.

### Claude Code and other JSON-configured clients

```json
{
  "mcpServers": {
    "best-cad-mcp": {
      "command": "cad-mcp",
      "env": {
        "CAD_MCP_TOOL_PROFILE": "core",
        "CAD_MCP_WORKSPACE_ROOT": "C:\\CAD\\your-project"
      }
    }
  }
}
```

Save this as `.mcp.json` in the CAD project root and start the client from that
project. `CAD_MCP_WORKSPACE_ROOT` should point to the CAD project being worked
on, not to this repository. With an installed package, setting both the process
`cwd` and workspace root to the project keeps runtime files together.

<details>
<summary>Install from source</summary>

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[visual]"
.\.venv\Scripts\python.exe -m src.doctor --check-autocad
```

For a source checkout, start `python -m src.server` from the repository or
set the MCP server `cwd` to the repository. Keep
`CAD_MCP_WORKSPACE_ROOT` pointed at the separate CAD project you want to
index.

</details>

## The guarded workflow

![Preflight, scan, dry-run, execute, and verify workflow](https://raw.githubusercontent.com/LokmenoWer/best-cad-mcp/master/docs/images/safe-workflow.svg)

1. **Preflight** — run `check_runtime_environment(check_autocad=true)` or
   `cad-mcp-doctor --check-autocad`; stop when the result reports `ok=false`.
2. **Scan** — run `scan_all_entities` before reasoning about an existing DWG.
   Use `topology_detail="full"` for primitive grounding or cross-entity profiles.
3. **Understand** — build CAD-IR, summarize the drawing, query semantics, and
   confirm important targets with `explain_entity`.
4. **Plan** — express multi-step changes as a CADPlan, then call
   `validate_cad_plan` and `dry_run_cad_plan`.
5. **Execute explicitly** — only after authorization and an acceptable dry-run,
   call `execute_cad_plan(..., allow_modify=true, transactional=true)`.
6. **Verify** — rescan, run geometric validation, export a clean view and
   overlay, and save only when the operator intends to persist the DWG.

For precise edits, prefer handles returned by AutoCAD over names inferred from
screenshots. For visual findings, treat grounding as evidence: confirm the
candidate entity and its geometry before changing it.

## What it can do

| Area | Representative capabilities |
| --- | --- |
| 2D drafting | Lines, polylines, curves, circles, regions, hatches, text, dimensions, leaders, tables, layers, blocks, and attributes |
| Editing | Move, copy, rotate, scale, mirror, offset, trim, extend, fillet, chamfer, arrays, properties, selections, and handle-targeted changes |
| Drawing understanding | SQLite scan, CAD-IR v2, summaries, semantic objects/graphs, constraints, dimension binding, validation, and repair proposals |
| Guarded automation | CADPlan variables, dependencies, captured handles, preconditions, postconditions, dry-runs, transactional execution, undo, and rollback attempts |
| Visual grounding | Clean exports, adaptive numeric overlays, pixel/world mapping, path and polygon grounding, tile crops, and VLM finding reconciliation |
| Image-to-CAD | ImageDrawingSpec tracing, calibration, fidelity checks, staged execution, and visual comparison against the source image |
| Mechanical drawings | Orthographic views, sections, hatches, centerlines, dimensions, BOMs, balloons, layouts, and assembly-oriented prompt/skill assets |
| 3D and output | 3D solids and operations, layouts, plotting, PDF/DXF/DWF/image export, and direct in-result image content |

### Tool profiles

The shipped client configs and examples recommend `core` because it keeps tool
selection reliable while covering normal guarded workflows. When the profile
environment variable is omitted, the Python server falls back to `full` for
backward compatibility.

| Profile | Tools | Intended use |
| --- | ---: | --- |
| `lean` | 113 | Smallest dependable surface for common drawing and inspection tasks |
| `core` | 210 | Recommended default for full guarded CAD workflows |
| `full` | 321 | Every registered tool, including specialized and legacy operations |

Select a profile with `CAD_MCP_TOOL_PROFILE=lean|core|full`. Fine-grained
allow/deny controls are also available through
`CAD_MCP_TOOLS_INCLUDE` and `CAD_MCP_TOOLS_EXCLUDE`.

## From real CAD to grounded evidence

The hero mounting plate is a real AutoCAD export, not a generated UI mockup. It
was created during a live MCP session from a validated and dry-run CADPlan,
then rescanned and validated before export. The diagram below illustrates how a
separate grounding layer keeps geometry readable while attaching exact handles.

![Clean CAD geometry mapped to path, polygon, and handle evidence](https://raw.githubusercontent.com/LokmenoWer/best-cad-mcp/master/docs/images/visual-grounding.svg)

### Copy a mechanical drawing from one image

A typical tracing loop is:

1. call `prepare_image_trace(image_path, domain="mechanical")`;
2. use `prepare_visual_semantic_context` and `get_trace_source_image` to inspect
   global and tiled source images;
3. produce `ImageDrawingSpec/v1`, echoing each observed image's
   `source_ref_template` for measured coordinates;
4. call `validate_image_drawing_spec`, then `submit_image_drawing_spec`;
5. call `compile_image_spec_to_cad_plan`;
6. call `validate_image_fidelity_contract(spec, cad_plan)`;
7. call `validate_cad_plan`, then `dry_run_cad_plan`;
8. only after authorization, call
   `execute_cad_plan(..., allow_modify=true, transactional=true)`;
9. rescan, validate, and compare the final AutoCAD export with the source.

Do not execute a trace just because its JSON is valid. Check view count,
symmetry, dimensions, centerlines, hole placement, and source/render fidelity
first.

## Visual grounding in v1.6

Version 1.6 adds drawing-level topology for boundaries assembled across
multiple entities, including line-line and supported line-curve intersections
plus closed-loop profiles. Use `scan_all_entities(topology_detail="full")` when
primitive relations are required. Grounding now carries real path/polygon
geometry, multiple-handle candidates, adaptive overlays, and tile-aware
pixel/world contracts.

This improves selection quality on mechanical profiles, but it does not make
vision infallible. Important edits should still follow:

```text
visual finding -> grounding candidates -> explain_entity -> handle-targeted edit
```

The default VLM review prompt is `vlm_review_drawing/v3`. Snapshot and overlay
schema versions are recorded in tool results so strict consumers can detect
contract changes.

## Safety model

- Read and scan before editing an existing drawing.
- Keep raw command execution, deletion, purge, audit, save, close, and
  `execute_cad_plan` behind explicit client approval.
- Validate and dry-run plans before modification.
- Use returned handles and structured geometry for exact targets.
- Rescan after modifications; do not rely on stale SQLite rows.
- Keep model-private notes and spatial annotations in `.cad_mcp/`, not in
  visible DWG geometry, XData, or hidden layers.
- Treat saving and closing as separate operator decisions.
- Treat top/plan model-space views as the strongest grounding case. View twist,
  custom UCS, 3D geometry, and complex layout viewports can reduce confidence.

Transaction and rollback support reduce risk but cannot guarantee recovery from
every AutoCAD or COM failure. Work on copies when the drawing is valuable.

## Workspace and data

`CAD_MCP_WORKSPACE_ROOT` controls
`<workspace>/.cad_mcp/workspace.db`. The default log, visual exports, and image
trace assets are written relative to the MCP process `cwd` as `cad_mcp.log`,
`cad_visual_exports/`, and `cad_image_traces/`.

External CAD projects are not ignored automatically. Add these entries to the
project's `.gitignore` when it is a Git repository:

```gitignore
.cad_mcp/
cad_mcp.log
cad_visual_exports/
cad_image_traces/
```

The database helps connect turns and tools, but AutoCAD remains authoritative.
If a drawing changes outside the server, scan it again before using stored
entities. A warning about a legacy root `autocad_data.db` means an older
database exists; verify migration, then archive it separately.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| AutoCAD is open but unavailable | Run `cad-mcp-doctor --check-autocad`; make sure both processes use the same Windows account and privilege level. |
| Server starts with too many tools | Set `CAD_MCP_TOOL_PROFILE=core` or `lean`, then restart the client. |
| Visual export is unavailable | The `[visual]` extra provides Pillow/CairoSVG for raster and SVG work. AutoCAD WMF-to-PNG usually still needs ImageMagick/Wand, Inkscape, or LibreOffice. Check `get_vision_capabilities()` and its `wmf_to_png_available` result; PDF can also be rasterized externally. |
| Queries return stale entities | Activate the intended drawing and rerun `scan_all_entities`. |
| MCP server starts in the wrong folder | Set server `cwd` to the source checkout only when developing; set `CAD_MCP_WORKSPACE_ROOT` to the CAD project. |
| A plan is rejected | Run `validate_cad_plan`, inspect the exact failing step, and dry-run again after correcting it. |

For machine-readable diagnostics:

```powershell
cad-mcp-doctor --json
```

## Development

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,visual]"
python -m pytest -q -m "not autocad_com"
```

Tests marked `autocad_com` require a live local AutoCAD session and are
excluded from the release workflow. Release publication validates the version,
runs the non-COM suite, builds the package, checks it with Twine, publishes to
PyPI, and then publishes the MCP server metadata.

Contributions are welcome. Please keep changes scoped, add regression tests for
behavior changes, and preserve the scan → plan → validate → verify safety model.

## Acknowledgements

The model-private annotation and pointer-style CAD context design was informed
by the public [Pointer-CAD](https://github.com/Snitro/Pointer-CAD) project and
paper. No Pointer-CAD source code is copied into this repository.

## License

MIT. See [LICENSE](https://github.com/LokmenoWer/best-cad-mcp/blob/master/LICENSE).
