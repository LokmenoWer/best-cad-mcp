"""CAD MCP Tools — Database query, groups, styles, and utilities."""
from typing import Optional, List, Dict, Any
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import threading
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, resolve_color, com_get as _com_get, com_set as _com_set
from src.cad_understanding.result import ToolResult, error_result, ok_result

ctrl = get_controller()
db = get_database()


# ── Database Query Tools ───────────────────────────────────────

def get_all_tables() -> str:
    """获取数据库中的所有表名。"""
    tables = db.get_tables()
    tables.sort()
    return f"数据库表 ({len(tables)} 个):\n" + "\n".join(f"  - {t}" for t in tables)


def get_table_schema(table_name: str) -> str:
    """获取指定表的列结构。

    Args:
        table_name: 表名
    """
    columns = db.get_table_schema(table_name)
    return json.dumps(columns, indent=2, ensure_ascii=False)


def execute_query(query: str,
                  max_rows: int = 1000,
                  timeout_ms: int = 5000,
                  max_result_bytes: int = 1_000_000) -> str:
    """在 CAD 元数据数据库上执行 SQL 查询。

    数据库包含扫描后的实体、图层、图块、文本模式等信息。
    这是 AI 理解图纸的关键工具 — 可以用只读 SQL 进行复杂的过滤、统计、关联分析。

    常用表:
      - cad_entities:  所有扫描的实体（handle, type, layer, color, properties JSON）
      - cad_geometry_primitives: 派生点、线、曲线、面、体
      - cad_geometry_relations:  派生拓扑关系（starts_at/ends_at/bounded_by）
      - cad_topology_summary:    每个实体的点线面体摘要
      - cad_layers:    图层配置
      - cad_blocks:    图块定义
      - text_patterns: 文本搜索统计
      - drawing_snapshots: 图纸快照

    常用查询示例:
      - 按类型统计: SELECT type, COUNT(*) FROM cad_entities GROUP BY type
      - 按图层过滤: SELECT * FROM cad_entities WHERE layer='WALL'
      - 搜索文字:   SELECT * FROM cad_entities WHERE json_extract(geometry, '$.text_string') LIKE '%门%'

    Args:
        query: 只读 SQL 查询字符串（SELECT/WITH/PRAGMA/EXPLAIN）
        max_rows: 最多返回行数（硬上限由服务端控制）
        timeout_ms: 查询执行超时窗口
        max_result_bytes: 返回 JSON 的近似字节上限
    """
    try:
        result = db.execute(
            query,
            read_only=True,
            max_rows=max_rows,
            timeout_ms=timeout_ms,
            max_result_bytes=max_result_bytes,
        )
        if "columns" in result:
            # Return as formatted table
            cols = result["columns"]
            rows = result["rows"]
            suffix = ""
            if result.get("truncated"):
                limits = result.get("limits", {})
                suffix = (
                    "\n\nTRUNCATED: result was limited by "
                    f"max_rows={limits.get('max_rows')}, "
                    f"timeout_ms={limits.get('timeout_ms')}, "
                    f"max_result_bytes={limits.get('max_result_bytes')}."
                )
            if not rows:
                return f"查询返回 0 行 (列: {', '.join(cols)}){suffix}"
            return json.dumps(rows, indent=2, ensure_ascii=False, default=str) + suffix
        else:
            return f"已执行，影响 {result['affected_rows']} 行"
    except Exception as e:
        return f"查询执行失败: {e}"


def execute_sql_query(query: str,
                      max_rows: int = 1000,
                      timeout_ms: int = 5000,
                      max_result_bytes: int = 1_000_000) -> str:
    """执行 SQL 查询（execute_query 的别名）。"""
    return execute_query(
        query,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_result_bytes=max_result_bytes,
    )


def get_workspace_context() -> str:
    """Return the active workspace/conversation/thread/drawing database scope."""
    return json.dumps(db.get_context_dict(), indent=2, ensure_ascii=False)


def get_database_maintenance_status() -> str:
    """Return SQLite maintenance and runtime-artifact status."""
    return json.dumps(db.get_maintenance_status(), indent=2, ensure_ascii=False, default=str)


def maintain_database(max_view_snapshots_per_scope: int = 20,
                      max_validation_reports_per_scope: int = 20,
                      incremental_vacuum_pages: int = 1000,
                      vacuum: bool = False) -> str:
    """Prune cache tables and reclaim SQLite free pages."""
    result = db.maintain(
        max_view_snapshots_per_scope=max_view_snapshots_per_scope,
        max_validation_reports_per_scope=max_validation_reports_per_scope,
        incremental_vacuum_pages=incremental_vacuum_pages,
        vacuum=vacuum,
    )
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def clear_understanding_cache() -> str:
    """Clear semantic/constraint/validation/view caches for the active thread."""
    result = db.clear_understanding_cache()
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def get_legacy_database_status() -> str:
    """Report whether the retired root-level autocad_data.db is present."""
    return json.dumps(db.get_legacy_database_status(), indent=2, ensure_ascii=False, default=str)


def set_workspace_context(workspace_root: Optional[str] = None,
                          workspace_id: Optional[str] = None,
                          conversation_id: Optional[str] = None,
                          thread_id: Optional[str] = None,
                          drawing_id: Optional[str] = None,
                          drawing_name: Optional[str] = None,
                          drawing_path: Optional[str] = None) -> str:
    """Set the database scope used by subsequent CAD metadata operations."""
    context = db.configure_context(
        workspace_root=workspace_root,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        drawing_id=drawing_id,
        drawing_name=drawing_name,
        drawing_path=drawing_path,
    )
    return json.dumps(context, indent=2, ensure_ascii=False)


def activate_workspace_drawing(drawing_name: str = "active",
                               drawing_path: str = "",
                               drawing_id: Optional[str] = None) -> str:
    """Switch the metadata scope to a drawing inside the current workspace."""
    context = db.activate_drawing(
        name=drawing_name,
        path=drawing_path,
        drawing_id=drawing_id,
    )
    return json.dumps(context, indent=2, ensure_ascii=False)


def list_workspace_drawings(limit: int = 100) -> str:
    """List drawings known to the current workspace metadata database."""
    rows = db.list_workspace_drawings(limit)
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str)


def _preflight_check(name: str,
                     ok: bool,
                     required: bool,
                     detail: str,
                     remediation: str = "") -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "required": bool(required),
        "detail": str(detail),
        "remediation": str(remediation or ""),
    }


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _first_existing_path(paths: List[str]) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    return ""


