# Security Policy

## Supported Versions

Security fixes and hardening updates are provided for the active release line and the default branch:

| Version | Supported |
| --- | --- |
| `1.7.x` (latest PyPI release) | Yes |
| `master` | Yes |
| `< 1.7.0` | No |

## Reporting a Vulnerability

Please report security issues privately rather than opening a public GitHub issue so maintainers can verify and patch affected versions before public disclosure.

- **Preferred channel:** Use [GitHub Private Vulnerability Reporting](https://github.com/LokmenoWer/best-cad-mcp/security/advisories/new) on `LokmenoWer/best-cad-mcp`.

When submitting a report, please include as much of the following as possible to speed up reproduction and triage:

- Affected version or commit SHA, `CAD_MCP_TOOL_PROFILE` (`lean`, `core`, or `full`), and MCP client configuration (e.g., Codex `.codex/config.toml` or `.mcp.json`).
- A minimal reproduction case: sample `.dwg`/`.dxf` snippet, `CADPlan` JSON, SQL query, tool payload, or exported artifact path.
- Expected security boundary vs. observed behavior, including any relevant logs or tracebacks.

## Threat Model & Trust Boundaries

`best-cad-mcp` connects AI coding and multimodal agents to live desktop AutoCAD instances on Windows. Because AutoCAD drawings are high-value engineering assets and the server interacts with local COM automation and local file converters, security relies on strict trust boundaries:

### 1. Local Host & Windows COM Boundary
- `best-cad-mcp` communicates over MCP `stdio` and runs on the same Windows user account and session as desktop AutoCAD, serializing COM calls onto a single event-loop thread.
- It is designed for local, operator-supervised workflows and is **not** intended to be exposed directly over unauthenticated remote network transports.

### 2. Untrusted Drawings & Visual Inputs (Indirect Prompt Injection)
- Engineering workflows frequently inspect third-party `.dwg`/`.dxf` files and external raster drawings whose `TEXT`/`MTEXT`, block attributes, layer names, XData, and rendered graphics enter LLM/VLM context.
- Untrusted drawing contents and visual observations are data, never trusted instructions: they must not silently escalate into arbitrary `send_command` execution, AutoCAD system-variable tampering, or destructive file writes without explicit operator approval.

### 3. Guarded Execution & Client Approval Gates
- Multi-step edits are modeled as `CADPlan` payloads (`validate_cad_plan` / `dry_run_cad_plan`) and require explicit `allow_modify=true` on `execute_cad_plan` (and `import_dxf`).
- The repository's [`.codex/config.toml`](.codex/config.toml) enforces `approval_mode = "prompt"` on:
  - Raw escapes and system state changes: `send_command`, `set_variable`, `execute_cad_plan`
  - Destructive drawing edits: `delete_entity`, `delete_entities`, `erase_selection_entities`, `delete_layer`, `purge_drawing`, `audit_drawing`
  - Drawing lifecycle boundaries: `open_drawing`, `import_dxf`, `save_drawing`, `close_drawing`

### 4. AutoLISP & Read-Only SQLite Sandboxing
- **AutoLISP literal validation (`src/cad_utils.py`):** Internal COM/AutoLISP command builders validate handles, finite numbers, coordinates, and quoted strings (`lisp_handent`, `lisp_number`, `lisp_point`, `lisp_quoted`) against expression breakout, covered by regression tests in `tests/test_lisp_literals.py`.
- **SQLite query sandbox (`src/cad_database.py`):** `execute_query` enforces read-only inspection over scanned drawing metadata via SQL token screening (`_is_read_only_sql`), SQLite's `conn.set_authorizer` (`_read_only_authorizer`), drawing-scoped temporary views (`_install_scoped_read_views`), row and payload-byte caps (`CAD_MCP_SQL_MAX_ROWS`, `CAD_MCP_SQL_MAX_RESULT_BYTES`), and `progress_handler` execution timeouts (`CAD_MCP_SQL_TIMEOUT_MS`).

### 5. Visual Rasterization Subprocess Pipeline
- Visual grounding and image-tracing helpers (`src/cad_understanding/view_grounding.py`, `src/cad_understanding/vision.py`) convert exported WMF/SVG artifacts to PNG using local tools (`ImageMagick`, `Inkscape`, `LibreOffice`, `cairosvg`, or parameterized PowerShell GDI+ `System.Drawing.Imaging.Metafile` scripts via `-EncodedCommand`) with explicit argument vectors and timeouts.

## Scope

### In-Scope Security Issues
- Bypasses of the SQLite read-only authorizer, drawing-scope view isolation, or resource/timeout caps in `execute_query`.
- AutoLISP, COM, or command injection reachable through structured (non-`send_command`) MCP tools.
- OS command injection, argument injection, parser vulnerabilities, or arbitrary file read/write/path-traversal flaws across WMF/SVG rasterizers, export tools, image-trace paths, or `.cad_mcp/` workspace paths.
- Bypasses of `allow_modify` guards, `CADPlan` validation gates, or `.codex` interactive approval boundaries.

### Out of Scope
- Intentional operator execution of explicitly approved `send_command` instructions in a trusted local AutoCAD session.
- Risks caused by exposing the local `stdio` MCP server directly to untrusted remote network clients without an authenticating gateway.
