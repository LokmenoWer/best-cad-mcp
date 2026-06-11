"""CAD MCP Tools — Hatch boundary, properties, gradients, and advanced fill operations."""
from typing import Optional, List
import json
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def hatch_add_boundary(handle: str, boundary_handles: List[str]) -> str:
    """为已有填充对象添加外边界环（闭合的曲线实体列表）。

    填充对象需要先通过 add_hatch 创建，然后用此工具添加边界。
    边界实体必须形成一个封闭区域。

    Args:
        handle:          填充对象句柄
        boundary_handles: 边界实体句柄列表（圆、闭合多段线等）
    """
    hatch_obj = ctrl._get_entity(handle)
    if hatch_obj is None:
        return f"错误: 未找到填充对象 {handle}"
    if hatch_obj.ObjectName != "AcDbHatch":
        return f"错误: 实体 {handle} 不是填充对象"
    entities = [ctrl._get_entity(h) for h in boundary_handles]
    entities = [e for e in entities if e is not None]
    if not entities:
        return "错误: 未找到有效的边界实体"
    try:
        ctrl.hatch_boundary(hatch_obj, entities)
        return format_success(f"已为填充 {handle} 添加外边界",
                              handle=handle, count=len(entities))
    except Exception as e:
        return f"添加边界失败: {e}"


def hatch_add_inner_loop(handle: str, inner_handles: List[str]) -> str:
    """向已有填充对象添加内部环（孤岛/空洞）。

    内部环定义了填充区域内的空洞（不被填充的区域）。

    Args:
        handle:        填充对象句柄
        inner_handles: 内部环实体句柄列表（闭合曲线）
    """
    r = ctrl.hatch_append_inner_loop(handle, inner_handles)
    if r["success"]:
        return format_success(r["message"], handle=handle,
                              islands=len(inner_handles))
    return f"添加内部环失败: {r['message']}"


def hatch_set_properties(handle: str,
                           pattern_scale: Optional[float] = None,
                           pattern_angle: Optional[float] = None,
                           pattern_double: Optional[bool] = None,
                           hatch_style: Optional[int] = None) -> str:
    """设置已有填充对象的图案属性。

    可同时修改多个属性。

    Args:
        handle:         填充对象句柄
        pattern_scale:  填充图案缩放比例（>1放大，<1缩小）
        pattern_angle:  填充图案旋转角度（度）
        pattern_double: 是否启用双线填充（垂直交叉）
        hatch_style:    孤岛检测样式: 0=Normal(普通), 1=Outer(外部), 2=Ignore(忽略)
    """
    r = ctrl.hatch_set_properties(handle, pattern_scale, pattern_angle,
                                     pattern_double, hatch_style)
    if r["success"]:
        return format_success(r["message"], handle=handle,
                              changed=str(r.get("changed", {})))
    return f"设置填充属性失败: {r['message']}"


def hatch_get_properties(handle: str) -> str:
    """获取填充对象的所有可读属性。

    返回填充图案名称、缩放、角度、面积、环数等信息。

    Args:
        handle: 填充对象句柄
    """
    r = ctrl.hatch_get_properties(handle)
    return json.dumps(r, indent=2, ensure_ascii=False)


def hatch_set_gradient(handle: str, gradient_type: int = 0,
                        color1: str = "cyan",
                        color2: str = "blue") -> str:
    """将已有填充对象设置为渐变色填充。

    渐变类型: 0=线性渐变, 1=圆柱体, 2=反转圆柱体, 3=球体...

    Args:
        handle:         填充对象句柄
        gradient_type:  渐变类型（0-9）
        color1:         起始颜色名称 (red/yellow/green/cyan/blue/magenta)
        color2:         终止颜色名称
    """
    r = ctrl.hatch_set_gradient(handle, gradient_type, color1, color2)
    if r["success"]:
        return r["message"]
    return f"设置渐变填充失败: {r['message']}"
