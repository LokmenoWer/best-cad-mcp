"""CAD MCP Tools — Drawing primitives and entities."""
from typing import Optional, List, Tuple
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import resolve_color, format_success, com_get as _com_get, com_set as _com_set

ctrl = get_controller()
db = get_database()


def _activate_db_drawing(name: str = "active", path: str = "") -> None:
    try:
        db.activate_drawing(name=name or "active", path=path or "")
    except Exception:
        pass


def create_new_drawing(template: Optional[str] = None) -> str:
    """创建新的 AutoCAD 图纸。可选择指定模板文件路径。

    Args:
        template: 模板文件(.dwt)路径，为None则使用默认模板
    """
    r = ctrl.create_drawing(template)
    if r["success"]:
        _activate_db_drawing(r.get("name", "active"), "")
        return format_success(r["message"], name=r.get("name", ""))
    return f"创建图纸失败: {r['message']}"


def open_drawing(filepath: str, password: Optional[str] = None) -> str:
    """打开现有的 AutoCAD 图纸文件。

    Args:
        filepath: 图纸文件(.dwg)的完整路径
        password: 如果文件有密码保护，提供密码
    """
    r = ctrl.open_drawing(filepath, password)
    if r["success"]:
        _activate_db_drawing(r.get("name", ""), filepath)
        return format_success(r["message"], name=r.get("name", ""))
    return f"打开图纸失败: {r['message']}"


def save_drawing(filepath: Optional[str] = None) -> str:
    """保存当前 AutoCAD 图纸。

    Args:
        filepath: 保存路径。为None则保存到原路径
    """
    r = ctrl.save_drawing(filepath)
    if r["success"]:
        if filepath:
            _activate_db_drawing(r.get("name", ""), filepath)
        return r["message"]
    return f"保存失败: {r['message']}"


def close_drawing(save: bool = False) -> str:
    """关闭当前图纸。

    Args:
        save: 关闭前是否保存
    """
    r = ctrl.close_drawing(save)
    return r["message"]


