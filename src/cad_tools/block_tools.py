"""CAD MCP Tools — Block creation, insertion, attribute editing, and Xref management."""
import os
from typing import List, Optional, Dict, Any
from src.cad_controller import get_controller, to_variant_point
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def _ensure_doc() -> bool:
    ctrl._ensure_connected()
    return ctrl.has_document


def create_block(name: str, base_x: float, base_y: float, base_z: float,
                 entity_handles: List[str]) -> str:
    """用选定的实体创建图块定义。

    Args:
        name:            新图块名称
        base_x,base_y,base_z: 图块基点坐标
        entity_handles:  要包含在图块中的实体句柄列表
    """
    if not _ensure_doc():
        return "错误: 未连接到打开的 AutoCAD 图形"
    entities = []
    not_found = []
    for h in entity_handles:
        ent = ctrl._get_entity(h)
        if ent:
            entities.append(ent)
        else:
            not_found.append(h)
    if not entities:
        return f"错误: 未找到任何指定实体"
    blk = ctrl.create_block(name, (base_x, base_y, base_z), entities)
    msg = format_success(f"已创建图块 '{name}'", entity_count=len(entities))
    if not_found:
        msg += f"\n  未找到的句柄: {not_found}"
    return msg


def insert_block(name: str, x: float, y: float,
                 z: float = 0.0, x_scale: float = 1.0,
                 y_scale: float = 1.0, z_scale: float = 1.0,
                 rotation: float = 0.0,
                 layer: Optional[str] = None) -> str:
    """在当前图形中插入图块参照。

    Args:
        name:      图块名称
        x, y, z:   插入点坐标
        x_scale:   X方向缩放（默认1）
        y_scale:   Y方向缩放（默认1）
        z_scale:   Z方向缩放（默认1）
        rotation:  旋转角度（度，默认0）
        layer:     图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    if not _ensure_doc():
        return "错误: 未连接到打开的 AutoCAD 图形"
    try:
        ref = ctrl.insert_block(name, x, y, z, x_scale, y_scale, z_scale, rotation)
        return format_success(f"已插入图块 '{name}'",
                              handle=ref.Handle,
                              position=f"({x}, {y}, {z})",
                              scale=f"({x_scale}, {y_scale}, {z_scale})",
                              rotation=f"{rotation}°")
    except Exception as e:
        return f"插入图块失败: {e}"


def get_all_blocks() -> str:
    """列出图纸中所有图块定义。"""
    if not _ensure_doc():
        return "错误: 未连接到打开的 AutoCAD 图形"
    blocks = ctrl.get_all_blocks()
    if not blocks:
        return "无图块定义"
    lines = [f"共 {len(blocks)} 个图块:"]
    for i, b in enumerate(blocks):
        tags = []
        if b["is_layout"]: tags.append("布局")
        if b["is_xref"]: tags.append("外部参照")
        tag_str = f" [{','.join(tags)}]" if tags else ""
        lines.append(f"  [{i}] {b['name']:<20s} 实体数:{b['count']:<4d}{tag_str}")
    return "\n".join(lines)


def explode_block(handle: str) -> str:
    """分解图块引用为基本实体。

    Args:
        handle: 图块引用句柄
    """
    if not _ensure_doc():
        return "错误: 未连接到打开的 AutoCAD 图形"
    r = ctrl.explode_entity(handle)
    if r["success"]:
        return format_success(f"已分解图块", entity_count=len(r.get("new_handles", [])))
    return f"分解失败: {r['message']}"


def attach_xref(filepath: str, insert_x: float = 0, insert_y: float = 0,
                insert_z: float = 0, scale: float = 1.0,
                rotation: float = 0.0,
                layer: Optional[str] = None) -> str:
    """附加外部参照 (Xref) 到当前图纸。

    Args:
        filepath: 外部参照文件(.dwg)的完整路径
        insert_x, insert_y, insert_z: 插入点
        scale:    缩放比例
        rotation: 旋转角度（度）
        layer:    图层名称
    """
    try:
        if not _ensure_doc():
            return "错误: 未连接到打开的 AutoCAD 图形"
        if layer:
            ctrl.create_layer(layer)
            ctrl.set_current_layer(layer)

        # Attach Xref via the AttachExternalReference method
        xref = ctrl.doc.ModelSpace.AttachExternalReference(
            filepath,
            os.path.basename(filepath).replace('.dwg', '').replace('.DWG', ''),
            to_variant_point(insert_x, insert_y, insert_z),
            scale, scale, scale,
            rotation * 3.14159 / 180.0,
            0  # Overlay=0, Attach=1
        )
        return format_success(f"已附加外部参照: {filepath}", handle=xref.Handle)
    except Exception as e:
        return f"附加外部参照失败: {e}"


def get_xrefs() -> str:
    """列出所有外部参照。"""
    try:
        if not _ensure_doc():
            return "错误: 未连接到打开的 AutoCAD 图形"
        blocks = ctrl.get_all_blocks()
        xrefs = [b for b in blocks if b["is_xref"]]
        if not xrefs:
            return "无外部参照"
        lines = [f"共 {len(xrefs)} 个外部参照:"]
        for i, xr in enumerate(xrefs):
            lines.append(f"  [{i}] {xr['name']:<20s} 路径:{xr['path']}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取外部参照列表失败: {e}"


def unload_xref(name: str) -> str:
    """卸载外部参照（保留链接但不加载）。

    Args:
        name: 外部参照名称
    """
    try:
        if not _ensure_doc():
            return "错误: 未连接到打开的 AutoCAD 图形"
        blk = ctrl.doc.Blocks.Item(name)
        blk.Unload()
        return format_success(f"已卸载外部参照 '{name}'")
    except Exception as e:
        return f"卸载失败: {e}"


def reload_xref(name: str) -> str:
    """重新加载外部参照。

    Args:
        name: 外部参照名称
    """
    try:
        if not _ensure_doc():
            return "错误: 未连接到打开的 AutoCAD 图形"
        blk = ctrl.doc.Blocks.Item(name)
        blk.Reload()
        return format_success(f"已重新加载外部参照 '{name}'")
    except Exception as e:
        return f"重新加载失败: {e}"
