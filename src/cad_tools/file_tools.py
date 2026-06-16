"""CAD MCP Tools — File I/O, export, import, purge, audit, undo/redo, commands."""
from datetime import datetime
import math
from pathlib import Path
from typing import Optional, List
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def _sync_db_drawing_from_info(info) -> None:
    try:
        if isinstance(info, dict) and "error" not in info:
            db.activate_drawing(
                name=info.get("name", "active"),
                path=info.get("full_name") or info.get("path", ""),
            )
    except Exception:
        pass


def get_document_info() -> str:
    """获取当前文档的完整信息（名称、路径、统计、元数据等）。"""
    import json
    info = ctrl.get_document_info()
    _sync_db_drawing_from_info(info)
    return json.dumps(info, indent=2, ensure_ascii=False, default=str)


def export_pdf(filepath: str) -> str:
    """将当前图纸导出为 PDF 文件。

    Args:
        filepath: PDF 文件保存路径（如 C:/output/drawing.pdf）
    """
    r = ctrl.export_drawing(filepath, "PDF")
    return r["message"]


def export_dxf(filepath: str) -> str:
    """将当前图纸导出为 DXF 文件。

    Args:
        filepath: DXF 文件保存路径
    """
    r = ctrl.export_drawing(filepath, "DXF")
    return r["message"]


def export_dwf(filepath: str) -> str:
    """将当前图纸导出为 DWF (Design Web Format) 文件。

    Args:
        filepath: DWF 文件保存路径
    """
    r = ctrl.export_drawing(filepath, "DWF")
    return r["message"]


def export_image(filepath: str) -> str:
    """将当前视图导出为图片 (BMP/WMF)。

    Args:
        filepath: 图片保存路径（.bmp 或 .wmf）
    """
    ext = filepath.split(".")[-1].upper() if "." in filepath else "BMP"
    r = ctrl.export_drawing(filepath, ext)
    return r["message"]


def export_view_image(filepath: Optional[str] = None,
                      zoom_extents_first: bool = False) -> str:
    """Export the current AutoCAD view for vision-capable model inspection.

    This model-facing verification helper writes a review artifact to disk and
    does not add visible geometry, layers, XData, or marks to the DWG. WMF is
    the reliable AutoCAD COM image export format in this server.
    """
    if filepath is None or not str(filepath).strip():
        out_dir = Path.cwd() / "cad_visual_exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = str(out_dir / f"cad_view_{stamp}.wmf")

    suffix = Path(filepath).suffix.lower()
    if suffix not in {".wmf", ".bmp"}:
        return (
            "ERROR: AutoCAD COM image export in this MCP supports WMF reliably "
            "and BMP is disabled because it can block in this environment. "
            "Use a .wmf filepath, or export_pdf and render the PDF externally "
            "if a raster PNG/JPG is required."
        )

    if zoom_extents_first:
        try:
            ctrl.zoom_extents()
            ctrl.regen("all")
        except Exception:
            pass

    message = export_image(filepath)
    return (
        f"{message}\n"
        f"Visual verification artifact: {filepath}\n"
        "This export is model-only review output and does not modify the DWG."
    )


def purge_drawing() -> str:
    """清理图纸中未使用的对象（图层、线型、块、文字样式等）。"""
    r = ctrl.purge_all()
    return r["message"]


def audit_drawing() -> str:
    """审计并修复当前图纸中的错误。"""
    r = ctrl.audit()
    return r["message"]


def undo(count: int = 1) -> str:
    """撤销上一步操作。

    Args:
        count: 撤销步数（默认1）
    """
    r = ctrl.undo(min(count, 100))
    return r["message"]


def redo(count: int = 1) -> str:
    """重做上一步撤销的操作。

    Args:
        count: 重做步数（默认1）
    """
    r = ctrl.redo(min(count, 100))
    return r["message"]


def regen(which: str = "all") -> str:
    """重新生成图形显示。

    Args:
        which: "all"=所有视口, "active"=仅活动视口
    """
    r = ctrl.regen(which)
    return r["message"]


def send_command(command: str) -> str:
    """向 AutoCAD 命令行发送原始命令（高级用户/谨慎使用）。

    这允许执行任何 AutoCAD 命令，用于 MCP 工具未覆盖的功能。

    Args:
        command: AutoCAD 命令字符串（如 "LINE 0,0 10,10 "）
    """
    r = ctrl.send_command(command)
    return r["message"]


def get_variable(variable_name: str) -> str:
    """获取 AutoCAD 系统变量的值。

    Args:
        variable_name: 系统变量名称（如 INSUNITS, LTSCALE, DIMSCALE...）
    """
    val = ctrl.get_variable(variable_name)
    return f"{variable_name} = {val}"


def set_variable(variable_name: str, value: str) -> str:
    """设置 AutoCAD 系统变量的值。

    Args:
        variable_name: 系统变量名称（如 LTSCALE, DIMSCALE, FILLETRAD）
        value:         要设置的值（支持数字和字符串）
    """
    # Try to convert value to number if possible
    try:
        numeric = float(value)
        if numeric == int(numeric):
            numeric = int(numeric)
        r = ctrl.set_variable(variable_name, numeric)
    except ValueError:
        r = ctrl.set_variable(variable_name, value)
    return r["message"]


def measure_distance(x1: float, y1: float, x2: float, y2: float,
                     z1: float = 0.0, z2: float = 0.0) -> str:
    """计算两点之间的直线距离。

    Args:
        x1, y1, z1: 第一个点坐标
        x2, y2, z2: 第二个点坐标
    """
    dist = ctrl.get_distance([x1, y1, z1], [x2, y2, z2])
    dx, dy = x2 - x1, y2 - y1
    angle = math.atan2(dy, dx) * 180.0 / math.pi
    return (f"距离: {dist:.4f} 单位\n"
            f"X增量: {dx:.4f}\nY增量: {dy:.4f}\n角度: {angle:.2f}°")


def create_snapshot(name: str = "") -> str:
    """创建当前图纸状态快照（保存到数据库，用于前后对比）。

    Args:
        name: 快照名称（可选，默认使用图纸名）
    """
    info = ctrl.get_document_info()
    if "error" in info:
        return f"创建快照失败: {info['error']}"
    _sync_db_drawing_from_info(info)

    # Scan current state
    scan = ctrl.scan_model_space(500)

    snapshot_id = db.create_snapshot(
        drawing_name=name or info.get("name", "unknown"),
        entity_count=info.get("entity_count", 0),
        layer_count=info.get("layers_count", 0),
        block_count=info.get("blocks_count", 0),
        type_stats=scan.get("type_stats", {}),
        snapshot_data={
            "document_info": info,
            "entity_count": len(scan.get("entities", [])),
        },
    )
    return format_success(f"已创建快照 #{snapshot_id}",
                          entity_count=scan.get("total", 0),
                          types=len(scan.get("type_stats", {})))


def get_snapshots(limit: int = 5) -> str:
    """列出最近的图纸快照。

    Args:
        limit: 返回的快照数量
    """
    import json
    snapshots = db.get_recent_snapshots(limit)
    if not snapshots:
        return "无快照记录"
    lines = [f"最近 {len(snapshots)} 个快照:"]
    for s in snapshots:
        lines.append(
            f"  #{s['id']} {s['drawing_name']} "
            f"实体:{s['entity_count']} 图层:{s['layer_count']} "
            f"时间:{s['created_at']}")
    return "\n".join(lines)