def draw_line(start_x: float, start_y: float, end_x: float, end_y: float,
              start_z: float = 0.0, end_z: float = 0.0,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制直线。

    Args:
        start_x: 起点 X 坐标
        start_y: 起点 Y 坐标
        end_x:   终点 X 坐标
        end_y:   终点 Y 坐标
        start_z: 起点 Z 坐标（默认0）
        end_z:   终点 Z 坐标（默认0）
        layer:   图层名称（可选，自动创建不存在的图层）
        color:   颜色名称或ACI索引 (red/yellow/green/cyan/blue/magenta/white)
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    line = ctrl.add_line(start_x, start_y, end_x, end_y, start_z, end_z)
    if color != "bylayer":
        try:
            _com_set(line, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(_com_get(line, "Handle", ""), "Line", "AcDbLine",
                     layer=_com_get(line, "Layer", "0"),
                     color=_com_get(line, "Color", 256),
                     geometry={"start_point": [start_x, start_y, start_z],
                               "end_point": [end_x, end_y, end_z]})
    length = ((end_x-start_x)**2 + (end_y-start_y)**2)**0.5
    return format_success(f"已绘制直线", handle=_com_get(line, "Handle", ""),
                          layer=_com_get(line, "Layer", "0"),
                          length=f"{length:.2f}")


def draw_circle(center_x: float, center_y: float, radius: float,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制圆。

    Args:
        center_x: 圆心 X 坐标
        center_y: 圆心 Y 坐标
        radius:   半径
        layer:    图层名称（可选）
        color:    颜色 (red/yellow/green/cyan/blue/magenta/white)
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    circle = ctrl.add_circle(center_x, center_y, radius)
    if color != "bylayer":
        try:
            _com_set(circle, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(circle.Handle, "Circle", "AcDbCircle",
                     layer=circle.Layer, color=_com_get(circle, "Color", 256),
                     geometry={"center": [center_x, center_y, 0], "radius": radius})
    return format_success(f"已绘制圆", handle=circle.Handle,
                          layer=circle.Layer, radius=radius,
                          diameter=radius*2, area=f"{3.14159*radius**2:.2f}")


def draw_arc(center_x: float, center_y: float, radius: float,
             start_angle: float, end_angle: float,
             layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制圆弧。

    Args:
        center_x:    圆心 X 坐标
        center_y:    圆心 Y 坐标
        radius:      半径
        start_angle: 起始角度（度，0度为3点钟方向，逆时针）
        end_angle:   终止角度（度）
        layer:       图层名称
        color:       颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    arc = ctrl.add_arc(center_x, center_y, radius, start_angle, end_angle)
    if color != "bylayer":
        try:
            _com_set(arc, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(arc.Handle, "Arc", "AcDbArc",
                     layer=arc.Layer, color=_com_get(arc, "Color", 256),
                     geometry={"center": [center_x, center_y, 0],
                               "radius": radius,
                               "start_angle": start_angle, "end_angle": end_angle})
    return format_success(f"已绘制圆弧", handle=arc.Handle, radius=radius,
                          span=f"{start_angle}° → {end_angle}°")


def draw_ellipse(center_x: float, center_y: float,
                 major_x: float, major_y: float, radius_ratio: float,
                 layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制椭圆。

    Args:
        center_x:     中心 X 坐标
        center_y:     中心 Y 坐标
        major_x:      长轴端点 X（相对于中心）
        major_y:      长轴端点 Y（相对于中心）
        radius_ratio: 短轴/长轴比例 (0~1)
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    ell = ctrl.add_ellipse(center_x, center_y, (major_x, major_y), radius_ratio)
    if color != "bylayer":
        try:
            _com_set(ell, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(ell.Handle, "Ellipse", "AcDbEllipse",
                     layer=ell.Layer, color=_com_get(ell, "Color", 256),
                     geometry={"center": [center_x, center_y, 0],
                               "major_axis": [major_x, major_y],
                               "radius_ratio": radius_ratio})
    return format_success(f"已绘制椭圆", handle=ell.Handle, ratio=radius_ratio)


def draw_polyline(points: List[float], closed: bool = False,
                  layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制二维多段线。

    Args:
        points: 坐标列表 [x1, y1, x2, y2, ...] 至少4个值(2个点)
        closed: 是否闭合（True=多边形）
        layer:  图层名称
        color:  颜色
    """
    if len(points) < 4:
        return "错误: points 至少需要4个值 (2个坐标点)"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pline = ctrl.add_polyline(points, closed)
    if color != "bylayer":
        try:
            _com_set(pline, "Color", resolve_color(color))
        except Exception:
            pass
    vertices = [[points[i], points[i+1]] for i in range(0, len(points), 2)]
    db.upsert_entity(pline.Handle, "Polyline", "AcDbPolyline",
                     layer=pline.Layer, color=_com_get(pline, "Color", 256),
                     geometry={"vertices": vertices, "closed": closed,
                               "length": pline.Length})
    return format_success(f"已绘制多段线", handle=pline.Handle,
                          vertices=len(vertices), closed=closed,
                          length=f"{pline.Length:.2f}")


def draw_3d_polyline(points: List[float], closed: bool = False,
                      layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维多段线（每个顶点可指定不同的Z坐标）。

    与普通多段线不同，三维多段线的每个顶点可以有不同的Z坐标。

    Args:
        points: 三维坐标列表 [x1,y1,z1, x2,y2,z2, ...] 至少6个值(2个点)
        closed: 是否闭合（True=形成封闭多段线）
        layer:  图层名称
        color:  颜色
    """
    if len(points) < 6:
        return "错误: 3D多段线至少需要6个值 (2个三维点)"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pline = ctrl.add_polyline_3d(points, closed)
    if color != "bylayer":
        try: _com_set(pline, "Color", resolve_color(color))
        except: pass
    vertices = [[points[i], points[i+1], points[i+2]] for i in range(0, len(points), 3)]
    db.upsert_entity(pline.Handle, "3DPolyline", "AcDb3dPolyline",
                     layer=pline.Layer, color=_com_get(pline, "Color", 256),
                     geometry={"vertices": vertices, "closed": closed,
                               "length": pline.Length})
    return format_success(f"已绘制3D多段线", handle=pline.Handle,
                          vertices=len(vertices), closed=closed)


def draw_rectangle(x1: float, y1: float, x2: float, y2: float,
                   layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制矩形（通过两个对角点）。

    Args:
        x1: 角点1 X坐标
        y1: 角点1 Y坐标
        x2: 对角点2 X坐标
        y2: 对角点2 Y坐标
        layer: 图层名称
        color: 颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    rect = ctrl.add_rectangle(x1, y1, x2, y2)
    if color != "bylayer":
        try:
            _com_set(rect, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(rect.Handle, "Rectangle", "AcDbPolyline",
                     layer=rect.Layer, color=_com_get(rect, "Color", 256),
                     geometry={"vertices": [[x1,y1],[x2,y1],[x2,y2],[x1,y2]],
                               "closed": True})
    w, h = abs(x2-x1), abs(y2-y1)
    return format_success(f"已绘制矩形", handle=rect.Handle,
                          width=f"{w:.2f}", height=f"{h:.2f}",
                          area=f"{w*h:.2f}")


def draw_polygon(center_x: float, center_y: float, radius: float, sides: int,
                 start_angle: float = 0.0,
                 layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制正多边形。

    Args:
        center_x:    中心 X 坐标
        center_y:    中心 Y 坐标
        radius:      外接圆半径
        sides:       边数（3=三角形, 4=正方形, 5=五边形, 6=六边形...）
        start_angle: 起始角度（度）
        layer:       图层名称
        color:       颜色
    """
    if sides < 3:
        return "错误: 边数至少为3"
    import math
    pts = []
    for i in range(sides):
        a = start_angle + 360 * i / sides
        rad = a * math.pi / 180
        pts.extend([center_x + radius*math.cos(rad), center_y + radius*math.sin(rad)])
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pline = ctrl.add_polyline(pts, closed=True)
    if color != "bylayer":
        try:
            _com_set(pline, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(pline.Handle, f"Polygon({sides})", "AcDbPolyline",
                     layer=pline.Layer, color=_com_get(pline, "Color", 256),
                     geometry={"sides": sides, "radius": radius, "center": [center_x, center_y]})
    return format_success(f"已绘制正{sides}边形", handle=pline.Handle, radius=radius)


def draw_spline(fit_points: List[float],
                start_tangent: Optional[Tuple[float,float,float]] = None,
                end_tangent: Optional[Tuple[float,float,float]] = None,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制样条曲线（通过拟合点）。

    Args:
        fit_points:    拟合点列表 [x1,y1,z1, x2,y2,z2, ...]，每个点3个值
        start_tangent: 起始切向量 [x,y,z]（可选）
        end_tangent:   终止切向量 [x,y,z]（可选）
        layer:         图层名称
        color:         颜色
    """
    if len(fit_points) < 6:
        return "错误: 至少需要2个拟合点（6个数值）"
    # Convert flat list to list of tuples
    pts = [(fit_points[i], fit_points[i+1], fit_points[i+2])
           for i in range(0, len(fit_points), 3)]
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    spline = ctrl.add_spline(pts, start_tangent, end_tangent)
    if color != "bylayer":
        try:
            _com_set(spline, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(spline.Handle, "Spline", "AcDbSpline",
                     layer=spline.Layer, color=_com_get(spline, "Color", 256),
                     geometry={"fit_points": pts})
    return format_success(f"已绘制样条曲线", handle=spline.Handle, fit_points=len(pts))


def draw_point(x: float, y: float, z: float = 0.0,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制点。

    Args:
        x: X 坐标
        y: Y 坐标
        z: Z 坐标（默认0）
        layer: 图层名称
        color: 颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pt = ctrl.add_point(x, y, z)
    if color != "bylayer":
        try:
            _com_set(pt, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(pt.Handle, "Point", "AcDbPoint",
                     layer=pt.Layer, color=_com_get(pt, "Color", 256),
                     geometry={"point": [x, y, z]})
    return format_success(f"已绘制点", handle=pt.Handle, position=f"({x}, {y}, {z})")


def draw_text(text: str, insert_x: float, insert_y: float,
              height: float = 2.5, rotation: float = 0.0, z: float = 0.0,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制单行文字。

    Args:
        text:      文字内容
        insert_x:  插入点 X 坐标
        insert_y:  插入点 Y 坐标
        height:    文字高度（默认2.5）
        rotation:  旋转角度（度，默认0）
        z:         插入点 Z 坐标
        layer:     图层名称
        color:     颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    txt = ctrl.add_text(text, insert_x, insert_y, height, rotation, z)
    if color != "bylayer":
        try:
            _com_set(txt, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(txt.Handle, "Text", "AcDbText",
                     layer=txt.Layer, color=_com_get(txt, "Color", 256),
                     geometry={"text_string": text, "height": height,
                               "insertion_point": [insert_x, insert_y, z],
                               "rotation": rotation})
    return format_success(f"已绘制文字 '{text}'", handle=txt.Handle, height=height)


def draw_mtext(text: str, insert_x: float, insert_y: float,
               width: float = 0.0, height: float = 2.5,
               rotation: float = 0.0,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """在 AutoCAD 中绘制多行文字 (MText)。

    Args:
        text:      文字内容（支持换行符\\n、格式化代码）
        insert_x:  插入点 X 坐标
        insert_y:  插入点 Y 坐标
        width:     文本框宽度（0=自动）
        height:    文字高度（默认2.5）
        rotation:  旋转角度（度）
        layer:     图层名称
        color:     颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    mt = ctrl.add_mtext(text, insert_x, insert_y, width, height, rotation)
    if color != "bylayer":
        try:
            _com_set(mt, "Color", resolve_color(color))
        except Exception:
            pass
    db.upsert_entity(mt.Handle, "MText", "AcDbMText",
                     layer=mt.Layer, color=_com_get(mt, "Color", 256),
                     geometry={"text_string": text, "height": height,
                               "width": width,
                               "insertion_point": [insert_x, insert_y, 0]})
    return format_success(f"已绘制多行文字", handle=mt.Handle, height=height,
                          width=width if width else "自动")


def draw_donut(center_x: float, center_y: float, inner_radius: float,
               outer_radius: float,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制圆环（两个同心圆）。

    Args:
        center_x:     中心 X 坐标
        center_y:     中心 Y 坐标
        inner_radius: 内圆半径
        outer_radius: 外圆半径
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    entities = ctrl.add_donut(center_x, center_y, inner_radius, outer_radius)
    handles = []
    for ent in entities:
        if color != "bylayer":
            try:
                _com_set(ent, "Color", resolve_color(color))
            except Exception:
                pass
        handles.append(ent.Handle)
    return format_success(f"已绘制圆环", handles=handles,
                          inner=inner_radius, outer=outer_radius,
                          thickness=outer_radius-inner_radius)


def draw_ray(origin_x: float, origin_y: float, origin_z: float = 0.0,
             direction_x: float = 1.0, direction_y: float = 0.0,
             direction_z: float = 0.0,
             layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制射线（从一个点向指定方向无限延伸的半直线）。

    射线常用于构造辅助线、投影线等。

    Args:
        origin_x,y,z:    起点坐标
        direction_x,y,z: 方向向量（默认 (1,0,0)=X轴正方向）
        layer:           图层名称
        color:           颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    ray = ctrl.add_ray((origin_x, origin_y, origin_z),
                       (direction_x, direction_y, direction_z))
    if color != "bylayer":
        try: _com_set(ray, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(ray.Handle, "Ray", "AcDbRay",
                     layer=ray.Layer, color=_com_get(ray, "Color", 256),
                     geometry={"origin": [origin_x, origin_y, origin_z],
                               "direction": [direction_x, direction_y, direction_z]})
    return format_success(f"已绘制射线", handle=ray.Handle,
                          origin=f"({origin_x},{origin_y},{origin_z})")


def draw_xline(point1_x: float, point1_y: float, point1_z: float = 0.0,
               point2_x: float = 1.0, point2_y: float = 0.0,
               point2_z: float = 0.0,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制构造线（通过两点的双向无限长直线）。

    构造线常用于布局辅助、投影参照等。

    Args:
        point1_x,y,z: 第一个点坐标
        point2_x,y,z: 第二个点坐标
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    xline = ctrl.add_xline((point1_x, point1_y, point1_z),
                           (point2_x, point2_y, point2_z))
    if color != "bylayer":
        try: _com_set(xline, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(xline.Handle, "XLine", "AcDbXline",
                     layer=xline.Layer, color=_com_get(xline, "Color", 256),
                     geometry={"point1": [point1_x, point1_y, point1_z],
                               "point2": [point2_x, point2_y, point2_z]})
    return format_success(f"已绘制构造线", handle=xline.Handle)


def draw_mline(points: List[float],
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制多线（平行双线/三线）。

    多线由多条平行的直线段组成，常用于绘制墙体、道路等。

    Args:
        points: 顶点坐标列表 [x1,y1, x2,y2, ...] 至少4个值(2个点)
        layer:  图层名称
        color:  颜色
    """
    if len(points) < 4:
        return "错误: points 至少需要4个值 (2个坐标点)"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pts = [(points[i], points[i+1]) for i in range(0, len(points), 2)]
    mline = ctrl.add_mline(pts)
    if color != "bylayer":
        try: _com_set(mline, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(mline.Handle, "MLine", "AcDbMLine",
                     layer=mline.Layer, color=_com_get(mline, "Color", 256),
                     geometry={"vertices": pts})
    return format_success(f"已绘制多线", handle=mline.Handle,
                          vertices=len(pts))


def draw_2d_solid(p1_x: float, p1_y: float, p1_z: float = 0.0,
                  p2_x: float = 0.0, p2_y: float = 0.0, p2_z: float = 0.0,
                  p3_x: float = 0.0, p3_y: float = 0.0, p3_z: float = 0.0,
                  p4_x: Optional[float] = None, p4_y: Optional[float] = None,
                  p4_z: Optional[float] = None,
                  layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制二维实体填充区域（3或4个顶点的实心面）。

    与 Solid 图案填充不同，2D Solid 是轻量级的填充区域。

    Args:
        p1_x,y,z:  第一个顶点
        p2_x,y,z:  第二个顶点
        p3_x,y,z:  第三个顶点
        p4_x,y,z:  第四个顶点（可选，省略则 p4=p3 形成三角形区域）
        layer:     图层名称
        color:     颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    pts = [(p1_x, p1_y, p1_z), (p2_x, p2_y, p2_z), (p3_x, p3_y, p3_z)]
    if p4_x is not None:
        pts.append((p4_x, p4_y, p4_z))
    solid = ctrl.add_solid(pts)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "2DSolid", "AcDbSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"vertices": [[p[0], p[1], p[2]] for p in pts]})
    return format_success(f"已绘制2D实体", handle=solid.Handle,
                          vertices=len(pts))


def draw_raster_image(filepath: str, insert_x: float, insert_y: float,
                      insert_z: float = 0.0, scale: float = 1.0,
                      rotation: float = 0.0,
                      layer: Optional[str] = None) -> str:
    """在 AutoCAD 中插入光栅图像（PNG, JPG, BMP, TIFF 等）。

    光栅图像可用作底图、参考图或装饰性插图。

    Args:
        filepath: 图像文件的完整路径
        insert_x,y,z: 插入点坐标
        scale:    缩放比例（默认1.0）
        rotation: 旋转角度（度，默认0）
        layer:    图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    img = ctrl.add_raster_image(filepath, insert_x, insert_y, scale, rotation, insert_z)
    if layer:
        try: img.Layer = layer
        except: pass
    db.upsert_entity(img.Handle, "RasterImage", "AcDbRasterImage",
                     layer=img.Layer, color=_com_get(img, "Color", 256) if hasattr(img, 'Color') else 256,
                     geometry={"filepath": filepath, "insertion_point": [insert_x, insert_y, insert_z],
                               "scale": scale, "rotation": rotation})
    return format_success(f"已插入光栅图像", handle=img.Handle,
                          filepath=filepath, scale=scale)


def draw_tolerance(text: str, insert_x: float, insert_y: float,
                   insert_z: float = 0.0, direction_x: float = 1.0,
                   direction_y: float = 0.0, direction_z: float = 0.0,
                   layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制几何公差标注（GD&T 特征控制框）。

    用于标注形状和位置公差，如平面度、平行度、位置度等。

    Args:
        text:         公差文字（如 "{\Fgdt;j}%%v0.05%%vA"）
        insert_x,y,z: 插入点坐标
        direction_x,y,z: 方向向量（控制框方向）
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    tol = ctrl.add_tolerance(text, insert_x, insert_y, insert_z,
                             direction_x, direction_y, direction_z)
    if color != "bylayer":
        try: _com_set(tol, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(tol.Handle, "Tolerance", "AcDbTolerance",
                     layer=tol.Layer, color=_com_get(tol, "Color", 256),
                     geometry={"text": text,
                               "insertion_point": [insert_x, insert_y, insert_z],
                               "direction": [direction_x, direction_y, direction_z]})
    return format_success(f"已绘制公差标注", handle=tol.Handle, text=text)


def draw_trace(points: List[float],
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制宽线 (Trace) — 具有宽度的二维线段。

    宽线是 AutoCAD 早期版本中的功能，由4个点定义一条有宽度的线段。

    Args:
        points: 4个点坐标 [x1,y1, x2,y2, x3,y3, x4,y4]
        layer:  图层名称
        color:  颜色
    """
    if len(points) < 8:
        return "错误: 宽线需要8个坐标值（4个点）"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    trace = ctrl.add_trace(points)
    if color != "bylayer":
        try: _com_set(trace, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(trace.Handle, "Trace", "AcDbTrace",
                     layer=trace.Layer, color=_com_get(trace, "Color", 256),
                     geometry={"points": [[points[i], points[i+1]] for i in range(0, len(points), 2)]})
    return format_success(f"已绘制宽线(Trace)", handle=trace.Handle)


def insert_minert_block(block_name: str, x: float, y: float,
                         z: float = 0.0, x_scale: float = 1.0,
                         y_scale: float = 1.0, z_scale: float = 1.0,
                         rotation: float = 0.0, rows: int = 1,
                         cols: int = 1, row_spacing: float = 0.0,
                         col_spacing: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """以矩形阵列方式插入图块 (MInsert) — 图块 + 矩形阵列的组合。

    在一个操作中创建图块的矩形阵列。与普通 insert_block + array 不同，
    MInsert 创建的是一个不可分解的单一实体。

    Args:
        block_name:  图块名称
        x, y, z:     插入点坐标
        x_scale:     X方向缩放（默认1）
        y_scale:     Y方向缩放（默认1）
        z_scale:     Z方向缩放（默认1）
        rotation:    旋转角度（度，默认0）
        rows:        行数（默认1）
        cols:        列数（默认1）
        row_spacing: 行间距
        col_spacing: 列间距
        layer:       图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    blk_ref = ctrl.add_minert_block(block_name, x, y, z, x_scale, y_scale,
                                     z_scale, rotation, rows, cols,
                                     row_spacing, col_spacing)
    if layer:
        try: blk_ref.Layer = layer
        except: pass
    db.upsert_entity(blk_ref.Handle, f"MInsert({block_name})", "AcDbMInsertBlock",
                     layer=blk_ref.Layer if hasattr(blk_ref, 'Layer') else layer or "0",
                     color=_com_get(blk_ref, "Color", 256) if hasattr(blk_ref, 'Color') else 256,
                     geometry={"block_name": block_name, "insertion_point": [x, y, z],
                               "rows": rows, "cols": cols, "scale": [x_scale, y_scale, z_scale]})
    return format_success(f"已阵列插入图块 '{block_name}'",
                          handle=blk_ref.Handle,
                          array=f"{rows}×{cols}")


def insert_minsert_block(block_name: str, x: float, y: float,
                         z: float = 0.0, x_scale: float = 1.0,
                         y_scale: float = 1.0, z_scale: float = 1.0,
                         rotation: float = 0.0, rows: int = 1,
                         cols: int = 1, row_spacing: float = 0.0,
                         col_spacing: float = 0.0,
                         layer: Optional[str] = None) -> str:
    """Insert a block as an AutoCAD MInsert rectangular block array."""
    return insert_minert_block(
        block_name, x, y, z, x_scale, y_scale, z_scale, rotation,
        rows, cols, row_spacing, col_spacing, layer
    )


def add_shape(shape_name: str, x: float, y: float,
              z: float = 0.0, scale: float = 1.0,
              rotation: float = 0.0,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制形 (Shape) — 从编译的 .shx 形状文件中插入预定义图形。

    需要先用 LOAD 命令加载对应的 .shx 形状文件。

    Args:
        shape_name: 形状名称（必须在已加载的 .shx 文件中定义）
        x, y, z:   插入点坐标
        scale:     缩放比例
        rotation:  旋转角度（度）
        layer:     图层名称
        color:     颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    shape = ctrl.add_shape(shape_name, x, y, z, scale, rotation)
    if color != "bylayer":
        try: _com_set(shape, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(shape.Handle, f"Shape({shape_name})", "AcDbShape",
                     layer=shape.Layer, color=_com_get(shape, "Color", 256),
                     geometry={"shape_name": shape_name,
                               "insertion_point": [x, y, z],
                               "scale": scale, "rotation": rotation})
    return format_success(f"已绘制形 '{shape_name}'", handle=shape.Handle,
                          scale=scale)
