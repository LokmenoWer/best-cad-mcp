"""CAD MCP Tools — Database query, groups, styles, and utilities."""
from typing import Optional, List
import json
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, resolve_color

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


def execute_query(query: str) -> str:
    """在 CAD 元数据数据库上执行 SQL 查询。

    数据库包含扫描后的实体、图层、图块、文本模式等信息。
    这是 AI 理解图纸的关键工具 — 可以用 SQL 进行复杂的过滤、统计、关联分析。

    常用表:
      - cad_entities:  所有扫描的实体（handle, type, layer, color, properties JSON）
      - cad_layers:    图层配置
      - cad_blocks:    图块定义
      - text_patterns: 文本搜索统计
      - drawing_snapshots: 图纸快照

    常用查询示例:
      - 按类型统计: SELECT type, COUNT(*) FROM cad_entities GROUP BY type
      - 按图层过滤: SELECT * FROM cad_entities WHERE layer='WALL'
      - 搜索文字:   SELECT * FROM cad_entities WHERE json_extract(properties, '$.text_string') LIKE '%门%'

    Args:
        query: SQL 查询字符串（SELECT/INSERT/UPDATE/DELETE）
    """
    try:
        result = db.execute(query)
        if "columns" in result:
            # Return as formatted table
            cols = result["columns"]
            rows = result["rows"]
            if not rows:
                return f"查询返回 0 行 (列: {', '.join(cols)})"
            return json.dumps(rows, indent=2, ensure_ascii=False, default=str)
        else:
            return f"已执行，影响 {result['affected_rows']} 行"
    except Exception as e:
        return f"查询执行失败: {e}"


def execute_sql_query(query: str) -> str:
    """执行 SQL 查询（execute_query 的别名）。"""
    return execute_query(query)


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
            hatch.Color = resolve_color(color)
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


def clear_selection_set(ss_name: str = "MCP_TEMP_SS") -> str:
    """Clear a selection set (remove entities from set, not from drawing).

    Args:
        ss_name: selection set name
    """
    r = ctrl.selection_clear(ss_name)
    return r["message"]


# ── Help / Documentation ───────────────────────────────────────

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
                      "execute_query", "get_all_tables", "get_table_schema"],
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
        return f"工具 '{tool_name}' — 使用 {tool_name}(args) 调用。\n详细参数请参考各工具函数的文档字符串。"

    lines = ["📐 CAD MCP 服务器 — 可用工具类别"]
    lines.append("=" * 60)
    total = 0
    for cat, tools in categories.items():
        lines.append(f"\n## {cat} ({len(tools)} 个工具)")
        total += len(tools)
        for t in tools:
            lines.append(f"  - {t}")
    lines.append(f"\n{'=' * 60}")
    lines.append(f"共 {total} 个工具，覆盖 AutoCAD 的完整功能。")
    return "\n".join(lines)
