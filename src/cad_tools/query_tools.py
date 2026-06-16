"""CAD MCP Tools — Selection sets, entity scanning, spatial queries, highlight."""
from typing import Optional, List, Dict, Any
import json
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


def _sync_db_active_drawing() -> None:
    try:
        info = ctrl.get_document_info()
        if isinstance(info, dict) and "error" not in info:
            db.activate_drawing(
                name=info.get("name", "active"),
                path=info.get("full_name") or info.get("path", ""),
            )
    except Exception:
        pass


def _scan_entity_record(ent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "error" in ent:
        return None
    metadata_keys = {
        "index", "handle", "name", "type", "layer", "color", "linetype",
        "bbox", "bounds", "error",
    }
    geometry = {
        key: value for key, value in ent.items()
        if key not in metadata_keys
    }
    bbox = ent.get("bbox", ent.get("bounds"))
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        bbox = tuple(bbox[:4])
    else:
        bbox = None
    return {
        "handle": ent.get("handle", ""),
        "name": ent.get("name", ent.get("type", "Unknown")),
        "type": ent.get("type", "Unknown"),
        "layer": ent.get("layer", "0"),
        "color": ent.get("color", 256),
        "linetype": ent.get("linetype", "ByLayer"),
        "geometry": geometry,
        "bbox": bbox,
    }


def scan_all_entities(clear_db: bool = True, max_entities: int = 5000,
                      clear_annotations: bool = False,
                      detail_level: str = "minimal",
                      include_bounding_boxes: bool = True,
                      derive_topology: bool = False) -> str:
    """扫描当前图纸所有实体并保存到数据库。

    这是 AI 理解图纸内容的核心工具 — 将 CAD 图形数据转换为结构化数据，
    存入数据库后，AI 可以用 SQL 查询、统计、过滤实体。

    Args:
        clear_db:     是否先清空数据库（默认True）
        max_entities: 最大扫描实体数（默认5000）
    """
    _sync_db_active_drawing()
    if clear_db:
        db.clear_entities(clear_annotations=clear_annotations)
    result = ctrl.scan_model_space(
        max_entities,
        detail_level=detail_level,
        include_bounding_boxes=include_bounding_boxes,
    )
    entities = result.get("entities", [])
    type_stats = result.get("type_stats", {})

    records = []
    errors = 0
    for ent in entities:
        record = _scan_entity_record(ent)
        if record is None:
            errors += 1
            continue
        records.append(record)
    saved = db.upsert_entities_batch(
        records,
        derive_topology=derive_topology,
        derive_bbox=False,
    )

    lines = [f"OK: 已扫描 {saved} 个实体并保存到数据库"]
    lines.append(f"\n实体类型统计 ({len(type_stats)} 种):")
    total_available = result.get("total_available")
    if total_available is not None:
        lines.append(
            f"Scanned {result.get('scanned', len(entities))}/{total_available} entities "
            f"(detail_level={result.get('detail_level', detail_level)}, "
            f"truncated={result.get('truncated', False)})."
        )
    if errors:
        lines.append(f"Skipped {errors} entities that returned scan errors.")
    if not derive_topology:
        lines.append("Topology derivation skipped for fast large-drawing scans.")
    if clear_annotations:
        lines.append("Model-private spatial annotations were cleared.")
    else:
        lines.append("Model-private spatial annotations were preserved.")
    for t, c in sorted(type_stats.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"  {t}: {c}")
    if len(type_stats) > 20:
        lines.append(f"  ... 及其他 {len(type_stats)-20} 种")
    return "\n".join(lines)


def scan_entities_in_area(x_min: float, y_min: float,
                          x_max: float, y_max: float) -> str:
    """扫描指定矩形区域内的实体。

    Args:
        x_min, y_min: 区域左下角坐标
        x_max, y_max: 区域右上角坐标
    """
    result = ctrl.scan_entities_in_area(x_min, y_min, x_max, y_max)
    entities = result.get("entities", [])
    lines = [f"在区域 [{x_min},{y_min}] → [{x_max},{y_max}] 中找到 {len(entities)} 个实体:"]
    for i, e in enumerate(entities[:30]):
        lines.append(f"  [{i}] {e['type']:<25s} Handle:{e['handle']} Layer:{e['layer']}")
    if len(entities) > 30:
        lines.append(f"  ... 及其他 {len(entities)-30} 个")
    return "\n".join(lines)


def select_by_window(x1: float, y1: float, x2: float, y2: float) -> str:
    """窗口选择（完全在窗口内的实体被选中）。

    Args:
        x1, y1: 窗口第一个角点
        x2, y2: 窗口对角点
    """
    r = ctrl.select_by_window([x1, y1, 0], [x2, y2, 0])
    return format_success(f"已选择 {r['count']} 个实体（窗口模式）",
                          handles=r.get("handles", [])[:20])


def select_by_crossing(x1: float, y1: float, x2: float, y2: float) -> str:
    """交叉选择（与选择框相交的实体都被选中）。

    Args:
        x1, y1: 选择框第一个角点
        x2, y2: 选择框对角点
    """
    r = ctrl.select_by_crossing([x1, y1, 0], [x2, y2, 0])
    return format_success(f"已选择 {r['count']} 个实体（交叉模式）",
                          handles=r.get("handles", [])[:20])


def select_all() -> str:
    """选择当前图纸中的所有实体。"""
    r = ctrl.select_all()
    handles = r.get("handles", [])[:20]
    if not r.get("selected", True):
        return format_success(
            f"全图实体较多，已返回 {len(handles)} 个句柄样本（共 {r['count']} 个实体，未创建全局选择集）",
            handles=handles,
            truncated=r.get("truncated", False),
        )
    return format_success(f"已选择全部 {r['count']} 个实体",
                          handles=handles,
                          truncated=r.get("truncated", False))


def highlight_entity(handle: str, color: int = 1) -> str:
    """通过句柄高亮显示指定实体（改变其颜色）。

    Args:
        handle: 实体句柄
        color:  高亮颜色 (1=红 2=黄 3=绿 4=青 5=蓝 6=洋红)
    """
    r = ctrl.highlight_entity(handle, color)
    if r["success"]:
        return format_success(f"已高亮实体 {handle}",
                              color=color,
                              original=r.get("original_color", "?"))
    return f"高亮失败: {r['message']}"


def highlight_entities(handles: List[str], color: int = 1) -> str:
    """批量高亮多个实体。

    Args:
        handles: 实体句柄列表
        color:   高亮颜色索引 (1-6)
    """
    r = ctrl.highlight_entities(handles, color)
    return r["message"]


def reset_entity_color(handle: str, original_color: int = 256) -> str:
    """重置实体颜色（恢复到高亮前的颜色）。

    Args:
        handle:         实体句柄
        original_color: 原始颜色索引
    """
    r = ctrl.reset_entity_color(handle, original_color)
    return r["message"]


def highlight_query_results(sql_query: str, color: int = 1) -> str:
    """执行数据库查询并用结果高亮对应实体。

    这是 AI 最强大的工具之一 — 先用 SQL 找出感兴趣的实体，
    再在 CAD 中高亮它们以供查看。

    Args:
        sql_query: 必须返回handle列的SQL查询
        color:     高亮颜色 (1-6)
    """
    try:
        result = db.execute(sql_query, read_only=True)
        rows = result.get("rows", [])
        if not rows:
            return "查询未返回任何结果"
        if "handle" not in result.get("columns", []):
            return "查询结果中未找到handle列"

        handles = [row["handle"] for row in rows if row.get("handle")]
        if not handles:
            return "结果中没有有效的handle值"

        r = ctrl.highlight_entities(handles, color)
        return f"✓ 查询返回 {len(rows)} 行，已高亮 {len(handles)} 个实体\n{r['message']}"
    except Exception as e:
        return f"查询并高亮失败: {e}"


def get_entity_statistics() -> str:
    """获取当前图纸的实体统计信息（从数据库）。"""
    type_stats = db.get_type_stats()
    layer_stats = db.get_layer_stats()
    total = sum(type_stats.values())

    lines = [f"图纸实体统计 (共 {total} 个)"]
    lines.append(f"\n按类型 ({len(type_stats)} 种):")
    for t, c in sorted(type_stats.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"  {t}: {c}")

    lines.append(f"\n按图层 ({len(layer_stats)} 个):")
    for l, c in sorted(layer_stats.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"  {l}: {c}")
    return "\n".join(lines)