def check_runtime_environment(check_autocad: bool = False,
                              require_visual_export: bool = False) -> ToolResult:
    """Run a read-only runtime preflight before CAD work.

    Args:
        check_autocad: Also try to connect to a live AutoCAD COM instance.
        require_visual_export: Treat missing raster/SVG render helpers as a
            blocking issue instead of a warning.
    """
    checks: List[Dict[str, Any]] = []
    checks.append(_preflight_check(
        "windows_host",
        platform.system().lower() == "windows",
        True,
        f"platform={platform.platform()}",
        "Run best-cad-mcp on Windows because AutoCAD COM is Windows-only.",
    ))
    checks.append(_preflight_check(
        "python_version",
        sys.version_info >= (3, 11),
        True,
        f"python={sys.version.split()[0]} executable={sys.executable}",
        "Use Python 3.11 or newer.",
    ))

    python_shim = "WindowsApps" in sys.executable
    checks.append(_preflight_check(
        "python_executable",
        not python_shim,
        False,
        sys.executable,
        "Point the MCP client at the project .venv/Scripts/python.exe or installed cad-mcp command.",
    ))

    for module_name in ["mcp", "win32com.client", "pythoncom", "comtypes", "pyautocad"]:
        checks.append(_preflight_check(
            f"python_module:{module_name}",
            _module_available(module_name),
            True,
            module_name,
            "Install with `python -m pip install -r requirements.txt && python -m pip install -e .`.",
        ))

    workspace = Path(os.environ.get("CAD_MCP_WORKSPACE_ROOT") or os.getcwd())
    checks.append(_preflight_check(
        "workspace_root",
        workspace.exists() and os.access(str(workspace), os.W_OK),
        True,
        str(workspace),
        "Start the MCP server from a writable workspace or set CAD_MCP_WORKSPACE_ROOT.",
    ))
    legacy_status = db.get_legacy_database_status()
    checks.append(_preflight_check(
        "legacy_root_database",
        not (legacy_status["exists"] and not legacy_status["is_active_db"]),
        False,
        legacy_status["recommendation"] or "no retired autocad_data.db detected",
        "Archive or delete the root-level autocad_data.db after confirming old data is no longer needed.",
    ))

    autocad_required = bool(check_autocad)
    if check_autocad:
        try:
            autocad_ok = bool(ctrl.connect(visible=True))
            detail = "connected" if autocad_ok else "AutoCAD COM connection failed"
        except Exception as exc:
            autocad_ok = False
            detail = f"{type(exc).__name__}: {exc}"
    else:
        autocad_ok = True
        detail = "skipped; pass check_autocad=True before live drawing/editing"
    checks.append(_preflight_check(
        "autocad_com_live",
        autocad_ok,
        autocad_required,
        detail,
        "Start and license AutoCAD under this Windows user, then retry.",
    ))

    python_visual_renderer = ""
    if _module_available("cairosvg") and _module_available("PIL"):
        python_visual_renderer = "python:cairosvg+Pillow"
    elif _module_available("cairosvg"):
        python_visual_renderer = "python:cairosvg"

    converter = (
        shutil.which("magick")
        or shutil.which("inkscape")
        or shutil.which("rsvg-convert")
        or python_visual_renderer
        or _first_existing_path([
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ])
    )
    checks.append(_preflight_check(
        "visual_review_renderer",
        bool(converter),
        bool(require_visual_export),
        converter or "not found",
        "Install ImageMagick/Inkscape/librsvg, or make Chrome/Edge available for SVG/PDF visual review.",
    ))

    blockers = [check for check in checks if check["required"] and not check["ok"]]
    warnings = [
        f"{check['name']}: {check['detail']}"
        for check in checks
        if not check["required"] and not check["ok"]
    ]
    data = {
        "ready": not blockers,
        "checks": checks,
        "policy": {
            "before_live_cad_work": [
                "check_runtime_environment(check_autocad=True)",
                "create_new_drawing or open_drawing",
                "scan_all_entities before editing existing drawings",
            ],
            "before_multi_step_modify": [
                "validate_cad_plan",
                "dry_run_cad_plan",
                "execute_cad_plan only with allow_modify=True",
            ],
            "after_modify": [
                "scan_all_entities",
                "validate_geometry",
                "export_view_image_with_mapping when visual evidence matters",
            ],
        },
    }
    if blockers:
        return error_result(
            "Runtime preflight failed; required CAD environment checks are not satisfied.",
            data={**data, "blockers": blockers},
            warnings=warnings,
            next_tools=["check_runtime_environment"],
        )
    return ok_result(
        "Runtime preflight passed.",
        data=data,
        warnings=warnings,
        next_tools=["create_new_drawing", "open_drawing", "recommend_cad_tools"],
    )


def get_entity_topology(handle: str) -> str:
    """Return derived point/line/surface topology for one entity."""
    topology = db.get_entity_topology(handle)
    return json.dumps(topology, indent=2, ensure_ascii=False, default=str)


def get_topology_summary(limit: int = 100) -> str:
    """Return compact topology summaries for scanned entities."""
    rows = db.get_topology_summary(limit)
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str)


