"""
CAD MCP Server — Comprehensive AutoCAD 2020+ MCP Server.

Exposes 125+ tools covering:
  - All drawing primitives (line, circle, arc, ellipse, spline, polyline, polygon, etc.)
  - Complete entity editing (move, rotate, copy, delete, mirror, scale, offset, array, explode)
  - Full layer management (CRUD, freeze/thaw, lock/unlock, isolate)
  - Text styles, MText, leaders, tables, find & replace
  - Dimensioning (linear, angular, radial, diametric, ordinate, baseline, continue, qdim)
  - Block creation, insertion, MInsert (block array), Xref management
  - View control (zoom, pan, layouts, named views, viewports, wipeout)
  - Selection sets, spatial queries, and scanning
  - SQLite-backed CAD metadata database with SQL query
  - File I/O (open, save, export PDF/DXF/DWF/image)
  - Undo/redo, system variables, raw command execution
  - 2D editing: fillet, chamfer, trim, extend, break, join, stretch, lengthen
  - Advanced entity properties: TrueColor, transparency, plot style

Architecture:
    server.py   → MCP tool definitions (this file)
    cad_tools/  → Tool implementations (logic, no MCP dependency)
    cad_controller.py → COM bridge to AutoCAD
    cad_database.py   → SQLite persistence
    cad_data_model.py → AI-readable data structures
    cad_utils.py      → Shared helpers

Start manually:
    python server.py

Or via Codex MCP config:
    [mcp_servers.best-cad-mcp]
    command = "python"
    args = ["-m", "src.server"]
    cwd = "C:/path/to/best-cad-mcp"
    default_tools_approval_mode = "approve"

Or via Claude Code project config:
    .mcp.json registers best-cad-mcp as:
      command = "python"
      args = ["${CLAUDE_PROJECT_DIR:-.}/src/server.py"]
    .claude/settings.json allows routine mcp__best-cad-mcp__* tool calls
    and asks before raw/destructive tools.
"""

import sys
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import functools
import inspect
import uuid

# Ensure the project root is on the path for src imports
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from typing import Optional, List, Tuple, Dict, Any, Union
from typing_extensions import TypedDict

def _env_int(name: str, default: int,
             minimum: int = 1, maximum: int = 100_000_000) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


class _CADContextLogFilter(logging.Filter):
    @staticmethod
    def _context_value(ctx, attr: str, env_name: str) -> str:
        value = getattr(ctx, attr, None)
        if not isinstance(value, str) or not value:
            value = os.environ.get(env_name, "-")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = None
        try:
            database = globals().get("db")
            ctx = database.get_context() if database is not None else None
        except Exception:
            ctx = None
        record.cad_workspace_id = self._context_value(ctx, "workspace_id", "CAD_MCP_WORKSPACE_ID")
        record.cad_drawing_id = self._context_value(ctx, "drawing_id", "CAD_MCP_DRAWING_ID")
        record.cad_thread_id = self._context_value(ctx, "thread_id", "CAD_MCP_THREAD_ID")
        return True


