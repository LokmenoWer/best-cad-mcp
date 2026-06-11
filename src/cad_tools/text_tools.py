"""CAD MCP Tools — Text, MText, leaders, tables, text styles."""
from typing import Optional, List, Tuple
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


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


def add_leader(points: List[float], annotation: Optional[str] = None,
               layer: Optional[str] = None) -> str:
    """绘制引线标注。

    Args:
        points:     引线顶点列表 [x1,y1,z1, x2,y2,z2, ...]，至少2个点
        annotation: 引线末端注释文字（可选）
        layer:      图层名称
    """
    if len(points) < 6:
        return "错误: 至少需要2个引线点（6个值）"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pts = [(points[i], points[i+1], points[i+2])
           for i in range(0, len(points), 3)]
    leader = ctrl.add_leader(pts, annotation)
    return format_success(f"已绘制引线", handle=leader.Handle)


def add_mleader(text: str, points: List[float],
                layer: Optional[str] = None) -> str:
    """绘制多重引线（带文字内容）。

    Args:
        text:   引线文字内容
        points: 引线顶点列表 [x1,y1,z1, x2,y2,z2, ...]
        layer:  图层名称
    """
    if len(points) < 6:
        return "错误: 至少需要2个点（6个值）"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pts = [(points[i], points[i+1], points[i+2])
           for i in range(0, len(points), 3)]
    ml = ctrl.add_mleader(text, pts)
    return format_success(f"已绘制多重引线 '{text}'", handle=ml.Handle)


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
        if not ctrl.has_document:
            return "错误: 无打开的文档"
        entities = []
        ms = ctrl.doc.ModelSpace
        for i in range(ms.Count):
            try:
                ent = ms.Item(i)
                if hasattr(ent, "TextString") and pattern in ent.TextString:
                    entities.append({
                        "handle": ent.Handle,
                        "text": ent.TextString,
                        "layer": ent.Layer,
                    })
                    if highlight_color:
                        ent.Color = highlight_color
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
        if not ctrl.has_document:
            return "错误: 无打开的文档"
        count = 0
        ms = ctrl.doc.ModelSpace
        for i in range(ms.Count):
            try:
                ent = ms.Item(i)
                if hasattr(ent, "TextString") and find in ent.TextString:
                    ent.TextString = ent.TextString.replace(find, replace)
                    count += 1
            except Exception:
                pass
        return format_success(f"已替换 {count} 处 '{find}' → '{replace}'")
    except Exception as e:
        return f"替换文字失败: {e}"
