"""CAD MCP Tools — Block attribute definition and reference operations."""
from typing import Optional, List, Dict
import json
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def insert_block_with_attributes(block_name: str, x: float, y: float,
                                   z: float = 0.0, x_scale: float = 1.0,
                                   y_scale: float = 1.0, z_scale: float = 1.0,
                                   rotation: float = 0.0,
                                   attributes: Optional[List[Dict[str, str]]] = None,
                                   layer: Optional[str] = None) -> str:
    """插入带有属性值的图块参照。

    指定属性标签-值对列表来填充图块定义中定义的属性。
    例如: [{"tag": "DOOR_NO", "value": "D01"}, {"tag": "WIDTH", "value": "900"}]

    Args:
        block_name: 图块名称
        x, y, z:    插入点坐标
        x_scale:    X方向缩放
        y_scale:    Y方向缩放
        z_scale:    Z方向缩放
        rotation:   旋转角度（度）
        attributes: 属性标签-值对列表 [{"tag": "标签", "value": "值"}, ...]
        layer:      图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    blk_ref = ctrl.insert_block_with_attributes(
        block_name, x, y, z, x_scale, y_scale, z_scale, rotation, attributes)
    if layer:
        try: blk_ref.Layer = layer
        except: pass
    db.upsert_entity(blk_ref.Handle, f"BlockRef({block_name})", "AcDbBlockReference",
                     layer=blk_ref.Layer, color=blk_ref.Color,
                     geometry={"block_name": block_name,
                               "insertion_point": [x, y, z],
                               "attributes": attributes or []})
    extra = f" (含 {len(attributes)} 个属性)" if attributes else ""
    return format_success(f"已插入图块 '{block_name}'{extra}",
                          handle=blk_ref.Handle,
                          point=f"({x},{y},{z})")


def get_block_attributes(handle: str) -> str:
    """获取图块参照的所有属性值及其详细信息。

    返回每个属性的标签(tag)、当前值(value)、提示(prompt)、
    高度、旋转角度、是否不可见/常量等完整信息。

    Args:
        handle: 图块参照的实体句柄
    """
    r = ctrl.get_block_attributes(handle)
    return json.dumps(r, indent=2, ensure_ascii=False, default=str)


def set_block_attribute(handle: str, tag: str, value: str) -> str:
    """设置图块参照中指定标签的属性值。

    通过属性标签(tag)精确设置对应的属性值。

    Args:
        handle: 图块参照的实体句柄
        tag:    属性标签名称
        value:  要设置的新值
    """
    r = ctrl.set_block_attribute(handle, tag, value)
    if r["success"]:
        return format_success(r["message"], handle=handle, tag=tag)
    return f"设置属性失败: {r['message']}"