def add_spatial_annotation(label: str, target_kind: str = "entity",
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
    """Store a hidden model-only spatial label in SQLite, not in the DWG."""
    try:
        row = db.upsert_spatial_annotation(
            annotation_id=annotation_id,
            label=label,
            target_kind=target_kind,
            description=description,
            entity_handle=handle,
            primitive_key=primitive_key,
            point=point,
            point2=point2,
            bbox=bbox,
            confidence=confidence,
            source=source,
            properties=properties,
        )
        return (
            "OK: stored model-private spatial annotation. "
            "No AutoCAD geometry, layers, XData, or dictionaries were modified.\n"
            + json.dumps(row, indent=2, ensure_ascii=False, default=str)
        )
    except Exception as e:
        return f"ERROR: add_spatial_annotation failed: {e}"


def list_spatial_annotations(annotation_id: Optional[str] = None,
                             label: Optional[str] = None,
                             target_kind: Optional[str] = None,
                             handle: Optional[str] = None,
                             limit: int = 100) -> str:
    """List hidden model-only spatial labels from the MCP SQLite database."""
    rows = db.list_spatial_annotations(
        annotation_id=annotation_id,
        label=label,
        target_kind=target_kind,
        entity_handle=handle,
        limit=limit,
    )
    return json.dumps(rows, indent=2, ensure_ascii=False, default=str)


def clear_spatial_annotations(annotation_id: Optional[str] = None,
                              label: Optional[str] = None,
                              target_kind: Optional[str] = None,
                              handle: Optional[str] = None) -> str:
    """Delete hidden model-only spatial labels from SQLite only."""
    count = db.delete_spatial_annotations(
        annotation_id=annotation_id,
        label=label,
        target_kind=target_kind,
        entity_handle=handle,
    )
    return (
        f"OK: removed {count} model-private spatial annotation(s). "
        "The DWG was not modified."
    )


# ── Group Tools ────────────────────────────────────────────────

def create_group(name: str, handles: List[str]) -> str:
    """创建实体组（将多个实体编组为一个可选择的组）。

    Args:
        name:    组名称
        handles: 要包含的实体句柄列表
    """
    r = ctrl.create_group(name, handles)
    return r["message"]


def get_all_groups() -> str:
    """列出所有已创建的实体组。"""
    groups = ctrl.get_all_groups()
    if not groups:
        return "无实体组"
    lines = [f"共 {len(groups)} 个组:"]
    for i, g in enumerate(groups):
        lines.append(f"  [{i}] {g['name']:<20s} 实体数: {g['count']}")
    return "\n".join(lines)


# ── Hatch Tools ────────────────────────────────────────────────

def add_hatch(pattern_name: str = "ANSI31",
              associativity: bool = True,
              layer: Optional[str] = None,
              color: str = "bylayer") -> str:
    """创建图案填充对象（需要后续调用 hatch_add_boundary 添加边界）。

    Args:
        pattern_name: 填充图案名称 (ANSI31=斜线, ANSI32=交叉斜线,
                      SOLID=实心, AR-CONC=混凝土, AR-SAND=沙土,
                      EARTH=泥土, GRASS=草地, etc.)
        associativity: 是否关联（边界改变时填充自动更新）
        layer:   图层名称
        color:   颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pattern_map = {
        "斜线": "ANSI31", "交叉斜线": "ANSI32", "实心": "SOLID",
        "混凝土": "AR-CONC", "砖": "AR-BRSTD", "沙土": "AR-SAND",
        "泥土": "EARTH", "草地": "GRASS", "网格": "NET",
        "点": "DOTS", "蜂窝": "HONEY",
    }
    actual_pattern = pattern_map.get(pattern_name, pattern_name)
    hatch = ctrl.add_hatch(0, actual_pattern, associativity)
    if color != "bylayer":
        try:
            _com_set(hatch, "Color", resolve_color(color))
        except Exception:
            pass
    return format_success(f"已创建填充对象 (图案:{actual_pattern})",
                          handle=hatch.Handle,
                          pattern=actual_pattern,
                          note="请使用 hatch_add_boundary 添加边界")


# ── Additional Utility Functions ────────────────────────────────

def angle_to_real(angle_str: str, unit: int = 0) -> str:
    """Parse angle string to radians.

    Args:
        angle_str: angle string (e.g., "45.5", "45d30'0\"")
        unit: 0=degrees, 1=deg/min/sec, 2=grads, 3=radians
    """
    rad = ctrl.angle_to_real(angle_str, unit)
    deg = rad * 180.0 / 3.141592653589793
    return f"输入: {angle_str} → {rad:.6f} 弧度 ({deg:.4f}°)"


def angle_to_string(angle_rad: float, unit: int = 0, precision: int = 2) -> str:
    """Format radian angle to string in specified unit.

    Args:
        angle_rad: angle in radians
        unit: 0=decimal degrees, 1=deg/min/sec, 2=grads, 3=radians
        precision: number of decimal places
    """
    s = ctrl.angle_to_string(angle_rad, unit, precision)
    return f"{angle_rad:.6f} rad → {s}"


def distance_to_real(dist_str: str, unit: int = 0) -> str:
    """Parse distance string to real value.

    Args:
        dist_str: distance string
        unit: 0=decimal, 1=engineering, 2=architectural, 3=fractional
    """
    val = ctrl.distance_to_real(dist_str, unit)
    return f"输入: {dist_str} → {val:.6f}"


def real_to_string(value: float, unit: int = 0, precision: int = 2) -> str:
    """Format real value to string in specified unit format.

    Args:
        value: numeric value
        unit: 0=decimal, 1=engineering, 2=architectural, 3=fractional
        precision: decimal precision
    """
    s = ctrl.real_to_string(value, unit, precision)
    return f"{value:.6f} → {s}"


def select_on_screen() -> str:
    """Prompt user to select entities interactively on screen."""
    r = ctrl.select_on_screen()
    if r["success"]:
        return format_success(f"屏幕选择 {r['count']} 个实体",
                              handles=r.get("handles", [])[:20])
    return f"屏幕选择失败: {r.get('message', '')}"


def delete_selection_set(ss_name: str = "MCP_TEMP_SS") -> str:
    """Erase all entities in a selection set.

    Args:
        ss_name: selection set name
    """
    r = ctrl.selection_erase(ss_name)
    return r["message"]


def erase_selection_entities(ss_name: str = "MCP_TEMP_SS") -> str:
    """Erase all drawing entities in a selection set.

    Args:
        ss_name: selection set name
    """
    return delete_selection_set(ss_name)


def clear_selection_set(ss_name: str = "MCP_TEMP_SS") -> str:
    """Clear a selection set (remove entities from set, not from drawing).

    Args:
        ss_name: selection set name
    """
    r = ctrl.selection_clear(ss_name)
    return r["message"]


def _schedule_process_exit(delay_seconds: float, exit_code: int) -> None:
    def delayed_exit():
        import time
        time.sleep(delay_seconds)
        os._exit(exit_code)

    thread = threading.Thread(target=delayed_exit, daemon=True)
    thread.start()


def restart_mcp(delay_seconds: float = 0.5, exit_code: int = 0) -> str:
    """Request a soft MCP restart after this tool response is returned."""
    delay = max(float(delay_seconds), 0.1)
    _schedule_process_exit(delay, int(exit_code))
    return (
        f"MCP restart requested. This process will exit in {delay}s so the MCP "
        "host can start a fresh process with the latest code."
    )


# ── Help / Documentation ───────────────────────────────────────

WORKFLOW_PLAYBOOKS = [
    {
        "name": "Existing or complex drawing understanding",
        "keywords": [
            "understand", "inspect", "analyze", "existing drawing",
            "complex drawing", "engineering drawing", "assembly drawing",
            "section view", "detail view", "title block", "gd&t",
            "tolerance", "bom", "drawing intent", "理解", "分析", "复杂图纸",
            "工程图", "装配图", "剖视图", "明细表", "标题栏",
        ],
        "steps": [
            "scan_all_entities(topology_detail='full' when primitive grounding matters)",
            "build_drawing_ir(sections=['overview', 'entities'] for orientation and handle lookup)",
            "summarize_drawing",
            "detect_semantic_objects(domain='mechanical' or another suitable domain)",
            "extract_drawing_constraints -> bind_all_dimensions -> check_drawing_constraints",
            "validate_geometry",
            "export_view_image_with_mapping(include_overlay=True, include_tiles=True for dense drawings)",
        ],
        "fidelity": [
            "Keep uncertain semantics explicit; do not collapse views, dimensions, BOMs, title blocks, or section/detail views into generic linework.",
            "Use CAD-IR resources and semantic graph evidence before editing or summarizing a dense drawing.",
        ],
    },
    {
        "name": "New complex drawing from specification",
        "keywords": [
            "draw from spec", "generate drawing", "create drawing",
            "complex", "assembly", "mechanical drawing", "floor plan",
            "layout", "bom", "parts list", "section", "exploded",
            "3d", "solid", "from specification", "复杂", "绘制", "生成",
            "装配", "机械图", "爆炸图", "剖面", "三维",
        ],
        "steps": [
            "recommend_cad_tools(intent) to identify purpose-built tools",
            "create CADPlan with variables, layers, save_as handles, dependencies, expectations, and postconditions",
            "validate_cad_plan -> dry_run_cad_plan",
            "execute_cad_plan only with allow_modify=True and transactional=True",
            "scan_all_entities -> build_drawing_ir -> validate_geometry -> export_view_image_with_mapping",
        ],
        "fidelity": [
            "Use blocks/arrays for repeated components, CAD tables for BOMs, dimensions for measurements, hatches for sections, and solids/booleans for 3D intent.",
            "If no exposed tool can preserve a requested feature, state the limitation instead of silently simplifying it.",
        ],
    },
    {
        "name": "Validation-led repair",
        "keywords": [
            "repair", "fix", "validate", "issue", "constraint violation",
            "dimension mismatch", "geometry problem", "修复", "校验",
            "约束", "尺寸错误", "问题",
        ],
        "steps": [
            "validate_geometry",
            "extract_drawing_constraints -> bind_all_dimensions -> check_drawing_constraints when design intent matters",
            "propose_repair_plan or propose_constraint_repair_plan",
            "validate_cad_plan -> dry_run_cad_plan",
            "execute_cad_plan only after explicit modification permission",
            "scan_all_entities -> validate_geometry -> export_view_image_with_mapping",
        ],
        "fidelity": [
            "Repair by handle and preserve blocks, associative dimensions, layers, hatches, and annotations when possible.",
            "Do not delete and redraw complex regions unless the plan states why that is the least destructive repair.",
        ],
    },
    {
        "name": "VLM visual review and grounding",
        "keywords": [
            "visual", "vlm", "screenshot", "overlay", "ground",
            "grounding", "pixel", "review", "view image", "视觉", "截图",
            "叠加", "定位", "审查",
        ],
        "steps": [
            "export_view_image_with_mapping(include_overlay=True, overlay_granularity='both')",
            "validate_vlm_review_output before trusting model JSON",
            "submit_vlm_review to ground overlay IDs or bboxes to handles",
            "get_vlm_findings -> fuse_vlm_findings_into_semantic_graph when findings identify semantic objects",
            "analyze_engineering_drawing_stages for layout/annotation reconciliation",
        ],
        "fidelity": [
            "VLM findings are hypotheses until grounded to handles or primitive candidates.",
            "Do not draw helper labels into the DWG for visual grounding.",
        ],
    },
]


TOOL_ROUTING_CATALOG = [
    {
        "category": "Understanding",
        "tool": "build_drawing_ir",
        "use": "Build CAD-IR v2, a structured drawing index for complex drawings, handle lookup, resources, and downstream validation.",
        "avoid": "Do not summarize or edit a dense drawing from primitive counts alone.",
        "keywords": ["cad-ir", "drawing ir", "structured index", "complex drawing", "understand drawing", "handle lookup", "工程图", "复杂图纸", "结构化索引"],
    },
    {
        "category": "Understanding",
        "tool": "summarize_drawing",
        "use": "Summarize scanned drawing metadata after CAD-IR and semantic extraction.",
        "avoid": "Do not infer drawing purpose from a small sample of entities.",
        "keywords": ["summary", "summarize", "overview", "drawing summary", "概览", "总结"],
    },
    {
        "category": "Understanding",
        "tool": "detect_semantic_objects",
        "use": "Detect domain-level objects such as parts, holes, labels, dimensions, tables, views, and drawing regions.",
        "avoid": "Do not flatten meaningful CAD objects into anonymous lines and text.",
        "keywords": ["semantic", "part", "assembly", "hole", "view", "title block", "bom", "gdt", "gd&t", "语义", "零件", "装配", "孔", "标题栏"],
    },
    {
        "category": "Understanding",
        "tool": "extract_drawing_constraints",
        "use": "Extract geometric and dimensional constraints such as alignment, symmetry, spacing, and measurement intent.",
        "avoid": "Do not move or redraw constrained geometry before checking design intent.",
        "keywords": ["constraint", "constraints", "alignment", "symmetry", "spacing", "design intent", "约束", "对齐", "对称", "间距"],
    },
    {
        "category": "Understanding",
        "tool": "bind_all_dimensions",
        "use": "Bind dimension entities to likely geometry handles before validation, repair, or explanation.",
        "avoid": "Do not treat dimension text as unconnected annotation when associative binding is needed.",
        "keywords": ["bind dimensions", "dimension binding", "dimensioned", "measurement", "尺寸绑定", "尺寸关联"],
    },
    {
        "category": "Understanding",
        "tool": "check_drawing_constraints",
        "use": "Check extracted constraints against current geometry and report violations.",
        "avoid": "Do not repair design-intent issues without checking the active constraint set.",
        "keywords": ["check constraints", "constraint violation", "violated", "约束检查", "违反约束"],
    },
    {
        "category": "Validation",
        "tool": "validate_geometry",
        "use": "Validate geometry, layers, blocks, annotations, dimensions, and constraints before and after changes.",
        "avoid": "Do not rely on visual appearance alone after a complex edit.",
        "keywords": ["validate", "validation", "geometry check", "quality", "校验", "验证", "质量"],
    },
    {
        "category": "Visual grounding",
        "tool": "export_view_image_with_mapping",
        "use": "Export clean image, overlay, pixel/world mapping, visible handles, and optional tiles for dense visual review.",
        "avoid": "Do not use visible helper geometry to label or ground model observations.",
        "keywords": ["visual mapping", "overlay", "pixel", "world mapping", "tiles", "view image", "grounding", "视觉", "叠加", "像素", "定位"],
    },
    {
        "category": "Engineering review",
        "tool": "analyze_engineering_drawing_stages",
        "use": "Analyze layout segmentation, annotations, title blocks, BOM-like tables, VLM parsing, and semantic reconciliation.",
        "avoid": "Do not simplify engineering sheets to one model-space outline when views and annotations carry meaning.",
        "keywords": ["engineering drawing", "assembly drawing", "section view", "detail view", "bom", "title block", "annotation detection", "工程图", "装配图", "剖视图", "标题栏", "明细表"],
    },
    {
        "category": "VLM grounding",
        "tool": "validate_vlm_review_output",
        "use": "Validate VLM JSON before grounding or persisting findings.",
        "avoid": "Do not trust unvalidated VLM prose or malformed JSON as drawing evidence.",
        "keywords": ["vlm json", "validate vlm", "review output", "视觉模型", "审查输出"],
    },
    {
        "category": "VLM grounding",
        "tool": "submit_vlm_review",
        "use": "Ground VLM overlay IDs or pixel bboxes to CAD handles and persist evidence.",
        "avoid": "Do not convert VLM findings into repairs before grounding candidates are inspected.",
        "keywords": ["submit vlm", "ground findings", "overlay id", "bbox", "VLM定位", "候选句柄"],
    },
    {
        "category": "VLM grounding",
        "tool": "fuse_vlm_findings_into_semantic_graph",
        "use": "Fuse grounded VLM findings into the semantic graph as evidence-bearing hypotheses.",
        "avoid": "Do not overwrite lower-level CAD evidence with unreviewed visual hypotheses.",
        "keywords": ["fuse vlm", "semantic graph", "visual semantics", "语义图", "融合"],
    },
    {
        "category": "CADPlan",
        "tool": "validate_cad_plan",
        "use": "Validate CADPlan schema, dependencies, tool bindings, safety, and postconditions before execution.",
        "avoid": "Do not execute a multi-step plan before validation.",
        "keywords": ["cadplan", "plan validation", "validate plan", "multi-step", "计划校验"],
    },
    {
        "category": "CADPlan",
        "tool": "dry_run_cad_plan",
        "use": "Dry-run a CADPlan to inspect planned operations and handle bindings without modifying AutoCAD.",
        "avoid": "Do not skip dry-run for multi-step generation or repair.",
        "keywords": ["dry run", "dry-run", "preview plan", "cadplan", "试运行", "预演"],
    },
    {
        "category": "CADPlan",
        "tool": "execute_cad_plan",
        "use": "Execute a validated CADPlan only after explicit modification permission with allow_modify=True.",
        "avoid": "Do not execute automatically or without transactional safeguards for complex changes.",
        "keywords": ["execute plan", "execute cadplan", "transactional", "allow_modify", "执行计划"],
    },
    {
        "category": "Repair planning",
        "tool": "propose_repair_plan",
        "use": "Propose a CADPlan for selected validation or VLM issues without modifying the DWG.",
        "avoid": "Do not manually patch validation issues when a proposed plan can preserve intent.",
        "keywords": ["repair plan", "fix issue", "validation issue", "repair drawing", "修复计划", "修复问题"],
    },
    {
        "category": "Repair planning",
        "tool": "propose_constraint_repair_plan",
        "use": "Propose a CADPlan for violated constraints without modifying the DWG.",
        "avoid": "Do not repair a constraint violation without exposing the intended constraint change.",
        "keywords": ["constraint repair", "violated constraint", "repair constraint", "约束修复"],
    },
    {
        "category": "2D drawing",
        "tool": "draw_rectangle",
        "use": "Draw any rectangle or square from two opposite corners.",
        "avoid": "Do not draw rectangles as four draw_line calls.",
        "keywords": ["rectangle", "rect", "square", "box outline", "矩形", "正方形"],
    },
    {
        "category": "2D drawing",
        "tool": "draw_polygon",
        "use": "Draw regular triangles, pentagons, hexagons, octagons, and other equal-sided polygons.",
        "avoid": "Do not build regular polygons from repeated line segments.",
        "keywords": ["polygon", "triangle", "hexagon", "octagon", "regular", "多边形", "三角形", "六边形", "八边形"],
    },
    {
        "category": "2D drawing",
        "tool": "draw_mline",
        "use": "Draw parallel multi-lines such as walls, roads, or double-line symbols.",
        "avoid": "Do not fake walls with two offset draw_line calls.",
        "keywords": ["wall", "walls", "parallel line", "double line", "mline", "墙", "墙体", "平行线", "双线"],
    },
    {
        "category": "2D drawing",
        "tool": "draw_spline",
        "use": "Draw smooth free-form curves through fit points.",
        "avoid": "Do not approximate smooth curves with many short lines.",
        "keywords": ["spline", "smooth curve", "freeform", "curve", "样条", "曲线", "平滑"],
    },
    {
        "category": "2D drawing",
        "tool": "draw_donut",
        "use": "Draw a ring, washer, gasket, or filled annulus.",
        "avoid": "Do not draw a ring as separate circles unless hatch/fill behavior is intentional.",
        "keywords": ["donut", "ring", "washer", "gasket", "annulus", "圆环", "垫圈"],
    },
    {
        "category": "Polyline detailing",
        "tool": "polyline_set_bulge",
        "use": "Make a polyline segment into a true arc segment.",
        "avoid": "Do not approximate arc segments with many short line segments.",
        "keywords": ["bulge", "arc segment", "curved polyline", "圆弧段", "凸度"],
    },
    {
        "category": "Polyline detailing",
        "tool": "fillet_polyline",
        "use": "Round all corners of one polyline in one operation.",
        "avoid": "Do not fillet each corner manually when the whole polyline should be rounded.",
        "keywords": ["fillet polyline", "round all corners", "rounded rectangle", "polyline fillet", "圆角矩形", "多段线圆角"],
    },
    {
        "category": "Polyline detailing",
        "tool": "chamfer_polyline",
        "use": "Chamfer all corners of one polyline in one operation.",
        "avoid": "Do not chamfer each corner manually when the whole polyline should be beveled.",
        "keywords": ["chamfer polyline", "bevel all corners", "多段线倒角", "倒角"],
    },
    {
        "category": "Editing",
        "tool": "move_entity",
        "use": "Move existing geometry by handle.",
        "avoid": "Do not delete and redraw geometry just to reposition it.",
        "keywords": ["move", "reposition", "shift", "移动", "平移"],
    },
    {
        "category": "Editing",
        "tool": "mirror_entity",
        "use": "Mirror existing geometry across a line.",
        "avoid": "Do not redraw a flipped copy by hand.",
        "keywords": ["mirror", "symmetry", "reflect", "镜像", "对称"],
    },
    {
        "category": "Editing",
        "tool": "offset_entity",
        "use": "Create a parallel or concentric copy at a distance.",
        "avoid": "Do not redraw an offset line, circle, wall, or boundary manually.",
        "keywords": ["offset", "parallel copy", "concentric", "偏移", "等距", "同心"],
    },
    {
        "category": "Editing",
        "tool": "array_rectangular",
        "use": "Create a row/column grid of repeated entities.",
        "avoid": "Do not use loops of copy_entity for grids.",
        "keywords": ["array", "grid", "rows", "columns", "repeated", "rectangular array", "阵列", "矩形阵列", "行列", "网格"],
    },
    {
        "category": "Editing",
        "tool": "array_polar",
        "use": "Create a circular/radial pattern around a center point.",
        "avoid": "Do not place bolt holes, teeth, spokes, or radial copies one by one.",
        "keywords": ["polar array", "circular pattern", "bolt holes", "gear teeth", "spokes", "radial", "环形阵列", "圆周阵列", "螺栓孔", "齿轮", "辐条"],
    },
    {
        "category": "Editing",
        "tool": "fillet_entities",
        "use": "Round the corner between two selected edges/entities.",
        "avoid": "Do not draw a tangent arc manually for a normal fillet.",
        "keywords": ["fillet", "round corner", "radius corner", "圆角", "倒圆"],
    },
    {
        "category": "Editing",
        "tool": "chamfer_entities",
        "use": "Bevel the corner between two selected edges/entities.",
        "avoid": "Do not trim and redraw bevels manually.",
        "keywords": ["chamfer", "bevel", "倒角"],
    },
    {
        "category": "Editing",
        "tool": "trim_entity",
        "use": "Cut an entity back to one or more cutting boundaries.",
        "avoid": "Do not manually calculate and redraw shortened geometry.",
        "keywords": ["trim", "cut to boundary", "修剪", "裁剪"],
    },
    {
        "category": "Editing",
        "tool": "extend_entity",
        "use": "Extend an entity to one or more boundary entities.",
        "avoid": "Do not redraw a longer version of existing geometry.",
        "keywords": ["extend", "extend to boundary", "延伸"],
    },
    {
        "category": "Blocks",
        "tool": "create_block",
        "use": "Turn repeated geometry into a reusable block definition.",
        "avoid": "Do not duplicate complex components as raw geometry when reuse is expected.",
        "keywords": ["block", "component", "symbol", "reuse", "块", "图块", "组件", "符号"],
    },
    {
        "category": "Blocks",
        "tool": "insert_block",
        "use": "Place an instance of an existing block.",
        "avoid": "Do not redraw a known component instance by hand.",
        "keywords": ["insert block", "place block", "block reference", "插入块", "图块参照"],
    },
    {
        "category": "Blocks",
        "tool": "insert_minsert_block",
        "use": "Insert a block as a rectangular MInsert block array entity.",
        "avoid": "Do not compose insert_block plus array_rectangular when a single MInsert entity is intended.",
        "keywords": ["minsert", "m insert", "block array", "rectangular block array", "insert_minert_block", "MInsert", "图块阵列", "矩形图块阵列"],
    },
    {
        "category": "Hatch and fill",
        "tool": "add_hatch",
        "use": "Create hatch/fill, then add boundaries with hatch_add_boundary.",
        "avoid": "Do not imitate fill with dense parallel lines.",
        "keywords": ["hatch", "fill", "section fill", "material fill", "填充", "剖面线", "图案"],
    },
    {
        "category": "Dimensions",
        "tool": "add_qdim",
        "use": "Create multiple related dimensions quickly from entity handles.",
        "avoid": "Do not build dimension chains from text and lines.",
        "keywords": ["qdim", "quick dimension", "batch dimension", "dimension", "dimensions", "dimension chain", "快速标注", "批量标注", "尺寸链"],
    },
    {
        "category": "Dimensions",
        "tool": "add_linear_dimension",
        "use": "Create an associative linear/aligned distance dimension.",
        "avoid": "Do not write measured distances as draw_text.",
        "keywords": ["linear dimension", "distance dimension", "length dimension", "dimension", "dimensions", "尺寸", "线性标注", "长度标注"],
    },
    {
        "category": "Dimensions",
        "tool": "add_radial_dimension",
        "use": "Create a radius dimension for circles/arcs.",
        "avoid": "Do not annotate radius as plain text.",
        "keywords": ["radius dimension", "radial dimension", "半径标注", "R标注"],
    },
    {
        "category": "Dimensions",
        "tool": "add_diametric_dimension",
        "use": "Create a diameter dimension for circles/arcs.",
        "avoid": "Do not annotate diameter as plain text.",
        "keywords": ["diameter dimension", "diametric dimension", "直径标注", "直径"],
    },
    {
        "category": "Annotation",
        "tool": "add_mleader",
        "use": "Create a modern callout leader with text.",
        "avoid": "Do not draw arrow lines and separate text manually for normal callouts.",
        "keywords": ["leader", "callout", "note arrow", "mleader", "引线", "多重引线", "标注说明"],
    },
    {
        "category": "Annotation",
        "tool": "add_table",
        "use": "Create a CAD table for schedules, BOMs, part lists, or notes.",
        "avoid": "Do not build tables from many lines and text entities.",
        "keywords": ["table", "schedule", "bom", "parts list", "表格", "材料表", "明细表"],
    },
    {
        "category": "3D solids",
        "tool": "draw_box",
        "use": "Create a true 3D box solid.",
        "avoid": "Do not draw a 3D box as a wireframe of lines.",
        "keywords": ["3d box", "cuboid", "solid box", "盒子", "长方体", "立方体"],
    },
    {
        "category": "3D solids",
        "tool": "draw_cylinder",
        "use": "Create a true 3D cylinder solid.",
        "avoid": "Do not draw cylinders as circles plus lines.",
        "keywords": ["cylinder", "hole cutter", "柱体", "圆柱"],
    },
    {
        "category": "3D solids",
        "tool": "add_region",
        "use": "Convert closed 2D curves into a region before extrusion or revolve.",
        "avoid": "Do not extrude loose open curves.",
        "keywords": ["region", "closed profile", "profile", "面域", "轮廓"],
    },
    {
        "category": "3D solids",
        "tool": "extrude_region",
        "use": "Create a 3D solid by extruding a region.",
        "avoid": "Do not model extrusions as wireframes.",
        "keywords": ["extrude", "extrusion", "拉伸", "挤出"],
    },
    {
        "category": "3D solids",
        "tool": "revolve_region",
        "use": "Create a revolved solid from a region and axis.",
        "avoid": "Do not approximate revolved solids with meshes unless required.",
        "keywords": ["revolve", "lathe", "shaft", "vase", "旋转", "回转", "轴"],
    },
    {
        "category": "3D solids",
        "tool": "solid_boolean",
        "use": "Union, subtract, or intersect 3D solids.",
        "avoid": "Do not manually redraw cut or merged solids.",
        "keywords": ["boolean", "subtract", "union", "intersect", "cut hole", "布尔", "差集", "并集", "交集", "开孔"],
    },
    {
        "category": "Query",
        "tool": "scan_all_entities",
        "use": "Scan an existing drawing into SQLite before analysis or edits. Default scans are lightweight but still derive topology summaries; use topology_detail='full' for primitive/relation topology.",
        "avoid": "Do not edit an unknown existing drawing without surveying handles first.",
        "keywords": ["scan", "survey", "inspect drawing", "existing drawing", "扫描", "识别", "现有图纸"],
    },
    {
        "category": "Query",
        "tool": "execute_query",
        "use": "Run read-only SQL over scanned CAD metadata to filter, count, and analyze entities.",
        "avoid": "Do not manually inspect many entities when SQL can filter them; do not use it for writes.",
        "keywords": ["sql", "query", "filter", "count", "statistics", "查询", "统计", "筛选"],
    },
    {
        "category": "Query",
        "tool": "get_topology_summary",
        "use": "Inspect derived point/line/curve/surface/solid counts for scanned entities.",
        "avoid": "Do not parse geometry JSON manually when topology summaries answer the relationship question.",
        "keywords": ["topology", "point", "line", "surface", "face", "点", "线", "面", "拓扑", "关系"],
    },
    {
        "category": "Query",
        "tool": "get_entity_topology",
        "use": "Inspect one entity's derived primitives and relations such as starts_at, ends_at, bounded_by.",
        "avoid": "Do not infer endpoints or boundaries from raw JSON when this tool can return them directly.",
        "keywords": ["topology", "entity topology", "relations", "bounded", "端点", "边界", "关系"],
    },
    {
        "category": "Vision verification",
        "tool": "export_view_image",
        "use": "Export the current AutoCAD view as a model-facing review image artifact without modifying the DWG.",
        "avoid": "Do not rely only on database scans when a vision-capable model needs to verify the visible drawing state.",
        "keywords": ["vision", "visual check", "image export", "view image", "screenshot", "verify drawing", "export image", "visual", "review"],
    },
    {
        "category": "Spatial annotations",
        "tool": "add_spatial_annotation",
        "use": "Add a hidden SQLite-only label for an entity, primitive, point, bbox, area, view, or group.",
        "avoid": "Do not draw helper labels, create hidden layers, or write XData when the mark is only for model reasoning.",
        "keywords": ["annotate", "spatial mark", "label entity", "pointer", "private label", "hidden mark", "mark part"],
    },
    {
        "category": "Spatial annotations",
        "tool": "list_spatial_annotations",
        "use": "Retrieve hidden SQLite-only labels that help the model remember drawing parts and spatial intent.",
        "avoid": "Do not rescan or visually inspect when the needed model-private mark is already stored.",
        "keywords": ["list annotations", "spatial marks", "private labels", "pointers", "model context"],
    },
    {
        "category": "Spatial annotations",
        "tool": "clear_spatial_annotations",
        "use": "Remove hidden SQLite-only labels without touching the AutoCAD drawing.",
        "avoid": "Do not erase DWG geometry when only model-private context should be cleared.",
        "keywords": ["clear annotations", "delete marks", "remove labels", "spatial marks"],
    },
    {
        "category": "System",
        "tool": "check_runtime_environment",
        "use": "Run a preflight that reports required Python, Windows, AutoCAD COM, workspace, and visual review capabilities.",
        "avoid": "Do not start live CAD edits when required preflight checks are failing.",
        "keywords": ["preflight", "doctor", "environment", "runtime", "dependencies", "autocad com", "install check"],
    },
    {
        "category": "System",
        "tool": "send_command",
        "use": "Raw AutoCAD command only when no dedicated MCP tool fits.",
        "avoid": "Do not use send_command for normal drawing/editing/dimensioning covered by named tools.",
        "keywords": ["raw command", "autocad command", "send command", "命令行", "原生命令"],
    },
]

_TOOL_ROUTING_BY_NAME = {entry["tool"]: entry for entry in TOOL_ROUTING_CATALOG}


def _score_keywords(intent: str, keywords: List[str]) -> int:
    query = intent.lower()
    score = 0
    for keyword in keywords:
        kw = keyword.lower()
        if kw and kw in query:
            score += 6
    return score


def _recommended_workflow_playbooks(intent: str) -> List[Dict[str, Any]]:
    scored = [
        (_score_keywords(intent, playbook["keywords"]), playbook)
        for playbook in WORKFLOW_PLAYBOOKS
    ]
    matches = [
        playbook
        for score, playbook in sorted(scored, key=lambda item: (-item[0], item[1]["name"]))
        if score > 0
    ]
    query = intent.lower()
    words = set(query.replace("_", " ").replace("-", " ").replace(",", " ").split())
    asks_understanding = (
        bool({"understand", "inspect", "analyze"} & words)
        or "review existing" in query
        or "理解" in query
        or "分析" in query
    )
    asks_creation = (
        bool({"draw", "create", "generate"} & words)
        or "from spec" in query
        or "from specification" in query
        or "绘制" in query
        or "生成" in query
        or "创建" in query
    )
    asks_repair = (
        bool({"repair", "fix"} & words)
        or "修复" in query
    )
    if asks_repair and not asks_creation:
        repair_matches = [playbook for playbook in matches if playbook["name"] == "Validation-led repair"]
        if repair_matches:
            return repair_matches
    if asks_understanding and not asks_creation:
        understanding_matches = [
            playbook for playbook in matches
            if playbook["name"] in {
                "Existing or complex drawing understanding",
                "VLM visual review and grounding",
            }
        ]
        if understanding_matches:
            return understanding_matches
    if asks_creation and not asks_understanding:
        creation_matches = [
            playbook for playbook in matches
            if playbook["name"] == "New complex drawing from specification"
        ]
        if creation_matches:
            return creation_matches
    return matches


def _score_tool_route(intent: str, entry: dict) -> int:
    query = intent.lower()
    score = 0
    if entry["tool"].lower() in query:
        score += 12
    haystack = " ".join(
        [entry["tool"], entry["category"], entry["use"], entry["avoid"]]
        + entry["keywords"]
    ).lower()
    score += _score_keywords(intent, entry["keywords"])
    for token in query.replace("_", " ").replace("-", " ").split():
        if len(token) >= 3 and token in haystack:
            score += 1
    return score


def recommend_cad_tools(intent: str, max_results: int = 8) -> str:
    """Recommend purpose-built CAD MCP tools for a natural-language intent.

    Args:
        intent: Natural-language CAD task description.
        max_results: Maximum recommendations to return.
    """
    if not intent or not intent.strip():
        return (
            "Provide a short CAD intent, for example: "
            "recommend_cad_tools('draw a rounded rectangle with dimensions')."
        )

    intent_text = intent.strip()
    max_results = max(1, min(int(max_results or 8), 20))
    playbooks = _recommended_workflow_playbooks(intent_text)[:2]
    scored = [
        (score, entry)
        for entry in TOOL_ROUTING_CATALOG
        if (score := _score_tool_route(intent_text, entry)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], item[1]["category"], item[1]["tool"]))

    if scored:
        entries = [entry for _, entry in scored[:max_results]]
    else:
        entries = [
            _TOOL_ROUTING_BY_NAME["scan_all_entities"],
            _TOOL_ROUTING_BY_NAME["execute_query"],
            _TOOL_ROUTING_BY_NAME["draw_rectangle"],
            _TOOL_ROUTING_BY_NAME["array_rectangular"],
            _TOOL_ROUTING_BY_NAME["add_qdim"],
            _TOOL_ROUTING_BY_NAME["send_command"],
        ][:max_results]

    lines = [f"Recommended CAD MCP tools for: {intent_text}"]
    if playbooks:
        lines.append("")
        lines.append("Workflow route:")
        for playbook in playbooks:
            lines.append(f"- {playbook['name']}")
            for step in playbook["steps"]:
                lines.append(f"  -> {step}")
            for guard in playbook["fidelity"]:
                lines.append(f"  Fidelity: {guard}")

    lines.append("")
    lines.append("Tool matches:")
    for idx, entry in enumerate(entries, 1):
        lines.append(f"{idx}. {entry['tool']} [{entry['category']}]")
        lines.append(f"   Use: {entry['use']}")
        lines.append(f"   Avoid: {entry['avoid']}")

    lines.append("")
    lines.append("Workflow guards:")
    lines.append("- Existing drawing: scan_all_entities -> get_entity_statistics/execute_query before edits.")
    lines.append("- Vision-capable model: call export_view_image_with_mapping whenever the visible CAD state needs confirmation.")
    lines.append("- Dense/complex drawing: use CAD-IR, semantic objects, constraints, validation, and overlay mapping before simplifying.")
    lines.append("- New complex drawing: use CADPlan validation and dry-run before execution.")
    lines.append("- Model-only context: use add_spatial_annotation/list_spatial_annotations instead of drawing helper labels.")
    lines.append("- Prefer named tools over draw_line/draw_circle/draw_polyline and repeated copy_entity.")
    lines.append("- Use send_command only after the catalog has no suitable tool.")
    lines.append("- Capture returned handles; most edits and dimensions need handles.")
    return "\n".join(lines)


def get_tool_help(tool_name: Optional[str] = None) -> str:
    """获取 MCP 工具的帮助信息。

    Args:
        tool_name: 工具名称（为空则列出所有可用工具类别）
    """
    categories = {
        "文档操作": ["create_new_drawing", "open_drawing", "save_drawing",
                    "close_drawing", "get_document_info", "export_pdf",
                    "export_dxf", "export_dwf", "export_image",
                    "purge_drawing", "audit_drawing", "set_document_properties",
                    "set_drawing_password", "get_file_dependencies",
                    "get_active_space_info", "get_application_info",
                    "is_autocad_idle"],
        "2D绘图": ["draw_line", "draw_circle", "draw_arc", "draw_ellipse",
                "draw_polyline", "draw_rectangle", "draw_polygon",
                "draw_spline", "draw_point", "draw_text", "draw_mtext",
                "draw_donut", "draw_ray", "draw_xline", "draw_mline",
                "draw_2d_solid", "draw_trace", "draw_raster_image",
                "draw_tolerance", "add_shape", "draw_wipeout",
                "insert_minert_block"],
        "3D建模": ["draw_box", "draw_cone", "draw_cylinder", "draw_sphere",
                  "draw_torus", "draw_wedge", "draw_elliptical_cone",
                  "draw_elliptical_cylinder", "draw_3d_mesh",
                  "draw_polyface_mesh", "draw_3d_face",
                  "add_region", "extrude_region", "extrude_region_along_path",
                  "revolve_region", "solid_boolean", "check_interference",
                  "slice_solid", "section_solid"],
        "编辑": ["move_entity", "rotate_entity", "copy_entity",
                "delete_entity", "delete_entities", "mirror_entity",
                "scale_entity", "offset_entity", "array_rectangular",
                "array_polar", "explode_entity", "rotate_3d", "mirror_3d",
                "transform_entity", "get_bounding_box", "intersect_with",
                "set_entity_properties", "get_entity_properties",
                "set_entity_truecolor", "set_entity_transparency",
                "set_entity_plot_style", "get_extension_dictionary",
                "fillet_entities", "chamfer_entities", "trim_entity",
                "extend_entity", "break_entity", "join_entities",
                "stretch_entities", "lengthen_entity"],
        "图层": ["create_layer", "delete_layer", "rename_layer",
                "freeze_layer", "thaw_layer", "lock_layer", "unlock_layer",
                "turn_off_layer", "turn_on_layer", "set_current_layer",
                "get_all_layers", "isolate_layer", "unisolate_layers"],
        "文字与标注": ["create_text_style", "set_current_text_style",
                      "get_text_styles", "add_leader", "add_mleader",
                      "add_table", "edit_table_cell", "find_text",
                      "replace_text", "add_linear_dimension",
                      "add_angular_dimension", "add_radial_dimension",
                      "add_diametric_dimension", "add_ordinate_dimension",
                      "add_rotated_dimension", "add_qdim",
                      "add_baseline_dimension", "add_continue_dimension",
                      "get_dimension_styles", "set_current_dimension_style",
                      "copy_dimension_style"],
        "图块": ["create_block", "insert_block", "get_all_blocks",
                "explode_block", "attach_xref", "get_xrefs",
                "unload_xref", "reload_xref",
                "insert_minert_block"],
        "视图": ["zoom_extents", "zoom_window", "zoom_center",
                "zoom_scale", "zoom_previous", "zoom_all", "pan",
                "get_current_view", "get_layouts", "set_active_layout",
                "create_layout", "save_named_view", "restore_named_view",
                "get_named_views", "delete_named_view",
                "add_viewport", "get_viewports", "set_viewport_properties"],
        "查询与分析": ["scan_all_entities", "scan_entities_in_area",
                      "select_by_window", "select_by_crossing", "select_all",
                      "select_by_fence", "select_by_wpolygon",
                      "select_by_cpolygon", "select_at_point",
                      "highlight_entity", "highlight_entities",
                      "highlight_query_results", "get_entity_statistics",
                      "execute_query", "get_all_tables", "get_table_schema",
                      "get_entity_topology", "get_topology_summary"],
        "文件与系统": ["undo", "redo", "regen", "send_command",
                      "get_variable", "set_variable", "measure_distance",
                      "create_snapshot", "get_snapshots",
                      "get_preference", "set_preference",
                      "get_preferences_display", "get_preferences_drafting",
                      "get_preferences_files", "get_preferences_opensave",
                      "get_preferences_selection", "get_preferences_system",
                      "get_preferences_user"],
        "组与填充": ["create_group", "get_all_groups", "add_hatch"],
        "材质与线型": ["create_material", "get_materials",
                     "set_entity_material", "set_active_material",
                     "load_linetype", "get_linetypes"],
        "UCS与坐标": ["create_ucs", "get_all_ucs", "set_active_ucs",
                     "get_active_ucs", "translate_coordinates",
                     "polar_point", "angle_from_xaxis"],
        "打印输出": ["plot_to_device", "plot_to_file", "plot_preview",
                    "get_plot_devices", "get_plot_style_tables",
                    "get_plot_configurations"],
        "数据扩展": ["add_hyperlink", "get_hyperlinks", "remove_hyperlink",
                    "get_xdata", "set_xdata", "create_registered_application",
                    "get_registered_applications", "get_dictionaries",
                    "execute_sql_query"],
    }

    if tool_name:
        key = tool_name.strip()
        entry = _TOOL_ROUTING_BY_NAME.get(key)
        if entry:
            return (
                f"Tool: {entry['tool']}\n"
                f"Category: {entry['category']}\n"
                f"Use when: {entry['use']}\n"
                f"Avoid: {entry['avoid']}\n"
                f"Keywords: {', '.join(entry['keywords'])}"
            )
        return f"工具 '{tool_name}' — 使用 {tool_name}(args) 调用。\n详细参数请参考各工具函数的文档字符串。"

    lines = ["📐 CAD MCP 服务器 — 可用工具类别"]
    lines.append("=" * 60)
    lines.append("Tool selection: use the most specific named tool first.")
    lines.append("If unsure, call recommend_cad_tools(intent).")
    lines.append("Avoid rebuilding CAD features from primitives when a dedicated tool exists.")
    total = 0
    for cat, tools in categories.items():
        lines.append(f"\n## {cat} ({len(tools)} 个工具)")
        total += len(tools)
        for t in tools:
            lines.append(f"  - {t}")
    lines.append(f"\n{'=' * 60}")
    lines.append(f"共 {total} 个工具，覆盖 AutoCAD 的完整功能。")
    return "\n".join(lines)
