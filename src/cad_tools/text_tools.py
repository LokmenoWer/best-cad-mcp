"""CAD MCP Tools — Text, MText, leaders, tables, text styles."""
from typing import Optional, List, Tuple, Any
import win32com.client
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, com_get as _com_get, com_set as _com_set

ctrl = get_controller()
db = get_database()


def _normalize_points(points: List[Any]) -> List[Tuple[float, float, float]]:
    if not points:
        return []
    if isinstance(points[0], (list, tuple)):
        normalized = []
        for p in points:
            if not isinstance(p, (list, tuple)):
                raise ValueError(
                    "points must be either a flat [x1,y1,z1,...] list or a nested [[x,y,z],...] list"
                )
            if len(p) < 2:
                raise ValueError("每个点至少需要 x,y 坐标")
            z = p[2] if len(p) > 2 else 0.0
            normalized.append((float(p[0]), float(p[1]), float(z)))
        return normalized
    if any(isinstance(p, (list, tuple)) for p in points):
        raise ValueError(
            "points must be either a flat [x1,y1,z1,...] list or a nested [[x,y,z],...] list"
        )
    if len(points) % 3 != 0:
        raise ValueError("点列表必须是 [x1,y1,z1, x2,y2,z2, ...] 或 [[x,y,z], ...]")
    return [
        (float(points[i]), float(points[i+1]), float(points[i+2]))
        for i in range(0, len(points), 3)
    ]


def _extract_handle_or_error(result: Any, action: str) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(result, dict):
        if not result.get("success", False):
            return None, f"{action}失败: {result.get('message', result)}"
        handle = result.get("handle") or result.get("new_handle")
    else:
        handle = getattr(result, "Handle", None)
    if not handle:
        return None, f"{action}失败: 未返回实体句柄"
    return str(handle), None


def create_text_style(name: str, font: str = "Arial",
                      height: float = 0.0, width: float = 1.0) -> str:
    """创建文字样式（设置字体、高度等）。

    Args:
        name:   样式名称
        font:   字体名称（如 Arial, SimSun, Romans）
        height: 默认文字高度（0=每次提示输入高度）
        width:  宽度因子（1=正常宽度）
    """
    r = ctrl.create_text_style(name, font, height, width)
    return r["message"]


def set_current_text_style(name: str) -> str:
    """设置当前文字样式。

    Args:
        name: 文字样式名称
    """
    r = ctrl.set_current_text_style(name)
    return r["message"]


def get_text_styles() -> str:
    """列出所有文字样式。"""
    styles = ctrl.get_text_styles()
    if not styles:
        return "无文字样式"
    lines = [f"共 {len(styles)} 个文字样式:"]
    for i, s in enumerate(styles):
        lines.append(f"  [{i}] {s['name']:<20s} 字体:{s['font_file']:<20s} "
                     f"高度:{s['height']} 宽度:{s['width']}")
    return "\n".join(lines)


def add_leader(points: List[Any], annotation: Optional[str] = None,
               layer: Optional[str] = None) -> str:
    """绘制引线标注。

    Args:
        points:     引线顶点列表 [x1,y1,z1, x2,y2,z2, ...] 或 [[x,y,z], ...]，至少2个点
        annotation: 引线末端注释文字（可选）
        layer:      图层名称
    """
    try:
        pts = _normalize_points(points)
    except (TypeError, ValueError) as e:
        return f"错误: {e}"
    if len(pts) < 2:
        return "错误: 至少需要2个引线点（6个值）"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    leader = ctrl.add_leader(pts, annotation)
    handle, error = _extract_handle_or_error(leader, "绘制引线")
    if error:
        return error
    return format_success(f"已绘制引线", handle=handle)


def add_mleader(text: str, points: List[Any],
                layer: Optional[str] = None) -> str:
    """绘制多重引线（带文字内容）。

    Args:
        text:   引线文字内容
        points: 引线顶点列表 [x1,y1,z1, x2,y2,z2, ...] 或 [[x,y,z], ...]
        layer:  图层名称
    """
    try:
        pts = _normalize_points(points)
    except (TypeError, ValueError) as e:
        return f"错误: {e}"
    if len(pts) < 2:
        return "错误: 至少需要2个点（6个值）"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    ml = ctrl.add_mleader(text, pts)
    handle, error = _extract_handle_or_error(ml, "绘制多重引线")
    if error:
        return error
    return format_success(f"已绘制多重引线 '{text}'", handle=handle)


