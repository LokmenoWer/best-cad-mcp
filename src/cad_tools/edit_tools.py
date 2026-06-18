"""CAD MCP Tools — Entity editing: move, rotate, copy, delete, scale, mirror,
offset, array, explode, and property manipulation."""
from typing import List, Optional, Dict, Any
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, com_get as _com_get

ctrl = get_controller()
db = get_database()


def move_entity(handle: str, from_point: List[float],
                to_point: List[float]) -> str:
    """移动实体到新位置。

    Args:
        handle:     实体句柄
        from_point: 基点坐标 [x, y, z]（从何处移动）
        to_point:   目标点坐标 [x, y, z]（移动到何处）
    """
    r = ctrl.move_entity(handle, from_point, to_point)
    if r["success"]:
        # Update database
        ent = db.get_entity(handle)
        if ent:
            geom = ent.get("geometry", {})
            dx = to_point[0] - from_point[0]
            dy = to_point[1] - from_point[1]
            dz = to_point[2] - from_point[2] if len(to_point) > 2 and len(from_point) > 2 else 0
            # Update geometry positions
            for key in ("start_point", "end_point", "center", "insertion_point"):
                if key in geom and isinstance(geom[key], list) and len(geom[key]) >= 2:
                    geom[key][0] += dx
                    geom[key][1] += dy
                    if len(geom[key]) > 2:
                        geom[key][2] += dz
            db.upsert_entity(handle, ent["name"], ent["type"],
                             layer=ent.get("layer", "0"),
                             color=ent.get("color", 256),
                             geometry=geom)
        return f"✓ 已移动实体 {handle}，位移: ({to_point[0]-from_point[0]:.2f}, {to_point[1]-from_point[1]:.2f})"
    return f"移动实体失败: {r['message']}"


def rotate_entity(handle: str, base_point: List[float],
                  angle: float) -> str:
    """旋转实体。

    Args:
        handle:     实体句柄
        base_point: 旋转中心点 [x, y, z]
        angle:      旋转角度（度，逆时针为正）
    """
    r = ctrl.rotate_entity(handle, base_point, angle)
    if r["success"]:
        return format_success(f"已旋转实体 {handle} {angle}°", handle=handle)
    return f"旋转实体失败: {r['message']}"


def copy_entity(handle: str, from_point: List[float] = None,
                to_point: List[float] = None) -> str:
    """复制实体。如果提供位移点，会在复制后移动新实体。

    Args:
        handle:     源实体句柄
        from_point: 位移基点 [x, y, z]（可选，默认不移）
        to_point:   位移目标点 [x, y, z]（可选）
    """
    r = ctrl.copy_entity(handle)
    if r["success"]:
        new_h = r["new_handle"]
        if from_point and to_point:
            ctrl.move_entity(new_h, from_point, to_point)
        # Copy in database
        orig = db.get_entity(handle)
        if orig:
            db.upsert_entity(new_h, orig.get("name","Copy"), orig.get("type",""),
                             layer=orig.get("layer","0"), color=orig.get("color",256),
                             geometry=orig.get("geometry",{}))
        return format_success(f"已复制实体", new_handle=new_h,
                              source_handle=handle)
    return f"复制实体失败: {r['message']}"


def delete_entity(handle: str) -> str:
    """删除指定实体。

    Args:
        handle: 要删除的实体句柄
    """
    r = ctrl.delete_entity(handle)
    if r["success"]:
        db.delete_entity(handle)
        return f"✓ 已删除实体 {handle}"
    return f"删除失败: {r['message']}"


def delete_entities(handles: List[str]) -> str:
    """批量删除多个实体。

    Args:
        handles: 实体句柄列表
    """
    r = ctrl.delete_entities(handles)
    for h in r.get("deleted", []):
        db.delete_entity(h)
    deleted = len(r.get("deleted", []))
    failed = r.get("failed", [])
    if not failed:
        return format_success("已删除实体", count=deleted)
    details = [
        f"{item.get('handle')}: {item.get('message')}"
        if isinstance(item, dict) else str(item)
        for item in failed[:10]
    ]
    return (
        f"PARTIAL: 已删除 {deleted} 个实体，{len(failed)} 个失败。\n"
        + "\n".join(details)
    )


