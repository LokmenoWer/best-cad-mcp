"""CAD MCP Tools — 3D solids, regions, meshes, boolean operations, and 3D editing."""
from typing import Optional, List, Tuple
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success, resolve_color, com_get as _com_get, com_set as _com_set

ctrl = get_controller()
db = get_database()


# ══════════════════════════════════════════════════════════════════
#  3D SOLID PRIMITIVES
# ══════════════════════════════════════════════════════════════════

def draw_box(center_x: float, center_y: float, center_z: float,
             length: float, width: float, height: float,
             layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维长方体。

    Args:
        center_x, center_y, center_z: 底面中心点坐标
        length: X方向长度
        width:  Y方向宽度
        height: Z方向高度
        layer:  图层名称
        color:  颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_box(center_x, center_y, center_z, length, width, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Box", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "length": length, "width": width, "height": height})
    return format_success(f"已绘制长方体", handle=solid.Handle,
                          dimensions=f"{length}×{width}×{height}")


def draw_cone(center_x: float, center_y: float, center_z: float,
              base_radius: float, height: float,
              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维圆锥体。

    Args:
        center_x, center_y, center_z: 底面中心点
        base_radius: 底面半径
        height:      高度（Z方向）
        layer:       图层名称
        color:       颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_cone(center_x, center_y, center_z, base_radius, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Cone", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "base_radius": base_radius, "height": height})
    return format_success(f"已绘制圆锥体", handle=solid.Handle,
                          radius=base_radius, height=height)


def draw_cylinder(center_x: float, center_y: float, center_z: float,
                  radius: float, height: float,
                  layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维圆柱体。

    Args:
        center_x, center_y, center_z: 底面中心点
        radius: 底面半径
        height: 高度（Z方向）
        layer:  图层名称
        color:  颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_cylinder(center_x, center_y, center_z, radius, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Cylinder", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "radius": radius, "height": height})
    return format_success(f"已绘制圆柱体", handle=solid.Handle,
                          radius=radius, height=height)


def draw_sphere(center_x: float, center_y: float, center_z: float,
                radius: float,
                layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维球体。

    Args:
        center_x, center_y, center_z: 球心坐标
        radius: 半径
        layer:  图层名称
        color:  颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_sphere(center_x, center_y, center_z, radius)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Sphere", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "radius": radius})
    return format_success(f"已绘制球体", handle=solid.Handle, radius=radius)


def draw_torus(center_x: float, center_y: float, center_z: float,
               torus_radius: float, tube_radius: float,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维圆环体。

    Args:
        center_x, center_y, center_z: 环心坐标
        torus_radius: 环半径（中心到管心）
        tube_radius:  管半径
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_torus(center_x, center_y, center_z, torus_radius, tube_radius)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Torus", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "torus_radius": torus_radius, "tube_radius": tube_radius})
    return format_success(f"已绘制圆环体", handle=solid.Handle,
                          torus_r=torus_radius, tube_r=tube_radius)


def draw_wedge(center_x: float, center_y: float, center_z: float,
               length: float, width: float, height: float,
               layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维楔形体。

    Args:
        center_x, center_y, center_z: 底面中心点
        length: X方向长度
        width:  Y方向宽度
        height: Z方向高度
        layer:  图层名称
        color:  颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_wedge(center_x, center_y, center_z, length, width, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "Wedge", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "length": length, "width": width, "height": height})
    return format_success(f"已绘制楔形体", handle=solid.Handle,
                          dimensions=f"{length}×{width}×{height}")


def draw_elliptical_cone(center_x: float, center_y: float, center_z: float,
                          major_radius: float, minor_radius: float, height: float,
                          layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维椭圆锥体。

    Args:
        center_x, center_y, center_z: 底面中心
        major_radius: 长轴半径
        minor_radius: 短轴半径
        height:       高度
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_elliptical_cone(center_x, center_y, center_z,
                                      major_radius, minor_radius, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "EllipticalCone", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "major_radius": major_radius,
                               "minor_radius": minor_radius, "height": height})
    return format_success(f"已绘制椭圆锥体", handle=solid.Handle,
                          major=major_radius, minor=minor_radius, height=height)


def draw_elliptical_cylinder(center_x: float, center_y: float, center_z: float,
                              major_radius: float, minor_radius: float, height: float,
                              layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维椭圆柱体。

    Args:
        center_x, center_y, center_z: 底面中心
        major_radius: 长轴半径
        minor_radius: 短轴半径
        height:       高度
        layer:        图层名称
        color:        颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    solid = ctrl.add_elliptical_cylinder(center_x, center_y, center_z,
                                          major_radius, minor_radius, height)
    if color != "bylayer":
        try: _com_set(solid, "Color", resolve_color(color))
        except: pass
    db.upsert_entity(solid.Handle, "EllipticalCylinder", "AcDb3dSolid",
                     layer=solid.Layer, color=_com_get(solid, "Color", 256),
                     geometry={"center": [center_x, center_y, center_z],
                               "major_radius": major_radius,
                               "minor_radius": minor_radius, "height": height})
    return format_success(f"已绘制椭圆柱体", handle=solid.Handle,
                          major=major_radius, minor=minor_radius, height=height)


# ══════════════════════════════════════════════════════════════════
#  REGION AND REGION-DERIVED SOLIDS
# ══════════════════════════════════════════════════════════════════

def add_region(entity_handles: List[str],
               layer: Optional[str] = None) -> str:
    """将闭合曲线转换为面域。

    原始曲线会被消耗（删除），生成新的面域对象。
    面域可用于拉伸、旋转等操作生成三维实体。

    Args:
        entity_handles: 闭合曲线实体句柄列表（如圆、封闭多段线）
        layer:          图层名称
    """
    if not entity_handles:
        return "错误: 至少需要一个实体句柄"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    try:
        regions = ctrl.add_region(entity_handles)
        handles = []
        if isinstance(regions, (list, tuple)):
            for region in regions:
                if layer:
                    region.Layer = layer
                handles.append(region.Handle)
        elif hasattr(regions, 'Count'):
            for i in range(regions.Count):
                regions.Item(i).Layer = layer or ctrl.doc.ActiveLayer.Name
                handles.append(regions.Item(i).Handle)
        elif regions:
            handles.append(regions.Handle)
        return format_success(f"已创建 {len(handles)} 个面域",
                              handles=handles)
    except Exception as e:
        return f"创建面域失败: {e}"


def extrude_region(region_handle: str, height: float,
                   taper_angle: float = 0.0,
                   layer: Optional[str] = None) -> str:
    """将面域拉伸为三维实体。

    Args:
        region_handle: 面域实体句柄
        height:        拉伸高度（正值向Z正向，负值向Z负向）
        taper_angle:   拔模角度（度，-90~90，0=垂直拉伸）
        layer:         图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    try:
        solid = ctrl.add_extruded_solid(region_handle, height, taper_angle)
        if layer:
            solid.Layer = layer
        return format_success(f"已拉伸面域（高度:{height}）",
                              handle=solid.Handle)
    except Exception as e:
        return f"拉伸面域失败: {e}"


def extrude_region_along_path(region_handle: str, path_handle: str,
                               layer: Optional[str] = None) -> str:
    """沿路径曲线拉伸面域。

    Args:
        region_handle: 面域实体句柄
        path_handle:   路径曲线句柄（多段线、样条曲线、圆弧等）
        layer:         图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    try:
        solid = ctrl.add_extruded_solid_along_path(region_handle, path_handle)
        if layer:
            solid.Layer = layer
        return format_success(f"已沿路径拉伸面域", handle=solid.Handle)
    except Exception as e:
        return f"沿路径拉伸失败: {e}"


def revolve_region(region_handle: str,
                    axis_x: float, axis_y: float, axis_z: float,
                    dir_x: float, dir_y: float, dir_z: float,
                    angle: float = 360.0,
                    layer: Optional[str] = None) -> str:
    """将面域绕轴旋转生成三维实体。

    Args:
        region_handle:        面域实体句柄
        axis_x, axis_y, axis_z: 旋转轴起点
        dir_x, dir_y, dir_z:   旋转轴方向向量
        angle:                 旋转角度（度，默认360=完整旋转体）
        layer:                 图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    try:
        solid = ctrl.add_revolved_solid(
            region_handle,
            (axis_x, axis_y, axis_z),
            (dir_x, dir_y, dir_z),
            angle)
        if layer:
            solid.Layer = layer
        return format_success(f"已旋转面域（{angle}°）", handle=solid.Handle)
    except Exception as e:
        return f"旋转面域失败: {e}"


# ══════════════════════════════════════════════════════════════════
#  BOOLEAN OPERATIONS & SOLID EDITING
# ══════════════════════════════════════════════════════════════════

def solid_boolean(target_handle: str, tool_handle: str,
                   operation: str = "union") -> str:
    """对两个三维实体执行布尔运算。

    支持的布尔运算：
      - "union" (并集): 合并两个实体
      - "intersect" (交集): 保留相交部分
      - "subtract" (差集): 从目标实体中减去工具实体

    Args:
        target_handle: 目标实体句柄（被修改的实体）
        tool_handle:   工具实体句柄
        operation:     运算类型: "union", "intersect", "subtract"
    """
    ops = {"union": 0, "intersect": 1, "subtract": 2}
    if operation not in ops:
        return f"错误: 不支持的运算 '{operation}'。请使用: union, intersect, subtract"
    r = ctrl.solid_boolean(target_handle, tool_handle, ops[operation])
    if r["success"]:
        return f"✓ 已执行{operation}运算: {target_handle} + {tool_handle}"
    return f"布尔运算失败: {r['message']}"


def check_interference(handle1: str, handle2: str,
                        create_solid: bool = True) -> str:
    """检查两个三维实体是否干涉（相交）。

    Args:
        handle1:      第一个实体句柄
        handle2:      第二个实体句柄
        create_solid: 是否创建干涉体（默认True）
    """
    r = ctrl.solid_check_interference(handle1, handle2, create_solid)
    import json
    return json.dumps(r, indent=2, ensure_ascii=False)


def slice_solid(handle: str,
                p1_x: float, p1_y: float, p1_z: float,
                p2_x: float, p2_y: float, p2_z: float,
                p3_x: float, p3_y: float, p3_z: float,
                negative_side_only: bool = False) -> str:
    """用三点定义的平面剖切三维实体。

    Args:
        handle:             实体句柄
        p1_x,p1_y,p1_z:     平面上第一个点
        p2_x,p2_y,p2_z:     平面上第二个点
        p3_x,p3_y,p3_z:     平面上第三个点
        negative_side_only: 是否只保留一侧（True=只保留一侧, False=保留两侧）
    """
    r = ctrl.solid_slice(handle,
                          [p1_x, p1_y, p1_z],
                          [p2_x, p2_y, p2_z],
                          [p3_x, p3_y, p3_z],
                          negative_side_only)
    if r["success"]:
        return format_success(f"已剖切实体 {handle}",
                              result_handle=r.get("result_handle"))
    return f"剖切失败: {r['message']}"


def section_solid(handle: str,
                   p1_x: float, p1_y: float, p1_z: float,
                   p2_x: float, p2_y: float, p2_z: float,
                   p3_x: float, p3_y: float, p3_z: float) -> str:
    """创建三维实体的截面（生成面域）。

    Args:
        handle:          实体句柄
        p1_x,p1_y,p1_z:  截面上第一个点
        p2_x,p2_y,p2_z:  截面上第二个点
        p3_x,p3_y,p3_z:  截面上第三个点
    """
    r = ctrl.solid_section(handle,
                            [p1_x, p1_y, p1_z],
                            [p2_x, p2_y, p2_z],
                            [p3_x, p3_y, p3_z])
    if r["success"]:
        return format_success(f"已创建截面", region_handle=r.get("region_handle"))
    return f"创建截面失败: {r['message']}"


# ══════════════════════════════════════════════════════════════════
#  MESHES & 3D FACES
# ══════════════════════════════════════════════════════════════════

def draw_3d_mesh(m_size: int, n_size: int, vertices: List[float],
                  layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维多边形网格 (M×N)。

    Args:
        m_size:   M方向顶点数 (2~256)
        n_size:   N方向顶点数 (2~256)
        vertices: 顶点坐标列表 [x1,y1,z1, x2,y2,z2, ...] 数量=M*N*3
        layer:    图层名称
        color:    颜色
    """
    if m_size < 2 or n_size < 2:
        return "错误: M和N至少为2"
    expected = m_size * n_size * 3
    if len(vertices) != expected:
        return f"错误: 需要 {expected} 个坐标值（{m_size}×{n_size}×3），得到 {len(vertices)}"
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    mesh = ctrl.add_3d_mesh(m_size, n_size, vertices)
    if color != "bylayer":
        try: _com_set(mesh, "Color", resolve_color(color))
        except: pass
    return format_success(f"已绘制3D网格 ({m_size}×{n_size})",
                          handle=mesh.Handle)


def draw_polyface_mesh(vertices: List[float], face_list: List[int],
                        layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制多面网格。

    Args:
        vertices:  顶点坐标 [x1,y1,z1, x2,y2,z2, ...]
        face_list: 面索引列表，每个面4个整数（负值=不可见边）
                   如 [1,2,3,4, 2,5,6,-3] 定义两个面
        layer:     图层名称
        color:     颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    mesh = ctrl.add_polyface_mesh(vertices, face_list)
    if color != "bylayer":
        try: _com_set(mesh, "Color", resolve_color(color))
        except: pass
    return format_success(f"已绘制多面网格", handle=mesh.Handle)


def draw_3d_face(x1: float, y1: float, z1: float,
                  x2: float, y2: float, z2: float,
                  x3: float, y3: float, z3: float,
                  x4: Optional[float] = None,
                  y4: Optional[float] = None,
                  z4: Optional[float] = None,
                  layer: Optional[str] = None, color: str = "bylayer") -> str:
    """绘制三维面（三角形或四边形）。

    Args:
        x1,y1,z1: 第一个顶点
        x2,y2,z2: 第二个顶点
        x3,y3,z3: 第三个顶点
        x4,y4,z4: 第四个顶点（可选，省略=三角形面）
        layer:    图层名称
        color:    颜色
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    p4 = (x4, y4, z4) if x4 is not None else None
    face = ctrl.add_3d_face((x1, y1, z1), (x2, y2, z2), (x3, y3, z3), p4)
    if color != "bylayer":
        try: _com_set(face, "Color", resolve_color(color))
        except: pass
    return format_success(f"已绘制3D面", handle=face.Handle)


# ══════════════════════════════════════════════════════════════════
#  3D ENTITY OPERATIONS
# ══════════════════════════════════════════════════════════════════

def rotate_3d(handle: str,
              axis_x1: float, axis_y1: float, axis_z1: float,
              axis_x2: float, axis_y2: float, axis_z2: float,
              angle: float) -> str:
    """围绕三维轴旋转实体。

    Args:
        handle:                             实体句柄
        axis_x1, axis_y1, axis_z1:          旋转轴起点
        axis_x2, axis_y2, axis_z2:          旋转轴终点
        angle:                              旋转角度（度，逆时针为正）
    """
    r = ctrl.rotate_3d(handle, [axis_x1, axis_y1, axis_z1],
                       [axis_x2, axis_y2, axis_z2], angle)
    if r["success"]:
        return format_success(f"已3D旋转实体 {handle} {angle}°")
    return f"3D旋转失败: {r['message']}"


def mirror_3d(handle: str,
              p1_x: float, p1_y: float, p1_z: float,
              p2_x: float, p2_y: float, p2_z: float,
              p3_x: float, p3_y: float, p3_z: float) -> str:
    """关于三维平面镜像实体。

    Args:
        handle:          实体句柄
        p1_x,p1_y,p1_z:  平面上第一个点
        p2_x,p2_y,p2_z:  平面上第二个点
        p3_x,p3_y,p3_z:  平面上第三个点
    """
    r = ctrl.mirror_3d(handle,
                       [p1_x, p1_y, p1_z],
                       [p2_x, p2_y, p2_z],
                       [p3_x, p3_y, p3_z])
    if r["success"]:
        return format_success(f"已3D镜像实体 {handle}",
                              new_handle=r.get("new_handle"))
    return f"3D镜像失败: {r['message']}"


def get_bounding_box(handle: str) -> str:
    """获取实体的轴对齐包围盒。

    Args:
        handle: 实体句柄
    """
    import json
    r = ctrl.get_bounding_box(handle)
    return json.dumps(r, indent=2, ensure_ascii=False)


def intersect_with(handle1: str, handle2: str,
                    extend_option: int = 0) -> str:
    """计算两个实体的交点。

    Args:
        handle1:       第一个实体句柄
        handle2:       第二个实体句柄
        extend_option: 延伸选项: 0=都延伸, 1=延伸第一个, 2=延伸第二个, 3=都不延伸
    """
    import json
    r = ctrl.intersect_with(handle1, handle2, extend_option)
    return json.dumps(r, indent=2, ensure_ascii=False)


def transform_entity(handle: str, matrix: List[List[float]]) -> str:
    """对实体应用4×4变换矩阵。

    Args:
        handle: 实体句柄
        matrix: 4×4变换矩阵（每行4个值，共16个值）
    """
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        return "错误: 变换矩阵必须是4×4"
    r = ctrl.transform_entity(handle, matrix)
    return r["message"]