def _configure_logging() -> None:
    log_path = os.environ.get("CAD_MCP_LOG_PATH") or os.path.join(os.getcwd(), "cad_mcp.log")
    max_bytes = _env_int("CAD_MCP_LOG_MAX_BYTES", 5_000_000, 100_000)
    backup_count = _env_int("CAD_MCP_LOG_BACKUP_COUNT", 5, 1, 100)
    level_name = os.environ.get("CAD_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - "
        "ws=%(cad_workspace_id)s drawing=%(cad_drawing_id)s "
        "thread=%(cad_thread_id)s - %(message)s"
    )
    context_filter = _CADContextLogFilter()
    handlers = [
        RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(
        getattr(logging, os.environ.get("CAD_MCP_MCP_LOG_LEVEL", "WARNING").upper(), logging.WARNING)
    )


# 日志文件存储在 Agent 的工作目录（MCP 客户端的 cwd），并按大小轮转。
_configure_logging()
logger = logging.getLogger("mcp_cad_server")

TOOL_SELECTION_INSTRUCTIONS = """
This AutoCAD MCP exposes hundreds of specialized CAD tools. Always choose the most
specific workflow and tool for the CAD intent before composing low-level
primitives. Treat the tool surface as indexed by intent, not as a flat list.

Tool-choice rules:
- Before live CAD work, call check_runtime_environment(check_autocad=True) so
  missing Python modules, workspace issues, AutoCAD COM availability, and visual
  review helpers are reported explicitly.
- For existing or complex drawings, route through the understanding stack before
  acting: scan_all_entities -> build_drawing_ir -> summarize_drawing ->
  detect_semantic_objects -> extract_drawing_constraints -> bind_all_dimensions
  -> check_drawing_constraints -> validate_geometry. Use topology_detail="full"
  when primitive grounding, dimension binding, or section/detail reasoning
  matters.
- For engineering drawings, assemblies, title blocks, BOMs, GD&T, surface
  finish notes, section/detail views, or exploded views, call
  analyze_engineering_drawing_stages and export_view_image_with_mapping with
  overlays. Do not reduce these artifacts to generic lines and labels.
- For new complex drawings, start with recommend_cad_tools(intent), then create
  a CADPlan using semantic operations, variables, handle capture, dependencies,
  expectations, and postconditions. Validate and dry-run before execution.
- For rectangles, polygons, splines, donuts, mlines, arrays, blocks, hatches,
  dimensions, leaders, 3D solids, trims, fillets, chamfers, offsets, and
  transforms, use the named tool. Do not rebuild them from draw_line,
  draw_circle, draw_polyline, or repeated copy_entity calls.
- Preserve drawing fidelity: repeated parts should become blocks or arrays,
  measured geometry should use associative dimensions, tabular data should use
  CAD tables, sections should use hatch/section tools, and 3D intent should use
  solids/regions/booleans instead of 2D wireframe approximations.
- Use draw_line/draw_circle/draw_polyline only for simple one-off geometry or
  when no named tool fits.
- Use send_command only as the final escape hatch after checking the exposed
  tools. It is raw AutoCAD command execution and is less validated.
- For existing drawings, scan first with scan_all_entities and query with
  get_entity_statistics/execute_query before editing. Capture returned handles;
  edits operate on handles.
- Vision-capable models may call export_view_image at any point to export the
  current AutoCAD view for visual confirmation. This writes a review artifact
  only and must not be replaced with visible helper geometry.
- Use add_spatial_annotation/list_spatial_annotations for model-private labels,
  pointers, and part names. These annotations live only in the MCP SQLite
  database and must not create layers, XData, blocks, or visible marks in DWG.
- If unsure which tool to use, call recommend_cad_tools(intent) or
  get_tool_help(tool_name) before drawing. Use cad://tool-selection,
  cad://tools, and cad://drawing/current/tool-guide as compact indexes.
"""

TOOL_DESCRIPTIONS = {
    "draw_line": (
        "Draw one straight line segment. LAST RESORT for composite shapes: "
        "use draw_rectangle for rectangles, draw_polygon for regular polygons, "
        "draw_mline for parallel wall lines, draw_spline for smooth curves, "
        "and dimension tools for measurements. Do not call draw_line repeatedly "
        "to fake a higher-level CAD object."
    ),
    "draw_circle": (
        "Draw one circle. For repeated holes or circular patterns, draw one "
        "circle and use array_polar instead of placing every circle manually."
    ),
    "draw_rectangle": (
        "Preferred tool for any rectangle or square from two opposite corners. "
        "Use this instead of four draw_line calls or a hand-built closed "
        "polyline."
    ),
    "draw_polygon": (
        "Preferred tool for regular polygons such as triangles, pentagons, "
        "hexagons, and octagons. Use this instead of manually calculating and "
        "drawing each side."
    ),
    "draw_polyline": (
        "Draw a connected 2D path. Use this for real polylines. Prefer "
        "draw_rectangle for rectangles, draw_polygon for regular polygons, "
        "draw_spline for smooth curves, polyline_set_bulge for arc segments, "
        "and fillet_polyline/chamfer_polyline for all-corner edits."
    ),
    "draw_spline": (
        "Preferred tool for smooth free-form curves through fit points. Use this "
        "instead of approximating curves with many short draw_line segments."
    ),
    "draw_mline": (
        "Preferred tool for parallel multi-lines such as walls, roads, and "
        "double-line symbols. Use this instead of drawing separate offset lines."
    ),
    "insert_minsert_block": (
        "Preferred tool for AutoCAD MInsert block arrays: insert one block "
        "reference as a rectangular row/column array entity. Use this when the "
        "intent says MInsert or block array; do not compose insert_block plus "
        "array_rectangular unless separate editable instances are required."
    ),
    "draw_text": (
        "Draw single-line annotation text. Do NOT fake dimensions with text and "
        "lines; use add_linear_dimension, add_qdim, add_radial_dimension, or "
        "the other add_*_dimension tools."
    ),
    "draw_mtext": (
        "Draw multiline annotation text. Do NOT use this for measured dimensions; "
        "use the associative dimension tools instead."
    ),
    "copy_entity": (
        "Make a single duplicate of an entity. For repeated grids or circular "
        "patterns, prefer array_rectangular or array_polar instead of loops of "
        "copy_entity."
    ),
    "array_rectangular": (
        "Preferred one-call tool for a row/column grid of repeated entities "
        "(columns, holes, fixtures, symbols). Use this instead of repeated "
        "copy_entity calls."
    ),
    "array_polar": (
        "Preferred one-call tool for circular patterns such as bolt holes, gear "
        "teeth, radial spokes, or repeated symbols around a center. Use this "
        "instead of manually placing each copy."
    ),
    "add_qdim": (
        "Fast batch dimension tool. Use for multiple related entities when a "
        "continuous, baseline, ordinate, radius, diameter, or staggered dimension "
        "set is needed. Do not fake dimension chains with lines and text."
    ),
    "add_linear_dimension": (
        "Preferred associative dimension for linear/aligned distance. Use this "
        "instead of drawing extension lines and writing measured text manually."
    ),
    "add_mleader": (
        "Preferred callout tool for an arrow leader with text. Use this instead "
        "of separate arrow lines plus draw_text."
    ),
    "add_table": (
        "Preferred CAD table tool for schedules, BOMs, part lists, and tabular "
        "notes. Use this instead of many draw_line/draw_text entities."
    ),
    "create_block": (
        "Create a reusable block definition from existing entity handles. Use "
        "blocks for repeated components instead of duplicating raw geometry."
    ),
    "insert_block": (
        "Insert an existing block reference. Use this for reusable components "
        "instead of redrawing the component each time."
    ),
    "add_hatch": (
        "Create hatch/fill, then call hatch_add_boundary. Use this for section "
        "patterns and fills instead of dense manual linework."
    ),
    "draw_box": (
        "Preferred true 3D box/cuboid solid. Use this instead of drawing a "
        "wireframe box from lines."
    ),
    "draw_cylinder": (
        "Preferred true 3D cylinder solid. Use this instead of circles plus "
        "vertical lines, and use solid_boolean for cylinder-cut holes."
    ),
    "fillet_polyline": (
        "Round all corners of a polyline in one operation. Use this instead of "
        "filleting each segment pair manually when the whole polyline is affected."
    ),
    "chamfer_polyline": (
        "Chamfer all corners of a polyline in one operation. Use this instead of "
        "editing each corner manually when the whole polyline is affected."
    ),
    "send_command": (
        "LAST RESORT raw AutoCAD command execution. Use only when no dedicated "
        "MCP tool covers the task after checking recommend_cad_tools/get_tool_help. "
        "Prefer validated named tools for drawing, editing, dimensioning, blocks, "
        "hatches, views, exports, and queries."
    ),
    "delete_selection_set": (
        "DESTRUCTIVE compatibility alias: erases the drawing entities contained "
        "in a selection set. It does not merely remove the selection-set container. "
        "Prefer erase_selection_entities for clarity; use clear_selection_set to "
        "clear a selection without deleting entities."
    ),
    "erase_selection_entities": (
        "DESTRUCTIVE: erase/delete all drawing entities currently contained in "
        "the named selection set. Use clear_selection_set instead when you only "
        "want to empty the selection."
    ),
    "clear_selection_set": (
        "Clear/remove members from a selection set without deleting drawing "
        "entities. Use erase_selection_entities/delete_selection_set only when "
        "the selected entities should be erased from the drawing."
    ),
    "get_tool_help": (
        "Read the CAD MCP tool index. Call with a tool name for guidance, or "
        "without arguments for categories. For an intent like 'draw a floor plan' "
        "or 'make bolt holes', prefer recommend_cad_tools(intent)."
    ),
    "export_view_image": (
        "Vision-model verification tool: export the current AutoCAD view as a "
        "review image artifact without modifying the DWG. Use whenever visual "
        "confirmation of the current drawing state would reduce ambiguity."
    ),
    "add_spatial_annotation": (
        "Store a model-private spatial label or pointer in SQLite only. Use for "
        "part names, semantic regions, entity/primitive references, and remembered "
        "view context without drawing helper geometry or writing XData."
    ),
    "list_spatial_annotations": (
        "List model-private spatial labels and pointers stored in SQLite. Use to "
        "recover the model's hidden CAD context after scans or multi-step edits."
    ),
    "clear_spatial_annotations": (
        "Remove model-private spatial labels from SQLite only. This does not erase "
        "or modify any AutoCAD drawing entity."
    ),
    "recommend_cad_tools": (
        "Semantic router for this CAD MCP. Pass a short natural-language intent "
        "and it returns the named tools to use, anti-patterns to avoid, and a "
        "safe workflow. Use this before composing primitives when unsure."
    ),
    "check_runtime_environment": (
        "Read-only preflight for this CAD MCP runtime. Checks Windows/Python, "
        "required Python modules, workspace writability, optional live AutoCAD "
        "COM connectivity, and visual review helpers. Call this before live "
        "CAD work so environment gaps become explicit instead of ad hoc."
    ),
    "restart_mcp": (
        "Request a soft MCP restart after this tool response is returned. "
        "Use this after updating the server code so the MCP host can launch a "
        "fresh process with the latest version."
    ),
}

# Create the MCP server
mcp = FastMCP(
    "AutoCAD-Comprehensive-Server",
    instructions=TOOL_SELECTION_INSTRUCTIONS,
)


def _humanize_tool_name(name: str) -> str:
    return name.replace("_", " ")


def _registration_category(name: str) -> str:
    if name in {
        "build_drawing_ir", "export_drawing_ir", "summarize_drawing",
        "explain_entity", "find_entities_by_description",
        "analyze_drawing_intent", "detect_semantic_objects",
        "get_semantic_graph", "find_semantic_objects",
        "extract_drawing_constraints", "check_drawing_constraints",
        "get_drawing_constraints", "bind_dimension_to_geometry",
        "bind_all_dimensions", "validate_geometry", "get_validation_report",
        "list_cad_resources", "get_cad_resource",
    }:
        return "CAD understanding"
    if name in {
        "export_view_image_with_mapping", "get_visible_entities_in_view",
        "map_pixel_to_world", "map_world_to_pixel",
        "map_pixel_region_to_world_bbox", "ground_vlm_region",
        "ground_vlm_overlay_id", "validate_vlm_review_output",
        "submit_vlm_review", "get_vlm_findings",
        "fuse_vlm_findings_into_semantic_graph", "evaluate_vlm_grounding",
        "promote_vlm_finding_to_validation_issue",
        "analyze_engineering_drawing_stages",
    }:
        return "visual grounding and engineering review"
    if name in {
        "propose_constraint_repair_plan", "propose_repair_plan",
        "validate_cad_plan", "dry_run_cad_plan", "execute_cad_plan",
    }:
        return "CADPlan planning and repair"
    if "dimension" in name or name in {"add_qdim", "set_text_alignment",
                                        "set_text_properties"}:
        return "dimensioning"
    if name.startswith(("draw_box", "draw_cone", "draw_cylinder", "draw_sphere",
                        "draw_torus", "draw_wedge", "draw_elliptical",
                        "draw_3d", "add_region", "extrude_", "revolve_",
                        "solid_", "slice_solid", "section_solid")):
        return "3D modeling"
    if name.startswith("draw_") or name.startswith("polyline_"):
        return "drawing"
    if name.endswith("_entity") or name.endswith("_entities") or name in {
        "copy_entity", "move_entity", "rotate_entity", "mirror_entity",
        "scale_entity", "offset_entity", "array_rectangular", "array_polar",
        "explode_entity", "fillet_entities", "chamfer_entities", "trim_entity",
        "extend_entity", "break_entity", "join_entities", "stretch_entities",
        "lengthen_entity", "rotate_3d", "mirror_3d", "transform_entity",
    }:
        return "editing"
    if "layer" in name:
        return "layer management"
    if "text" in name or "leader" in name or "table" in name:
        return "annotation"
    if "block" in name or "xref" in name or "attribute" in name:
        return "blocks, xrefs, and attributes"
    if "hatch" in name:
        return "hatch and fill"
    if name == "export_view_image":
        return "vision verification"
    if name.endswith("_spatial_annotation") or name.endswith("_spatial_annotations"):
        return "model-private spatial annotations"
    if name.startswith(("scan_", "select_", "highlight_", "execute_",
                        "get_entity", "get_all_tables", "get_table_schema")):
        return "query and selection"
    if name.startswith(("zoom_", "pan", "get_current_view", "get_layout",
                        "set_active_layout", "create_layout", "view")):
        return "view and layout"
    if name.startswith(("open_", "save_", "close_", "create_new_drawing",
                        "export_", "purge_", "audit_")):
        return "document and export"
    return "CAD utility"


def _default_tool_description(name: str) -> str:
    specific = {
        "build_drawing_ir": (
            "Build CAD-IR v2, the compact structured drawing index. Use after "
            "scan_all_entities for complex drawings before summarizing, "
            "editing, validating, or planning repairs."
        ),
        "summarize_drawing": (
            "Summarize the scanned drawing from structured metadata. Use with "
            "CAD-IR, semantic objects, constraints, and validation evidence "
            "instead of guessing from primitive counts."
        ),
        "detect_semantic_objects": (
            "Detect domain-level objects such as parts, holes, walls, labels, "
            "tables, dimensions, and drawing regions from scanned metadata. "
            "Use before simplifying complex drawings into generic geometry."
        ),
        "extract_drawing_constraints": (
            "Extract geometric and dimensional constraints from scanned CAD "
            "metadata. Use when design intent, alignment, symmetry, spacing, "
            "or dimension consistency matters."
        ),
        "bind_all_dimensions": (
            "Bind dimension entities to likely geometry handles. Use before "
            "changing or validating dimensioned drawings."
        ),
        "check_drawing_constraints": (
            "Check extracted constraints against current geometry and report "
            "violations for repair planning."
        ),
        "validate_geometry": (
            "Validate scanned geometry, annotations, layers, blocks, and "
            "constraints. Use before and after repairs or complex generation."
        ),
        "export_view_image_with_mapping": (
            "Export a clean view, overlay, pixel/world mapping, visible-handle "
            "index, and optional tiles for visual grounding of complex drawings."
        ),
        "analyze_engineering_drawing_stages": (
            "Analyze engineering drawing layout stages: views, sections, "
            "annotations, title blocks, BOM-like tables, VLM evidence, and "
            "semantic reconciliation."
        ),
        "propose_repair_plan": (
            "Create a guarded CADPlan from validation or VLM issues. It "
            "proposes repairs only and does not modify the DWG."
        ),
        "propose_constraint_repair_plan": (
            "Create a guarded CADPlan from violated drawing constraints. It "
            "proposes repairs only and does not modify the DWG."
        ),
        "validate_cad_plan": (
            "Validate a CADPlan's schema, dependencies, operation bindings, "
            "safety rules, and postconditions before any execution."
        ),
        "dry_run_cad_plan": (
            "Dry-run a CADPlan to preview steps, handle bindings, and likely "
            "effects without modifying AutoCAD."
        ),
        "execute_cad_plan": (
            "Execute a validated CADPlan only with explicit allow_modify=True. "
            "Use transactional=True for multi-step edits and inspect rollback "
            "status on failure."
        ),
        "execute_query": (
            "Run a scoped, bounded read-only SQL query over scanned CAD "
            "metadata. Use after scan_all_entities to filter, count, and "
            "analyze drawing entities."
        ),
        "execute_sql_query": (
            "Alias for execute_query: run a read-only SQL query over scanned "
            "CAD metadata."
        ),
        "get_all_tables": (
            "List tables available in the CAD metadata database."
        ),
        "get_table_schema": (
            "Show the column schema for a CAD metadata database table."
        ),
        "get_dimension_styles": (
            "List dimension styles in the active AutoCAD document. Requires "
            "AutoCAD to be running with an open drawing."
        ),
    }
    if name in specific:
        return specific[name]
    category = _registration_category(name)
    action = _humanize_tool_name(name)
    return (
        f"AutoCAD {category} tool: {action}. Use this named MCP tool for its "
        "specific CAD operation and capture returned handles for later edits."
    )


def _safe_log_value(value: Any) -> Any:
    if isinstance(value, Context):
        return "<mcp.Context>"
    if isinstance(value, str):
        return value if len(value) <= 160 else value[:157] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        preview = [_safe_log_value(item) for item in list(value)[:5]]
        if len(value) > 5:
            preview.append(f"...({len(value)} items)")
        return preview
    if isinstance(value, dict):
        items = list(value.items())[:10]
        result = {str(k): _safe_log_value(v) for k, v in items}
        if len(value) > 10:
            result["..."] = f"{len(value)} keys"
        return result
    return repr(value)[:160]


def _tool_call_log_context(fn, args, kwargs) -> str:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        data = {
            name: _safe_log_value(value)
            for name, value in bound.arguments.items()
            if name != "ctx"
        }
    except Exception:
        data = {
            "args": _safe_log_value(args),
            "kwargs": _safe_log_value(kwargs),
        }
    return json.dumps(data, ensure_ascii=False, default=str)


def _wrap_tool_errors(fn):
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                call_id = uuid.uuid4().hex[:12]
                logger.exception(
                    "MCP tool failed call_id=%s tool=%s params=%s error_type=%s error=%s",
                    call_id,
                    fn.__name__,
                    _tool_call_log_context(fn, args, kwargs),
                    type(exc).__name__,
                    exc,
                )
                return f"ERROR: {fn.__name__} failed: {exc} (call_id={call_id})"

        async_wrapper.__signature__ = inspect.signature(fn)
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            call_id = uuid.uuid4().hex[:12]
            logger.exception(
                "MCP tool failed call_id=%s tool=%s params=%s error_type=%s error=%s",
                call_id,
                fn.__name__,
                _tool_call_log_context(fn, args, kwargs),
                type(exc).__name__,
                exc,
            )
            return f"ERROR: {fn.__name__} failed: {exc} (call_id={call_id})"

    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


_raw_mcp_tool = mcp.tool


def _safe_mcp_tool(name=None, title=None, description=None, annotations=None,
                   icons=None, meta=None, structured_output=None):
    def decorator(fn):
        tool_name = name or fn.__name__
        tool_description = description or _default_tool_description(tool_name)
        wrapped = _wrap_tool_errors(fn)
        _raw_mcp_tool(
            name=name,
            title=title,
            description=tool_description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )(wrapped)
        return wrapped

    return decorator


mcp.tool = _safe_mcp_tool

# ── Import tool modules ─────────────────────────────────────────

from src.cad_tools import drawing_tools
from src.cad_tools import edit_tools
from src.cad_tools import layer_tools
from src.cad_tools import text_tools
from src.cad_tools import dimension_tools
from src.cad_tools import block_tools
from src.cad_tools import view_tools
from src.cad_tools import query_tools
from src.cad_tools import file_tools
from src.cad_tools import utility_tools
from src.cad_tools import solid_tools
from src.cad_tools import advanced_tools
from src.cad_tools import polyline_tools
from src.cad_tools import hatch_tools
from src.cad_tools import attribute_tools
from src.cad_understanding import analysis as understanding_analysis
from src.cad_understanding import constraints as understanding_constraints
from src.cad_understanding import dimension_binding as understanding_dimensions
from src.cad_understanding import engineering_review as understanding_engineering
from src.cad_understanding import ir_builder as understanding_ir_builder
from src.cad_understanding import plan as understanding_plan
from src.cad_understanding import resources as understanding_resources
from src.cad_understanding import semantic_graph as understanding_semantic
from src.cad_understanding import validators as understanding_validators
from src.cad_understanding import view_grounding as understanding_view
from src.cad_understanding import vlm as understanding_vlm
from src.cad_understanding.result import ok_result

# Initialize subsystems
from src.cad_controller import get_controller
from src.cad_database import get_database

ctrl = get_controller()
db = get_database()

logger.info("启动 CAD MCP 服务器")
logger.info("配置文件加载成功")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _run_strict_startup_preflight() -> Optional[Dict[str, Any]]:
    if not _env_flag("CAD_MCP_STRICT_PREFLIGHT"):
        return None
    result = utility_tools.check_runtime_environment(
        check_autocad=_env_flag("CAD_MCP_PREFLIGHT_CHECK_AUTOCAD", True),
        require_visual_export=_env_flag("CAD_MCP_PREFLIGHT_REQUIRE_VISUAL", False),
    )
    if not result.get("ok"):
        blockers = result.get("data", {}).get("blockers", [])
        details = "; ".join(
            f"{item.get('name')}: {item.get('detail')}" for item in blockers
        )
        raise RuntimeError(f"CAD MCP strict preflight failed: {details}")
    logger.info("CAD MCP strict startup preflight passed")
    return result


_run_strict_startup_preflight()

PointListInput = Union[List[float], List[List[float]]]


class XDataPair(TypedDict):
    code: int
    value: Union[str, int, float]


# ══════════════════════════════════════════════════════════════════
#  DOCUMENT TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_new_drawing(ctx: Context, template: Optional[str] = None) -> str:
    """创建新的 AutoCAD 图纸。

    这是开始任何 CAD 工作的第一步。创建空白图纸后，可以开始绘制实体。

    Args:
        template: 模板文件(.dwt)路径。为None则使用默认模板(acad.dwt)
    """
    return drawing_tools.create_new_drawing(template)


@mcp.tool()
def open_drawing(ctx: Context, filepath: str, password: Optional[str] = None) -> str:
    """打开现有的 AutoCAD 图纸文件(.dwg)。

    Args:
        filepath: 图纸文件的完整路径，如 C:/drawings/my_project.dwg
        password: 如果文件有密码保护，提供密码
    """
    return drawing_tools.open_drawing(filepath, password)


@mcp.tool()
def save_drawing(ctx: Context, filepath: Optional[str] = None) -> str:
    """保存当前 AutoCAD 图纸。

    Args:
        filepath: 保存路径。为None则保存到原路径（覆盖原文件）
    """
    return drawing_tools.save_drawing(filepath)


@mcp.tool()
def close_drawing(ctx: Context, save: bool = False) -> str:
    """关闭当前图纸。

    Args:
        save: 关闭前是否保存
    """
    return drawing_tools.close_drawing(save)


@mcp.tool()
def get_document_info(ctx: Context) -> str:
    """获取当前文档的完整信息。

    返回文档名称、路径、实体数量、图层数、块数、样式数、
    单位、作者、标题等元数据。这是 AI 了解图纸概况的主要工具。
    """
    return file_tools.get_document_info()


# ══════════════════════════════════════════════════════════════════
#  DRAWING TOOLS — Primitives
# ══════════════════════════════════════════════════════════════════

@mcp.tool(description=TOOL_DESCRIPTIONS["draw_line"])
def draw_line(ctx: Context, start_x: float, start_y: float,
              end_x: float, end_y: float, start_z: float = 0.0,
              end_z: float = 0.0, layer: Optional[str] = None,
              color: str = "bylayer") -> str:
    """在AutoCAD中绘制直线。

    这是最基础的绘图工具。两点确定一条直线。
    绘制后的实体信息会自动存储到数据库中。

    Args:
        start_x: 起点 X 坐标
        start_y: 起点 Y 坐标
        end_x:   终点 X 坐标
        end_y:   终点 Y 坐标
        start_z: 起点 Z 坐标（默认0，二维绘图可忽略）
        end_z:   终点 Z 坐标（默认0）
        layer:   图层名称。如果图层不存在会自动创建
        color:   颜色名称 (red/yellow/green/cyan/blue/magenta/white) 或 "bylayer"
    """
    return drawing_tools.draw_line(start_x, start_y, end_x, end_y,
                                   start_z, end_z, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_circle"])
def draw_circle(ctx: Context, center_x: float, center_y: float,
                radius: float, layer: Optional[str] = None,
                color: str = "bylayer") -> str:
    """在AutoCAD中绘制圆。

    指定圆心和半径即可创建圆。

    Args:
        center_x: 圆心 X 坐标
        center_y: 圆心 Y 坐标
        radius:   半径（正数）
        layer:    图层名称（可选）
        color:    颜色 (red/yellow/green/cyan/blue/magenta/white)
    """
    return drawing_tools.draw_circle(center_x, center_y, radius, layer, color)


@mcp.tool()
def draw_arc(ctx: Context, center_x: float, center_y: float,
             radius: float, start_angle: float, end_angle: float,
             layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制圆弧。

    角度以度为单位，从3点钟方向逆时针计算。

    Args:
        center_x:    圆心 X 坐标
        center_y:    圆心 Y 坐标
        radius:      半径
        start_angle: 起始角度（度），0=3点钟方向
        end_angle:   终止角度（度），如180=9点钟方向(半圆)
        layer:       图层名称
        color:       颜色
    """
    return drawing_tools.draw_arc(center_x, center_y, radius,
                                  start_angle, end_angle, layer, color)


@mcp.tool()
def draw_ellipse(ctx: Context, center_x: float, center_y: float,
                 major_x: float, major_y: float, radius_ratio: float,
                 layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制椭圆。

    Args:
        center_x:     中心 X 坐标
        center_y:     中心 Y 坐标
        major_x:      长轴端点 X（相对中心的偏移量）
        major_y:      长轴端点 Y（相对中心的偏移量）
        radius_ratio: 短轴/长轴比例 (0~1，如0.5=半高椭圆)
        layer:        图层名称
        color:        颜色
    """
    return drawing_tools.draw_ellipse(center_x, center_y, major_x, major_y,
                                      radius_ratio, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_polyline"])
def draw_polyline(ctx: Context, points: List[float],
                  closed: bool = False, layer: Optional[str] = None,
                  color: str = "bylayer") -> str:
    """绘制二维多段线(连续折线)。

    多段线是 AutoCAD 中最常用的复合图元，可以包含多条直线段和圆弧段。

    Args:
        points: 坐标列表 [x1, y1, x2, y2, ...] 至少4个值(2个点)
        closed: 是否闭合（True=形成封闭多边形）
        layer:  图层名称
        color:  颜色
    """
    return drawing_tools.draw_polyline(points, closed, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_rectangle"])
def draw_rectangle(ctx: Context, x1: float, y1: float,
                   x2: float, y2: float, layer: Optional[str] = None,
                   color: str = "bylayer") -> str:
    """绘制矩形（指定两个对角点）。

    这是 draw_polyline 的便捷封装。

    Args:
        x1, y1: 第一个角点坐标
        x2, y2: 对角的第二个角点坐标
        layer:  图层名称
        color:  颜色
    """
    return drawing_tools.draw_rectangle(x1, y1, x2, y2, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_polygon"])
def draw_polygon(ctx: Context, center_x: float, center_y: float,
                 radius: float, sides: int, start_angle: float = 0.0,
                 layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制正多边形（三角形、正方形、五边形、六边形...）。

    Args:
        center_x:    中心 X 坐标
        center_y:    中心 Y 坐标
        radius:      外接圆半径
        sides:       边数（3=三角形, 4=正方形, 5=五边形, 6=六边形, 8=八边形）
        start_angle: 起始旋转角度（度）
        layer:       图层名称
        color:       颜色
    """
    return drawing_tools.draw_polygon(center_x, center_y, radius, sides,
                                      start_angle, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_spline"])
def draw_spline(ctx: Context, fit_points: List[float],
                start_tangent: Optional[Tuple[float,float,float]] = None,
                end_tangent: Optional[Tuple[float,float,float]] = None,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制样条曲线（通过拟合点的光滑曲线）。

    样条曲线用于创建自由形状曲线，如地形轮廓、产品设计等。

    Args:
        fit_points:    拟合点列表 [x1,y1,z1, x2,y2,z2, ...]，每个点3个值，至少2个点
        start_tangent: 起始切向量 [x,y,z]（可选，控制起点方向）
        end_tangent:   终止切向量 [x,y,z]（可选，控制终点方向）
        layer:         图层名称
        color:         颜色
    """
    return drawing_tools.draw_spline(fit_points, start_tangent, end_tangent,
                                     layer, color)


@mcp.tool()
def draw_point(ctx: Context, x: float, y: float, z: float = 0.0,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制点。

    点是最简单的图元，用于标记位置。可以配合 PDMODE 系统变量改变点的显示样式。

    Args:
        x, y, z: 点坐标
        layer:   图层名称
        color:   颜色
    """
    return drawing_tools.draw_point(x, y, z, layer, color)


@mcp.tool()
def draw_3d_polyline(ctx: Context, points: List[float],
                      closed: bool = False, layer: Optional[str] = None,
                      color: str = "bylayer") -> str:
    """绘制三维多段线（每个顶点可以有不同的Z坐标）。

    与 draw_polyline 不同，3D多段线的每个顶点可以独立指定Z坐标。

    Args:
        points: 三维坐标 [x1,y1,z1, x2,y2,z2, ...] 至少6个值(2个点)
        closed: 是否闭合（True=封闭3D多段线）
        layer:  图层名称
        color:  颜色
    """
    return drawing_tools.draw_3d_polyline(points, closed, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_text"])
def draw_text(ctx: Context, text: str, insert_x: float, insert_y: float,
              height: float = 2.5, rotation: float = 0.0, z: float = 0.0,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制单行文字。

    用于添加标注、标签、标题等文字内容。

    Args:
        text:      文字内容
        insert_x:  插入点 X 坐标
        insert_y:  插入点 Y 坐标
        height:    文字高度（默认2.5单位）
        rotation:  旋转角度（度，默认0=水平）
        z:         插入点 Z 坐标
        layer:     图层名称
        color:     颜色
    """
    return drawing_tools.draw_text(text, insert_x, insert_y, height,
                                   rotation, z, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_mtext"])
def draw_mtext(ctx: Context, text: str, insert_x: float, insert_y: float,
               width: float = 0.0, height: float = 2.5,
               rotation: float = 0.0, layer: Optional[str] = None,
               color: str = "bylayer") -> str:
    """在AutoCAD中绘制多行文字(MTEXT)。

    支持换行、段落和丰富的格式化选项。

    Args:
        text:      文字内容（支持\\n换行, \\P分段）
        insert_x:  插入点 X 坐标
        insert_y:  插入点 Y 坐标
        width:     文本框宽度（0=自动宽度，不换行）
        height:    文字高度（默认2.5）
        rotation:  旋转角度（度）
        layer:     图层名称
        color:     颜色
    """
    return drawing_tools.draw_mtext(text, insert_x, insert_y, width, height,
                                    rotation, layer, color)


@mcp.tool()
def draw_donut(ctx: Context, center_x: float, center_y: float,
               inner_radius: float, outer_radius: float,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制圆环（两个同心圆之间的填充区域）。

    常用于绘制垫圈、环形构件等。

    Args:
        center_x:     中心 X 坐标
        center_y:     中心 Y 坐标
        inner_radius: 内圆半径
        outer_radius: 外圆半径（必须大于内圆半径）
        layer:        图层名称
        color:        颜色
    """
    return drawing_tools.draw_donut(center_x, center_y, inner_radius,
                                    outer_radius, layer, color)


@mcp.tool()
def draw_ray(ctx: Context, origin_x: float, origin_y: float,
              origin_z: float = 0.0, direction_x: float = 1.0,
              direction_y: float = 0.0, direction_z: float = 0.0,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制射线（从起点向指定方向无限延伸的半直线）。

    射线常用于指示方向、构建辅助几何线、投影参照等。

    Args:
        origin_x,y,z:    起点坐标
        direction_x,y,z: 方向向量（默认 (1,0,0)=X轴正方向）
        layer:           图层名称
        color:           颜色
    """
    return drawing_tools.draw_ray(origin_x, origin_y, origin_z,
                                   direction_x, direction_y, direction_z,
                                   layer, color)


@mcp.tool()
def draw_xline(ctx: Context, point1_x: float, point1_y: float,
                point1_z: float = 0.0, point2_x: float = 1.0,
                point2_y: float = 0.0, point2_z: float = 0.0,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制构造线（通过两点的双向无限长直线）。

    构造线是双向延伸的无限直线，用于布局参照、轴线、对称线等。

    Args:
        point1_x,y,z: 第一个点坐标
        point2_x,y,z: 第二个点坐标
        layer:        图层名称
        color:        颜色
    """
    return drawing_tools.draw_xline(point1_x, point1_y, point1_z,
                                     point2_x, point2_y, point2_z,
                                     layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_mline"])
def draw_mline(ctx: Context, points: List[float],
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制多线（平行多线，如墙体双线）。

    多线由多条平行的直线段组成，可自定义线数、间距和样式。
    常用于绘制建筑墙体、道路等平行线结构。

    Args:
        points: 顶点坐标列表 [x1,y1, x2,y2, ...] 至少4个值(2个点)
        layer:  图层名称
        color:  颜色
    """
    return drawing_tools.draw_mline(points, layer, color)


@mcp.tool()
def draw_2d_solid(ctx: Context, p1_x: float, p1_y: float,
                   p1_z: float = 0.0, p2_x: float = 0.0,
                   p2_y: float = 0.0, p2_z: float = 0.0,
                   p3_x: float = 0.0, p3_y: float = 0.0,
                   p3_z: float = 0.0, p4_x: Optional[float] = None,
                   p4_y: Optional[float] = None,
                   p4_z: Optional[float] = None,
                   layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制二维实体填充面（3或4个顶点的填充区域）。

    与 Hatch 填充不同，2D Solid 是轻量级的填充面，适合简单的实心区。

    Args:
        p1_x,y,z:  第一个顶点
        p2_x,y,z:  第二个顶点
        p3_x,y,z:  第三个顶点
        p4_x,y,z:  第四个顶点（可选，省略=三角形填充）
        layer:     图层名称
        color:     颜色
    """
    return drawing_tools.draw_2d_solid(p1_x, p1_y, p1_z,
                                        p2_x, p2_y, p2_z,
                                        p3_x, p3_y, p3_z,
                                        p4_x, p4_y, p4_z,
                                        layer, color)


@mcp.tool()
def draw_raster_image(ctx: Context, filepath: str, insert_x: float,
                       insert_y: float, insert_z: float = 0.0,
                       scale: float = 1.0, rotation: float = 0.0,
                       layer: Optional[str] = None) -> str:
    """在图纸中插入光栅图像（PNG, JPG, BMP, TIFF）。

    光栅图像可用作底图参考、LOGO、现场照片等。

    Args:
        filepath: 图像文件完整路径
        insert_x, insert_y, insert_z: 插入点坐标
        scale:    缩放比例（默认1.0=原始大小）
        rotation: 旋转角度（度，默认0）
        layer:    图层名称
    """
    return drawing_tools.draw_raster_image(filepath, insert_x, insert_y,
                                            insert_z, scale, rotation, layer)


@mcp.tool()
def draw_tolerance(ctx: Context, text: str, insert_x: float,
                    insert_y: float, insert_z: float = 0.0,
                    direction_x: float = 1.0,
                    direction_y: float = 0.0,
                    direction_z: float = 0.0,
                    layer: Optional[str] = None,
                    color: str = "bylayer") -> str:
    """绘制几何公差标注（GD&T特征控制框）。

    用于标注形状和位置公差：平面度、平行度、垂直度、位置度等。
    公差文字格式：如 "{\\Fgdt;j}%%v0.05%%vA"。

    Args:
        text:          公差文字（GDT格式）
        insert_x,y,z:  插入点坐标
        direction_x,y,z: 方向向量（控制框朝向）
        layer:         图层名称
        color:         颜色
    """
    return drawing_tools.draw_tolerance(text, insert_x, insert_y, insert_z,
                                         direction_x, direction_y, direction_z,
                                         layer, color)


@mcp.tool()
def draw_trace(ctx: Context, points: List[float],
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制宽线 (Trace) — 具有宽度的二维线段。

    宽线由4个点定义一条有宽度的线段，是早期 AutoCAD 版本的功能。
    点序应为 [x1,y1, x2,y2, x3,y3, x4,y4]。

    Args:
        points: 4个点坐标 [x1,y1, x2,y2, x3,y3, x4,y4]
        layer:  图层名称
        color:  颜色
    """
    return drawing_tools.draw_trace(points, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["insert_minsert_block"])
def insert_minsert_block(ctx: Context, block_name: str, x: float,
                         y: float, z: float = 0.0, x_scale: float = 1.0,
                         y_scale: float = 1.0, z_scale: float = 1.0,
                         rotation: float = 0.0, rows: int = 1,
                         cols: int = 1, row_spacing: float = 0.0,
                         col_spacing: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """Insert a block as an AutoCAD MInsert rectangular block array.

    Args:
        block_name: Block definition name.
        x, y, z: Insertion point.
        x_scale, y_scale, z_scale: Block scale factors.
        rotation: Rotation angle in degrees.
        rows: Number of rows.
        cols: Number of columns.
        row_spacing: Row spacing.
        col_spacing: Column spacing.
        layer: Optional layer name.
    """
    return drawing_tools.insert_minsert_block(block_name, x, y, z, x_scale,
                                              y_scale, z_scale, rotation,
                                              rows, cols, row_spacing,
                                              col_spacing, layer)


@mcp.tool(description=TOOL_DESCRIPTIONS["insert_minsert_block"])
def insert_minert_block(ctx: Context, block_name: str, x: float,
                          y: float, z: float = 0.0, x_scale: float = 1.0,
                          y_scale: float = 1.0, z_scale: float = 1.0,
                          rotation: float = 0.0, rows: int = 1,
                          cols: int = 1, row_spacing: float = 0.0,
                          col_spacing: float = 0.0,
                          layer: Optional[str] = None) -> str:
    """以矩形阵列方式插入图块 (MInsert)。

    将图块插入 + 矩形阵列组合为一个不可分解的实体。
    比单独 insert_block + array 更高效。

    Args:
        block_name:  图块名称
        x, y, z:     插入点坐标
        x_scale:     X缩放（默认1，负值=镜像）
        y_scale:     Y缩放（默认1）
        z_scale:     Z缩放（默认1）
        rotation:    旋转角度（度，默认0）
        rows:        阵列行数（默认1）
        cols:        阵列列数（默认1）
        row_spacing: 行间距
        col_spacing: 列间距
        layer:       图层名称
    """
    return drawing_tools.insert_minert_block(block_name, x, y, z, x_scale,
                                              y_scale, z_scale, rotation,
                                              rows, cols, row_spacing,
                                              col_spacing, layer)


@mcp.tool()
def add_shape(ctx: Context, shape_name: str, x: float, y: float,
               z: float = 0.0, scale: float = 1.0,
               rotation: float = 0.0,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制形 (Shape) — 从 .shx 形状文件中插入预定义图形。

    需要先用 LOAD 命令加载对应的 .shx 形状文件。
    形文件是编译过的轻量级符号库。

    Args:
        shape_name: 形状名称（必须在已加载的.shx文件中定义）
        x, y, z:   插入点坐标
        scale:     缩放比例
        rotation:  旋转角度（度）
        layer:     图层名称
        color:     颜色
    """
    return drawing_tools.add_shape(shape_name, x, y, z, scale, rotation,
                                    layer, color)


# ══════════════════════════════════════════════════════════════════
#  EDIT TOOLS — Entity Editing
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def move_entity(ctx: Context, handle: str, from_point: List[float],
                to_point: List[float]) -> str:
    """移动实体到新位置。

    Args:
        handle:     实体句柄（唯一标识符，由绘图工具返回）
        from_point: 基点坐标 [x, y, z]，位移参考点
        to_point:   目标点坐标 [x, y, z]，移动到此处
    """
    return edit_tools.move_entity(handle, from_point, to_point)


@mcp.tool()
def rotate_entity(ctx: Context, handle: str, base_point: List[float],
                  angle: float) -> str:
    """旋转实体。

    Args:
        handle:     实体句柄
        base_point: 旋转中心点 [x, y, z]
        angle:      旋转角度（度），逆时针为正
    """
    return edit_tools.rotate_entity(handle, base_point, angle)


@mcp.tool(description=TOOL_DESCRIPTIONS["copy_entity"])
def copy_entity(ctx: Context, handle: str,
                from_point: Optional[List[float]] = None,
                to_point: Optional[List[float]] = None) -> str:
    """复制实体。可选地在复制后移动新实体。

    Args:
        handle:     源实体句柄
        from_point: 位移基点 [x, y, z]（可选）
        to_point:   位移目标点 [x, y, z]（可选）
    """
    return edit_tools.copy_entity(handle, from_point, to_point)


@mcp.tool()
def delete_entity(ctx: Context, handle: str) -> str:
    """删除指定实体。

    警告：此操作不可撤销（除非使用undo命令）。

    Args:
        handle: 要删除的实体句柄
    """
    return edit_tools.delete_entity(handle)


@mcp.tool()
def delete_entities(ctx: Context, handles: List[str]) -> str:
    """批量删除多个实体。

    Args:
        handles: 要删除的实体句柄列表
    """
    return edit_tools.delete_entities(handles)


@mcp.tool()
def mirror_entity(ctx: Context, handle: str,
                  line_start: List[float], line_end: List[float]) -> str:
    """镜像实体（关于指定直线对称复制）。

    Args:
        handle:      实体句柄
        line_start:  镜像线起点 [x, y, z]
        line_end:    镜像线终点 [x, y, z]
    """
    return edit_tools.mirror_entity(handle, line_start, line_end)


@mcp.tool()
def scale_entity(ctx: Context, handle: str, base_point: List[float],
                 scale: float) -> str:
    """缩放实体（等比放大或缩小）。

    Args:
        handle:     实体句柄
        base_point: 缩放基点 [x, y, z]（不动点）
        scale:      缩放倍率（2=放大2倍, 0.5=缩小一半）
    """
    return edit_tools.scale_entity(handle, base_point, scale)


@mcp.tool()
def offset_entity(ctx: Context, handle: str, distance: float) -> str:
    """偏移实体（创建等距的平行线/同心圆/等距曲线）。

    适用于多段线、圆、圆弧、直线、样条曲线等。

    Args:
        handle:   实体句柄
        distance: 偏移距离。正值=外侧/右侧, 负值=内侧/左侧
    """
    return edit_tools.offset_entity(handle, distance)


@mcp.tool(description=TOOL_DESCRIPTIONS["array_rectangular"])
def array_rectangular(ctx: Context, handle: str, rows: int, columns: int,
                      row_spacing: float, column_spacing: float) -> str:
    """矩形阵列：按行和列复制实体。

    常用于创建均匀分布的对象，如柱网、螺栓孔等。

    Args:
        handle:         实体句柄
        rows:           行数（Y方向）
        columns:        列数（X方向）
        row_spacing:    行间距（正值向上）
        column_spacing: 列间距（正值向右）
    """
    return edit_tools.array_rectangular(handle, rows, columns,
                                        row_spacing, column_spacing)


@mcp.tool(description=TOOL_DESCRIPTIONS["array_polar"])
def array_polar(ctx: Context, handle: str, count: int, fill_angle: float,
                center_x: float, center_y: float,
                center_z: float = 0.0) -> str:
    """环形阵列：围绕中心点环形复制实体。

    常用于创建圆形分布的对象，如齿轮齿、法兰孔等。

    Args:
        handle:     实体句柄
        count:      阵列数量（包含原始实体）
        fill_angle: 填充角度（度），360=整圈
        center_x:   阵列中心 X 坐标
        center_y:   阵列中心 Y 坐标
        center_z:   阵列中心 Z 坐标
    """
    return edit_tools.array_polar(handle, count, fill_angle,
                                  center_x, center_y, center_z)


@mcp.tool()
def explode_entity(ctx: Context, handle: str) -> str:
    """分解实体（将块/多段线/标注/填充等复合实体分解为基本图元）。

    Args:
        handle: 要分解的实体句柄
    """
    return edit_tools.explode_entity(handle)


@mcp.tool()
def set_entity_properties(ctx: Context, handle: str,
                          color: Optional[int] = None,
                          layer: Optional[str] = None,
                          linetype: Optional[str] = None,
                          linetype_scale: Optional[float] = None,
                          lineweight: Optional[float] = None,
                          visible: Optional[bool] = None,
                          thickness: Optional[float] = None,
                          elevation: Optional[float] = None) -> str:
    """设置实体的显示属性（颜色、图层、线型等）。

    可以一次设置多个属性。只为需要改变的属性传值。

    Args:
        handle:         实体句柄
        color:          颜色索引 (1=红 2=黄 3=绿 4=青 5=蓝 6=洋红 7=白 256=ByLayer)
        layer:          目标图层名称（图层必须存在）
        linetype:       线型名称 (Continuous, Dashed, Hidden, Center, Phantom...)
        linetype_scale: 线型比例因子
        lineweight:     线宽(毫米) (0, 0.05, 0.09, 0.13, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.53, 0.60, 0.70, 0.80, 0.90, 1.00, 1.06, 1.20, 1.40, 1.58, 2.00, 2.11)
        visible:        是否可见 (True/False)
        thickness:      拉伸厚度（赋予2D实体Z方向高度）
        elevation:      标高（Z方向基面位置）
    """
    return edit_tools.set_entity_properties(
        handle, color=color, layer=layer, linetype=linetype,
        linetype_scale=linetype_scale, lineweight=lineweight,
        visible=visible, thickness=thickness, elevation=elevation)


@mcp.tool()
def get_entity_properties(ctx: Context, handle: str) -> str:
    """获取实体的完整属性（包括类型特定的几何信息）。

    返回 JSON 格式的属性列表，包含句柄、类型、图层、颜色、线型等基本属性，
    以及类型特有的几何数据（如直线有起终点，圆有圆心和半径等）。

    Args:
        handle: 实体句柄
    """
    return edit_tools.get_entity_properties(handle)


@mcp.tool()
def set_entity_truecolor(ctx: Context, handle: str, red: int,
                           green: int, blue: int) -> str:
    """将实体的颜色设置为 RGB 真彩色（超过1600万色）。

    RGB 真彩色精度远超 ACI 255色索引。可精确匹配品牌色或视觉效果。

    Args:
        handle: 实体句柄
        red:    红色分量 (0-255)
        green:  绿色分量 (0-255)
        blue:   蓝色分量 (0-255)
    """
    return edit_tools.set_entity_truecolor(handle, red, green, blue)


@mcp.tool()
def set_entity_transparency(ctx: Context, handle: str,
                             transparency: float) -> str:
    """设置实体的透明度。

    透明度 0=完全不透明，90=接近完全透明。
    可用于创建水印效果、淡化背景参照等。

    Args:
        handle:       实体句柄
        transparency: 透明度值 (0-90)
    """
    return edit_tools.set_entity_transparency(handle, transparency)


@mcp.tool()
def set_entity_plot_style(ctx: Context, handle: str,
                            plot_style: str) -> str:
    """设置实体的打印样式。

    打印样式控制在打印/出图时实体的外观（覆盖颜色、线宽等）。

    Args:
        handle:     实体句柄
        plot_style: 打印样式名称
    """
    return edit_tools.set_entity_plot_style(handle, plot_style)


@mcp.tool()
def get_extension_dictionary(ctx: Context, handle: str) -> str:
    """获取实体的扩展字典信息。

    扩展字典是可附加在任何实体上的用户自定义数据容器。
    可用于存储 XRecords（自定义命名数据）。

    Args:
        handle: 实体句柄
    """
    return edit_tools.get_extension_dictionary(handle)


@mcp.tool()
def fillet_entities(ctx: Context, handle1: str, handle2: str,
                    radius: float) -> str:
    """对两个实体执行圆角倒角（用指定半径的圆弧平滑连接）。

    适用于直线、多段线、圆弧等。圆角是机械制图中最常见的边角处理。

    Args:
        handle1: 第一个实体句柄
        handle2: 第二个实体句柄
        radius:  圆角半径（必须为正数）
    """
    return edit_tools.fillet_entities(handle1, handle2, radius)


@mcp.tool()
def chamfer_entities(ctx: Context, handle1: str, handle2: str,
                      distance1: float, distance2: float) -> str:
    """对两个实体执行倒角（斜角连接）。

    在两条线的交点处创建指定距离的斜角边。

    Args:
        handle1:   第一个实体句柄
        handle2:   第二个实体句柄
        distance1: 第一条边的倒角距离
        distance2: 第二条边的倒角距离
    """
    return edit_tools.chamfer_entities(handle1, handle2, distance1, distance2)


@mcp.tool()
def trim_entity(ctx: Context, trim_handle: str,
                 cutting_handles: List[str]) -> str:
    """用剪切边修剪实体（切除超出边界的部分）。

    需要指定一个或多个剪切边（作为边界），以及需要被修剪的实体。

    Args:
        trim_handle:     需要被修剪的实体句柄
        cutting_handles: 作为剪切边界的实体句柄列表
    """
    return edit_tools.trim_entity(trim_handle, cutting_handles)


@mcp.tool()
def extend_entity(ctx: Context, extend_handle: str,
                   boundary_handles: List[str]) -> str:
    """延伸实体到指定边界。

    将实体的一端延伸到与边界相交的位置。

    Args:
        extend_handle:   需要延伸的实体句柄
        boundary_handles:边界实体句柄列表
    """
    return edit_tools.extend_entity(extend_handle, boundary_handles)


@mcp.tool()
def break_entity(ctx: Context, handle: str, point1_x: float,
                  point1_y: float, point1_z: float = 0.0,
                  point2_x: Optional[float] = None,
                  point2_y: Optional[float] = None,
                  point2_z: float = 0.0) -> str:
    """在指定点处打断实体（将实体一分为二）。

    如果只指定一个点，则在该点处将实体分为两部分。
    如果指定两个点，则移除两点之间的部分。

    Args:
        handle:       实体句柄
        point1_x,y,z: 第一个打断点
        point2_x,y,z: 第二个打断点（可选，省略则在同一点打断）
    """
    return edit_tools.break_entity(handle, point1_x, point1_y, point1_z,
                                    point2_x, point2_y, point2_z)


@mcp.tool()
def join_entities(ctx: Context, handles: List[str]) -> str:
    """将多个同类型实体合并为一个连续实体。

    支持合并共线的直线、同心同半径的圆弧、相同多段线等。

    Args:
        handles: 需要合并的实体句柄列表（至少2个）
    """
    return edit_tools.join_entities(handles)


@mcp.tool()
def stretch_entities(ctx: Context, x1: float, y1: float,
                      x2: float, y2: float, from_x: float,
                      from_y: float, from_z: float = 0.0,
                      to_x: float = 0.0, to_y: float = 0.0,
                      to_z: float = 0.0) -> str:
    """拉伸选择窗口内实体（移动被选中的顶点/端点）。

    与 MOVE 不同，STRETCH 只移动实体中被选中的端点，
    保持与其他固定部分的连接。这是最常用的编辑操作之一。

    Args:
        x1,y1:     交叉选择窗口的第一个角点
        x2,y2:     交叉选择窗口的对角点
        from_x,y,z: 位移起点
        to_x,y,z:   位移终点
    """
    return edit_tools.stretch_entities(x1, y1, x2, y2, from_x, from_y,
                                        from_z, to_x, to_y, to_z)


@mcp.tool()
def lengthen_entity(ctx: Context, handle: str, mode: str = "delta",
                     value: float = 1.0, end: str = "both") -> str:
    """修改实体的长度（延长或缩短）。

    三种模式：
      delta   — 线性增量（正值=延长，负值=缩短）
      percent — 百分比（100=不变，200=翻倍，50=缩短一半）
      total   — 设置为指定总长度

    Args:
        handle: 实体句柄
        mode:   "delta"(增量), "percent"(百分比), "total"(总长)
        value:  修改量（与mode相关）
        end:    修改端: "both"(两端), "start"(起点端), "end"(终点端)
    """
    return edit_tools.lengthen_entity(handle, mode, value, end)


@mcp.tool()
def divide_entity(ctx: Context, handle: str, segments: int, block_name: str = "") -> str:
    """定数等分实体（在实体上按指定段数等分插入点或图块）。

    Args:
        handle:     实体句柄
        segments:   等分段数 (2-32767)
        block_name: (可选)用于标记的块名，留空则插入点对象
    """
    return edit_tools.divide_entity(handle, segments, block_name)


@mcp.tool()
def measure_entity(ctx: Context, handle: str, length: float, block_name: str = "") -> str:
    """定距等分实体（在实体上按指定间距插入点或图块）。

    Args:
        handle:     实体句柄
        length:     间距距离
        block_name: (可选)用于标记的块名，留空则插入点对象
    """
    return edit_tools.measure_entity(handle, length, block_name)


@mcp.tool()
def align_entities(ctx: Context, handles: List[str], points: List[List[float]]) -> str:
    """对齐实体。通过成对的源点和目标点集移动、旋转实体的二维或三维操作。
    如果是两对点，执行 2D 对齐（平移+旋转+缩放）。
    如果是三对点，执行 3D 对齐。

    Args:
        handles: 需要对齐的实体句柄列表
        points:  对齐点对。格式为 [[源点1, 目标点1], [源点2, 目标点2], ...]
    """
    return edit_tools.align_entities(handles, points)


@mcp.tool(description=TOOL_DESCRIPTIONS["chamfer_polyline"])
def chamfer_polyline(ctx: Context, handle: str, distance1: float, distance2: float) -> str:
    """对整个多段线的所有角点进行倒角。实用接口。

    Args:
        handle:     多段线实体句柄
        distance1:  第一条边的倒角距离
        distance2:  第二条边的倒角距离
    """
    return edit_tools.chamfer_polyline(handle, distance1, distance2)


@mcp.tool(description=TOOL_DESCRIPTIONS["fillet_polyline"])
def fillet_polyline(ctx: Context, handle: str, radius: float) -> str:
    """对整个多段线的所有角点进行圆角。实用接口。

    Args:
        handle: 多段线实体句柄
        radius: 圆角半径
    """
    return edit_tools.fillet_polyline(handle, radius)


# ══════════════════════════════════════════════════════════════════
#  LAYER TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_layer(ctx: Context, name: str, color: int = 7,
                 linetype: str = "Continuous") -> str:
    """创建新图层或修改已有图层的颜色。

    图层是 CAD 组织图形的基本单位。创建图层后，绘图工具可指定 layer 参数。

    Args:
        name:     图层名称（建议使用有意义的名称，如 WALL, DOOR, DIM）
        color:    颜色索引 (1=红,2=黄,3=绿,4=青,5=蓝,6=洋红,7=白/黑)
        linetype: 线型名称 (Continuous, Dashed, Center, Hidden, Phantom, Divide, Border)
    """
    return layer_tools.create_layer(name, color, linetype)


@mcp.tool()
def delete_layer(ctx: Context, name: str) -> str:
    """删除图层。注意：不能删除当前图层、0图层或包含实体的图层。

    Args:
        name: 要删除的图层名称
    """
    return layer_tools.delete_layer(name)


@mcp.tool()
def rename_layer(ctx: Context, old_name: str, new_name: str) -> str:
    """重命名图层。

    Args:
        old_name: 当前图层名
        new_name: 新图层名
    """
    return layer_tools.rename_layer(old_name, new_name)


@mcp.tool()
def freeze_layer(ctx: Context, name: str) -> str:
    """冻结图层（图层不可见且不参与重生成，提升性能）。

    Args:
        name: 图层名称
    """
    return layer_tools.freeze_layer(name)


@mcp.tool()
def thaw_layer(ctx: Context, name: str) -> str:
    """解冻图层（恢复冻结图层的可见性）。

    Args:
        name: 图层名称
    """
    return layer_tools.thaw_layer(name)


@mcp.tool()
def lock_layer(ctx: Context, name: str) -> str:
    """锁定图层（图层可见但不能编辑）。

    锁定图层上的实体可以被看到、捕捉、测量，但不能被修改或删除。

    Args:
        name: 图层名称
    """
    return layer_tools.lock_layer(name)


@mcp.tool()
def unlock_layer(ctx: Context, name: str) -> str:
    """解锁图层（恢复可编辑状态）。

    Args:
        name: 图层名称
    """
    return layer_tools.unlock_layer(name)


@mcp.tool()
def turn_off_layer(ctx: Context, name: str) -> str:
    """关闭图层（不可见但参与重生成计算）。

    与冻结不同，关闭的图层仍参与重生成。

    Args:
        name: 图层名称
    """
    return layer_tools.turn_off_layer(name)


@mcp.tool()
def turn_on_layer(ctx: Context, name: str) -> str:
    """打开图层（恢复可见）。

    Args:
        name: 图层名称
    """
    return layer_tools.turn_on_layer(name)


@mcp.tool()
def set_current_layer(ctx: Context, name: str) -> str:
    """设置当前图层。后续绘图操作默认在此图层上进行。

    Args:
        name: 图层名称
    """
    return layer_tools.set_current_layer(name)


@mcp.tool()
def get_all_layers(ctx: Context) -> str:
    """列出所有图层及其状态。

    显示每个图层的名称、颜色、线型、状态（冻结/锁定/关闭/正常）。
    """
    return layer_tools.get_all_layers()


@mcp.tool()
def isolate_layer(ctx: Context, name: str) -> str:
    """隔离图层：关闭除指定图层外的所有其他图层。

    这让你可以专注于特定图层上的内容。

    Args:
        name: 要保留可见的图层名称
    """
    return layer_tools.isolate_layer(name)


@mcp.tool()
def unisolate_layers(ctx: Context) -> str:
    """取消图层隔离：打开所有被隔离关闭的图层。"""
    return layer_tools.unisolate_layers()


# ══════════════════════════════════════════════════════════════════
#  TEXT & ANNOTATION TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_text_style(ctx: Context, name: str, font: str = "Arial",
                      height: float = 0.0, width: float = 1.0) -> str:
    """创建文字样式（定义字体和默认属性）。

    Args:
        name:   样式名称（如 "标题", "标注"）
        font:   字体名称 (Arial, SimSun/宋体, SimHei/黑体, Romans, txt.shx)
        height: 默认文字高度（0=每次提示输入高度, 正数=固定高度）
        width:  宽度因子（1=正常宽度, <1=窄体, >1=宽体）
    """
    return text_tools.create_text_style(name, font, height, width)


@mcp.tool()
def set_current_text_style(ctx: Context, name: str) -> str:
    """设置当前文字样式。后续创建的文字将使用此样式。

    Args:
        name: 文字样式名称
    """
    return text_tools.set_current_text_style(name)


@mcp.tool()
def get_text_styles(ctx: Context) -> str:
    """列出所有文字样式及其字体配置。"""
    return text_tools.get_text_styles()


@mcp.tool()
def add_leader(ctx: Context, points: PointListInput,
               annotation: Optional[str] = None,
               layer: Optional[str] = None) -> str:
    """绘制引线标注（带箭头的指引线 + 可选文字注释）。

    引线通常用于指向图形中的特定位置并添加说明文字。

    Args:
        points:     引线顶点列表 [x1,y1,z1, x2,y2,z2, ...] 或 [[x,y,z], ...]，至少2个点
                    最后一个点是箭头指向的位置
        annotation: 引线末端注释文字（可选）
        layer:      图层名称
    """
    return text_tools.add_leader(points, annotation, layer)


@mcp.tool(description=TOOL_DESCRIPTIONS["add_mleader"])
def add_mleader(ctx: Context, text: str, points: PointListInput,
                layer: Optional[str] = None) -> str:
    """绘制多重引线（现代化的引线标注，带文字和更好的格式）。

    Args:
        text:   引线文字内容
        points: 引线顶点列表 [x1,y1,z1, x2,y2,z2, ...] 或 [[x,y,z], ...]，至少2个点
        layer:  图层名称
    """
    return text_tools.add_mleader(text, points, layer)


@mcp.tool(description=TOOL_DESCRIPTIONS["add_table"])
def add_table(ctx: Context, insert_x: float, insert_y: float,
              rows: int, columns: int, row_height: float = 1.0,
              column_width: float = 5.0, insert_z: float = 0.0,
              layer: Optional[str] = None) -> str:
    """在图纸中插入表格。

    适合创建材料清单、零件表、标注表等。

    Args:
        insert_x:     插入点 X 坐标
        insert_y:     插入点 Y 坐标
        rows:         行数
        columns:      列数
        row_height:   行高（默认1.0）
        column_width: 列宽（默认5.0）
        insert_z:     插入点 Z 坐标（默认0）
        layer:        图层名称
    """
    return text_tools.add_table(insert_x, insert_y, rows, columns,
                                row_height, column_width, insert_z, layer)


@mcp.tool()
def edit_table_cell(ctx: Context, table_handle: str, row: int,
                    col: int, text: str) -> str:
    """编辑表格中指定单元格的文字内容。

    Args:
        table_handle: 表格实体的句柄
        row:          行号（从0开始计数）
        col:          列号（从0开始计数）
        text:         要设置的文字内容
    """
    return text_tools.edit_table_cell(table_handle, row, col, text)


@mcp.tool()
def find_text(ctx: Context, pattern: str, highlight_color: int = 1) -> str:
    """在图纸中搜索包含指定文本的所有文字实体。

    搜索范围包括单行文字(Text)和多行文字(MText)。
    匹配的实体可选地被高亮显示。

    Args:
        pattern:         要搜索的文本模式（区分大小写）
        highlight_color: 高亮颜色 (1=红, 2=黄, 3=绿, 0=不高亮)
    """
    return text_tools.find_text(pattern, highlight_color)


@mcp.tool()
def replace_text(ctx: Context, find: str, replace: str) -> str:
    """替换图纸中所有文字实体中的指定文本。

    会遍历所有文字实体，将其中的匹配部分替换为新文本。

    Args:
        find:    要查找的文本
        replace: 替换为的文本
    """
    return text_tools.replace_text(find, replace)


# ══════════════════════════════════════════════════════════════════
#  DIMENSION TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool(description=TOOL_DESCRIPTIONS["add_linear_dimension"])
def add_linear_dimension(ctx: Context, x1: float, y1: float,
                         x2: float, y2: float, text_x: float, text_y: float,
                         z1: float = 0.0, z2: float = 0.0,
                         text_z: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """添加对齐线性标注（自动测量两点间距离并显示）。

    适用于标注墙长、间距、尺寸等。

    Args:
        x1, y1:  第一个测量点坐标
        x2, y2:  第二个测量点坐标
        text_x, text_y: 标注文字线位置（距离标注对象多远）
        z1, z2, text_z: Z坐标（二维可忽略）
        layer:   图层名称
    """
    return dimension_tools.add_linear_dimension(x1, y1, x2, y2, text_x, text_y,
                                                z1, z2, text_z, layer)


@mcp.tool()
def add_rotated_dimension(ctx: Context, x1: float, y1: float,
                          x2: float, y2: float, text_x: float, text_y: float,
                          rotation: float, layer: Optional[str] = None) -> str:
    """添加旋转线性标注（可指定标注方向）。

    适用于标注斜向距离。

    Args:
        x1, y1:   第一个测量点
        x2, y2:   第二个测量点
        text_x, text_y: 标注文字位置
        rotation: 标注线的旋转角度（度）
        layer:    图层名称
    """
    return dimension_tools.add_rotated_dimension(x1, y1, x2, y2, text_x, text_y,
                                                 rotation, layer)


@mcp.tool()
def add_angular_dimension(ctx: Context, center_x: float, center_y: float,
                          x1: float, y1: float, x2: float, y2: float,
                          text_x: float, text_y: float,
                          layer: Optional[str] = None) -> str:
    """添加角度标注（测量并显示两条线之间的角度）。

    Args:
        center_x, center_y: 角的顶点坐标
        x1, y1:  第一条边上的任意点
        x2, y2:  第二条边上的任意点
        text_x, text_y: 标注文字位置
        layer:   图层名称
    """
    return dimension_tools.add_angular_dimension(center_x, center_y,
                                                 x1, y1, x2, y2,
                                                 text_x, text_y, layer)


@mcp.tool()
def add_radial_dimension(ctx: Context, center_x: float, center_y: float,
                         chord_x: float, chord_y: float,
                         leader_length: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """添加半径标注（用于标注圆弧或圆的半径）。

    标注文字会自动添加 R 前缀。

    Args:
        center_x, center_y: 圆心坐标
        chord_x, chord_y:   圆弧上的标注点
        leader_length:      引线长度（0=自动）
        layer:              图层名称
    """
    return dimension_tools.add_radial_dimension(center_x, center_y,
                                                chord_x, chord_y,
                                                leader_length, layer)


@mcp.tool()
def add_diametric_dimension(ctx: Context, chord1_x: float, chord1_y: float,
                            chord2_x: float, chord2_y: float,
                            leader_length: float = 0.0,
                            layer: Optional[str] = None) -> str:
    """添加直径标注（用于标注圆的直径）。

    标注文字会自动添加 ⌀ 前缀。

    Args:
        chord1_x, chord1_y: 直径的一个端点（圆周上）
        chord2_x, chord2_y: 直径的另一个端点（对面的圆周上）
        leader_length:      引线长度（0=自动）
        layer:              图层名称
    """
    return dimension_tools.add_diametric_dimension(chord1_x, chord1_y,
                                                   chord2_x, chord2_y,
                                                   leader_length, layer)


@mcp.tool()
def add_ordinate_dimension(ctx: Context, x: float, y: float,
                           leader_end_x: float, leader_end_y: float,
                           use_x_axis: bool = True,
                           layer: Optional[str] = None) -> str:
    """添加坐标标注（显示 X 或 Y 坐标值）。

    用于标注点的绝对坐标位置，常用于加工图纸。

    Args:
        x, y:           要标注的点坐标
        leader_end_x, leader_end_y: 引线终点（标注文字位置）
        use_x_axis:     True=标注X坐标, False=标注Y坐标
        layer:          图层名称
    """
    return dimension_tools.add_ordinate_dimension(x, y, leader_end_x,
                                                  leader_end_y, use_x_axis, layer)


@mcp.tool()
def get_dimension_styles(ctx: Context) -> str:
    """列出所有标注样式。"""
    return dimension_tools.get_dimension_styles()


@mcp.tool()
def set_current_dimension_style(ctx: Context, name: str) -> str:
    """设置当前标注样式。后续创建的标注将使用此样式。

    Args:
        name: 标注样式名称（如 "Standard", "ISO-25"）
    """
    return dimension_tools.set_current_dimension_style(name)


@mcp.tool()
def copy_dimension_style(ctx: Context, source_name: str,
                         new_name: str) -> str:
    """复制标注样式。基于现有样式创建新的标注样式。

    Args:
        source_name: 源样式名称
        new_name:    新样式名称
    """
    return dimension_tools.copy_dimension_style(source_name, new_name)


@mcp.tool(description=TOOL_DESCRIPTIONS["add_qdim"])
def add_qdim(ctx: Context, entity_handles: List[str],
              dimension_type: str = "continuous",
              layer: Optional[str] = None) -> str:
    """快速标注 (QDIM) — 一次性为多个实体生成连续尺寸。

    选择一组实体，快速生成连续/交错/基线等类型的尺寸链。
    这是最高效的批量标注方法。

    Args:
        entity_handles: 需要标注的实体句柄列表
        dimension_type: "continuous"(连续), "staggered"(交错),
                        "baseline"(基线), "ordinate"(坐标),
                        "radius"(半径), "diameter"(直径)
        layer:          图层名称
    """
    return dimension_tools.add_qdim(entity_handles, dimension_type, layer)


@mcp.tool()
def add_baseline_dimension(ctx: Context, x: float, y: float,
                             z: float = 0.0,
                             layer: Optional[str] = None) -> str:
    """添加基线标注（从上一个标注的公共基线延伸）。

    使用前提：必须先有一个线性标注作为基准标注。
    所有后续基线标注共享基准标注的第一条尺寸界线。

    Args:
        x, y, z: 下一个标注的第二个测量点（基准标注的起点为公共基线）
        layer:   图层名称
    """
    return dimension_tools.add_baseline_dimension(x, y, z, layer)


@mcp.tool()
def add_continue_dimension(ctx: Context, x: float, y: float,
                             z: float = 0.0,
                             layer: Optional[str] = None) -> str:
    """添加连续标注（首尾相接的尺寸链）。

    使用前提：必须先有一个线性标注作为起始标注。
    每个后续标注以上一个标注的终点为起点，形成尺寸链。

    Args:
        x, y, z: 下一个标注的第二个测量点
        layer:   图层名称
    """
    return dimension_tools.add_continue_dimension(x, y, z, layer)


@mcp.tool()
def draw_wipeout(ctx: Context, p1_x: float, p1_y: float,
                  p2_x: float, p2_y: float,
                  p3_x: Optional[float] = None,
                  p3_y: Optional[float] = None,
                  p4_x: Optional[float] = None,
                  p4_y: Optional[float] = None,
                  layer: Optional[str] = None) -> str:
    """创建区域覆盖 (Wipeout) — 用空白遮罩覆盖背后对象。

    用于在密集图纸中创建清晰的标注空间，或隐藏不需要显示的部分。

    Args:
        p1_x,y,p1_y: 第一个顶点
        p2_x,y,p2_y: 第二个顶点
        p3_x,y,p3_y: 第三个顶点（可选，省略则为三角形覆盖区）
        p4_x,y,p4_y: 第四个顶点（可选，省略则为三角形覆盖区）
        layer:       图层名称
    """
    return dimension_tools.draw_wipeout(p1_x, p1_y, p2_x, p2_y,
                                         p3_x, p3_y, p4_x, p4_y, layer)


@mcp.tool()
def add_arc_dimension(ctx: Context, center_x: float, center_y: float,
                       start_x: float, start_y: float,
                       end_x: float, end_y: float,
                       text_x: float, text_y: float,
                       layer: Optional[str] = None) -> str:
    """添加弧长标注（标注圆弧的弧长）。

    弧长标注在圆弧上方显示弧长值和小弧符号。

    Args:
        center_x, center_y: 圆心坐标
        start_x, start_y:   圆弧起点
        end_x, end_y:       圆弧终点
        text_x, text_y:     标注文字位置
        layer:              图层名称
    """
    return dimension_tools.add_arc_dimension(center_x, center_y,
                                               start_x, start_y,
                                               end_x, end_y,
                                               text_x, text_y, layer)


@mcp.tool()
def add_3point_angular_dimension(ctx: Context, vertex_x: float,
                                    vertex_y: float, ref1_x: float,
                                    ref1_y: float, ref2_x: float,
                                    ref2_y: float, text_x: float,
                                    text_y: float,
                                    layer: Optional[str] = None) -> str:
    """添加三点角度标注（通过三个点定义角度）。

    不需要圆心参数，直接通过三个点定义夹角。

    Args:
        vertex_x, vertex_y: 角度顶点
        ref1_x, ref1_y:     第一条参照线的端点
        ref2_x, ref2_y:     第二条参照线的端点
        text_x, text_y:     标注文字位置
        layer:              图层名称
    """
    return dimension_tools.add_3point_angular_dimension(
        vertex_x, vertex_y, ref1_x, ref1_y,
        ref2_x, ref2_y, text_x, text_y, layer)


@mcp.tool()
def set_dimension_text_override(ctx: Context, handle: str,
                                  text: str) -> str:
    """覆盖标注文字（用自定义文本替换自动测量值）。

    例如将 "100.00" 替换为 "100±0.5" 或 "参考尺寸"。
    空字符串 "" 恢复自动测量值。

    Args:
        handle: 标注实体句柄
        text:   自定义文字（空字符串=恢复自动）
    """
    return dimension_tools.set_dimension_text_override(handle, text)


@mcp.tool()
def get_dimension_measurement(ctx: Context, handle: str) -> str:
    """获取标注的测量值、文字覆盖、旋转角度等详细信息。

    Args:
        handle: 标注实体句柄
    """
    return dimension_tools.get_dimension_measurement(handle)


@mcp.tool()
def set_text_alignment(ctx: Context, handle: str, alignment: int,
                        align_x: Optional[float] = None,
                        align_y: Optional[float] = None,
                        align_z: float = 0.0) -> str:
    """设置文字对象的对齐方式。

    0=Left, 1=Center, 2=Right, 4=Middle,
    7=TopCenter, 10=MiddleCenter, 13=BottomCenter 等。

    Args:
        handle:    文字实体句柄
        alignment: 对齐代码 (0-14)
        align_x, align_y: 对齐点坐标
        align_z:   Z坐标
    """
    return dimension_tools.set_text_alignment(handle, alignment,
                                                align_x, align_y, align_z)


@mcp.tool()
def set_text_properties(ctx: Context, handle: str,
                         oblique_angle: Optional[float] = None,
                         scale_factor: Optional[float] = None,
                         style_name: Optional[str] = None) -> str:
    """设置文字的高级属性（倾斜角度、宽度因子、文字样式）。

    倾斜角度可用于创建斜体效果，宽度因子控制字体宽度。

    Args:
        handle:        文字实体句柄
        oblique_angle: 倾斜角度（度，0=正常）
        scale_factor:  宽度因子（1=正常, 0.8=窄体, 1.2=宽体）
        style_name:    文字样式名称
    """
    return dimension_tools.set_text_properties(handle, oblique_angle,
                                                 scale_factor, style_name)


# ══════════════════════════════════════════════════════════════════
#  BLOCK TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool(description=TOOL_DESCRIPTIONS["create_block"])
def create_block(ctx: Context, name: str, base_x: float, base_y: float,
                 base_z: float, entity_handles: List[str]) -> str:
    """创建图块定义（将多个实体组合为一个可重复使用的图块）。

    图块是 CAD 中最重要的重用机制。创建图块后，可以用 insert_block
    在多个位置插入它的实例。

    Args:
        name:            图块名称（建议有意义的名称，如 "Door_900", "Screw_M8"）
        base_x,base_y,base_z: 图块基点（插入时的参考点）
        entity_handles:  要包含在图块中的实体句柄列表
    """
    return block_tools.create_block(name, base_x, base_y, base_z, entity_handles)


@mcp.tool(description=TOOL_DESCRIPTIONS["insert_block"])
def insert_block(ctx: Context, name: str, x: float, y: float,
                 z: float = 0.0, x_scale: float = 1.0,
                 y_scale: float = 1.0, z_scale: float = 1.0,
                 rotation: float = 0.0,
                 layer: Optional[str] = None) -> str:
    """在当前图形中插入图块参照。

    可以指定插入点、缩放和旋转角度。缩放可用于创建不同尺寸的变体。

    Args:
        name:     图块名称（必须是已定义的图块）
        x, y, z:  插入点坐标
        x_scale:  X方向缩放（默认1=原始大小, 负值=镜像）
        y_scale:  Y方向缩放（默认1）
        z_scale:  Z方向缩放（默认1）
        rotation: 旋转角度（度，默认0）
        layer:    图层名称
    """
    return block_tools.insert_block(name, x, y, z, x_scale, y_scale, z_scale,
                                    rotation, layer)


@mcp.tool()
def get_all_blocks(ctx: Context) -> str:
    """列出图纸中所有图块定义（包括它们的实体数量和类型）。

    这对于了解图纸中有哪些可复用的图块非常有用。
    """
    return block_tools.get_all_blocks()


@mcp.tool()
def explode_block(ctx: Context, handle: str) -> str:
    """分解图块引用为基本实体。

    分解后的实体可以被单独编辑。

    Args:
        handle: 图块引用的句柄（不是图块定义名）
    """
    return block_tools.explode_block(handle)


@mcp.tool()
def attach_xref(ctx: Context, filepath: str, insert_x: float = 0,
                insert_y: float = 0, insert_z: float = 0,
                scale: float = 1.0, rotation: float = 0.0,
                layer: Optional[str] = None) -> str:
    """附加外部参照 (Xref)：将另一个 DWG 文件链接到当前图纸。

    外部参照文件独立存在，修改源文件会自动更新所有引用它的图纸。

    Args:
        filepath: 外部参照文件(.dwg)的完整路径
        insert_x, insert_y, insert_z: 插入点坐标
        scale:    缩放比例
        rotation: 旋转角度（度）
        layer:    图层名称
    """
    return block_tools.attach_xref(filepath, insert_x, insert_y, insert_z,
                                   scale, rotation, layer)


@mcp.tool()
def get_xrefs(ctx: Context) -> str:
    """列出所有外部参照及其状态。"""
    return block_tools.get_xrefs()


@mcp.tool()
def unload_xref(ctx: Context, name: str) -> str:
    """卸载外部参照（保留链接但不加载，提高性能）。

    Args:
        name: 外部参照名称
    """
    return block_tools.unload_xref(name)


@mcp.tool()
def reload_xref(ctx: Context, name: str) -> str:
    """重新加载外部参照（获取源文件的最新内容）。

    Args:
        name: 外部参照名称
    """
    return block_tools.reload_xref(name)


@mcp.tool()
def insert_block_with_attributes(ctx: Context, block_name: str,
                                   x: float, y: float,
                                   z: float = 0.0, x_scale: float = 1.0,
                                   y_scale: float = 1.0,
                                   z_scale: float = 1.0,
                                   rotation: float = 0.0,
                                   attributes: Optional[List[Dict[str, str]]] = None,
                                   layer: Optional[str] = None) -> str:
    """插入带有属性值的图块参照。

    在插入图块时同时设置属性值，避免了先插入再逐个设置属性的麻烦。
    属性列表格式: [{"tag": "DOOR_NO", "value": "D01"}, ...]

    Args:
        block_name: 图块名称
        x, y, z:    插入点坐标
        x_scale:    X缩放
        y_scale:    Y缩放
        z_scale:    Z缩放
        rotation:   旋转角度（度）
        attributes: 属性列表 [{"tag": "标签名", "value": "值"}, ...]
        layer:      图层名称
    """
    return attribute_tools.insert_block_with_attributes(
        block_name, x, y, z, x_scale, y_scale, z_scale,
        rotation, attributes, layer)


@mcp.tool()
def get_block_attributes(ctx: Context, handle: str) -> str:
    """获取图块参照的所有属性标签、值、提示等信息。

    返回完整的属性列表，每个属性包含 tag、value、prompt、height、rotation 等。

    Args:
        handle: 图块参照的实体句柄
    """
    return attribute_tools.get_block_attributes(handle)


@mcp.tool()
def set_block_attribute(ctx: Context, handle: str, tag: str,
                         value: str) -> str:
    """通过标签名设置图块参照中的单个属性值。

    精确地修改指定标签的属性值，其他属性保持不变。

    Args:
        handle: 图块参照的实体句柄
        tag:    属性标签名称
        value:  要设置的新值
    """
    return attribute_tools.set_block_attribute(handle, tag, value)


# ══════════════════════════════════════════════════════════════════
#  VIEW TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def zoom_extents(ctx: Context) -> str:
    """缩放到图形范围（让所有对象可见并填满视图）。

    这是最常用的视图命令，确保你能看到图纸中的所有内容。
    """
    return view_tools.zoom_extents()


@mcp.tool()
def zoom_window(ctx: Context, x1: float, y1: float,
                x2: float, y2: float) -> str:
    """缩放到指定矩形窗口区域。

    Args:
        x1, y1: 窗口第一个角点坐标
        x2, y2: 窗口对角点坐标
    """
    return view_tools.zoom_window(x1, y1, x2, y2)


@mcp.tool()
def zoom_center(ctx: Context, center_x: float, center_y: float,
                height: float) -> str:
    """居中缩放到指定位置。

    Args:
        center_x, center_y: 新的视图中心坐标
        height:             视图高度（图形单位，越小越放大）
    """
    return view_tools.zoom_center(center_x, center_y, height)


@mcp.tool()
def zoom_scale(ctx: Context, scale: float) -> str:
    """按比例缩放视图。

    Args:
        scale: 缩放倍率（2=放大2倍, 0.5=缩小一半）
    """
    return view_tools.zoom_scale(scale)


@mcp.tool()
def zoom_previous(ctx: Context) -> str:
    """恢复到前一个视图（撤销视图变化）。"""
    return view_tools.zoom_previous()


@mcp.tool()
def zoom_all(ctx: Context) -> str:
    """缩放到图形界限（显示整个绘图区域）。"""
    return view_tools.zoom_all()


@mcp.tool()
def pan(ctx: Context, x_offset: float, y_offset: float) -> str:
    """平移视图（不改变缩放级别）。

    Args:
        x_offset: X方向平移量（正值向右移动视图）
        y_offset: Y方向平移量（正值向上移动视图）
    """
    return view_tools.pan(x_offset, y_offset)


@mcp.tool()
def get_current_view(ctx: Context) -> str:
    """获取当前视图信息（中心、高度、宽度、目标等）。"""
    return view_tools.get_current_view()


@mcp.tool()
def get_layouts(ctx: Context) -> str:
    """列出所有布局（模型空间和所有图纸空间布局）。"""
    return view_tools.get_layouts()


@mcp.tool()
def set_active_layout(ctx: Context, name: str) -> str:
    """切换到指定布局。

    Args:
        name: 布局名称。使用 "Model" 切换到模型空间
    """
    return view_tools.set_active_layout(name)


@mcp.tool()
def create_layout(ctx: Context, name: str) -> str:
    """创建新的图纸空间布局（用于打印/出图设置）。

    Args:
        name: 新布局名称
    """
    return view_tools.create_layout(name)


# ══════════════════════════════════════════════════════════════════
#  QUERY & SELECTION TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def scan_all_entities(ctx: Context, clear_db: bool = True,
                      max_entities: int = 5000,
                      clear_annotations: bool = False,
                      clear_understanding: bool = True,
                      detail_level: str = "minimal",
                      include_bounding_boxes: bool = True,
                      derive_topology: bool = True,
                      topology_detail: str = "summary") -> str:
    """扫描当前图纸中的所有实体并保存到数据库。

    这是 AI 理解图纸的核心工具 — 它将 AutoCAD 中的图形对象转换为结构化数据，
    存入 SQLite 数据库。之后 AI 可以通过 execute_query 进行 SQL 查询、
    统计分析和智能过滤。

    建议在对图纸进行重大修改后重新扫描。

    Args:
        clear_db:     是否先清空数据库（默认True=重新扫描, False=追加）
        max_entities: 最大扫描实体数（默认5000，超大图纸请谨慎）
        clear_annotations: 是否同时清空模型私有空间标注（默认False=保留）
        clear_understanding: 是否清空当前线程派生理解缓存（默认True=避免旧缓存混入新扫描）
        detail_level: minimal/standard/full。大图默认 minimal 更快。
        include_bounding_boxes: 是否读取实体包围盒，便于后续空间查询。
        derive_topology: 是否生成拓扑表。默认生成轻量摘要，便于 agent 识别。
        topology_detail: summary/full/none。summary 只写拓扑摘要，full 写点线面关系。
    """
    return query_tools.scan_all_entities(
        clear_db=clear_db,
        max_entities=max_entities,
        clear_annotations=clear_annotations,
        clear_understanding=clear_understanding,
        detail_level=detail_level,
        include_bounding_boxes=include_bounding_boxes,
        derive_topology=derive_topology,
        topology_detail=topology_detail,
    )


@mcp.tool()
def scan_entities_in_area(ctx: Context, x_min: float, y_min: float,
                          x_max: float, y_max: float) -> str:
    """扫描指定矩形区域内的实体。

    用于关注图纸的特定区域，避免扫描整个图纸。

    Args:
        x_min, y_min: 区域左下角坐标
        x_max, y_max: 区域右上角坐标
    """
    return query_tools.scan_entities_in_area(x_min, y_min, x_max, y_max)


@mcp.tool()
def select_by_window(ctx: Context, x1: float, y1: float,
                     x2: float, y2: float) -> str:
    """窗口选择（完全在矩形窗口内的实体被选中）。

    Args:
        x1, y1: 选择窗口的第一个角点
        x2, y2: 选择窗口的对角点
    """
    return query_tools.select_by_window(x1, y1, x2, y2)


@mcp.tool()
def select_by_crossing(ctx: Context, x1: float, y1: float,
                       x2: float, y2: float) -> str:
    """交叉选择（与选择框相交的实体都被选中，比窗口选择更宽松）。

    Args:
        x1, y1: 选择框的第一个角点
        x2, y2: 选择框的对角点
    """
    return query_tools.select_by_crossing(x1, y1, x2, y2)


@mcp.tool()
def select_all(ctx: Context) -> str:
    """选择当前图纸中的所有实体。"""
    return query_tools.select_all()


@mcp.tool()
def highlight_entity(ctx: Context, handle: str, color: int = 1) -> str:
    """通过句柄高亮显示指定实体（临时改变其颜色）。

    这是查看特定实体在图中位置的最直接方式。

    Args:
        handle: 实体句柄
        color:  高亮颜色索引 (1=红, 2=黄, 3=绿, 4=青, 5=蓝, 6=洋红)
    """
    return query_tools.highlight_entity(handle, color)


@mcp.tool()
def highlight_entities(ctx: Context, handles: List[str],
                       color: int = 1) -> str:
    """批量高亮多个实体。

    Args:
        handles: 实体句柄列表
        color:   高亮颜色索引
    """
    return query_tools.highlight_entities(handles, color)


@mcp.tool()
def reset_entity_color(ctx: Context, handle: str,
                       original_color: int = 256) -> str:
    """重置实体颜色（恢复到高亮前的颜色）。

    Args:
        handle:         实体句柄
        original_color: 原始颜色索引（默认256=ByLayer）
    """
    return query_tools.reset_entity_color(handle, original_color)


@mcp.tool()
def highlight_query_results(ctx: Context, sql_query: str,
                            color: int = 1) -> str:
    """执行数据库查询并用结果在 CAD 中高亮对应实体。

    这是 AI 最强大的分析工具之一：
    1. 用 SQL 查询找出所有符合特定条件的实体
    2. 在 CAD 中高亮它们，方便人工查看

    使用 workflow:
    1. scan_all_entities  → 扫描图纸到数据库
    2. execute_query      → 用 SQL 分析数据
    3. highlight_query_results → 在 CAD 中高亮结果

    Args:
        sql_query: 必须返回 handle 列的 SQL 查询
        color:     高亮颜色 (1-6)
    """
    return query_tools.highlight_query_results(sql_query, color)


@mcp.tool()
def get_entity_statistics(ctx: Context) -> str:
    """获取当前图纸的实体统计信息（按类型和图层分类）。

    显示数据库中各类实体的数量和分布，帮助快速了解图纸的构成。
    """
    return query_tools.get_entity_statistics()


# ══════════════════════════════════════════════════════════════════
#  FILE & SYSTEM TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def export_pdf(ctx: Context, filepath: str) -> str:
    """将当前图纸导出为 PDF 文件。

    生成的 PDF 可用于打印、分享和归档。

    Args:
        filepath: PDF 保存路径，如 C:/output/drawing.pdf
    """
    return file_tools.export_pdf(filepath)


@mcp.tool()
def export_dxf(ctx: Context, filepath: str) -> str:
    """将当前图纸导出为 DXF 文件。

    DXF 是一种开放的 CAD 交换格式，可被大多数 CAD 软件读取。

    Args:
        filepath: DXF 保存路径，如 C:/output/drawing.dxf
    """
    return file_tools.export_dxf(filepath)


@mcp.tool()
def export_dwf(ctx: Context, filepath: str) -> str:
    """将当前图纸导出为 DWF (Design Web Format) 文件。

    DWF 用于在线查看和标记，文件小、不可编辑。

    Args:
        filepath: DWF 保存路径
    """
    return file_tools.export_dwf(filepath)


@mcp.tool()
def export_image(ctx: Context, filepath: str) -> str:
    """将当前视图导出为图片（BMP 或 WMF 格式）。

    Args:
        filepath: 图片保存路径（.bmp 或 .wmf 扩展名）
    """
    return file_tools.export_image(filepath)


@mcp.tool(description=TOOL_DESCRIPTIONS["export_view_image"])
def export_view_image(ctx: Context, filepath: Optional[str] = None,
                      zoom_extents_first: bool = False) -> str:
    """Export the current AutoCAD view for visual model verification.

    This creates a review artifact for a vision-capable model without adding
    visible geometry, layers, XData, blocks, or dictionaries to the DWG. Use
    zoom_extents_first=True only when the model needs the whole drawing framed.

    Args:
        filepath: Optional .wmf output path. When omitted, a timestamped file
            is written under cad_visual_exports in the MCP working directory.
        zoom_extents_first: Whether to run zoom_extents before exporting.
    """
    return file_tools.export_view_image(filepath, zoom_extents_first)


@mcp.tool()
def purge_drawing(ctx: Context) -> str:
    """清理图纸：删除所有未使用的图层、线型、文字样式、块等。

    这可以减小文件大小，提高性能。
    """
    return file_tools.purge_drawing()


@mcp.tool()
def audit_drawing(ctx: Context) -> str:
    """审计并修复当前图纸中的错误。"""
    return file_tools.audit_drawing()


@mcp.tool()
def undo(ctx: Context, count: int = 1) -> str:
    """撤销操作（回退到之前的状态）。

    Args:
        count: 撤销步数（默认1，最大100）
    """
    return file_tools.undo(count)


@mcp.tool()
def begin_undo_group(ctx: Context, name: str = "MCP") -> Dict[str, Any]:
    """Begin an AutoCAD undo group for transactional CADPlan execution."""
    return file_tools.begin_undo_group(name)


@mcp.tool()
def end_undo_group(ctx: Context, name: str = "MCP") -> Dict[str, Any]:
    """End an AutoCAD undo group."""
    return file_tools.end_undo_group(name)


@mcp.tool()
def rollback_undo_group(ctx: Context, name: str = "MCP") -> Dict[str, Any]:
    """Rollback the current AutoCAD undo group when possible."""
    return file_tools.rollback_undo_group(name)


@mcp.tool()
def redo(ctx: Context, count: int = 1) -> str:
    """重做被撤销的操作。

    Args:
        count: 重做步数（默认1）
    """
    return file_tools.redo(count)


@mcp.tool()
def regen(ctx: Context, which: str = "all") -> str:
    """重新生成图形显示（刷新视图）。

    Args:
        which: "all"=所有视口, "active"=仅活动视口
    """
    return file_tools.regen(which)


@mcp.tool(
    description=TOOL_DESCRIPTIONS["send_command"],
    annotations=ToolAnnotations(destructiveHint=True, openWorldHint=True),
)
def send_command(ctx: Context, command: str) -> str:
    """向 AutoCAD 命令行发送原始命令。

    高级工具 — 当内置工具无法满足需求时，可以直接执行任何 AutoCAD 命令。
    使用前请确保理解命令的含义。

    Args:
        command: AutoCAD 命令字符串。例如 "CIRCLE 0,0 10" 在原点绘制半径10的圆
    """
    return file_tools.send_command(command)


@mcp.tool()
def get_variable(ctx: Context, variable_name: str) -> str:
    """获取 AutoCAD 系统变量的当前值。

    常见变量: INSUNITS(单位), LTSCALE(线型比例), DIMSCALE(标注比例),
    TEXTSIZE(文字高度), PDMODE(点样式), FILLETRAD(圆角半径), etc.

    Args:
        variable_name: 系统变量名称（不区分大小写）
    """
    return file_tools.get_variable(variable_name)


@mcp.tool()
def set_variable(ctx: Context, variable_name: str, value: str) -> str:
    """设置 AutoCAD 系统变量的值。

    可以修改 CAD 行为的各种参数。

    Args:
        variable_name: 系统变量名称（如 LTSCALE, DIMSCALE, FILLETRAD）
        value:         要设置的值（数字或字符串，会自动转换类型）
    """
    return file_tools.set_variable(variable_name, value)


@mcp.tool()
def measure_distance(ctx: Context, x1: float, y1: float,
                     x2: float, y2: float, z1: float = 0.0,
                     z2: float = 0.0) -> str:
    """计算两点之间的直线距离和角度。

    不依赖 AutoCAD — 纯数学计算。

    Args:
        x1, y1, z1: 第一个点坐标
        x2, y2, z2: 第二个点坐标
    """
    return file_tools.measure_distance(x1, y1, x2, y2, z1, z2)


@mcp.tool()
def create_snapshot(ctx: Context, name: str = "") -> str:
    """创建当前图纸状态的快照（保存到数据库用于追踪变化）。

    可以在不同时间点创建多个快照，然后对比。

    Args:
        name: 快照名称（可选，默认使用图纸文件名）
    """
    return file_tools.create_snapshot(name)


@mcp.tool()
def get_snapshots(ctx: Context, limit: int = 5) -> str:
    """列出最近的图纸快照记录。

    Args:
        limit: 返回的快照数量（默认5）
    """
    return file_tools.get_snapshots(limit)


# ══════════════════════════════════════════════════════════════════
#  DATABASE TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_all_tables(ctx: Context) -> str:
    """获取 CAD 元数据库中的所有表名。

    查看数据库中有哪些数据可用。
    """
    return utility_tools.get_all_tables()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_table_schema(ctx: Context, table_name: str) -> str:
    """获取指定数据库表的列结构（列名、类型、约束）。

    Args:
        table_name: 表名（如 cad_entities, cad_layers）
    """
    return utility_tools.get_table_schema(table_name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def execute_query(ctx: Context, query: str,
                  max_rows: int = 1000,
                  timeout_ms: int = 5000,
                  max_result_bytes: int = 1_000_000) -> str:
    """在 CAD 元数据数据库上执行 SQL 查询。

    数据库包含扫描后的实体、图层、图块、文本模式等信息。
    这是 AI 理解和分析图纸内容的核心工具。

    常用表：
      cad_entities     — 所有扫描的实体（type, layer, color, properties JSON）
      cad_layers       — 图层配置
      cad_blocks       — 图块定义
      cad_geometry_primitives — 派生点/线/曲线/面/体
      cad_geometry_relations  — starts_at/ends_at/bounded_by 等拓扑关系
      cad_topology_summary    — 每个实体的点线面体摘要
      text_patterns    — 文本搜索统计
      drawing_snapshots— 图纸快照

    常用查询示例：
      SELECT type, COUNT(*) as n FROM cad_entities GROUP BY type ORDER BY n DESC
      SELECT * FROM cad_entities WHERE layer = 'WALL'
      SELECT * FROM cad_entities WHERE json_extract(geometry, '$.radius') > 10
      SELECT * FROM cad_entities WHERE type = 'AcDbText' AND json_extract(geometry, '$.text_string') LIKE '%门%'

    Args:
        query: 只读 SQL 查询字符串（SELECT/WITH/PRAGMA/EXPLAIN）
        max_rows: 最大返回行数（默认1000）
        timeout_ms: 查询超时时间（默认5000ms）
        max_result_bytes: 返回 JSON 的近似字节上限（默认1MB）
    """
    return utility_tools.execute_query(
        query,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_result_bytes=max_result_bytes,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def execute_sql_query(ctx: Context, query: str,
                      max_rows: int = 1000,
                      timeout_ms: int = 5000,
                      max_result_bytes: int = 1_000_000) -> str:
    """执行 SQL 查询（execute_query 的别名，兼容不同的命名习惯）。"""
    return utility_tools.execute_sql_query(
        query,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_result_bytes=max_result_bytes,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_workspace_context(ctx: Context) -> str:
    """Return the active workspace/conversation/thread/drawing database scope."""
    return utility_tools.get_workspace_context()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_database_maintenance_status(ctx: Context) -> str:
    """Return SQLite freelist, cache-table, and legacy database status."""
    return utility_tools.get_database_maintenance_status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def maintain_database(ctx: Context,
                      max_view_snapshots_per_scope: int = 20,
                      max_validation_reports_per_scope: int = 20,
                      incremental_vacuum_pages: int = 1000,
                      vacuum: bool = False) -> str:
    """Prune derived cache history and reclaim SQLite free pages."""
    return utility_tools.maintain_database(
        max_view_snapshots_per_scope=max_view_snapshots_per_scope,
        max_validation_reports_per_scope=max_validation_reports_per_scope,
        incremental_vacuum_pages=incremental_vacuum_pages,
        vacuum=vacuum,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def clear_understanding_cache(ctx: Context) -> str:
    """Clear semantic/constraint/validation/view caches for the active thread."""
    return utility_tools.clear_understanding_cache()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_legacy_database_status(ctx: Context) -> str:
    """Report whether retired root-level autocad_data.db is present."""
    return utility_tools.get_legacy_database_status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_workspace_context(ctx: Context,
                          workspace_root: Optional[str] = None,
                          workspace_id: Optional[str] = None,
                          conversation_id: Optional[str] = None,
                          thread_id: Optional[str] = None,
                          drawing_id: Optional[str] = None,
                          drawing_name: Optional[str] = None,
                          drawing_path: Optional[str] = None) -> str:
    """Set the workspace-aware metadata database scope for this MCP call path."""
    return utility_tools.set_workspace_context(
        workspace_root=workspace_root,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        drawing_id=drawing_id,
        drawing_name=drawing_name,
        drawing_path=drawing_path,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def activate_workspace_drawing(ctx: Context, drawing_name: str = "active",
                               drawing_path: str = "",
                               drawing_id: Optional[str] = None) -> str:
    """Switch metadata reads and writes to a drawing inside the current workspace."""
    return utility_tools.activate_workspace_drawing(
        drawing_name=drawing_name,
        drawing_path=drawing_path,
        drawing_id=drawing_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def list_workspace_drawings(ctx: Context, limit: int = 100) -> str:
    """List drawings known to the current workspace metadata database."""
    return utility_tools.list_workspace_drawings(limit)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_entity_topology(ctx: Context, handle: str) -> str:
    """获取单个实体派生出的点、线、面与拓扑关系。"""
    return utility_tools.get_entity_topology(handle)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_topology_summary(ctx: Context, limit: int = 100) -> str:
    """列出实体的点/线/面/体拓扑摘要，便于理解图纸几何关系。"""
    return utility_tools.get_topology_summary(limit)


@mcp.tool(description=TOOL_DESCRIPTIONS["add_spatial_annotation"])
def add_spatial_annotation(ctx: Context, label: str,
                           target_kind: str = "entity",
                           handle: Optional[str] = None,
                           primitive_key: Optional[str] = None,
                           description: str = "",
                           point: Optional[List[float]] = None,
                           point2: Optional[List[float]] = None,
                           bbox: Optional[List[float]] = None,
                           confidence: float = 1.0,
                           properties: Optional[Dict[str, Any]] = None,
                           annotation_id: Optional[str] = None,
                           source: str = "model") -> str:
    """Store a model-private spatial label or pointer in SQLite only.

    Use this to remember semantic parts such as "base plate", "hole array",
    "target face", or "section A" without creating any visible CAD geometry.
    Targets may be entity handles, derived primitive keys, points, bounding
    boxes, areas, views, or groups.
    """
    return utility_tools.add_spatial_annotation(
        label=label,
        target_kind=target_kind,
        handle=handle,
        primitive_key=primitive_key,
        description=description,
        point=point,
        point2=point2,
        bbox=bbox,
        confidence=confidence,
        properties=properties,
        annotation_id=annotation_id,
        source=source,
    )


@mcp.tool(
    description=TOOL_DESCRIPTIONS["list_spatial_annotations"],
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
def list_spatial_annotations(ctx: Context,
                             annotation_id: Optional[str] = None,
                             label: Optional[str] = None,
                             target_kind: Optional[str] = None,
                             handle: Optional[str] = None,
                             limit: int = 100) -> str:
    """List model-private spatial labels stored in SQLite only."""
    return utility_tools.list_spatial_annotations(
        annotation_id=annotation_id,
        label=label,
        target_kind=target_kind,
        handle=handle,
        limit=limit,
    )


@mcp.tool(description=TOOL_DESCRIPTIONS["clear_spatial_annotations"])
def clear_spatial_annotations(ctx: Context,
                              annotation_id: Optional[str] = None,
                              label: Optional[str] = None,
                              target_kind: Optional[str] = None,
                              handle: Optional[str] = None) -> str:
    """Clear model-private spatial labels from SQLite without touching the DWG."""
    return utility_tools.clear_spatial_annotations(
        annotation_id=annotation_id,
        label=label,
        target_kind=target_kind,
        handle=handle,
    )


# ══════════════════════════════════════════════════════════════════
#  GROUP & HATCH TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_group(ctx: Context, name: str, handles: List[str]) -> str:
    """创建实体组（将多个实体编组为一个可整体操作的组）。

    组的优势：可以一次性选择/编辑组内所有实体，但每个实体仍保持独立。

    Args:
        name:    组名称（建议有意义的名称）
        handles: 要包含在组中的实体句柄列表
    """
    return utility_tools.create_group(name, handles)


@mcp.tool()
def get_all_groups(ctx: Context) -> str:
    """列出所有已定义的实体组。"""
    return utility_tools.get_all_groups()


@mcp.tool(description=TOOL_DESCRIPTIONS["add_hatch"])
def add_hatch(ctx: Context, pattern_name: str = "ANSI31",
              associativity: bool = True, layer: Optional[str] = None,
              color: str = "bylayer") -> str:
    """创建图案填充对象。

    常用填充图案：
      ANSI31 = 斜线（最常用，表示剖面）
      ANSI32 = 交叉斜线
      ANSI33 = 三线交叉
      SOLID  = 实心填充
      AR-CONC= 混凝土纹理
      AR-BRSTD= 砖纹理
      AR-SAND= 沙土纹理
      EARTH  = 泥土纹理
      GRASS  = 草地纹理

    注意：创建填充对象后，还需要用 hatch_add_boundary 添加边界，
    或者直接使用 send_command 执行完整 HATCH 命令。

    Args:
        pattern_name: 填充图案名称
        associativity: 是否关联（边界改变时填充自动更新）
        layer:   图层名称
        color:   颜色
    """
    return utility_tools.add_hatch(pattern_name, associativity, layer, color)


@mcp.tool()
def hatch_add_boundary(ctx: Context, handle: str,
                        boundary_handles: List[str]) -> str:
    """为已有填充对象添加外边界环。

    填充对象必须先通过 add_hatch 创建。
    边界实体必须是闭合曲线（圆、椭圆、闭合多段线等）。

    Args:
        handle:          填充对象句柄（由 add_hatch 返回）
        boundary_handles: 边界实体句柄列表
    """
    return hatch_tools.hatch_add_boundary(handle, boundary_handles)


@mcp.tool()
def hatch_add_inner_loop(ctx: Context, handle: str,
                          inner_handles: List[str]) -> str:
    """向已有填充对象添加内部环（孤岛/空洞）。

    内部环定义了填充区域内的空白区域。

    Args:
        handle:        填充对象句柄
        inner_handles: 内部环实体句柄列表
    """
    return hatch_tools.hatch_add_inner_loop(handle, inner_handles)


@mcp.tool()
def hatch_set_properties(ctx: Context, handle: str,
                          pattern_scale: Optional[float] = None,
                          pattern_angle: Optional[float] = None,
                          pattern_double: Optional[bool] = None,
                          hatch_style: Optional[int] = None) -> str:
    """修改已有填充对象的图案属性。

    可同时设置填充比例、旋转角度、双线等。

    Args:
        handle:         填充对象句柄
        pattern_scale:  图案缩放比例（如 2=放大2倍, 0.5=缩小一半）
        pattern_angle:  图案旋转角度（度）
        pattern_double: 是否启用双线填充（True/False）
        hatch_style:    孤岛检测: 0=Normal, 1=Outer, 2=Ignore
    """
    return hatch_tools.hatch_set_properties(handle, pattern_scale,
                                              pattern_angle,
                                              pattern_double, hatch_style)


@mcp.tool()
def hatch_get_properties(ctx: Context, handle: str) -> str:
    """获取填充对象的所有属性（图案名、缩放、角度、面积、环数等）。

    Args:
        handle: 填充对象句柄
    """
    return hatch_tools.hatch_get_properties(handle)


@mcp.tool()
def hatch_set_gradient(ctx: Context, handle: str,
                        gradient_type: int = 0,
                        color1: str = "cyan",
                        color2: str = "blue") -> str:
    """将已有填充对象设置为渐变色填充。

    支持各种渐变类型（线性、圆柱、球体等）。

    Args:
        handle:         填充对象句柄
        gradient_type:  渐变类型 (0=线性, 1=圆柱, 3=球体...)
        color1:         起始颜色名称
        color2:         终止颜色名称
    """
    return hatch_tools.hatch_set_gradient(handle, gradient_type,
                                           color1, color2)


# ══════════════════════════════════════════════════════════════════
#  HELP TOOL
# ══════════════════════════════════════════════════════════════════

def _registered_tools():
    return sorted(mcp._tool_manager.list_tools(), key=lambda tool: tool.name)


def _tool_category(name: str) -> str:
    if name in {
        "build_drawing_ir", "export_drawing_ir", "summarize_drawing",
        "explain_entity", "find_entities_by_description",
        "analyze_drawing_intent", "detect_semantic_objects",
        "get_semantic_graph", "find_semantic_objects",
        "extract_drawing_constraints", "check_drawing_constraints",
        "get_drawing_constraints", "bind_dimension_to_geometry",
        "bind_all_dimensions", "validate_geometry", "get_validation_report",
        "list_cad_resources", "get_cad_resource",
    }:
        return "CAD understanding"
    if name in {
        "export_view_image_with_mapping", "get_visible_entities_in_view",
        "map_pixel_to_world", "map_world_to_pixel",
        "map_pixel_region_to_world_bbox", "ground_vlm_region",
        "ground_vlm_overlay_id", "validate_vlm_review_output",
        "submit_vlm_review", "get_vlm_findings",
        "fuse_vlm_findings_into_semantic_graph", "evaluate_vlm_grounding",
        "promote_vlm_finding_to_validation_issue",
        "analyze_engineering_drawing_stages",
    }:
        return "Visual grounding and engineering review"
    if name in {
        "propose_constraint_repair_plan", "propose_repair_plan",
        "validate_cad_plan", "dry_run_cad_plan", "execute_cad_plan",
    }:
        return "CADPlan planning and repair"
    if name == "export_view_image":
        return "Vision verification"
    if name in {"add_spatial_annotation", "list_spatial_annotations",
                "clear_spatial_annotations"}:
        return "Model-private spatial annotations"
    if name in {"create_new_drawing", "open_drawing", "save_drawing",
                "close_drawing", "get_document_info"} or name.startswith("export_"):
        return "Document and export"
    if name.startswith(("draw_box", "draw_cone", "draw_cylinder", "draw_sphere",
                        "draw_torus", "draw_wedge", "draw_elliptical",
                        "draw_3d", "add_region", "extrude_", "revolve_",
                        "solid_", "check_interference", "slice_solid",
                        "section_solid", "rotate_3d", "mirror_3d",
                        "transform_entity", "intersect_with",
                        "get_bounding_box")):
        return "3D solids and surfaces"
    if name.startswith(("draw_", "insert_minsert", "insert_minert", "add_shape")):
        return "Drawing primitives and entities"
    if name.startswith("polyline_"):
        return "Polyline operations"
    if name.endswith("_entity") or name.endswith("_entities") or name in {
        "copy_entity", "move_entity", "rotate_entity", "mirror_entity",
        "scale_entity", "offset_entity", "array_rectangular", "array_polar",
        "explode_entity", "fillet_entities", "chamfer_entities", "trim_entity",
        "extend_entity", "break_entity", "join_entities", "stretch_entities",
        "lengthen_entity", "divide_entity", "measure_entity", "align_entities",
        "fillet_polyline", "chamfer_polyline",
    }:
        return "Editing and transforms"
    if "dimension" in name or name in {"add_qdim", "set_text_alignment",
                                        "set_text_properties"}:
        return "Dimensions"
    if name.startswith(("create_layer", "delete_layer", "rename_layer",
                        "freeze_layer", "thaw_layer", "lock_layer",
                        "unlock_layer", "turn_", "set_current_layer",
                        "get_all_layers", "isolate_layer", "unisolate")):
        return "Layers"
    if "text" in name or "leader" in name or "table" in name:
        return "Text and annotation"
    if "block" in name or "xref" in name or "attribute" in name:
        return "Blocks, xrefs, attributes"
    if "hatch" in name:
        return "Hatch and fill"
    if name.startswith(("scan_", "select_", "highlight_", "reset_", "get_entity",
                        "execute_", "get_all_tables", "get_table_schema",
                        "get_topology_summary",
                        "get_tool_help", "recommend_cad_tools")):
        return "Query, selection, help"
    if name.startswith(("zoom_", "pan", "get_current_view", "get_layout",
                        "set_active_layout", "create_layout", "save_named_view",
                        "restore_named_view", "get_named_views", "delete_named_view",
                        "add_viewport", "get_viewports", "set_viewport")):
        return "Views and layouts"
    if name.startswith(("plot_", "get_plot")):
        return "Plotting"
    if name.startswith(("create_ucs", "get_all_ucs", "set_active_ucs",
                        "get_active_ucs", "translate_coordinates",
                        "polar_point", "angle_from_xaxis", "angle_to_",
                        "distance_to_", "real_to_")):
        return "Coordinates and units"
    if name.startswith(("add_hyperlink", "get_hyperlinks", "remove_hyperlink",
                        "get_xdata", "set_xdata", "create_registered",
                        "get_registered", "get_dictionaries", "create_material",
                        "get_materials", "set_entity_material",
                        "set_active_material", "load_linetype", "get_linetypes")):
        return "Metadata, materials, linetypes"
    return "System and utilities"


def _first_description_line(description: Optional[str]) -> str:
    if not description:
        return ""
    return " ".join(description.strip().splitlines()[0].split())


def _build_registered_tool_help(tool_name: Optional[str] = None) -> str:
    tools = _registered_tools()
    by_name = {tool.name: tool for tool in tools}

    if tool_name:
        name = tool_name.strip()
        tool = by_name.get(name)
        if not tool:
            matches = [t.name for t in tools if name.lower() in t.name.lower()]
            if matches:
                return (
                    f"Tool '{tool_name}' was not found. Similar tools:\n"
                    + "\n".join(f"  - {match}" for match in matches[:30])
                )
            return f"Tool '{tool_name}' was not found. Call get_tool_help() for the full registered list."

        lines = [f"Tool: {tool.name}", f"Category: {_tool_category(tool.name)}"]
        route_help = utility_tools.get_tool_help(tool.name)
        if route_help.startswith("Tool: "):
            lines.append("")
            lines.append(route_help)
        if tool.description:
            lines.append("")
            lines.append(tool.description.strip())
        props = tool.parameters.get("properties", {})
        required = set(tool.parameters.get("required", []))
        if props:
            lines.append("")
            lines.append("Parameters:")
            for param_name, schema in props.items():
                req = "required" if param_name in required else "optional"
                param_type = schema.get("type") or " / ".join(
                    item.get("type", "any") for item in schema.get("anyOf", [])
                ) or "any"
                default = f", default={schema['default']!r}" if "default" in schema else ""
                lines.append(f"  - {param_name}: {param_type} ({req}{default})")
        return "\n".join(lines)

    grouped: Dict[str, List[str]] = {}
    for tool in tools:
        grouped.setdefault(_tool_category(tool.name), []).append(tool.name)

    lines = [
        f"CAD MCP registered tools: {len(tools)}",
        "Use recommend_cad_tools(intent) when unsure; use named tools before primitives.",
        "send_command is a last-resort raw AutoCAD escape hatch.",
        "",
    ]
    for category in sorted(grouped):
        names = grouped[category]
        lines.append(f"## {category} ({len(names)})")
        for name in names:
            desc = _first_description_line(by_name[name].description)
            suffix = f" - {desc}" if desc else ""
            lines.append(f"  - {name}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip()


@mcp.resource("cad://tool-selection",
              name="CAD Tool Selection Rules",
              mime_type="text/markdown")
def cad_tool_selection_resource() -> str:
    """Model-facing rules for choosing the right CAD MCP tool."""
    return TOOL_SELECTION_INSTRUCTIONS.strip()


@mcp.resource("cad://tools",
              name="Registered CAD Tool Index",
              mime_type="text/markdown")
def cad_registered_tools_resource() -> str:
    """Complete index generated from registered MCP tools."""
    return _build_registered_tool_help()


@mcp.tool(
    description=TOOL_DESCRIPTIONS["recommend_cad_tools"],
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
def recommend_cad_tools(ctx: Context, intent: str,
                        max_results: int = 8) -> str:
    """Recommend purpose-built CAD MCP tools for a natural-language intent.

    Args:
        intent: Short task description, e.g. "draw a rectangle floor plan",
                "make 12 bolt holes", or "dimension all wall segments".
        max_results: Maximum number of matching tool recommendations to return.
    """
    return utility_tools.recommend_cad_tools(intent, max_results)


@mcp.tool(
    description=TOOL_DESCRIPTIONS["get_tool_help"],
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
def get_tool_help(ctx: Context, tool_name: Optional[str] = None) -> str:
    """获取 CAD MCP 工具帮助。

    无参数时列出所有可用工具的分类概览。
    指定工具名称时显示该工具的简要说明。

    Args:
        tool_name: 工具名称（可选，不指定则显示全部工具概览）
    """
    return _build_registered_tool_help(tool_name)


@mcp.tool(
    description=TOOL_DESCRIPTIONS["check_runtime_environment"],
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def check_runtime_environment(ctx: Context,
                              check_autocad: bool = False,
                              require_visual_export: bool = False) -> Dict[str, Any]:
    """Run runtime preflight checks before live CAD work.

    Args:
        check_autocad: Try to connect to a live AutoCAD COM instance.
        require_visual_export: Fail if no local raster/SVG review renderer is available.
    """
    return utility_tools.check_runtime_environment(
        check_autocad=check_autocad,
        require_visual_export=require_visual_export,
    )


@mcp.tool(
    description=TOOL_DESCRIPTIONS["restart_mcp"],
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
def restart_mcp(ctx: Context, delay_seconds: float = 0.5,
                exit_code: int = 0) -> str:
    """Request a soft restart of this MCP server process.

    The tool returns first, then exits the current stdio process after a short
    delay. MCP hosts that supervise the process can then restart it from the
    configured command, loading the latest code.

    Args:
        delay_seconds: Seconds to wait before exiting; clamped to 0.1..60.
        exit_code: Exit code to use when terminating the process.
    """
    return utility_tools.restart_mcp(delay_seconds, exit_code)


# ══════════════════════════════════════════════════════════════════
#  3D SOLID TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool(description=TOOL_DESCRIPTIONS["draw_box"])
def draw_box(ctx: Context, center_x: float, center_y: float,
              center_z: float, length: float, width: float, height: float,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维长方体。

    长方体是最基本的三维实体。length=X方向, width=Y方向, height=Z方向。

    Args:
        center_x, center_y, center_z: 底面中心点坐标
        length: X方向长度（正数）
        width:  Y方向宽度（正数）
        height: Z方向高度（正数）
        layer:  图层名称
        color:  颜色
    """
    return solid_tools.draw_box(center_x, center_y, center_z,
                                   length, width, height, layer, color)


@mcp.tool()
def draw_cone(ctx: Context, center_x: float, center_y: float,
               center_z: float, base_radius: float, height: float,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维圆锥体。

    Args:
        center_x, center_y, center_z: 底面中心点坐标
        base_radius: 底面半径（正数）
        height:      高度（正数）
        layer:       图层名称
        color:       颜色
    """
    return solid_tools.draw_cone(center_x, center_y, center_z,
                                    base_radius, height, layer, color)


@mcp.tool(description=TOOL_DESCRIPTIONS["draw_cylinder"])
def draw_cylinder(ctx: Context, center_x: float, center_y: float,
                   center_z: float, radius: float, height: float,
                   layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维圆柱体。

    Args:
        center_x, center_y, center_z: 底面中心点坐标
        radius: 底面半径（正数）
        height: 高度（正数）
        layer:  图层名称
        color:  颜色
    """
    return solid_tools.draw_cylinder(center_x, center_y, center_z,
                                        radius, height, layer, color)


@mcp.tool()
def draw_sphere(ctx: Context, center_x: float, center_y: float,
                 center_z: float, radius: float,
                 layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维球体。

    Args:
        center_x, center_y, center_z: 球心坐标
        radius: 半径（正数）
        layer:  图层名称
        color:  颜色
    """
    return solid_tools.draw_sphere(center_x, center_y, center_z,
                                      radius, layer, color)


@mcp.tool()
def draw_torus(ctx: Context, center_x: float, center_y: float,
                center_z: float, torus_radius: float, tube_radius: float,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维圆环体。

    Args:
        center_x, center_y, center_z: 环心坐标
        torus_radius: 环半径（中心到管心的距离）
        tube_radius:  管半径
        layer:        图层名称
        color:        颜色
    """
    return solid_tools.draw_torus(center_x, center_y, center_z,
                                     torus_radius, tube_radius, layer, color)


@mcp.tool()
def draw_wedge(ctx: Context, center_x: float, center_y: float,
                center_z: float, length: float, width: float, height: float,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维楔形体（楔形块）。

    Args:
        center_x, center_y, center_z: 底面中心点坐标
        length: X方向长度
        width:  Y方向宽度
        height: Z方向高度
        layer:  图层名称
        color:  颜色
    """
    return solid_tools.draw_wedge(center_x, center_y, center_z,
                                     length, width, height, layer, color)


@mcp.tool()
def draw_elliptical_cone(ctx: Context, center_x: float, center_y: float,
                           center_z: float, major_radius: float,
                           minor_radius: float, height: float,
                           layer: Optional[str] = None,
                           color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维椭圆锥体。

    Args:
        center_x, center_y, center_z: 底面中心
        major_radius: 长轴半径（X方向）
        minor_radius: 短轴半径（Y方向）
        height:       高度（Z方向）
        layer:        图层名称
        color:        颜色
    """
    return solid_tools.draw_elliptical_cone(center_x, center_y, center_z,
                                               major_radius, minor_radius,
                                               height, layer, color)


@mcp.tool()
def draw_elliptical_cylinder(ctx: Context, center_x: float, center_y: float,
                               center_z: float, major_radius: float,
                               minor_radius: float, height: float,
                               layer: Optional[str] = None,
                               color: str = "bylayer") -> str:
    """在AutoCAD中绘制三维椭圆柱体。

    Args:
        center_x, center_y, center_z: 底面中心
        major_radius: 长轴半径
        minor_radius: 短轴半径
        height:       高度
        layer:        图层名称
        color:        颜色
    """
    return solid_tools.draw_elliptical_cylinder(center_x, center_y, center_z,
                                                   major_radius, minor_radius,
                                                   height, layer, color)


@mcp.tool()
def draw_3d_mesh(ctx: Context, m_size: int, n_size: int,
                  vertices: List[float],
                  layer: Optional[str] = None,
                  color: str = "bylayer") -> str:
    """绘制三维多边形网格 (M×N 顶点网格)。

    Args:
        m_size:   M方向顶点数 (2-256)
        n_size:   N方向顶点数 (2-256)
        vertices: 顶点坐标列表 [x1,y1,z1, x2,y2,z2, ...] 数量 = M×N×3
        layer:    图层名称
        color:    颜色
    """
    return solid_tools.draw_3d_mesh(m_size, n_size, vertices, layer, color)


@mcp.tool()
def draw_polyface_mesh(ctx: Context, vertices: List[float],
                        face_list: List[int],
                        layer: Optional[str] = None,
                        color: str = "bylayer") -> str:
    """绘制多面网格。

    Args:
        vertices:  顶点坐标 [x1,y1,z1, x2,y2,z2, ...]
        face_list: 面索引列表，每个面4个整数（负值表示不可见边）
        layer:     图层名称
        color:     颜色
    """
    return solid_tools.draw_polyface_mesh(vertices, face_list, layer, color)


@mcp.tool()
def draw_3d_face(ctx: Context,
                  x1: float, y1: float, z1: float,
                  x2: float, y2: float, z2: float,
                  x3: float, y3: float, z3: float,
                  x4: Optional[float] = None,
                  y4: Optional[float] = None,
                  z4: Optional[float] = None,
                  layer: Optional[str] = None,
                  color: str = "bylayer") -> str:
    """绘制三维面（三角形或四边形）。

    四个顶点可定义一个四边形面；如果省略第四个顶点则为三角形面。

    Args:
        x1,y1,z1: 第一个顶点
        x2,y2,z2: 第二个顶点
        x3,y3,z3: 第三个顶点
        x4,y4,z4: 第四个顶点（可选，省略则为三角形）
        layer:    图层名称
        color:    颜色
    """
    return solid_tools.draw_3d_face(x1, y1, z1, x2, y2, z2,
                                       x3, y3, z3, x4, y4, z4, layer, color)


# ══════════════════════════════════════════════════════════════════
#  REGION & SOLID EDITING TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def add_region(ctx: Context, entity_handles: List[str],
                layer: Optional[str] = None) -> str:
    """将闭合曲线转换为面域对象。

    面域是二维封闭区域，可用于拉伸、旋转生成三维实体。
    原始曲线会被删除。

    Args:
        entity_handles: 闭合曲线的实体句柄列表（圆、封闭多段线、椭圆等）
        layer:          图层名称
    """
    return solid_tools.add_region(entity_handles, layer)


@mcp.tool()
def extrude_region(ctx: Context, region_handle: str, height: float,
                    taper_angle: float = 0.0,
                    layer: Optional[str] = None) -> str:
    """将面域拉伸为三维实体。

    Args:
        region_handle: 面域实体句柄
        height:        拉伸高度（正值向Z正向）
        taper_angle:   拔模角度（度，-90~90，0=垂直拉伸）
        layer:         图层名称
    """
    return solid_tools.extrude_region(region_handle, height,
                                         taper_angle, layer)


@mcp.tool()
def extrude_region_along_path(ctx: Context, region_handle: str,
                               path_handle: str,
                               layer: Optional[str] = None) -> str:
    """沿路径曲线拉伸面域生成三维实体。

    路径可以是多段线、样条曲线、圆弧等曲线对象。

    Args:
        region_handle: 面域实体句柄
        path_handle:   路径曲线句柄
        layer:         图层名称
    """
    return solid_tools.extrude_region_along_path(region_handle,
                                                    path_handle, layer)


@mcp.tool()
def revolve_region(ctx: Context, region_handle: str,
                    axis_x: float, axis_y: float, axis_z: float,
                    dir_x: float, dir_y: float, dir_z: float,
                    angle: float = 360.0,
                    layer: Optional[str] = None) -> str:
    """将面域绕轴旋转生成三维旋转体。

    常用于创建回转体零件，如轴、轮毂、花瓶等。

    Args:
        region_handle: 面域实体句柄
        axis_x,y,z:    旋转轴起点坐标
        dir_x,y,z:     旋转轴方向向量
        angle:         旋转角度（度，默认360=完整旋转体）
        layer:         图层名称
    """
    return solid_tools.revolve_region(region_handle,
                                         axis_x, axis_y, axis_z,
                                         dir_x, dir_y, dir_z,
                                         angle, layer)


@mcp.tool()
def solid_boolean(ctx: Context, target_handle: str, tool_handle: str,
                   operation: str = "union") -> str:
    """对两个三维实体执行布尔运算（并集/交集/差集）。

    这是三维建模的核心操作之一，可以组合多个简单实体创建复杂形状。

    Args:
        target_handle: 目标实体句柄（被修改的实体）
        tool_handle:   工具实体句柄
        operation:     运算类型: "union"(并集), "intersect"(交集), "subtract"(差集)
    """
    return solid_tools.solid_boolean(target_handle, tool_handle, operation)


@mcp.tool()
def check_interference(ctx: Context, handle1: str, handle2: str,
                        create_solid: bool = True) -> str:
    """检查两个三维实体是否干涉（相交/碰撞）。

    可用于检查零件装配干涉、碰撞检测等。

    Args:
        handle1:      第一个实体句柄
        handle2:      第二个实体句柄
        create_solid: 是否创建干涉体（True=创建相交部分的实体）
    """
    return solid_tools.check_interference(handle1, handle2, create_solid)


@mcp.tool()
def slice_solid(ctx: Context, handle: str,
                 p1_x: float, p1_y: float, p1_z: float,
                 p2_x: float, p2_y: float, p2_z: float,
                 p3_x: float, p3_y: float, p3_z: float,
                 negative_side_only: bool = False) -> str:
    """用三点定义的平面对三维实体进行剖切。

    Args:
        handle:             实体句柄
        p1_x,y,z:           平面上第一个点
        p2_x,y,z:           平面上第二个点
        p3_x,y,z:           平面上第三个点
        negative_side_only: 是否只保留一侧（默认保留两侧）
    """
    return solid_tools.slice_solid(handle,
                                      p1_x, p1_y, p1_z,
                                      p2_x, p2_y, p2_z,
                                      p3_x, p3_y, p3_z,
                                      negative_side_only)


@mcp.tool()
def section_solid(ctx: Context, handle: str,
                   p1_x: float, p1_y: float, p1_z: float,
                   p2_x: float, p2_y: float, p2_z: float,
                   p3_x: float, p3_y: float, p3_z: float) -> str:
    """创建三维实体的截面（生成二维面域）。

    不修改原始实体，生成一个新的面域表示截面形状。

    Args:
        handle:      实体句柄
        p1_x,y,z:    截面上第一个点
        p2_x,y,z:    截面上第二个点
        p3_x,y,z:    截面上第三个点
    """
    return solid_tools.section_solid(handle,
                                        p1_x, p1_y, p1_z,
                                        p2_x, p2_y, p2_z,
                                        p3_x, p3_y, p3_z)


# ══════════════════════════════════════════════════════════════════
#  3D ENTITY OPERATIONS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def rotate_3d(ctx: Context, handle: str,
               axis_x1: float, axis_y1: float, axis_z1: float,
               axis_x2: float, axis_y2: float, axis_z2: float,
               angle: float) -> str:
    """围绕三维空间中的轴旋转实体。

    与普通旋转不同，此操作可围绕任意3D轴旋转。

    Args:
        handle:                  实体句柄
        axis_x1,y1,z1:           旋转轴起点
        axis_x2,y2,z2:           旋转轴终点
        angle:                   旋转角度（度，右手法则）
    """
    return solid_tools.rotate_3d(handle,
                                    axis_x1, axis_y1, axis_z1,
                                    axis_x2, axis_y2, axis_z2,
                                    angle)


@mcp.tool()
def mirror_3d(ctx: Context, handle: str,
               p1_x: float, p1_y: float, p1_z: float,
               p2_x: float, p2_y: float, p2_z: float,
               p3_x: float, p3_y: float, p3_z: float) -> str:
    """关于三维空间中的平面对实体进行3D镜像。

    Args:
        handle:       实体句柄
        p1_x,y,z:     平面上第一个点
        p2_x,y,z:     平面上第二个点
        p3_x,y,z:     平面上第三个点
    """
    return solid_tools.mirror_3d(handle,
                                    p1_x, p1_y, p1_z,
                                    p2_x, p2_y, p2_z,
                                    p3_x, p3_y, p3_z)


@mcp.tool()
def get_bounding_box(ctx: Context, handle: str) -> str:
    """获取实体的轴对齐包围盒（最小/最大坐标）。

    Args:
        handle: 实体句柄
    """
    return solid_tools.get_bounding_box(handle)


@mcp.tool()
def intersect_with(ctx: Context, handle1: str, handle2: str,
                    extend_option: int = 0) -> str:
    """计算两个实体的交点。

    适用于直线、圆弧、圆、椭圆、样条曲线、多段线之间的求交。

    Args:
        handle1:       第一个实体句柄
        handle2:       第二个实体句柄
        extend_option: 延伸选项:
                       0=都延伸 1=延伸第一个
                       2=延伸第二个 3=都不延伸
    """
    return solid_tools.intersect_with(handle1, handle2, extend_option)


@mcp.tool()
def transform_entity(ctx: Context, handle: str,
                      matrix: List[List[float]]) -> str:
    """对实体应用4×4变换矩阵（平移、旋转、缩放的综合变换）。

    矩阵格式: [[a,b,c,d], [e,f,g,h], [i,j,k,l], [m,n,o,p]]

    Args:
        handle: 实体句柄
        matrix: 4×4变换矩阵（16个数值的二维数组）
    """
    return solid_tools.transform_entity(handle, matrix)


# ══════════════════════════════════════════════════════════════════
#  HYPERLINKS & XDATA TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def add_hyperlink(ctx: Context, handle: str, url: str,
                   description: str = "",
                   named_location: str = "") -> str:
    """为实体添加超链接。

    可以链接到网页、本地文件或图纸中的命名视图。

    Args:
        handle:          实体句柄
        url:             链接URL（网页地址或本地文件路径）
        description:     链接描述文字（鼠标悬停时显示）
        named_location:  目标中的命名位置（可选）
    """
    return advanced_tools.add_hyperlink(handle, url, description,
                                         named_location)


@mcp.tool()
def get_hyperlinks(ctx: Context, handle: str) -> str:
    """获取实体上所有超链接的详细信息。

    Args:
        handle: 实体句柄
    """
    return advanced_tools.get_hyperlinks(handle)


@mcp.tool()
def remove_hyperlink(ctx: Context, handle: str, index: int = 0) -> str:
    """删除实体上的指定超链接。

    Args:
        handle: 实体句柄
        index:  超链接索引（0=第一个，1=第二个，...）
    """
    return advanced_tools.remove_hyperlink(handle, index)


@mcp.tool()
def get_xdata(ctx: Context, handle: str, app_name: str = "") -> str:
    """获取实体上的扩展数据 (XData)。

    扩展数据是附着在实体上的自定义数据，
    可用于存储AI生成的元数据、分类标签、注释等。

    Args:
        handle:   实体句柄
        app_name: 注册应用名称（空字符串=获取所有应用的XData）
    """
    return advanced_tools.get_xdata(handle, app_name)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_xdata(ctx: Context, handle: str, app_name: str,
               data_pairs: List[XDataPair]) -> str:
    """为实体设置扩展数据 (XData)。

    可以在实体上存储自定义的结构化数据。
    例如: [{"code": 1000, "value": "承重墙"}, {"code": 1040, "value": 3.5}]

    常用DXF组码:
      1000: ASCII字符串  1001: 应用名称
      1040: 实数        1070: 整数

    Args:
        handle:     实体句柄
        app_name:   注册应用名称（会自动注册）
        data_pairs: 数据对列表 [{"code": 组码, "value": 值}, ...]
    """
    return advanced_tools.set_xdata(handle, app_name, data_pairs)


# ══════════════════════════════════════════════════════════════════
#  UCS TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_ucs(ctx: Context, origin_x: float, origin_y: float,
                origin_z: float, x_axis_x: float, x_axis_y: float,
                x_axis_z: float, y_axis_x: float, y_axis_y: float,
                y_axis_z: float, name: str) -> str:
    """创建命名用户坐标系 (UCS)。

    UCS 定义自定义坐标系统，用于在3D空间中的特定平面上绘图。

    Args:
        origin_x,y,z:  UCS原点（WCS坐标）
        x_axis_x,y,z:  X轴正方向点（定义X轴方向）
        y_axis_x,y,z:  Y轴正方向点（定义Y轴方向）
        name:          UCS名称
    """
    return advanced_tools.create_ucs(origin_x, origin_y, origin_z,
                                      x_axis_x, x_axis_y, x_axis_z,
                                      y_axis_x, y_axis_y, y_axis_z,
                                      name)


@mcp.tool()
def get_all_ucs(ctx: Context) -> str:
    """列出所有命名UCS（用户坐标系）。"""
    return advanced_tools.get_all_ucs()


@mcp.tool()
def set_active_ucs(ctx: Context, name: str) -> str:
    """激活指定的UCS。

    Args:
        name: UCS名称
    """
    return advanced_tools.set_active_ucs(name)


@mcp.tool()
def get_active_ucs(ctx: Context) -> str:
    """获取当前活动UCS的详细信息。"""
    return advanced_tools.get_active_ucs()


# ══════════════════════════════════════════════════════════════════
#  NAMED VIEWS TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def save_named_view(ctx: Context, name: str) -> str:
    """将当前视图保存为命名视图（方便后续快速恢复视角）。

    Args:
        name: 视图名称（如 "正面视角", "细节视图A"）
    """
    return advanced_tools.save_named_view(name)


@mcp.tool()
def restore_named_view(ctx: Context, name: str) -> str:
    """恢复到之前保存的命名视图。

    Args:
        name: 视图名称
    """
    return advanced_tools.restore_named_view(name)


@mcp.tool()
def get_named_views(ctx: Context) -> str:
    """列出所有命名视图及其配置。"""
    return advanced_tools.get_named_views()


@mcp.tool()
def delete_named_view(ctx: Context, name: str) -> str:
    """删除命名视图。

    Args:
        name: 视图名称
    """
    return advanced_tools.delete_named_view(name)


# ══════════════════════════════════════════════════════════════════
#  VIEWPORT TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def add_viewport(ctx: Context, center_x: float, center_y: float,
                  width: float, height: float,
                  layer: Optional[str] = None) -> str:
    """在图纸空间布局中创建视口。

    视口用于在布局中显示模型空间的内容，可设置不同的比例和视角。

    Args:
        center_x, center_y: 视口中心点（图纸空间坐标）
        width:              视口宽度
        height:             视口高度
        layer:              图层名称
    """
    return advanced_tools.add_viewport(center_x, center_y, width, height, layer)


@mcp.tool()
def get_viewports(ctx: Context) -> str:
    """列出所有图纸空间视口。"""
    return advanced_tools.get_viewports()


@mcp.tool()
def set_viewport_properties(ctx: Context, handle: str,
                             display_locked: Optional[bool] = None,
                             custom_scale: Optional[float] = None,
                             on: Optional[bool] = None) -> str:
    """设置图纸空间视口的属性。

    Args:
        handle:         视口句柄
        display_locked: 是否锁定视口显示（锁定后无法缩放/平移）
        custom_scale:   自定义缩放比例（如 1/100 = 0.01）
        on:             是否打开视口
    """
    return advanced_tools.set_viewport_properties(handle,
                                                   display_locked,
                                                   custom_scale, on)


# ══════════════════════════════════════════════════════════════════
#  PLOT/PRINT TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def plot_to_device(ctx: Context, plot_config: str = "") -> str:
    """将当前布局发送到打印设备/绘图仪。

    Args:
        plot_config: 打印配置名称（PC3文件名或Windows打印机名称）
    """
    return advanced_tools.plot_to_device(plot_config)


@mcp.tool()
def plot_to_file(ctx: Context, filepath: str, plot_config: str = "") -> str:
    """将当前布局打印输出到 PLT 文件。

    Args:
        filepath:    输出文件路径（如 C:/plots/drawing.plt）
        plot_config: 打印配置名称
    """
    return advanced_tools.plot_to_file(filepath, plot_config)


@mcp.tool()
def plot_preview(ctx: Context, preview_type: int = 1) -> str:
    """显示打印预览窗口。

    Args:
        preview_type: 0=部分预览（纸张大小）, 1=完整预览（含图形内容）
    """
    return advanced_tools.plot_preview(preview_type)


@mcp.tool()
def get_plot_devices(ctx: Context) -> str:
    """列出所有可用的打印设备/绘图仪。"""
    return advanced_tools.get_plot_devices()


@mcp.tool()
def get_plot_style_tables(ctx: Context) -> str:
    """列出所有可用的打印样式表（CTB和STB文件）。"""
    return advanced_tools.get_plot_style_tables()


@mcp.tool()
def get_plot_configurations(ctx: Context) -> str:
    """列出所有命名页面设置（打印配置）。"""
    return advanced_tools.get_plot_configurations()


# ══════════════════════════════════════════════════════════════════
#  MATERIALS TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_material(ctx: Context, name: str, description: str = "") -> str:
    """创建新的渲染材质。

    Args:
        name:        材质名称（如 "不锈钢", "木材"）
        description: 材质描述
    """
    return advanced_tools.create_material(name, description)


@mcp.tool()
def get_materials(ctx: Context) -> str:
    """列出所有已定义的材质。"""
    return advanced_tools.get_materials()


@mcp.tool()
def set_entity_material(ctx: Context, handle: str,
                         material_name: str) -> str:
    """为实体分配材质。

    Args:
        handle:        实体句柄
        material_name: 材质名称
    """
    return advanced_tools.set_entity_material(handle, material_name)


@mcp.tool()
def set_active_material(ctx: Context, material_name: str) -> str:
    """设置当前材质（新创建的对象将使用此材质）。

    Args:
        material_name: 材质名称
    """
    return advanced_tools.set_active_material(material_name)


# ══════════════════════════════════════════════════════════════════
#  LINETYPE TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def load_linetype(ctx: Context, name: str, filename: str = "acad.lin") -> str:
    """从线型库文件加载线型。

    常用线型: HIDDEN(虚线), CENTER(中心线), DASHDOT(点划线),
    PHANTOM(假想线), DIVIDE(分界线), BORDER(边界线)

    Args:
        name:     线型名称
        filename: 线型库文件名（默认 acad.lin，也可用 acadiso.lin）
    """
    return advanced_tools.load_linetype(name, filename)


@mcp.tool()
def get_linetypes(ctx: Context) -> str:
    """列出所有已加载的线型。"""
    return advanced_tools.get_linetypes()


# ══════════════════════════════════════════════════════════════════
#  UTILITY / GEOMETRY TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def polar_point(ctx: Context, x: float, y: float, z: float,
                 angle_deg: float, distance: float) -> str:
    """计算从起点出发，指定角度和距离的目标点坐标。

    常用于：已知一个点和方向，计算另一个点的位置。

    Args:
        x, y, z:   起点坐标
        angle_deg: 角度（度，从X轴逆时针计算）
        distance:  距离
    """
    return advanced_tools.polar_point(x, y, z, angle_deg, distance)


@mcp.tool()
def translate_coordinates(ctx: Context, x: float, y: float, z: float,
                           from_cs: int = 0, to_cs: int = 1) -> str:
    """在不同坐标系之间转换坐标。

    坐标系统: 0=WCS世界, 1=UCS用户, 2=DCS显示, 3=PSDCS图纸空间, 4=OCS对象

    Args:
        x, y, z: 源坐标
        from_cs: 源坐标系代码（默认0=WCS）
        to_cs:   目标坐标系代码（默认1=UCS）
    """
    return advanced_tools.translate_coordinates(x, y, z, from_cs, to_cs)


@mcp.tool()
def angle_from_xaxis(ctx: Context, x1: float, y1: float,
                      x2: float, y2: float, z1: float = 0.0,
                      z2: float = 0.0) -> str:
    """计算两点连线与X轴之间的夹角。

    Args:
        x1,y1,z1: 第一个点
        x2,y2,z2: 第二个点
    """
    return advanced_tools.angle_from_xaxis(x1, y1, x2, y2, z1, z2)


# ══════════════════════════════════════════════════════════════════
#  PREFERENCES TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def get_preference(ctx: Context, pref_path: str) -> str:
    """读取单个 AutoCAD 偏好设置值。

    pref_path 格式: "Category.Property"

    常用示例:
      Display.CursorSize — 十字光标大小 (1-100)
      Drafting.AutoSnapMarker — 自动捕捉标记开关
      OpenSave.AutoSaveInterval — 自动保存间隔(分钟)
      Selection.PickBoxSize — 拾取框大小
      Selection.DisplayGrips — 夹点显示

    Args:
        pref_path: 偏好路径
    """
    return advanced_tools.get_preference(pref_path)


@mcp.tool()
def set_preference(ctx: Context, pref_path: str, value: str) -> str:
    """设置单个 AutoCAD 偏好设置。

    Args:
        pref_path: 偏好路径（如 OpenSave.AutoSaveInterval）
        value:     新值（数字或 True/False）
    """
    return advanced_tools.set_preference(pref_path, value)


@mcp.tool()
def get_preferences_display(ctx: Context) -> str:
    """获取显示相关偏好设置（光标大小、布局选项卡等）。"""
    return advanced_tools.get_preferences_display()


@mcp.tool()
def get_preferences_drafting(ctx: Context) -> str:
    """获取绘图相关偏好设置（自动捕捉、极轴追踪等）。"""
    return advanced_tools.get_preferences_drafting()


@mcp.tool()
def get_preferences_files(ctx: Context) -> str:
    """获取文件路径偏好设置（支持路径、模板路径等）。"""
    return advanced_tools.get_preferences_files()


@mcp.tool()
def get_preferences_opensave(ctx: Context) -> str:
    """获取打开/保存偏好设置（自动保存间隔、备份等）。"""
    return advanced_tools.get_preferences_opensave()


@mcp.tool()
def get_preferences_selection(ctx: Context) -> str:
    """获取选择偏好设置（夹点、拾取框大小等）。"""
    return advanced_tools.get_preferences_selection()


@mcp.tool()
def get_preferences_system(ctx: Context) -> str:
    """获取系统偏好设置。"""
    return advanced_tools.get_preferences_system()


@mcp.tool()
def get_preferences_user(ctx: Context) -> str:
    """获取用户偏好设置。"""
    return advanced_tools.get_preferences_user()


# ══════════════════════════════════════════════════════════════════
#  APPLICATION & DOCUMENT UTILITY TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def get_application_info(ctx: Context) -> str:
    """获取 AutoCAD 应用程序信息（版本号、安装路径等）。"""
    return advanced_tools.get_application_info()


@mcp.tool()
def is_autocad_idle(ctx: Context) -> str:
    """检查 AutoCAD 是否处于空闲状态（未处理命令）。"""
    return advanced_tools.is_autocad_idle()


@mcp.tool()
def set_document_properties(ctx: Context, title: Optional[str] = None,
                             subject: Optional[str] = None,
                             author: Optional[str] = None,
                             keywords: Optional[str] = None,
                             comments: Optional[str] = None) -> str:
    """设置当前图纸的摘要属性（标题、作者、关键词等）。

    Args:
        title:    图纸标题
        subject:  主题
        author:   作者
        keywords: 关键词（逗号分隔）
        comments: 注释
    """
    return advanced_tools.set_document_properties(title, subject, author,
                                                   keywords, comments)


@mcp.tool()
def set_drawing_password(ctx: Context, password: str) -> str:
    """为当前图纸设置打开密码（加密保存）。

    注意：设置密码后必须保存文件才能生效。

    Args:
        password: 密码字符串
    """
    return advanced_tools.set_drawing_password(password)


@mcp.tool()
def get_file_dependencies(ctx: Context) -> str:
    """列出当前图纸的所有文件依赖（外部参照、图片、字体等）。"""
    return advanced_tools.get_file_dependencies()


@mcp.tool()
def get_active_space_info(ctx: Context) -> str:
    """获取当前工作空间信息（模型空间还是图纸空间）。"""
    return advanced_tools.get_active_space_info()


# ══════════════════════════════════════════════════════════════════
#  SELECTION ENHANCEMENTS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def select_by_fence(ctx: Context, points: List[float]) -> str:
    """栏选：选择与指定折线相交的所有实体。

    栏选是 CAD 中高效的批量选择方式，穿越栏选线的实体都会被选中。

    Args:
        points: 栏选折线的顶点坐标 [x1,y1, x2,y2, x3,y3, ...]
    """
    return advanced_tools.select_by_fence(points)


@mcp.tool()
def select_by_wpolygon(ctx: Context, points: List[float]) -> str:
    """窗口多边形选择：选择完全在多边形内部的实体。

    Args:
        points: 多边形顶点坐标列表 [x1,y1, x2,y2, ...]
    """
    return advanced_tools.select_by_wpolygon(points)


@mcp.tool()
def select_by_cpolygon(ctx: Context, points: List[float]) -> str:
    """交叉多边形选择：选择与多边形相交或在其内的实体。

    Args:
        points: 多边形顶点坐标列表 [x1,y1, x2,y2, ...]
    """
    return advanced_tools.select_by_cpolygon(points)


@mcp.tool()
def select_at_point(ctx: Context, x: float, y: float,
                    z: float = 0.0) -> str:
    """选择经过指定点的所有实体。

    Args:
        x, y, z: 选择点坐标
    """
    return advanced_tools.select_at_point(x, y, z)


# ══════════════════════════════════════════════════════════════════
#  DICTIONARIES & REGISTERED APPS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def create_registered_application(ctx: Context, name: str) -> str:
    """注册新的应用名称（用于存储扩展数据 XData）。

    Args:
        name: 应用名称（如 "MY_AI_METADATA", "PROJECT_DATA"）
    """
    return advanced_tools.create_registered_application(name)


@mcp.tool()
def get_registered_applications(ctx: Context) -> str:
    """列出所有已注册的应用名称。"""
    return advanced_tools.get_registered_applications()


@mcp.tool()
def get_dictionaries(ctx: Context) -> str:
    """列出所有命名字典（用于存储自定义对象和 XRecords）。"""
    return advanced_tools.get_dictionaries()


# ══════════════════════════════════════════════════════════════════
#  ADDITIONAL UTILITY TOOLS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def angle_to_real(ctx: Context, angle_str: str, unit: int = 0) -> str:
    """将角度字符串解析为弧度值。

    单位: 0=十进制度, 1=度/分/秒, 2=百分度(grads), 3=弧度

    Args:
        angle_str: 角度字符串（如 "45.5", "45d30'0\""）
        unit:      角度单位
    """
    return utility_tools.angle_to_real(angle_str, unit)


@mcp.tool()
def angle_to_string(ctx: Context, angle_rad: float, unit: int = 0,
                     precision: int = 2) -> str:
    """将弧度角度格式化为指定单位的字符串。

    Args:
        angle_rad: 角度值（弧度）
        unit:      单位: 0=十进制度, 1=度/分/秒, 2=百分度, 3=弧度
        precision: 精度位数
    """
    return utility_tools.angle_to_string(angle_rad, unit, precision)


@mcp.tool()
def distance_to_real(ctx: Context, dist_str: str, unit: int = 0) -> str:
    """将距离字符串解析为实数。

    单位: 0=十进制, 1=工程制, 2=建筑制, 3=分数制

    Args:
        dist_str: 距离字符串
        unit:     单位
    """
    return utility_tools.distance_to_real(dist_str, unit)


@mcp.tool()
def real_to_string(ctx: Context, value: float, unit: int = 0,
                    precision: int = 2) -> str:
    """将实数值格式化为指定单位的字符串。

    Args:
        value:     数值
        unit:      单位: 0=十进制, 1=工程制, 2=建筑制, 3=分数制
        precision: 精度位数
    """
    return utility_tools.real_to_string(value, unit, precision)


@mcp.tool()
def select_on_screen(ctx: Context) -> str:
    """交互式屏幕选择 — 提示用户在 AutoCAD 中手动选择实体。

    注意：这需要用户正在交互式使用 AutoCAD。
    """
    return utility_tools.select_on_screen()


@mcp.tool(
    description=TOOL_DESCRIPTIONS["delete_selection_set"],
    annotations=ToolAnnotations(destructiveHint=True),
)
def delete_selection_set(ctx: Context, ss_name: str = "MCP_TEMP_SS") -> str:
    """删除指定选择集中的所有实体。

    Args:
        ss_name: 选择集名称（默认 "MCP_TEMP_SS"）
    """
    return utility_tools.delete_selection_set(ss_name)


@mcp.tool(
    description=TOOL_DESCRIPTIONS["erase_selection_entities"],
    annotations=ToolAnnotations(destructiveHint=True),
)
def erase_selection_entities(ctx: Context, ss_name: str = "MCP_TEMP_SS") -> str:
    """Erase/delete all drawing entities contained in the named selection set.

    Args:
        ss_name: Selection set name (default "MCP_TEMP_SS").
    """
    return utility_tools.erase_selection_entities(ss_name)


@mcp.tool(
    description=TOOL_DESCRIPTIONS["clear_selection_set"],
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
def clear_selection_set(ctx: Context, ss_name: str = "MCP_TEMP_SS") -> str:
    """清空指定选择集（不移除实体，只清空选择）。

    Args:
        ss_name: 选择集名称（默认 "MCP_TEMP_SS"）
    """
    return utility_tools.clear_selection_set(ss_name)


# ══════════════════════════════════════════════════════════════════
#  POLYLINE OPERATIONS
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def polyline_set_bulge(ctx: Context, handle: str, index: int,
                        bulge: float) -> str:
    """设置多段线顶点的凸度（创建曲线段/圆弧段）。

    凸度因子: 0=直线段, 正值=逆时针圆弧, 负值=顺时针圆弧。
    凸度 = tan(圆心角/4)，常用范围 -1 到 1。

    Args:
        handle: 多段线实体句柄
        index:  顶点索引（从0开始）
        bulge:  凸度值
    """
    return polyline_tools.polyline_set_bulge(handle, index, bulge)


@mcp.tool()
def polyline_get_bulge(ctx: Context, handle: str, index: int) -> str:
    """获取多段线顶点的凸度值。

    Args:
        handle: 多段线实体句柄
        index:  顶点索引
    """
    return polyline_tools.polyline_get_bulge(handle, index)


@mcp.tool()
def polyline_set_width(ctx: Context, handle: str, seg_index: int,
                        start_width: float, end_width: float) -> str:
    """设置多段线段的起点和终点宽度（创建变宽线段）。

    可创建渐变宽度效果，如箭头、流线型等。

    Args:
        handle:      多段线实体句柄
        seg_index:   段索引（段连接顶点 seg_index 和 seg_index+1）
        start_width: 段起点宽度
        end_width:   段终点宽度
    """
    return polyline_tools.polyline_set_width(handle, seg_index,
                                              start_width, end_width)


@mcp.tool()
def polyline_get_width(ctx: Context, handle: str, seg_index: int) -> str:
    """获取多段线段的起点和终点宽度。

    Args:
        handle:    多段线实体句柄
        seg_index: 段索引
    """
    return polyline_tools.polyline_get_width(handle, seg_index)


@mcp.tool()
def polyline_add_vertex(ctx: Context, handle: str, index: int,
                         x: float, y: float) -> str:
    """向多段线在指定位置添加新顶点。

    Args:
        handle: 多段线实体句柄
        index:  插入位置索引（0=开头, -1=末尾）
        x, y:   新顶点坐标
    """
    return polyline_tools.polyline_add_vertex(handle, index, x, y)


@mcp.tool()
def polyline_constant_width(ctx: Context, handle: str,
                             width: Optional[float] = None) -> str:
    """获取或设置多段线的统一线宽。

    不传 width 参数=获取当前统一宽度。
    传入 width 值=将所有段设为该统一宽度。

    Args:
        handle: 多段线实体句柄
        width:  统一宽度（不传=获取）
    """
    return polyline_tools.polyline_constant_width(handle, width)


@mcp.tool()
def polyline_num_vertices(ctx: Context, handle: str) -> str:
    """获取多段线的顶点总数。

    Args:
        handle: 多段线实体句柄
    """
    return polyline_tools.polyline_num_vertices(handle)


@mcp.tool()
def polyline_get_point_at_param(ctx: Context, handle: str,
                                 param: float) -> str:
    """获取多段线上指定参数处的3D坐标。

    Args:
        handle: 多段线实体句柄
        param:  参数值（沿多段线的归一化距离）
    """
    return polyline_tools.polyline_get_point_at_param(handle, param)


@mcp.tool()
def polyline_get_segment_type(ctx: Context, handle: str,
                                index: int) -> str:
    """获取多段线段的类型（直线段 "line" 或圆弧段 "arc"）。

    Args:
        handle: 多段线实体句柄
        index:  段索引（从0开始）
    """
    return polyline_tools.polyline_get_segment_type(handle, index)


# =================================================================================================
#  CAD UNDERSTANDING TOOLS
# =================================================================================================

@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def build_drawing_ir(ctx: Context,
                     rescan: bool = False,
                     profile: str = "agent",
                     sections: Optional[List[str]] = None,
                     entity_limit: int = 1000,
                     include_raw: bool = False) -> Dict[str, Any]:
    """Build CAD-IR v2 from scanned metadata, with optional section filtering."""
    drawing_ir = understanding_ir_builder.build_drawing_ir(
        rescan=rescan,
        profile=profile,
        sections=sections,
        entity_limit=entity_limit,
        include_raw=include_raw,
    )
    return ok_result(
        "Built CAD drawing IR.",
        data={"drawing_ir": drawing_ir},
        handles=[
            str(entity["handle"])
            for entity in drawing_ir.get("sections", {}).get("entities", {}).get("items", [])
            if entity.get("handle")
        ],
        warnings=drawing_ir.get("manifest", {}).get("warnings", []),
        next_tools=["summarize_drawing", "detect_semantic_objects", "validate_geometry"],
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def export_drawing_ir(ctx: Context,
                      filepath: str,
                      rescan: bool = False,
                      profile: str = "agent",
                      sections: Optional[List[str]] = None,
                      entity_limit: int = 1000,
                      include_raw: bool = False) -> Dict[str, Any]:
    """Export the current CAD-IR v2 as JSON without modifying the DWG."""
    exported = understanding_ir_builder.export_drawing_ir(
        filepath,
        rescan=rescan,
        profile=profile,
        sections=sections,
        entity_limit=entity_limit,
        include_raw=include_raw,
    )
    return ok_result(
        "Exported CAD drawing IR.",
        data=exported,
        handles=[
            str(entity["handle"])
            for entity in exported["drawing_ir"].get("sections", {}).get("entities", {}).get("items", [])
            if entity.get("handle")
        ],
        next_tools=["summarize_drawing", "validate_geometry"],
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def summarize_drawing(ctx: Context, level: str = "normal") -> Dict[str, Any]:
    """Summarize scanned drawing metadata for an agent."""
    return understanding_analysis.summarize_drawing(level=level)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def explain_entity(ctx: Context, handle: str) -> Dict[str, Any]:
    """Explain one scanned entity, including topology, nearby entities, and annotations."""
    return understanding_analysis.explain_entity(handle)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def find_entities_by_description(ctx: Context, query: str,
                                 top_k: int = 20) -> Dict[str, Any]:
    """Find scanned entities with rule-based lexical and spatial matching."""
    return understanding_analysis.find_entities_by_description(query, top_k=top_k)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def analyze_drawing_intent(ctx: Context,
                           domain_hint: Optional[str] = None) -> Dict[str, Any]:
    """Infer likely CAD drawing domain from scanned metadata."""
    return understanding_analysis.analyze_drawing_intent(domain_hint=domain_hint)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def detect_semantic_objects(ctx: Context, domain: str = "generic") -> Dict[str, Any]:
    """Detect rule-based semantic CAD objects and store them in SQLite only."""
    return understanding_semantic.detect_semantic_objects(domain=domain)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_semantic_graph(ctx: Context) -> Dict[str, Any]:
    """Return the current SQLite-backed semantic graph."""
    return understanding_semantic.get_semantic_graph()


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def find_semantic_objects(ctx: Context,
                          object_type: Optional[str] = None,
                          label_query: Optional[str] = None,
                          handle: Optional[str] = None,
                          bbox_region: Optional[List[float]] = None,
                          domain: Optional[str] = None,
                          confidence_threshold: float = 0.0,
                          top_k: int = 20) -> Dict[str, Any]:
    """Search detected semantic objects by type or label."""
    return understanding_semantic.find_semantic_objects(
        object_type=object_type,
        label_query=label_query,
        handle=handle,
        bbox_region=bbox_region,
        domain=domain,
        confidence_threshold=confidence_threshold,
        top_k=top_k,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def extract_drawing_constraints(ctx: Context) -> Dict[str, Any]:
    """Extract dimension and rule-based geometric constraints into SQLite."""
    return understanding_constraints.extract_drawing_constraints()


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def check_drawing_constraints(ctx: Context,
                              tolerance: float = 1e-6) -> Dict[str, Any]:
    """Check extracted constraints without modifying the DWG."""
    return understanding_constraints.check_constraint_satisfaction(tolerance=tolerance)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_drawing_constraints(ctx: Context,
                            status: Optional[str] = None) -> Dict[str, Any]:
    """List extracted constraints, optionally filtered by status."""
    return understanding_constraints.get_constraints(status=status)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def bind_dimension_to_geometry(ctx: Context, handle: str,
                               tolerance: float = 1e-3) -> Dict[str, Any]:
    """Bind one scanned dimension annotation to likely measured geometry."""
    return understanding_dimensions.bind_dimension_to_geometry(handle, tolerance=tolerance)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def bind_all_dimensions(ctx: Context,
                        tolerance: float = 1e-3) -> Dict[str, Any]:
    """Bind all scanned dimension annotations to likely measured geometry."""
    return understanding_dimensions.bind_all_dimensions(tolerance=tolerance)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def propose_constraint_repair_plan(ctx: Context,
                                   constraint_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Propose a non-executing CAD repair plan for violated constraints."""
    return understanding_constraints.propose_constraint_repair_plan(constraint_ids=constraint_ids)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def validate_geometry(ctx: Context,
                      checks: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create a geometry validation report from scanned metadata."""
    return understanding_validators.validate_geometry(checks=checks)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_validation_report(ctx: Context) -> Dict[str, Any]:
    """Return the latest cached validation report."""
    return understanding_validators.get_validation_report()


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def propose_repair_plan(ctx: Context,
                        issue_ids: List[str]) -> Dict[str, Any]:
    """Propose a non-executing CAD repair plan for validation issues."""
    return understanding_validators.propose_repair_plan(issue_ids=issue_ids)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def export_view_image_with_mapping(ctx: Context,
                                   filepath: Optional[str] = None,
                                   include_overlay: bool = True,
                                   include_entity_bboxes: bool = True,
                                   overlay_granularity: str = "entity",
                                   overlay_style: str = "bbox",
                                   include_tiles: bool = False,
                                   tile_size: int = 640,
                                   tile_overlap: float = 0.2) -> Dict[str, Any]:
    """Export a view artifact plus sidecar world/pixel/entity mapping."""
    return understanding_view.export_view_image_with_mapping(
        filepath=filepath,
        include_overlay=include_overlay,
        include_entity_bboxes=include_entity_bboxes,
        overlay_granularity=overlay_granularity,
        overlay_style=overlay_style,
        include_tiles=include_tiles,
        tile_size=tile_size,
        tile_overlap=tile_overlap,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_visible_entities_in_view(ctx: Context, snapshot_id: str) -> Dict[str, Any]:
    """List handles visible in a mapped view snapshot."""
    return understanding_view.get_visible_entities_in_view(snapshot_id)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def map_pixel_to_world(ctx: Context, snapshot_id: str,
                       x: float, y: float) -> Dict[str, Any]:
    """Map snapshot pixel coordinates to approximate world coordinates."""
    return understanding_view.map_pixel_to_world(snapshot_id, x, y)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def map_world_to_pixel(ctx: Context, snapshot_id: str,
                       x: float, y: float, z: float = 0.0) -> Dict[str, Any]:
    """Map world coordinates to snapshot pixel coordinates."""
    return understanding_view.map_world_to_pixel(snapshot_id, x, y, z)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def map_pixel_region_to_world_bbox(ctx: Context, snapshot_id: str,
                                   bbox: List[float]) -> Dict[str, Any]:
    """Map a pixel bbox region from a snapshot to an approximate world bbox."""
    return understanding_view.map_pixel_region_to_world_bbox(snapshot_id, bbox)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def ground_vlm_region(ctx: Context, snapshot_id: str,
                      bbox: List[float],
                      top_k: int = 10) -> Dict[str, Any]:
    """Ground a VLM pixel bbox to likely AutoCAD handles from a mapped snapshot."""
    return understanding_view.ground_vlm_region(snapshot_id, bbox, top_k=top_k)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def ground_vlm_overlay_id(ctx: Context, snapshot_id: str,
                          overlay_id: str) -> Dict[str, Any]:
    """Ground a VLM overlay ID to an AutoCAD handle and primitive candidates."""
    return understanding_view.ground_vlm_overlay_id(snapshot_id, overlay_id)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def validate_vlm_review_output(ctx: Context,
                               review: Dict[str, Any],
                               snapshot_id: Optional[str] = None) -> Dict[str, Any]:
    """Validate VLM review JSON before grounding or persistence."""
    return understanding_vlm.validate_vlm_review_output(review, snapshot_id=snapshot_id)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def submit_vlm_review(ctx: Context,
                      snapshot_id: str,
                      review: Dict[str, Any],
                      source_model: str = "unknown",
                      prompt_version: str = "vlm_review_drawing/v2",
                      top_k: int = 10) -> Dict[str, Any]:
    """Validate, ground, and store VLM review findings in SQLite only."""
    return understanding_vlm.submit_vlm_review(
        snapshot_id=snapshot_id,
        review=review,
        source_model=source_model,
        prompt_version=prompt_version,
        top_k=top_k,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_vlm_findings(ctx: Context,
                     snapshot_id: Optional[str] = None,
                     status: Optional[str] = None,
                     issue_type: Optional[str] = None,
                     limit: int = 100) -> Dict[str, Any]:
    """List persisted VLM review findings for the current drawing scope."""
    return understanding_vlm.get_vlm_findings(
        snapshot_id=snapshot_id,
        status=status,
        issue_type=issue_type,
        limit=limit,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def fuse_vlm_findings_into_semantic_graph(ctx: Context,
                                          finding_ids: Optional[List[str]] = None,
                                          min_confidence: float = 0.5) -> Dict[str, Any]:
    """Materialize VLM findings as SQLite semantic graph objects and relations."""
    return understanding_vlm.fuse_vlm_findings_into_semantic_graph(
        finding_ids=finding_ids,
        min_confidence=min_confidence,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def evaluate_vlm_grounding(ctx: Context,
                           ground_truth: List[Dict[str, Any]],
                           snapshot_id: Optional[str] = None,
                           top_k: int = 3) -> Dict[str, Any]:
    """Score persisted VLM findings against expected handles and issue labels."""
    return understanding_vlm.evaluate_vlm_grounding(
        ground_truth=ground_truth,
        snapshot_id=snapshot_id,
        top_k=top_k,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def promote_vlm_finding_to_validation_issue(ctx: Context,
                                            finding_ids: Optional[List[str]] = None,
                                            min_confidence: float = 0.0) -> Dict[str, Any]:
    """Copy selected VLM findings into the cached validation report."""
    return understanding_vlm.promote_vlm_finding_to_validation_issue(
        finding_ids=finding_ids,
        min_confidence=min_confidence,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def analyze_engineering_drawing_stages(ctx: Context,
                                       snapshot_id: Optional[str] = None,
                                       domain: str = "mechanical") -> Dict[str, Any]:
    """Build layout, annotation, VLM, and reconciliation stages as JSON."""
    return understanding_engineering.analyze_engineering_drawing_stages(
        snapshot_id=snapshot_id,
        domain=domain,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def validate_cad_plan(ctx: Context, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a guarded CAD plan without modifying the DWG."""
    return understanding_plan.validate_cad_plan(plan)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def dry_run_cad_plan(ctx: Context, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Dry-run a guarded CAD plan without modifying the DWG."""
    return understanding_plan.dry_run_cad_plan(plan)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
def execute_cad_plan(ctx: Context, plan: Dict[str, Any],
                     allow_modify: bool = False,
                     transactional: bool = True,
                     rollback_on_error: bool = True,
                     rollback_on_high_severity_validation: bool = True,
                     validate_after_each_step: bool = False,
                     validate_after_plan: bool = True,
                     rescan_after_plan: bool = False,
                     export_view_after_plan: bool = False) -> Dict[str, Any]:
    """Execute a guarded CAD plan only when allow_modify=True."""
    return understanding_plan.execute_cad_plan(
        plan,
        allow_modify=allow_modify,
        transactional=transactional,
        rollback_on_error=rollback_on_error,
        rollback_on_high_severity_validation=rollback_on_high_severity_validation,
        validate_after_each_step=validate_after_each_step,
        validate_after_plan=validate_after_plan,
        rescan_after_plan=rescan_after_plan,
        export_view_after_plan=export_view_after_plan,
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def list_cad_resources(ctx: Context) -> Dict[str, Any]:
    """List CAD understanding resource URIs."""
    return understanding_resources.list_cad_resources()


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
def get_cad_resource(ctx: Context, uri: str) -> Dict[str, Any]:
    """Return a read-only CAD understanding resource by URI."""
    return understanding_resources.get_cad_resource(uri)


@mcp.resource("cad://workspace/context",
              name="CAD Workspace Context",
              mime_type="application/json")
def cad_workspace_context_resource() -> str:
    return understanding_resources.get_resource_json("cad://workspace/context")


@mcp.resource("cad://drawing/current/summary",
              name="Current Drawing Summary",
              mime_type="application/json")
def cad_current_drawing_summary_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/summary")


@mcp.resource("cad://drawing/current/ir",
              name="Current Drawing CAD-IR",
              mime_type="application/json")
def cad_current_drawing_ir_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/ir")


@mcp.resource("cad://drawing/current/ir/overview",
              name="Current Drawing CAD-IR Overview",
              mime_type="application/json")
def cad_current_drawing_ir_overview_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/ir/overview")


@mcp.resource("cad://drawing/current/ir/entities",
              name="Current Drawing CAD-IR Entity Index",
              mime_type="application/json")
def cad_current_drawing_ir_entities_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/ir/entities")


@mcp.resource("cad://drawing/current/topology",
              name="Current Drawing Topology",
              mime_type="application/json")
def cad_current_drawing_topology_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/topology")


@mcp.resource("cad://drawing/current/semantic-graph",
              name="Current Drawing Semantic Graph",
              mime_type="application/json")
def cad_current_semantic_graph_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/semantic-graph")


@mcp.resource("cad://drawing/current/constraints",
              name="Current Drawing Constraints",
              mime_type="application/json")
def cad_current_constraints_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/constraints")


@mcp.resource("cad://drawing/current/validation-report",
              name="Current Drawing Validation Report",
              mime_type="application/json")
def cad_current_validation_report_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/validation-report")


@mcp.resource("cad://drawing/current/tool-guide",
              name="CAD Understanding Tool Guide",
              mime_type="application/json")
def cad_understanding_tool_guide_resource() -> str:
    return understanding_resources.get_resource_json("cad://drawing/current/tool-guide")


# ══════════════════════════════════════════════════════════════════
#  PROMPTS
# ══════════════════════════════════════════════════════════════════

@mcp.prompt()
def cad_workflow_guide() -> str:
    """CAD MCP workflow guide for model-facing tool use."""
    return f"""{TOOL_SELECTION_INSTRUCTIONS.strip()}

## Recommended workflow
0. Preflight: call check_runtime_environment(check_autocad=True) before live
   CAD work; fix required blockers before drawing or editing.
1. Classify the request: existing drawing understanding, new drawing from
   specification, repair, VLM review, or export/plot.
2. Existing or complex drawing: scan_all_entities, build_drawing_ir,
   summarize_drawing, detect_semantic_objects, extract_drawing_constraints,
   bind_all_dimensions, check_drawing_constraints, validate_geometry, and use
   cad://drawing/current/ir/overview for fast orientation.
3. Engineering drawing or assembly: preserve views, sections, BOMs, title
   blocks, GD&T, hatches, dimensions, and repeated parts; use
   analyze_engineering_drawing_stages and export_view_image_with_mapping.
4. New complex drawing: use recommend_cad_tools(intent), then CADPlan with
   variables, save_as handles, dependencies, expectations, and postconditions.
5. Plan layers before drawing: create_layer and draw with color="bylayer".
6. Pick the named tool for the intent: rectangle, polygon, block, hatch,
   dimension, leader, array, fillet, chamfer, trim, offset, 3D solid, etc.
7. Edit by handle with editing tools; do not delete and redraw just to move,
   mirror, scale, offset, trim, or array.
8. Dimension with add_*_dimension or add_qdim; never fake dimensions with text
   and lines.
9. Vision-capable verification: call export_view_image_with_mapping whenever
   seeing the current view would reduce ambiguity; use overlays and tiles for
   dense drawings.
10. Model-private context: use add_spatial_annotation/list_spatial_annotations
   for hidden part labels or pointer-style references; do not draw helper
   labels into the DWG for model memory.
11. Verify with scan_all_entities, validate_geometry, visual mapping, and only
    save/export when requested.

When unsure, call recommend_cad_tools(intent). For a full generated index, use
get_tool_help() or resource cad://tools.
"""


@mcp.prompt()
def cad_layer_planning() -> str:
    """CAD layer planning guidance for model-facing drafting workflows."""
    return """## CAD Layer Planning Guide

Use explicit layers before drawing. Prefer short, stable names that encode the
discipline and object purpose.

### Recommended layer naming

Simplified AIA-style prefixes:

| Discipline | Prefix | Examples |
|------------|--------|----------|
| Architecture | A- | A-WALL, A-DOOR, A-WINDOW |
| Structure | S- | S-COLUMN, S-BEAM, S-FOUND |
| Electrical | E- | E-LIGHT, E-POWER, E-DATA |
| Mechanical/HVAC | M- | M-DUCT, M-PIPE |
| Plumbing | P- | P-COLD, P-HOT, P-DRAIN |
| Dimensions | DIM- | DIM-PLAN, DIM-SECTION |
| Text/notes | TEXT- | TEXT-NOTE, TEXT-TITLE |

### Color conventions

- ACI 1 red / ACI 6 magenta: primary construction elements.
- ACI 4 cyan / ACI 3 green: secondary or reference elements.
- ACI 5 blue: dimensions and annotation.
- ACI 252-254 gray: underlays and references.

### Typical setup

Before drawing, create layers explicitly:
```
create_layer("A-WALL", color=1)      # wall layer
create_layer("A-DOOR", color=3)      # door layer
create_layer("A-WINDOW", color=4)    # window layer
create_layer("DIM-PLAN", color=5)    # plan dimensions
create_layer("TEXT-NOTE", color=7)   # notes
```
"""


def _load_prompt_file(filename: str, fallback: str) -> str:
    prompt_path = os.path.join(_project_root, "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as prompt_file:
            content = prompt_file.read().strip()
            if content:
                return content
    except OSError:
        pass
    return fallback.strip()


@mcp.prompt()
def understand_existing_drawing() -> str:
    """Workflow for safely understanding an existing DWG."""
    return _load_prompt_file("understand_existing_drawing.md", """## Understand Existing Drawing

1. open_drawing if needed.
2. scan_all_entities(topology_detail="full" when primitive grounding is needed).
3. build_drawing_ir.
4. summarize_drawing.
5. detect_semantic_objects.
6. extract_drawing_constraints.
7. bind_all_dimensions.
8. check_drawing_constraints.
9. validate_geometry.
10. export_view_image_with_mapping(include_overlay=True) when visual review helps.

Do not modify the DWG during understanding. Use handles, evidence,
confidence, warnings, and recommended next tools from each structured result.
Ambiguous dimensions and low-confidence semantic objects must remain uncertain.
""")


@mcp.prompt()
def precise_draw_from_spec() -> str:
    """Workflow for precise CAD generation through a guarded plan."""
    return _load_prompt_file("precise_draw_from_spec.md", """## Precise Draw From Spec

1. Analyze the spec.
2. Create a CADPlan using high-level tools, variables, save_as, dependencies,
   expectations, and postconditions.
3. validate_cad_plan.
4. dry_run_cad_plan.
5. execute_cad_plan only after explicit permission with allow_modify=True and
   transactional=True.
6. scan_all_entities.
7. build_drawing_ir.
8. validate_geometry.
9. export_view_image_with_mapping(include_overlay=True).

Do not put send_command in a plan unless the user explicitly approves a
dangerous operation.
If execution fails, inspect failed_step, completed_steps, and rollback_status.
""")


@mcp.prompt()
def vlm_review_drawing() -> str:
    """Workflow for VLM-backed drawing review and grounding."""
    return _load_prompt_file("vlm_review_drawing.md", """## VLM Review Drawing

1. export_view_image_with_mapping(include_overlay=True).
2. Give the clean artifact, overlay artifact, and sidecar JSON to the VLM.
3. Require VLM JSON with overlay IDs, handles, pixel bbox, issue type,
   confidence, and evidence.
4. ground_vlm_overlay_id for overlay IDs or ground_vlm_region for each VLM bbox.
5. map_pixel_region_to_world_bbox when world extents are needed.
6. explain_entity for likely handles and inspect primitive candidates.
7. propose_repair_plan or propose_constraint_repair_plan for selected issues.

Do not create visible helper geometry in the DWG for review annotations.
Do not claim exact grounding when limitations or low confidence are returned.
""")


@mcp.prompt()
def repair_drawing() -> str:
    """Workflow for validation-led drawing repair."""
    return _load_prompt_file("repair_drawing.md", """## Repair Drawing

1. validate_geometry.
2. extract_drawing_constraints, bind_all_dimensions, and check_drawing_constraints
   when dimension or geometric intent matters.
3. propose_repair_plan or propose_constraint_repair_plan.
4. validate_cad_plan.
5. dry_run_cad_plan.
6. execute_cad_plan only after explicit permission with allow_modify=True and
   transactional=True.
7. scan_all_entities.
8. validate_geometry again.
9. export_view_image_with_mapping.

Analysis, validation, grounding, and dry-run must not modify the DWG.
Never execute a repair automatically; ambiguous issues should return alternatives.
""")


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    """Entry point for the cad-mcp console script."""
    logger.info("CAD服务已初始化")
    logger.info("服务器正在使用 stdio 传输运行")
    mcp.run()


if __name__ == "__main__":
    main()