def mirror_entity(handle: str, line_start: List[float],
                  line_end: List[float]) -> str:
    """镜像实体（关于指定直线对称）。

    Args:
        handle:      实体句柄
        line_start:  镜像线起点 [x, y, z]
        line_end:    镜像线终点 [x, y, z]
    """
    r = ctrl.mirror_entity(handle, line_start, line_end)
    if r["success"]:
        return format_success(f"已镜像实体 {handle}",
                              new_handle=r.get("new_handle"))
    return f"镜像失败: {r['message']}"


def scale_entity(handle: str, base_point: List[float], scale: float) -> str:
    """缩放实体。

    Args:
        handle:     实体句柄
        base_point: 缩放基点 [x, y, z]
        scale:      缩放倍率（>1放大, <1缩小）
    """
    r = ctrl.scale_entity(handle, base_point, scale)
    if r["success"]:
        return format_success(f"已缩放实体 {handle} ({scale}x)")
    return f"缩放失败: {r['message']}"


def offset_entity(handle: str, distance: float) -> str:
    """偏移实体（适用于多段线、圆、弧等曲线）。

    Args:
        handle:   实体句柄
        distance: 偏移距离（正=外侧/右侧, 负=内侧/左侧）
    """
    r = ctrl.offset_entity(handle, distance)
    if r["success"]:
        return format_success(f"已偏移实体 {handle}",
                              distance=distance,
                              new_handles=r.get("new_handles", []))
    return f"偏移失败: {r['message']}"


def array_rectangular(handle: str, rows: int, columns: int,
                      row_spacing: float, column_spacing: float) -> str:
    """矩形阵列复制实体。

    Args:
        handle:         实体句柄
        rows:           行数
        columns:        列数
        row_spacing:    行间距
        column_spacing: 列间距
    """
    r = ctrl.array_rectangular(handle, rows, columns, row_spacing, column_spacing)
    if r["success"]:
        return format_success(f"已矩形阵列: {rows}行×{columns}列",
                              handles=r.get("new_handles", []),
                              total=len(r.get("new_handles", [])))
    return f"阵列失败: {r['message']}"


def array_polar(handle: str, count: int, fill_angle: float,
                center_x: float, center_y: float,
                center_z: float = 0.0) -> str:
    """环形阵列复制实体。

    Args:
        handle:     实体句柄
        count:      阵列数量（含原始）
        fill_angle: 填充角度（度，360=整圈）
        center_x:   阵列中心 X
        center_y:   阵列中心 Y
        center_z:   阵列中心 Z
    """
    r = ctrl.array_polar(handle, count, fill_angle, [center_x, center_y, center_z])
    if r["success"]:
        return format_success(f"已环形阵列 {count} 个，角度: {fill_angle}°",
                              handles=r.get("new_handles", []),
                              total=len(r.get("new_handles", [])))
    return f"阵列失败: {r['message']}"


def explode_entity(handle: str) -> str:
    """分解实体（将块/多段线/标注等分解为基本图元）。

    Args:
        handle: 实体句柄
    """
    r = ctrl.explode_entity(handle)
    if r["success"]:
        return format_success(f"已分解实体 {handle}",
                              new_entity_count=len(r.get("new_handles",[])),
                              new_handles=r.get("new_handles",[]))
    return f"分解失败: {r['message']}"


def set_entity_properties(handle: str,
                          color: Optional[int] = None,
                          layer: Optional[str] = None,
                          linetype: Optional[str] = None,
                          linetype_scale: Optional[float] = None,
                          lineweight: Optional[float] = None,
                          visible: Optional[bool] = None,
                          thickness: Optional[float] = None,
                          elevation: Optional[float] = None) -> str:
    """设置实体的显示属性。

    Args:
        handle:         实体句柄
        color:          颜色索引 (1-256, 0=ByBlock, 256=ByLayer)
        layer:          图层名称（必须存在）
        linetype:       线型名称（如 Continuous, Dashed, Center）
        linetype_scale: 线型比例
        lineweight:     线宽 (mm)
        visible:        是否可见
        thickness:      拉伸厚度（3D挤出高度）
        elevation:      标高（Z方向基面）
    """
    kwargs = {k: v for k, v in {
        "color": color, "layer": layer, "linetype": linetype,
        "linetypescale": linetype_scale, "lineweight": lineweight,
        "visible": visible, "thickness": thickness, "elevation": elevation,
    }.items() if v is not None}
    if not kwargs:
        return "错误: 至少指定一个要修改的属性"
    r = ctrl.set_entity_properties(handle, **kwargs)
    if r["success"]:
        # Update database
        ent = db.get_entity(handle)
        if ent:
            for key, val in r.get("changed", {}).items():
                if key in ent:
                    ent[key] = val
            db.upsert_entity(handle, ent["name"], ent["type"],
                             layer=ent.get("layer","0"),
                             color=ent.get("color",256),
                             linetype=ent.get("linetype","ByLayer"),
                             geometry=ent.get("geometry",{}))
    return r["message"]