def add_table(insert_x: float, insert_y: float, rows: int, columns: int,
              row_height: float = 1.0, column_width: float = 5.0,
              insert_z: float = 0.0, layer: Optional[str] = None) -> str:
    """在图纸中插入表格。

    Args:
        insert_x:     插入点 X 坐标
        insert_y:     插入点 Y 坐标
        rows:         行数
        columns:      列数
        row_height:   行高
        column_width: 列宽
        insert_z:     插入点 Z 坐标
        layer:        图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    table = ctrl.add_table((insert_x, insert_y, insert_z), rows, columns,
                           row_height, column_width)
    return format_success(f"已插入 {rows}×{columns} 表格",
                          handle=table.Handle)


def edit_table_cell(table_handle: str, row: int, col: int,
                    text: str) -> str:
    """编辑表格中指定单元格的文字。

    Args:
        table_handle: 表格句柄
        row:          行号（从0开始）
        col:          列号（从0开始）
        text:         要设置的文字
    """
    try:
        ctrl._ensure_connected()
        if not ctrl.has_document:
            return "??: ??????"
        ctrl.doc = ctrl.acad.ActiveDocument
        ent = ctrl.doc.HandleToObject(table_handle)
        ent.SetCellValue(row, col, text)
        return format_success(f"已设置表格[{row},{col}] = '{text}'")
    except Exception as e:
        return f"编辑表格失败: {e}"


def find_text(pattern: str, highlight_color: int = 1) -> str:
    """在图纸中搜索包含指定文本模式的所有文字实体。

    Args:
        pattern:         要搜索的文本模式
        highlight_color: 高亮颜色 (1=红, 2=黄, 3=绿)
    """
    try:
        ctrl._ensure_connected()
        if not ctrl.has_document:
            return "错误: 无打开的文档"
        ctrl.doc = ctrl.acad.ActiveDocument
        entities = []
        ms = ctrl.doc.ModelSpace
        for i in range(ms.Count - 1, -1, -1):
            try:
                ent = ms.Item(i)
                obj_name = _com_get(ent, "ObjectName", "")
                if obj_name not in {"AcDbText", "AcDbMText", "AcDbAttributeDefinition"}:
                    continue
                text = _com_get(ent, "TextString", None)
                if text and pattern in text:
                    entities.append({
                        "handle": _com_get(ent, "Handle", ""),
                        "text": text,
                        "layer": _com_get(ent, "Layer", "0"),
                    })
                    if highlight_color:
                        _com_set(ent, "Color", highlight_color)
                    if len(entities) >= 20:
                        break
            except Exception:
                pass
        if not entities:
            return f"未找到包含 '{pattern}' 的文字"
        lines = [f"找到 {len(entities)} 处 '{pattern}':"]
        for e in entities[:20]:
            lines.append(f"  {e['handle']}: '{e['text']}' (图层:{e['layer']})")
        if len(entities) > 20:
            lines.append(f"  ... 及其他 {len(entities)-20} 处")
        return "\n".join(lines)
    except Exception as e:
        return f"搜索文字失败: {e}"


def replace_text(find: str, replace: str) -> str:
    """替换图纸中所有匹配的文字。

    Args:
        find:    要查找的文本
        replace: 替换为的文本
    """
    try:
        ctrl._ensure_connected()
        if not ctrl.has_document:
            return "错误: 无打开的文档"
        ctrl.doc = ctrl.acad.ActiveDocument
        count = 0
        ms = ctrl.doc.ModelSpace
        for i in range(ms.Count - 1, -1, -1):
            try:
                ent = ms.Item(i)
                obj_name = _com_get(ent, "ObjectName", "")
                if obj_name not in {"AcDbText", "AcDbMText", "AcDbAttributeDefinition"}:
                    continue
                text = _com_get(ent, "TextString", None)
                if text and find in text:
                    ent.TextString = text.replace(find, replace)
                    count += 1
            except Exception:
                pass
        return format_success(f"已替换 {count} 处 '{find}' → '{replace}'")
    except Exception as e:
        return f"替换文字失败: {e}"
