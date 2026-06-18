"""Shared database and geometry helpers for the CAD Understanding Layer."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase, get_database

BBox = Tuple[float, float, float, float]

INTERNAL_ROW_KEYS = {
    "id",
    "workspace_id",
    "drawing_id",
    "conversation_id",
    "thread_id",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def get_db(database: Optional[CADDatabase] = None) -> CADDatabase:
    return database or get_database()


def decode_json(value: Any, default: Optional[Any] = None) -> Any:
    if value is None:
        return {} if default is None else default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {} if default is None else default


def json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in dict(row).items() if k not in INTERNAL_ROW_KEYS}


def clamp_limit(limit: int, default: int = 1000, maximum: int = 100000) -> int:
    try:
        value = int(limit)
    except Exception:
        value = default
    return max(1, min(value, maximum))


def ensure_understanding_schema(database: Optional[CADDatabase] = None) -> None:
    db = get_db(database)
    with db._conn() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_semantic_objects (
                object_id TEXT PRIMARY KEY,
                object_type TEXT,
                label TEXT,
                source TEXT,
                confidence REAL,
                bbox_min_x REAL,
                bbox_min_y REAL,
                bbox_max_x REAL,
                bbox_max_y REAL,
                entity_handles TEXT DEFAULT '[]',
                properties TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_semantic_relations (
                relation_id TEXT PRIMARY KEY,
                from_object_id TEXT,
                to_object_id TEXT,
                relation_type TEXT,
                confidence REAL,
                evidence TEXT DEFAULT '{}',
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_constraints (
                constraint_id TEXT PRIMARY KEY,
                constraint_type TEXT,
                source TEXT,
                target_handles TEXT DEFAULT '[]',
                target_object_ids TEXT DEFAULT '[]',
                value REAL,
                actual REAL,
                tolerance REAL,
                unit TEXT,
                confidence REAL,
                status TEXT,
                evidence TEXT DEFAULT '{}',
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_validation_reports (
                report_id TEXT PRIMARY KEY,
                passed INTEGER,
                score REAL,
                issue_count INTEGER,
                issues TEXT DEFAULT '[]',
                recommended_next_tools TEXT DEFAULT '[]',
                generated_at TEXT,
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_view_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                image_path TEXT DEFAULT '',
                overlay_image_path TEXT DEFAULT '',
                context_json_path TEXT DEFAULT '',
                snapshot_data TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_vlm_findings (
                finding_id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                source_model TEXT DEFAULT '',
                prompt_version TEXT DEFAULT '',
                issue_type TEXT,
                severity TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'validated',
                confidence REAL,
                overlay_id TEXT DEFAULT '',
                pixel_bbox TEXT DEFAULT '[]',
                world_bbox TEXT DEFAULT '{}',
                claimed_handles TEXT DEFAULT '[]',
                grounded_handles TEXT DEFAULT '[]',
                grounding_candidates TEXT DEFAULT '[]',
                evidence TEXT DEFAULT '{}',
                raw_finding TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cad_image_traces (
                image_id TEXT PRIMARY KEY,
                image_path TEXT DEFAULT '',
                normalized_image_path TEXT DEFAULT '',
                tile_index_path TEXT DEFAULT '',
                image_width INTEGER,
                image_height INTEGER,
                domain TEXT DEFAULT 'mechanical',
                units TEXT DEFAULT '',
                calibration TEXT DEFAULT '{}',
                spec_json TEXT DEFAULT '{}',
                warnings TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                workspace_id TEXT DEFAULT '',
                drawing_id TEXT DEFAULT '',
                conversation_id TEXT DEFAULT '',
                thread_id TEXT DEFAULT ''
            )
        ''')
        for table in (
            "cad_semantic_objects",
            "cad_semantic_relations",
            "cad_constraints",
            "cad_validation_reports",
            "cad_view_snapshots",
            "cad_vlm_findings",
            "cad_image_traces",
        ):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_scope "
                f"ON {table}(workspace_id, drawing_id, conversation_id, thread_id)"
            )


def current_scope(database: Optional[CADDatabase] = None) -> Dict[str, str]:
    ctx = get_db(database).get_context()
    return {
        "workspace_id": ctx.workspace_id,
        "drawing_id": ctx.drawing_id,
        "conversation_id": ctx.conversation_id,
        "thread_id": ctx.thread_id,
    }


def drawing_scope(database: Optional[CADDatabase] = None) -> Dict[str, str]:
    ctx = get_db(database).get_context()
    return {
        "workspace_id": ctx.workspace_id,
        "drawing_id": ctx.drawing_id,
    }


def all_entities(database: Optional[CADDatabase] = None,
                 limit: int = 100000) -> List[Dict[str, Any]]:
    db = get_db(database)
    rows = db.query_entities(limit=clamp_limit(limit), offset=0)
    return [clean_row(row) for row in rows]


def get_entity(database: Optional[CADDatabase], handle: str) -> Optional[Dict[str, Any]]:
    db = get_db(database)
    row = db.get_entity(handle)
    return clean_row(row) if row else None


def all_layers(database: Optional[CADDatabase] = None) -> List[Dict[str, Any]]:
    return [clean_row(row) for row in get_db(database).get_layers()]


def all_blocks(database: Optional[CADDatabase] = None) -> List[Dict[str, Any]]:
    return [clean_row(row) for row in get_db(database).get_blocks()]


def topology_summary(database: Optional[CADDatabase] = None,
                     limit: int = 100000) -> List[Dict[str, Any]]:
    return get_db(database).get_topology_summary(limit=clamp_limit(limit))


def topology_for_handle(database: Optional[CADDatabase],
                        handle: str) -> Dict[str, Any]:
    return get_db(database).get_entity_topology(handle)


def all_topology_primitives(database: Optional[CADDatabase] = None) -> List[Dict[str, Any]]:
    db = get_db(database)
    ctx = db.get_context()
    with db._conn() as conn:
        rows = conn.execute('''
            SELECT e.native_handle AS entity_handle, gp.primitive_key,
                   gp.primitive_type, gp.role, gp.sequence_index,
                   gp.parent_key, gp.x, gp.y, gp.z, gp.x2, gp.y2, gp.z2,
                   gp.radius, gp.length, gp.area, gp.is_closed, gp.source,
                   gp.properties
            FROM cad_geometry_primitives gp
            JOIN cad_entities e ON e.handle = gp.entity_handle
            WHERE e.workspace_id = ? AND e.drawing_id = ?
            ORDER BY e.native_handle, gp.sequence_index, gp.primitive_key
        ''', (ctx.workspace_id, ctx.drawing_id)).fetchall()
    result = []
    for row in rows:
        item = clean_row(dict(row))
        item["is_closed"] = bool(item.get("is_closed"))
        item["properties"] = decode_json(item.get("properties"))
        result.append(item)
    return result


def all_topology_relations(database: Optional[CADDatabase] = None) -> List[Dict[str, Any]]:
    db = get_db(database)
    ctx = db.get_context()
    with db._conn() as conn:
        rows = conn.execute('''
            SELECT e.native_handle AS entity_handle, gr.from_key, gr.to_key,
                   gr.relation_type, gr.sequence_index, gr.properties
            FROM cad_geometry_relations gr
            JOIN cad_entities e ON e.handle = gr.entity_handle
            WHERE e.workspace_id = ? AND e.drawing_id = ?
            ORDER BY e.native_handle, gr.sequence_index, gr.relation_type
        ''', (ctx.workspace_id, ctx.drawing_id)).fetchall()
    result = []
    for row in rows:
        item = clean_row(dict(row))
        item["properties"] = decode_json(item.get("properties"))
        result.append(item)
    return result


def all_annotations(database: Optional[CADDatabase] = None,
                    limit: int = 1000) -> List[Dict[str, Any]]:
    return [clean_row(row) for row in get_db(database).list_spatial_annotations(
        limit=clamp_limit(limit, maximum=1000)
    )]


def bbox_from_row(row: Dict[str, Any]) -> Optional[BBox]:
    keys = ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y")
    if all(row.get(k) is not None for k in keys):
        try:
            return tuple(float(row[k]) for k in keys)  # type: ignore[return-value]
        except Exception:
            return None
    raw = row.get("bbox") or row.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        except Exception:
            return None
    geom = decode_json(row.get("geometry"))
    raw = geom.get("bbox") or geom.get("bounds")
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        except Exception:
            return None
    return None


def bbox_dict(bbox: Optional[BBox]) -> Dict[str, Any]:
    if bbox is None:
        return {"min": None, "max": None, "center": None, "width": None, "height": None}
    min_x, min_y, max_x, max_y = bbox
    return {
        "min": [min_x, min_y],
        "max": [max_x, max_y],
        "center": [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0],
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def bbox_union(bboxes: Iterable[Optional[BBox]]) -> Optional[BBox]:
    valid = [bbox for bbox in bboxes if bbox is not None]
    if not valid:
        return None
    return (
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    )


def bbox_area(bbox: Optional[BBox]) -> float:
    if bbox is None:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_center(bbox: Optional[BBox]) -> Optional[Tuple[float, float]]:
    if bbox is None:
        return None
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def bbox_contains(outer: Optional[BBox], inner: Optional[BBox],
                  tolerance: float = 0.0) -> bool:
    if outer is None or inner is None:
        return False
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[2] + tolerance >= inner[2]
        and outer[3] + tolerance >= inner[3]
    )


def bbox_intersects(a: Optional[BBox], b: Optional[BBox]) -> bool:
    if a is None or b is None:
        return False
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def bbox_iou(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> float:
    if a is None or b is None or len(a) < 4 or len(b) < 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(v) for v in a[:4]]
    bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = bbox_area((ax1, ay1, ax2, ay2)) + bbox_area((bx1, by1, bx2, by2)) - inter
    return inter / union if union > 0 else 0.0


def point_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2)


def entity_text(entity: Dict[str, Any]) -> str:
    geometry = decode_json(entity.get("geometry"))
    properties = decode_json(entity.get("properties"))
    parts = [
        entity.get("handle", ""),
        entity.get("native_handle", ""),
        entity.get("name", ""),
        entity.get("type", ""),
        entity.get("layer", ""),
        geometry.get("text", ""),
        geometry.get("text_string", ""),
        properties.get("text", ""),
        properties.get("text_string", ""),
        properties.get("effective_name", ""),
        properties.get("name", ""),
        geometry.get("block_name", ""),
    ]
    return " ".join(str(part) for part in parts if part is not None).lower()


def entity_type(entity: Dict[str, Any]) -> str:
    return str(entity.get("type") or entity.get("entity_type") or entity.get("name") or "").lower()


def entity_geometry(entity: Dict[str, Any]) -> Dict[str, Any]:
    geometry = decode_json(entity.get("geometry"))
    return geometry if isinstance(geometry, dict) else {}


def point3(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return [
            float(value[0]),
            float(value[1]),
            float(value[2]) if len(value) > 2 else 0.0,
        ]
    except Exception:
        return None


def point_list(value: Any) -> List[List[float]]:
    if not isinstance(value, (list, tuple)):
        return []
    if value and all(isinstance(v, (int, float)) for v in value):
        step = 3 if len(value) % 3 == 0 else 2
        return [p for p in (point3(value[i:i + step]) for i in range(0, len(value), step)) if p]
    return [p for p in (point3(item) for item in value) if p]


def line_points(entity: Dict[str, Any]) -> Optional[Tuple[List[float], List[float]]]:
    geometry = entity_geometry(entity)
    start = point3(geometry.get("start_point") or geometry.get("start"))
    end = point3(geometry.get("end_point") or geometry.get("end"))
    if start and end:
        return start, end
    bbox = bbox_from_row(entity)
    if bbox and "line" in entity_type(entity):
        return [bbox[0], bbox[1], 0.0], [bbox[2], bbox[3], 0.0]
    return None


def line_length(entity: Dict[str, Any]) -> Optional[float]:
    geometry = entity_geometry(entity)
    if geometry.get("length") is not None:
        try:
            measured = float(geometry.get("length"))
            points = line_points(entity)
            if measured > 1e-9 or not points:
                return measured
            derived = point_distance(points[0], points[1])
            return derived if derived > measured else measured
        except Exception:
            pass
    points = line_points(entity)
    if not points:
        return None
    return point_distance(points[0], points[1])


def line_angle(entity: Dict[str, Any]) -> Optional[float]:
    points = line_points(entity)
    if not points:
        return None
    start, end = points
    return math.atan2(end[1] - start[1], end[0] - start[0])


def circle_center_radius(entity: Dict[str, Any]) -> Optional[Tuple[List[float], float]]:
    geometry = entity_geometry(entity)
    center = point3(geometry.get("center"))
    radius = geometry.get("radius")
    bbox = bbox_from_row(entity)
    if center is None:
        if bbox:
            center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0, 0.0]
    try:
        numeric_radius = float(radius) if radius is not None else None
    except Exception:
        numeric_radius = None
    if (numeric_radius is None or numeric_radius <= 1e-9) and bbox:
        derived_radius = min(abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])) / 2.0
        if derived_radius > 1e-9:
            numeric_radius = derived_radius
    if numeric_radius is None:
        if bbox:
            numeric_radius = min(abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])) / 2.0
    try:
        return (center, float(numeric_radius)) if center is not None and numeric_radius is not None else None
    except Exception:
        return None


def is_closed_polyline(entity: Dict[str, Any]) -> bool:
    etype = entity_type(entity)
    if "polyline" not in etype and "lwpolyline" not in etype:
        return False
    geometry = entity_geometry(entity)
    if bool(geometry.get("closed")):
        return True
    vertices = point_list(geometry.get("vertices") or geometry.get("points"))
    return len(vertices) > 2 and point_distance(vertices[0], vertices[-1]) < 1e-9


def latest_validation_report(database: Optional[CADDatabase] = None) -> Optional[Dict[str, Any]]:
    ensure_understanding_schema(database)
    db = get_db(database)
    scope = current_scope(db)
    with db._conn() as conn:
        row = conn.execute('''
            SELECT * FROM cad_validation_reports
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchone()
    if not row:
        return None
    item = clean_row(dict(row))
    item["passed"] = bool(item.get("passed"))
    item["issues"] = decode_json(item.get("issues"), [])
    item["recommended_next_tools"] = decode_json(item.get("recommended_next_tools"), [])
    return item