def get_entity_properties(handle: str) -> str:
    """获取实体的完整属性（类型相关）。

    Args:
        handle: 实体句柄
    """
    r = ctrl.get_entity_properties(handle)
    if isinstance(r, dict) and r.get("success") == False:
        return f"获取属性失败: {r.get('message')}"
    import json
    return json.dumps(r, indent=2, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════
#  ADVANCED ENTITY PROPERTIES (TrueColor / Transparency / PlotStyle)
# ══════════════════════════════════════════════════════════════════

def set_entity_truecolor(handle: str, red: int, green: int,
                         blue: int) -> str:
    """将实体的颜色设置为 RGB 真彩色。

    RGB 真彩色提供超过 1600 万种颜色选择，远超 ACI 的 255 色限制。

    Args:
        handle: 实体句柄
        red:    红色分量 (0-255)
        green:  绿色分量 (0-255)
        blue:   蓝色分量 (0-255)
    """
    r = ctrl.set_entity_truecolor(handle, red, green, blue)
    if r["success"]:
        return format_success(r["message"], handle=handle)
    return f"设置真彩色失败: {r['message']}"


def set_entity_transparency(handle: str, transparency: float) -> str:
    """设置实体的透明度（0=完全不透明, 90=接近完全透明）。

    透明度值必须在 0 到 90 之间。

    Args:
        handle:       实体句柄
        transparency: 透明度 (0-90, 或 "ByLayer"/"ByBlock")
    """
    r = ctrl.set_entity_properties(handle, transparency=transparency)
    if r["success"]:
        return format_success(f"已设置透明度: {transparency}", handle=handle)
    return f"设置透明度失败: {r['message']}"


def set_entity_plot_style(handle: str, plot_style: str) -> str:
    """设置实体的打印样式名称。

    打印样式控制实体在打印输出时的外观（颜色、线宽等）。

    Args:
        handle:     实体句柄
        plot_style: 打印样式名称（如 "Style 1", "ByLayer"）
    """
    r = ctrl.set_entity_plot_style(handle, plot_style)
    if r["success"]:
        return r["message"]
    return f"设置打印样式失败: {r['message']}"


def get_extension_dictionary(handle: str) -> str:
    """获取实体的扩展字典（用于存储自定义 XRecords）。

    扩展字典是一个容器，可以附加自定义结构化数据到任何实体上。

    Args:
        handle: 实体句柄
    """
    d = ctrl.get_extension_dictionary(handle)
    if d:
        import json
        info = {"name": _com_get(d, "Name", "ExtensionDict"),
                "handle": _com_get(d, "Handle", ""),
                "count": _com_get(d, "Count", 0)}
        return json.dumps(info, indent=2, ensure_ascii=False)
    return f"实体 {handle} 没有扩展字典"


# ══════════════════════════════════════════════════════════════════
#  EDITING COMMANDS (via validated send_command wrappers)
# ══════════════════════════════════════════════════════════════════

def fillet_entities(handle1: str, handle2: str, radius: float) -> str:
    """对两个实体执行圆角倒角（在交点或延长线上创建指定半径的圆弧连接）。

    适用于直线、多段线、圆弧等。

    Args:
        handle1: 第一个实体句柄
        handle2: 第二个实体句柄
        radius:  圆角半径（正数）
    """
    if radius <= 0:
        return "错误: 圆角半径必须为正数"
    r = ctrl.fillet(handle1, handle2, radius)
    if not r["success"]:
        return f"圆角失败: {r['message']}"
    return format_success(f"已圆角倒角 (R={radius})",
                          entities=f"{handle1} + {handle2}",
                          arc_handle=r.get("new_handle"))


def chamfer_entities(handle1: str, handle2: str,
                      distance1: float, distance2: float) -> str:
    """对两个实体执行倒角（在交点处创建斜角连接）。

    适用于直线和多段线。两个距离值指定从交点到各条边的倒角长度。

    Args:
        handle1:   第一个实体句柄
        handle2:   第二个实体句柄
        distance1: 第一条边的倒角距离
        distance2: 第二条边的倒角距离
    """
    if distance1 < 0 or distance2 < 0:
        return "错误: 倒角距离不能为负数"
    r = ctrl.chamfer(handle1, handle2, distance1, distance2)
    if not r["success"]:
        return f"倒角失败: {r['message']}"
    return format_success(f"已倒角 (D1={distance1}, D2={distance2})",
                          entities=f"{handle1} + {handle2}")


def trim_entity(trim_handle: str, cutting_handles: List[str]) -> str:
    """用剪切边修剪实体（切除超出剪切边的部分）。

    需要先有一个剪切边(边界实体)，然后指定被修剪的实体。

    Args:
        trim_handle:     需要被修剪的实体句柄
        cutting_handles: 作为剪切边的实体句柄列表
    """
    if not cutting_handles:
        return "错误: 至少需要一个剪切边"
    r = ctrl.trim([trim_handle], cutting_handles)
    if not r["success"]:
        return f"修剪失败: {r['message']}"
    return format_success(f"已修剪实体 {trim_handle}",
                          cutting_edges=cutting_handles)


def extend_entity(extend_handle: str,
                  boundary_handles: List[str]) -> str:
    """延伸实体到指定边界。

    实体被延伸到与边界线相交的位置。

    Args:
        extend_handle:   需要延伸的实体句柄
        boundary_handles: 边界实体句柄列表
    """
    if not boundary_handles:
        return "错误: 至少需要一个边界"
    r = ctrl.extend([extend_handle], boundary_handles)
    if not r["success"]:
        return f"延伸失败: {r['message']}"
    return format_success(f"已延伸实体 {extend_handle}",
                          boundaries=boundary_handles)


def break_entity(handle: str, point1_x: float, point1_y: float,
                 point1_z: float = 0.0, point2_x: Optional[float] = None,
                 point2_y: Optional[float] = None,
                 point2_z: float = 0.0) -> str:
    """在指定点处打断实体（将实体分成两部分）。

    如果只指定一个点，在这一点将实体一分为二。
    如果指定两个点，移除两点之间的部分。

    Args:
        handle:     实体句柄
        point1_x,y,z: 第一个打断点
        point2_x,y,z: 第二个打断点（可选）
    """
    p1 = [point1_x, point1_y, point1_z]
    p2 = None if point2_x is None else [point2_x, point2_y, point2_z]
    r = ctrl.break_at(handle, p1, p2)
    if not r["success"]:
        return f"打断失败: {r['message']}"
    return format_success(f"已打断实体 {handle}",
                          point1=f"({point1_x},{point1_y})",
                          point2=f"({point2_x},{point2_y})" if point2_x is not None else "同一点")


def join_entities(handles: List[str]) -> str:
    """将多个同类型的实体合并为一个。

    支持合并共线的直线段、同心同半径的圆弧、同一多段线 上的实体等。

    Args:
        handles: 需要合并的实体句柄列表（至少2个）
    """
    if len(handles) < 2:
        return "错误: 至少需要2个实体进行合并"
    r = ctrl.join(handles)
    if not r["success"]:
        return f"合并失败: {r['message']}"
    return format_success(f"已合并 {len(handles)} 个实体", handles=handles)


def stretch_entities(x1: float, y1: float, x2: float, y2: float,
                     from_x: float, from_y: float, from_z: float = 0.0,
                     to_x: float = 0.0, to_y: float = 0.0,
                     to_z: float = 0.0) -> str:
    """拉伸选择窗口内的实体（移动交叉窗口选中的顶点）。

    与 MOVE 不同，STRETCH 只移动实体中被选中的端点/顶点，
    保持与其他固定部分连接。

    Args:
        x1,y1:     交叉选择窗口第一个角点
        x2,y2:     交叉选择窗口对角点
        from_x,y,z: 位移基点
        to_x,y,z:   位移目标点
    """
    cmd = (f"STRETCH C {x1},{y1} {x2},{y2} \n"
           f"{from_x},{from_y},{from_z} {to_x},{to_y},{to_z} \n")
    r = ctrl.send_command(cmd)
    return format_success(f"已拉伸选择区域",
                          window=f"({x1},{y1}) → ({x2},{y2})",
                          displacement=f"({to_x-from_x},{to_y-from_y})")


def lengthen_entity(handle: str, mode: str = "delta",
                     value: float = 1.0, end: str = "both") -> str:
    """修改实体的长度（延长或缩短）。

    模式说明:
      delta  — 线性增量（正值=延长, 负值=缩短）
      percent— 按百分比修改长度（100=不变, 200=翻倍）
      total  — 设置总长度为指定值

    Args:
        handle: 实体句柄（直线、圆弧、开放多段线等）
        mode:   "delta", "percent", 或 "total"
        value:  修改值（与模式相关）
        end:    修改哪一端: "both"(默认), "start", "end"
    """
    opts = {"delta": "DE", "percent": "P", "total": "T"}
    if mode not in opts:
        return f"错误: 不支持的修改模式 '{mode}'。请使用: delta, percent, total"
    r = ctrl.lengthen(handle, mode, value, end)
    if not r["success"]:
        return f"修改长度失败: {r['message']}"
    return format_success(f"已修改实体 {handle} 长度", mode=mode, value=value)


def divide_entity(handle: str, segments: int, block_name: str = "") -> str:
    """定数等分实体（在实体上按指定段数等分插入点或图块）。

    Args:
        handle:     实体句柄
        segments:   等分段数 (2-32767)
        block_name: (可选)用于标记的块名，留空则插入点对象
    """
    if segments < 2:
        return "错误: 段数必须大于等于2"
    r = ctrl.divide(handle, segments, block_name)
    if not r["success"]:
        return f"等分失败: {r['message']}"
    return format_success(f"已将实体 {handle} 等分为 {segments} 段")


def measure_entity(handle: str, length: float, block_name: str = "") -> str:
    """定距等分实体（在实体上按指定间距插入点或图块）。

    Args:
        handle:     实体句柄
        length:     间距距离
        block_name: (可选)用于标记的块名，留空则插入点对象
    """
    if length <= 0:
        return "错误: 间距必须大于0"
    r = ctrl.measure(handle, length, block_name)
    if not r["success"]:
        return f"等分失败: {r['message']}"
    return format_success(f"已将实体 {handle} 按间距 {length} 等分")


def align_entities(handles: List[str], points: List[List[float]]) -> str:
    """对齐实体。通过成对的源点和目标点集移动、旋转实体的二维或三维操作。
    如果是两对点，执行 2D 对齐（平移+旋转+缩放）。
    如果是三对点，执行 3D 对齐。

    Args:
        handles: 需要对齐的实体句柄列表
        points:  对齐点对。格式为 [[源点1, 目标点1], [源点2, 目标点2], ...]
                 其中源点1格式为 [x, y, z]
    """
    if len(points) < 1 or len(points) > 3:
        return "错误: 对齐点对必须为 1 到 3 对之间"
    r = ctrl.align(handles, points)
    if not r["success"]:
        return f"对齐失败: {r['message']}"
    return format_success(f"已对齐 {len(handles)} 个实体", points_used=len(points))


def chamfer_polyline(handle: str, distance1: float, distance2: float) -> str:
    """对整个多段线的所有角点进行倒角。

    Args:
        handle:     多段线实体句柄
        distance1:  第一条边的倒角距离
        distance2:  第二条边的倒角距离
    """
    if distance1 < 0 or distance2 < 0:
        return "错误: 倒角距离不能为负数"
    r = ctrl.chamfer_poly(handle, distance1, distance2)
    if not r["success"]:
        return f"多段线倒角失败: {r['message']}"
    return format_success(f"已对多段线倒角 (D1={distance1}, D2={distance2})")


def fillet_polyline(handle: str, radius: float) -> str:
    """对整个多段线的所有角点进行圆角。

    Args:
        handle:多段线实体句柄
        radius:圆角半径
    """
    if radius <= 0:
        return "错误: 半径必须大于0"
    r = ctrl.fillet_poly(handle, radius)
    if not r["success"]:
        return f"多段线圆角失败: {r['message']}"
    return format_success(f"已对多段线圆角 (R={radius})")
    return format_success(f"已修改实体长度",
                          handle=handle, mode=mode, value=value)
