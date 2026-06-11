"""CAD MCP Tools — View control, zoom, pan, layout/viewport management."""
from typing import Optional, List
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def zoom_extents() -> str:
    """缩放到图形范围（显示所有对象）。"""
    r = ctrl.zoom_extents()
    return r["message"]


def zoom_window(x1: float, y1: float, x2: float, y2: float) -> str:
    """缩放到指定窗口区域。

    Args:
        x1, y1: 窗口第一个角点坐标
        x2, y2: 窗口对角点坐标
    """
    r = ctrl.zoom_window([x1, y1, 0], [x2, y2, 0])
    return r["message"]


def zoom_center(center_x: float, center_y: float, height: float) -> str:
    """居中缩放到指定位置。

    Args:
        center_x, center_y: 视图中心坐标
        height:             视图高度（图形单位）
    """
    r = ctrl.zoom_center(center_x, center_y, height)
    return r["message"]


def zoom_scale(scale: float) -> str:
    """按比例缩放视图。

    Args:
        scale: 缩放倍率（>1放大, <1缩小, 2=放大2倍）
    """
    r = ctrl.zoom_scale(scale)
    return r["message"]


def zoom_previous() -> str:
    """恢复到前一个视图。"""
    r = ctrl.zoom_previous()
    return r["message"]


def zoom_all() -> str:
    """缩放到图形界限（包含所有对象和界限）。"""
    r = ctrl.zoom_all()
    return r["message"]


def pan(x_offset: float, y_offset: float) -> str:
    """平移视图。

    Args:
        x_offset: X方向平移量（图形单位）
        y_offset: Y方向平移量（图形单位）
    """
    r = ctrl.pan(x_offset, y_offset)
    return r["message"]


def get_current_view() -> str:
    """获取当前视图信息（中心、大小、目标、方向）。"""
    import json
    view = ctrl.get_current_view()
    return json.dumps(view, indent=2, ensure_ascii=False, default=str)


def get_layouts() -> str:
    """列出所有布局（模型空间和图纸空间）。"""
    layouts = ctrl.get_layouts()
    if not layouts:
        return "无布局信息"
    lines = [f"共 {len(layouts)} 个布局:"]
    for i, l in enumerate(layouts):
        type_str = "模型空间" if l["model_type"] else "图纸空间"
        lines.append(f"  [{i}] {l['name']:<20s} [{type_str}] Tab:{l['tab_order']}")
    return "\n".join(lines)


def set_active_layout(name: str) -> str:
    """切换到指定布局（如 "Model" 切换到模型空间）。

    Args:
        name: 布局名称
    """
    r = ctrl.set_active_layout(name)
    return r["message"]


def create_layout(name: str) -> str:
    """创建新的图纸空间布局。

    Args:
        name: 新布局名称
    """
    r = ctrl.create_layout(name)
    return r["message"]
