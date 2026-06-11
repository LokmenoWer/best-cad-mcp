"""CAD MCP Tools — Layer management (full CRUD, freeze/thaw, lock/unlock, isolate)."""
from typing import Optional, List
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def create_layer(name: str, color: int = 7,
                 linetype: str = "Continuous") -> str:
    """创建新图层或修改已有图层的颜色。

    Args:
        name:     图层名称
        color:    颜色索引 (1=红, 2=黄, 3=绿, 4=青, 5=蓝, 6=洋红, 7=白/黑)
        linetype: 线型名称 (Continuous, Dashed, Center, Hidden, etc.)
    """
    r = ctrl.create_layer(name, color, linetype)
    if r["success"]:
        if r.get("existing"):
            return f"图层 '{name}' 已存在，颜色已更新为 {color}"
        return format_success(f"已创建图层 '{name}'", color=color, linetype=linetype)
    return f"创建图层失败: {r['message']}"


def delete_layer(name: str) -> str:
    """删除图层（不能删除当前图层、图层0或包含实体的图层）。

    Args:
        name: 图层名称
    """
    r = ctrl.delete_layer(name)
    return r["message"]


def rename_layer(old_name: str, new_name: str) -> str:
    """重命名图层。

    Args:
        old_name: 当前图层名
        new_name: 新图层名
    """
    r = ctrl.rename_layer(old_name, new_name)
    return r["message"]


def freeze_layer(name: str) -> str:
    """冻结指定图层（冻结的图层不可见且不参与重生成）。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, frozen=True)
    return r["message"]


def thaw_layer(name: str) -> str:
    """解冻指定图层。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, frozen=False)
    return r["message"]


def lock_layer(name: str) -> str:
    """锁定图层（锁定的图层可见但不可编辑）。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, locked=True)
    return r["message"]


def unlock_layer(name: str) -> str:
    """解锁图层。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, locked=False)
    return r["message"]


def turn_off_layer(name: str) -> str:
    """关闭图层（不可见但参与重生成）。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, on=False)
    return r["message"]


def turn_on_layer(name: str) -> str:
    """打开图层。

    Args:
        name: 图层名称
    """
    r = ctrl.set_layer_state(name, on=True)
    return r["message"]


def set_current_layer(name: str) -> str:
    """将指定图层设为当前图层（后续绘图在此图层上）。

    Args:
        name: 图层名称
    """
    r = ctrl.set_current_layer(name)
    return r["message"]


def get_all_layers() -> str:
    """列出所有图层及其状态（名称、颜色、冻结/锁定/开关状态）。"""
    layers = ctrl.get_all_layers()
    if not layers:
        return "无图层信息"
    lines = [f"共 {len(layers)} 个图层:"]
    for i, l in enumerate(layers):
        status = []
        if l["is_frozen"]: status.append("冻结")
        if l["is_locked"]: status.append("锁定")
        if not l["is_on"]: status.append("关闭")
        status_str = ",".join(status) if status else "正常"
        lines.append(
            f"  [{i}] {l['name']:<20s} 颜色:{l['color']:<3d} "
            f"线型:{l['linetype']:<12s} [{status_str}]")
    return "\n".join(lines)


def isolate_layer(name: str) -> str:
    """隔离图层：关闭除指定图层外的所有图层。

    Args:
        name: 要保留的图层名称
    """
    try:
        layers = ctrl.get_all_layers()
        isolated = 0
        for l in layers:
            if l["name"] != name and l["is_on"]:
                ctrl.set_layer_state(l["name"], on=False)
                isolated += 1
        ctrl.set_current_layer(name)
        return f"✓ 已隔离图层 '{name}'，关闭了 {isolated} 个其他图层"
    except Exception as e:
        return f"隔离图层失败: {e}"


def unisolate_layers() -> str:
    """取消图层隔离：打开所有图层。"""
    try:
        layers = ctrl.get_all_layers()
        turned_on = 0
        for l in layers:
            if not l["is_on"] and not l["is_frozen"]:
                ctrl.set_layer_state(l["name"], on=True)
                turned_on += 1
        return f"✓ 已打开 {turned_on} 个图层"
    except Exception as e:
        return f"取消隔离失败: {e}"


def save_layers_to_db() -> str:
    """将当前图纸的图层配置保存到数据库。"""
    layers = ctrl.get_all_layers()
    db.save_layers(layers)
    return format_success(f"已保存 {len(layers)} 个图层配置到数据库")
