"""CAD MCP Tools — Dimensioning: linear, aligned, angular, radial, diametric, ordinate."""
from typing import Any, Optional, List, Tuple
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, format_error

ctrl = get_controller()
db = get_database()


def _controller_error(result: Any, action: str) -> Optional[str]:
    if isinstance(result, dict) and not result.get("success", True):
        message = result.get("message") or result.get("error") or str(result)
        return format_error(f"{action} failed: {message}")
    return None


def _extract_handle_or_error(result: Any, action: str) -> Tuple[Optional[str], Optional[str]]:
    error = _controller_error(result, action)
    if error:
        return None, error
    if isinstance(result, dict):
        handle = result.get("handle") or result.get("new_handle")
    else:
        handle = getattr(result, "Handle", None)
    if not handle:
        return None, format_error(f"{action} failed: no entity handle was returned.")
    return str(handle), None


def add_linear_dimension(x1: float, y1: float, x2: float, y2: float,
                         text_x: float, text_y: float,
                         z1: float = 0.0, z2: float = 0.0, text_z: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """添加对齐线性标注（测量两点间距离）。

    Args:
        x1, y1: 第一个测量点坐标
        x2, y2: 第二个测量点坐标
        text_x, text_y: 标注文字位置
        z1, z2, text_z: Z坐标
        layer: 图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_linear(
        (x1, y1, z1), (x2, y2, z2), (text_x, text_y, text_z))
    handle, error = _extract_handle_or_error(dim, "Add linear dimension")
    if error:
        return error
    dist = ((x2-x1)**2 + (y2-y1)**2)**0.5
    return format_success(f"Added linear dimension ({dist:.2f} units)",
                          handle=handle)


def add_rotated_dimension(x1: float, y1: float, x2: float, y2: float,
                          text_x: float, text_y: float, rotation: float,
                          layer: Optional[str] = None) -> str:
    """添加旋转线性标注。

    Args:
        x1, y1:   第一个测量点
        x2, y2:   第二个测量点
        text_x, text_y: 标注文字位置
        rotation: 标注旋转角度（度）
        layer:    图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_rotated(
        (x1, y1, 0), (x2, y2, 0), (text_x, text_y, 0), rotation)
    handle, error = _extract_handle_or_error(dim, "Add rotated dimension")
    if error:
        return error
    return format_success(f"Added rotated dimension ({rotation} degrees)",
                          handle=handle)


def add_angular_dimension(center_x: float, center_y: float,
                          x1: float, y1: float, x2: float, y2: float,
                          text_x: float, text_y: float,
                          layer: Optional[str] = None) -> str:
    """添加角度标注。

    Args:
        center_x, center_y: 角度顶点坐标
        x1, y1:  第一条边上的点
        x2, y2:  第二条边上的点
        text_x, text_y: 标注文字位置
        layer:   图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_angular(
        (center_x, center_y, 0), (x1, y1, 0), (x2, y2, 0), (text_x, text_y, 0))
    handle, error = _extract_handle_or_error(dim, "Add angular dimension")
    if error:
        return error
    return format_success("Added angular dimension", handle=handle)


def add_radial_dimension(center_x: float, center_y: float,
                         chord_x: float, chord_y: float,
                         leader_length: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """添加半径标注（用于圆弧或圆）。

    Args:
        center_x, center_y: 圆心坐标
        chord_x, chord_y:   标注点坐标（圆弧上的点）
        leader_length:      引线长度（0=自动）
        layer:              图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_radial(
        (center_x, center_y, 0), (chord_x, chord_y, 0), leader_length)
    handle, error = _extract_handle_or_error(dim, "Add radial dimension")
    if error:
        return error
    import math
    r = math.sqrt((chord_x-center_x)**2 + (chord_y-center_y)**2)
    return format_success(f"Added radial dimension (R{r:.2f})", handle=handle)


def add_diametric_dimension(chord1_x: float, chord1_y: float,
                            chord2_x: float, chord2_y: float,
                            leader_length: float = 0.0,
                            layer: Optional[str] = None) -> str:
    """添加直径标注（用于圆）。

    Args:
        chord1_x, chord1_y: 第一个直径端点
        chord2_x, chord2_y: 第二个直径端点（对点）
        leader_length:      引线长度
        layer:              图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_diametric(
        (chord1_x, chord1_y, 0), (chord2_x, chord2_y, 0), leader_length)
    handle, error = _extract_handle_or_error(dim, "Add diametric dimension")
    if error:
        return error
    import math
    d = math.sqrt((chord2_x-chord1_x)**2 + (chord2_y-chord1_y)**2)
    return format_success(f"Added diametric dimension (D{d:.2f})", handle=handle)


def add_ordinate_dimension(x: float, y: float,
                           leader_end_x: float, leader_end_y: float,
                           use_x_axis: bool = True,
                           layer: Optional[str] = None) -> str:
    """添加坐标标注（显示X或Y坐标值）。

    Args:
        x, y:           要标注的点坐标
        leader_end_x, leader_end_y: 引线终点
        use_x_axis:     True=标注X坐标, False=标注Y坐标
        layer:          图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_ordinate(
        (x, y, 0), (leader_end_x, leader_end_y, 0), use_x_axis)
    handle, error = _extract_handle_or_error(dim, "Add ordinate dimension")
    if error:
        return error
    axis = "X" if use_x_axis else "Y"
    return format_success(f"Added {axis} ordinate dimension", handle=handle)


def get_dimension_styles() -> str:
    """List all dimension styles."""
    styles = ctrl.get_dim_styles()
    error = _controller_error(styles, "Get dimension styles")
    if error:
        return error
    if not styles:
        return "No dimension styles."
    lines = [f"Dimension styles ({len(styles)}):"]
    for i, s in enumerate(styles):
        if isinstance(s, dict):
            name = s.get("name", "<unnamed>")
        else:
            name = str(s)
        lines.append(f"  [{i}] {name}")
    return "\n".join(lines)


def copy_dimension_style(source_name: str, new_name: str) -> str:
    """复制标注样式（基于现有样式创建新样式）。

    Args:
        source_name: 源样式名称
        new_name:    新样式名称
    """
    r = ctrl.copy_dim_style(source_name, new_name)
    return r["message"]


def set_current_dimension_style(name: str) -> str:
    """设置当前标注样式。

    Args:
        name: 标注样式名称
    """
    r = ctrl.set_current_dim_style(name)
    return r["message"]


def add_qdim(entity_handles: List[str],
              dimension_type: str = "continuous",
              layer: Optional[str] = None) -> str:
    """快速标注 (QDIM) — 从选定的实体快速生成连续的尺寸标注。

    一次性为多个实体添加标注，大幅提高标注效率。

    Args:
        entity_handles: 要标注的实体句柄列表
        dimension_type: 标注类型: "continuous"(连续标注),
                       "staggered"(交错标注), "baseline"(基线标注),
                       "ordinate"(坐标标注), "radius"(半径标注),
                       "diameter"(直径标注), "datum"(基准点标注)
        layer:          图层名称
    """
    if not entity_handles:
        return "错误: 至少需要一个实体"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    type_map = {
        "continuous": "C", "staggered": "S", "baseline": "B",
        "ordinate": "O", "radius": "R", "diameter": "D", "datum": "P"
    }
    dt = type_map.get(dimension_type, "C")
    # Build a selection set of the entities by handle, then run QDIM on it.
    ssadd = "".join(f'(ssadd (handent "{h}") ss)' for h in entity_handles)
    place = ' "0,0"' if dt in ("C", "S", "B", "O") else ""
    lisp = (f'(setq ss (ssadd)){ssadd}'
            f'(command "._QDIM" ss "" "_{dt}"{place})')
    r = ctrl.run_lisp(lisp)
    if not r["success"]:
        return f"快速标注失败: {r['message']}"
    return format_success(f"已快速标注 {len(entity_handles)} 个实体",
                          type=dimension_type)


def add_baseline_dimension(x: float, y: float, z: float = 0.0,
                            layer: Optional[str] = None) -> str:
    """添加基线标注（从上一个标注的基线继续延伸）。

    使用该工具之前，必须先用 add_linear_dimension 创建一个基准标注。
    基线标注将从基准标注的第一条界线作为公共起点连续生成多个尺寸。

    Args:
        x, y, z: 下一个标注的第二个测量点
        layer:   图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    cmd = f"DIMBASELINE {x},{y},{z} \n \n"
    r = ctrl.send_command(cmd)
    return format_success(f"已添加基线标注", point=f"({x},{y},{z})")


def add_continue_dimension(x: float, y: float, z: float = 0.0,
                            layer: Optional[str] = None) -> str:
    """添加连续标注（从上一个标注的终点继续延伸，形成首尾相接的尺寸链）。

    使用该工具之前，必须先用 add_linear_dimension 创建一个起始标注。
    后续每个标注以上一个标注的终点为起点。

    Args:
        x, y, z: 下一个标注的第二个测量点
        layer:   图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    cmd = f"DIMCONTINUE {x},{y},{z} \n \n"
    r = ctrl.send_command(cmd)
    return format_success(f"已添加连续标注", point=f"({x},{y},{z})")


def draw_wipeout(p1_x: float, p1_y: float, p2_x: float, p2_y: float,
                 p3_x: Optional[float] = None, p3_y: Optional[float] = None,
                 p4_x: Optional[float] = None, p4_y: Optional[float] = None,
                 layer: Optional[str] = None) -> str:
    """创建区域覆盖 (Wipeout) — 用空白区域覆盖背后的对象。

    非常适合在密集的图纸中创建清晰的标注空间。
    支持指定 3 点（三角形）或 4 点（四边形）多边形。

    Args:
        p1_x,p1_y: 第一个顶点
        p2_x,p2_y: 第二个顶点
        p3_x,p3_y: 第三个顶点（可选）
        p4_x,p4_y: 第四个顶点（可选）
        layer:     图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    if p3_x is None or p3_y is None:
        return "错误: draw_wipeout 至少需要 3 个顶点；两个点不能定义有效遮罩区域"
    points = [(p1_x, p1_y), (p2_x, p2_y), (p3_x, p3_y)]
    if p4_x is not None and p4_y is not None:
        points.append((p4_x, p4_y))
    point_expr = " ".join(f"(list {x} {y} 0)" for x, y in points)
    r = ctrl.run_lisp(f'(command "._WIPEOUT" {point_expr} "")')
    if not r["success"]:
        return f"区域覆盖失败: {r['message']}"
    return format_success(f"已创建区域覆盖",
                          vertices=len(points))


def add_arc_dimension(center_x: float, center_y: float,
                       start_x: float, start_y: float,
                       end_x: float, end_y: float,
                       text_x: float, text_y: float,
                       layer: Optional[str] = None) -> str:
    """添加弧长标注（标注圆弧的弧长而不是弦长或角度）。

    弧长标注在尺寸数字上方显示圆弧符号 ⌒。

    Args:
        center_x, center_y: 圆心坐标
        start_x, start_y:   圆弧起点
        end_x, end_y:       圆弧终点
        text_x, text_y:     标注文字位置
        layer:              图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_arc(
        (center_x, center_y, 0),
        (start_x, start_y, 0),
        (end_x, end_y, 0),
        (text_x, text_y, 0))
    import math
    r = math.sqrt((start_x - center_x)**2 + (start_y - center_y)**2)
    return format_success(f"已添加弧长标注 (R={r:.2f})",
                          handle=dim.Handle)


def add_3point_angular_dimension(vertex_x: float, vertex_y: float,
                                    ref1_x: float, ref1_y: float,
                                    ref2_x: float, ref2_y: float,
                                    text_x: float, text_y: float,
                                    layer: Optional[str] = None) -> str:
    """添加三点角度标注（通过三个点定义的角度进行标注）。

    不需要圆心。适合标注非圆弧的夹角。

    Args:
        vertex_x, vertex_y: 角度顶点坐标
        ref1_x, ref1_y:     第一条角度参照线端点
        ref2_x, ref2_y:     第二条角度参照线端点
        text_x, text_y:     标注文字位置
        layer:              图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    dim = ctrl.add_dimension_3point_angular(
        (vertex_x, vertex_y, 0),
        (ref1_x, ref1_y, 0),
        (ref2_x, ref2_y, 0),
        (text_x, text_y, 0))
    return format_success(f"已添加三点角度标注",
                          handle=dim.Handle)


def set_dimension_text_override(handle: str, text: str) -> str:
    """覆盖标注的文字显示（用自定义文本替代自动测量的尺寸值）。

    空字符串 "" 表示移除覆盖，恢复自动测量值。

    Args:
        handle: 标注实体句柄
        text:   要显示的文本（空字符串=恢复自动测量值）
    """
    ent = ctrl._get_entity(handle)
    if ent is None:
        return f"错误: 未找到标注实体 {handle}"
    try:
        ent.TextOverride = text
        return format_success(
            f"已{'设置' if text else '移除'}标注文字覆盖: '{text}'" if text else "已恢复自动测量值",
            handle=handle)
    except Exception as e:
        return f"设置标注文字失败: {e}"


def get_dimension_measurement(handle: str) -> str:
    """获取标注的实际测量值（原始测量数据，不包含覆盖文字）。

    Args:
        handle: 标注实体句柄
    """
    ent = ctrl._get_entity(handle)
    if ent is None:
        return f"错误: 未找到标注实体 {handle}"
    try:
        import json
        info = {
            "handle": handle,
            "measurement": getattr(ent, 'Measurement', None),
            "text_override": getattr(ent, 'TextOverride', ""),
            "rotation": getattr(ent, 'Rotation', 0) * 180.0 / 3.141592653589793,
            "style_name": getattr(ent, 'StyleName', ""),
            "text_position": list(getattr(ent, 'TextPosition', [0, 0, 0])),
        }
        return json.dumps(info, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"获取标注信息失败: {e}"


def set_text_alignment(handle: str, alignment: int,
                        align_x: Optional[float] = None,
                        align_y: Optional[float] = None,
                        align_z: float = 0.0) -> str:
    """设置单行文字的水平和垂直对齐方式。

    对齐代码: 0=Left, 1=Center, 2=Right,
              3=Align, 4=Middle, 5=Fit,
              6=TopLeft, 7=TopCenter, 8=TopRight,
              9=MiddleLeft, 10=MiddleCenter, 11=MiddleRight,
              12=BottomLeft, 13=BottomCenter, 14=BottomRight

    Args:
        handle:    文字实体句柄
        alignment: 对齐代码 (0-14)
        align_x, align_y, align_z: 对齐点坐标（某些对齐方式需要）
    """
    ent = ctrl._get_entity(handle)
    if ent is None:
        return f"错误: 未找到文字实体 {handle}"
    if ent.ObjectName not in ("AcDbText", "AcDbMText"):
        return f"错误: 实体 {handle} 不是文字对象"
    try:
        ent.Alignment = int(alignment)
        if align_x is not None and align_y is not None:
            import pythoncom
            import win32com.client
            pt = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8,
                [float(align_x), float(align_y), float(align_z)])
            ent.TextAlignmentPoint = pt
        return format_success(f"已设置文字对齐方式={alignment}",
                              handle=handle)
    except Exception as e:
        return f"设置文字对齐失败: {e}"


def set_text_properties(handle: str, oblique_angle: Optional[float] = None,
                          scale_factor: Optional[float] = None,
                          style_name: Optional[str] = None) -> str:
    """设置单行文字的额外属性。

    可以设置倾斜角度（斜体效果）、宽度因子、文字样式。

    Args:
        handle:        文字实体句柄
        oblique_angle: 倾斜角度（度，0=正常, 15=右斜）
        scale_factor:  宽度因子（1=正常, <1=窄体, >1=宽体）
        style_name:    文字样式名称
    """
    ent = ctrl._get_entity(handle)
    if ent is None:
        return f"错误: 未找到文字实体 {handle}"
    if ent.ObjectName not in ("AcDbText", "AcDbMText"):
        return f"错误: 实体 {handle} 不是文字对象"
    changed = {}
    try:
        if oblique_angle is not None:
            ent.ObliqueAngle = float(oblique_angle) * 3.141592653589793 / 180.0
            changed["oblique_angle"] = oblique_angle
        if scale_factor is not None:
            ent.ScaleFactor = float(scale_factor)
            changed["scale_factor"] = scale_factor
        if style_name is not None:
            ent.StyleName = str(style_name)
            changed["style_name"] = style_name
        return format_success(f"已更新文字属性",
                              handle=handle, changed=str(changed))
    except Exception as e:
        return f"设置文字属性失败: {e}"
