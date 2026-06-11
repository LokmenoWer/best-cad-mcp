"""CAD MCP Tools — Polyline vertex and segment operations.
Bulge, width, vertex add/remove, segment inspection.
"""
from typing import Optional, List, Dict, Any
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success
import json

ctrl = get_controller()
db = get_database()


def polyline_set_bulge(handle: str, index: int, bulge: float) -> str:
    """设置多段线顶点的凸度（创建圆弧段）。

    凸度为 0 表示直线段，正值表示逆时针圆弧，负值表示顺时针圆弧。
    凸度的绝对值 = tan(圆心角/4)，即 bulge = h/s * 2，其中 h 为拱高，s 为弦长的一半。

    Args:
        handle: 多段线实体句柄
        index:  顶点索引（从0开始）
        bulge:  凸度值（-1~1 典型范围, 0=直线）
    """
    r = ctrl.polyline_set_bulge(handle, index, bulge)
    if r["success"]:
        return format_success(r["message"], handle=handle, index=index, bulge=bulge)
    return f"设置凸度失败: {r['message']}"


def polyline_get_bulge(handle: str, index: int) -> str:
    """获取多段线顶点的凸度值。

    返回凸度因子：0=直线段, 正值=逆时针圆弧, 负值=顺时针圆弧。

    Args:
        handle: 多段线实体句柄
        index:  顶点索引（从0开始）
    """
    r = ctrl.polyline_get_bulge(handle, index)
    return json.dumps(r, indent=2, ensure_ascii=False)


def polyline_set_width(handle: str, seg_index: int,
                          start_width: float, end_width: float) -> str:
    """设置多段线段的起点和终点宽度（创建变宽线段）。

    可以创建箭头、渐变宽度等效果。

    Args:
        handle:      多段线实体句柄
        seg_index:   段索引（从0开始, 段连接顶点 seg_index 和 seg_index+1）
        start_width: 段起点宽度
        end_width:   段终点宽度
    """
    r = ctrl.polyline_set_width(handle, seg_index, start_width, end_width)
    if r["success"]:
        return format_success(r["message"], handle=handle)
    return f"设置宽度失败: {r['message']}"


def polyline_get_width(handle: str, seg_index: int) -> str:
    """获取多段线段的起点和终点宽度。

    Args:
        handle:    多段线实体句柄
        seg_index: 段索引（从0开始）
    """
    r = ctrl.polyline_get_width(handle, seg_index)
    return json.dumps(r, indent=2, ensure_ascii=False)


def polyline_add_vertex(handle: str, index: int,
                           x: float, y: float) -> str:
    """在指定位置向多段线添加新顶点。

    新顶点将插入到 index 位置，后续顶点索引顺延。

    Args:
        handle: 多段线实体句柄
        index:  插入位置索引（0=第一个顶点前, -1=末尾）
        x, y:   新顶点坐标
    """
    r = ctrl.polyline_add_vertex(handle, index, x, y)
    if r["success"]:
        return format_success(r["message"], handle=handle, point=f"({x},{y})")
    return f"添加顶点失败: {r['message']}"


def polyline_constant_width(handle: str,
                               width: Optional[float] = None) -> str:
    """获取或设置多段线的统一线宽。

    获取：不传 width 参数，返回当前统一宽度。
    设置：传入 width 值，设置所有段为统一宽度。

    Args:
        handle: 多段线实体句柄
        width:  要设置的统一宽度（不传=获取当前值）
    """
    r = ctrl.polyline_constant_width(handle, width)
    return json.dumps(r, indent=2, ensure_ascii=False)


def polyline_num_vertices(handle: str) -> str:
    """获取多段线的顶点数量。

    Args:
        handle: 多段线实体句柄
    """
    r = ctrl.polyline_num_vertices(handle)
    return json.dumps(r, indent=2, ensure_ascii=False)


def polyline_get_point_at_param(handle: str, param: float) -> str:
    """在多段线上获取指定参数处的3D坐标点。

    参数是沿多段线的归一化距离（0=起点, 1=终点，或按段索引）。

    Args:
        handle: 多段线实体句柄
        param:  参数值
    """
    r = ctrl.polyline_get_point_at_param(handle, param)
    return json.dumps(r, indent=2, ensure_ascii=False)


def polyline_get_segment_type(handle: str, index: int) -> str:
    """获取多段线段的类型（直线段或圆弧段）。

    返回值: "line" 或 "arc"

    Args:
        handle: 多段线实体句柄
        index:  段索引（从0开始）
    """
    r = ctrl.polyline_get_segment_type(handle, index)
    return json.dumps(r, indent=2, ensure_ascii=False)
