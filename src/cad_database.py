"""
CAD Database — SQLite persistence layer for CAD metadata.

Stores entity metadata, layer info, block definitions, text patterns,
and spatial indexes. Supports both raw SQL queries and structured
query building for LLM consumption.

Schema overview:
  - cad_entities:      all scanned entities with JSON properties
  - cad_layers:        layer configurations
  - cad_blocks:        block definitions
  - text_patterns:     text search/count results
  - spatial_index:     bounding boxes for fast area queries
  - query_history:     log of queries for context
"""

import sqlite3
import json
import math
import os
import logging
from typing import Optional, List, Dict, Any, Tuple, Union
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 数据库存储在 Agent 的工作目录（MCP 客户端的 cwd），而非 MCP 程序目录
# 这样每个 Agent 实例的数据库互相隔离
# 使用函数而非模块级常量，确保每次获取时都是当前 cwd
def _get_default_db_path() -> str:
    return str(Path(os.getcwd()) / "autocad_data.db")


class CADDatabase:
    """Manages the SQLite database for CAD metadata persistence."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _get_default_db_path()
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            c = conn.cursor()

            # Core entity table
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    handle TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    layer TEXT DEFAULT '0',
                    color INTEGER DEFAULT 256,
                    linetype TEXT DEFAULT 'ByLayer',
                    linetype_scale REAL DEFAULT 1.0,
                    lineweight REAL DEFAULT -1.0,
                    visible INTEGER DEFAULT 1,
                    bbox_min_x REAL,
                    bbox_min_y REAL,
                    bbox_max_x REAL,
                    bbox_max_y REAL,
                    properties TEXT DEFAULT '{}',
                    geometry TEXT DEFAULT '{}',
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Indexes for common queries
            c.execute('CREATE INDEX IF NOT EXISTS idx_entities_handle ON cad_entities(handle)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_entities_type ON cad_entities(type)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_entities_layer ON cad_entities(layer)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_entities_color ON cad_entities(color)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_entities_bbox ON cad_entities(bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y)')

            # Geometry primitives derived from cad_entities.geometry.
            # This gives LLMs a queryable point/edge/surface graph without
            # needing to reverse-engineer free-form JSON for every entity type.
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_geometry_primitives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_handle TEXT NOT NULL,
                    primitive_key TEXT NOT NULL,
                    primitive_type TEXT NOT NULL,
                    role TEXT DEFAULT '',
                    sequence_index INTEGER DEFAULT 0,
                    parent_key TEXT DEFAULT '',
                    x REAL,
                    y REAL,
                    z REAL,
                    x2 REAL,
                    y2 REAL,
                    z2 REAL,
                    radius REAL,
                    length REAL,
                    area REAL,
                    is_closed INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'derived',
                    properties TEXT DEFAULT '{}',
                    UNIQUE(entity_handle, primitive_key),
                    FOREIGN KEY(entity_handle) REFERENCES cad_entities(handle) ON DELETE CASCADE
                )
            ''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_entity ON cad_geometry_primitives(entity_handle)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_type ON cad_geometry_primitives(primitive_type)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_role ON cad_geometry_primitives(role)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_xy ON cad_geometry_primitives(x, y)')

            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_geometry_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_handle TEXT NOT NULL,
                    from_key TEXT NOT NULL,
                    to_key TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    sequence_index INTEGER DEFAULT 0,
                    properties TEXT DEFAULT '{}',
                    UNIQUE(entity_handle, from_key, to_key, relation_type, sequence_index),
                    FOREIGN KEY(entity_handle) REFERENCES cad_entities(handle) ON DELETE CASCADE
                )
            ''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_rel_entity ON cad_geometry_relations(entity_handle)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_geom_rel_type ON cad_geometry_relations(relation_type)')

            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_topology_summary (
                    entity_handle TEXT PRIMARY KEY,
                    dimensionality INTEGER DEFAULT 0,
                    point_count INTEGER DEFAULT 0,
                    line_count INTEGER DEFAULT 0,
                    curve_count INTEGER DEFAULT 0,
                    surface_count INTEGER DEFAULT 0,
                    solid_count INTEGER DEFAULT 0,
                    is_closed INTEGER DEFAULT 0,
                    length REAL,
                    area REAL,
                    summary TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(entity_handle) REFERENCES cad_entities(handle) ON DELETE CASCADE
                )
            ''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_topology_dim ON cad_topology_summary(dimensionality)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_topology_closed ON cad_topology_summary(is_closed)')

            # Layer table
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_layers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    color INTEGER DEFAULT 7,
                    linetype TEXT DEFAULT 'Continuous',
                    lineweight REAL DEFAULT -1.0,
                    is_frozen INTEGER DEFAULT 0,
                    is_locked INTEGER DEFAULT 0,
                    is_on INTEGER DEFAULT 1,
                    is_plottable INTEGER DEFAULT 1,
                    description TEXT DEFAULT '',
                    handle TEXT
                )
            ''')

            # Block table
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    entity_count INTEGER DEFAULT 0,
                    is_layout INTEGER DEFAULT 0,
                    is_xref INTEGER DEFAULT 0,
                    origin_x REAL DEFAULT 0,
                    origin_y REAL DEFAULT 0,
                    origin_z REAL DEFAULT 0,
                    path TEXT DEFAULT ''
                )
            ''')

            # Text patterns
            c.execute('''
                CREATE TABLE IF NOT EXISTS text_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT NOT NULL,
                    count INTEGER DEFAULT 0,
                    drawing TEXT,
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Query history
            c.execute('''
                CREATE TABLE IF NOT EXISTS query_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    result_count INTEGER,
                    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Drawing snapshots
            c.execute('''
                CREATE TABLE IF NOT EXISTS drawing_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drawing_name TEXT,
                    entity_count INTEGER,
                    layer_count INTEGER,
                    block_count INTEGER,
                    type_stats TEXT DEFAULT '{}',
                    snapshot_data TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            logger.info(f"数据库已初始化: {self.db_path}")

    # ── Entity CRUD ────────────────────────────────────────────

    @staticmethod
    def _point3(value: Any) -> Optional[List[float]]:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            return [
                float(value[0]),
                float(value[1]),
                float(value[2]) if len(value) > 2 else 0.0,
            ]
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_point(cls, geometry: Dict[str, Any], *keys: str) -> Optional[List[float]]:
        for key in keys:
            point = cls._point3(geometry.get(key))
            if point is not None:
                return point
        return None

    @classmethod
    def _point_list(cls, value: Any) -> List[List[float]]:
        if not isinstance(value, (list, tuple)) or not value:
            return []
        if all(isinstance(v, (int, float)) for v in value):
            step = 3 if len(value) % 3 == 0 else 2
            points = []
            for i in range(0, len(value) - step + 1, step):
                point = cls._point3(value[i:i + step])
                if point is not None:
                    points.append(point)
            return points
        points = []
        for item in value:
            point = cls._point3(item)
            if point is not None:
                points.append(point)
        return points

    @staticmethod
    def _distance(a: List[float], b: List[float]) -> float:
        return math.sqrt(
            (b[0] - a[0]) ** 2
            + (b[1] - a[1]) ** 2
            + (b[2] - a[2]) ** 2
        )

    @staticmethod
    def _polygon_area(points: List[List[float]]) -> float:
        if len(points) < 3:
            return 0.0
        area = 0.0
        for i, p in enumerate(points):
            q = points[(i + 1) % len(points)]
            area += p[0] * q[1] - q[0] * p[1]
        return abs(area) / 2.0

    @classmethod
    def _derive_bbox(cls, entity_type: str,
                     geometry: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
        geometry = geometry or {}
        raw_bbox = geometry.get("bbox") or geometry.get("bounds")
        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) >= 4:
            try:
                return (
                    float(raw_bbox[0]),
                    float(raw_bbox[1]),
                    float(raw_bbox[2]),
                    float(raw_bbox[3]),
                )
            except (TypeError, ValueError):
                pass

        primitives, _, _ = cls._derive_topology(entity_type, geometry)
        xs: List[float] = []
        ys: List[float] = []
        for primitive in primitives:
            for x_key, y_key in (("x", "y"), ("x2", "y2")):
                x = primitive.get(x_key)
                y = primitive.get(y_key)
                if x is not None and y is not None:
                    xs.append(float(x))
                    ys.append(float(y))
            radius = primitive.get("radius")
            x = primitive.get("x")
            y = primitive.get("y")
            if radius is not None and x is not None and y is not None:
                r = float(radius)
                xs.extend([float(x) - r, float(x) + r])
                ys.extend([float(y) - r, float(y) + r])
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    @classmethod
    def _derive_topology(cls, entity_type: str,
                         geometry: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                            List[Dict[str, Any]],
                                                            Dict[str, Any]]:
        primitives: List[Dict[str, Any]] = []
        relations: List[Dict[str, Any]] = []

        def add_primitive(key: str, primitive_type: str, role: str = "",
                          sequence_index: int = 0,
                          point: Optional[List[float]] = None,
                          point2: Optional[List[float]] = None,
                          parent_key: str = "",
                          radius: Optional[float] = None,
                          length: Optional[float] = None,
                          area: Optional[float] = None,
                          is_closed: bool = False,
                          properties: Optional[Dict[str, Any]] = None):
            primitives.append({
                "primitive_key": key,
                "primitive_type": primitive_type,
                "role": role,
                "sequence_index": sequence_index,
                "parent_key": parent_key,
                "x": point[0] if point else None,
                "y": point[1] if point else None,
                "z": point[2] if point else None,
                "x2": point2[0] if point2 else None,
                "y2": point2[1] if point2 else None,
                "z2": point2[2] if point2 else None,
                "radius": radius,
                "length": length,
                "area": area,
                "is_closed": int(is_closed),
                "properties": properties or {},
            })

        def add_relation(from_key: str, to_key: str, relation_type: str,
                         sequence_index: int = 0,
                         properties: Optional[Dict[str, Any]] = None):
            relations.append({
                "from_key": from_key,
                "to_key": to_key,
                "relation_type": relation_type,
                "sequence_index": sequence_index,
                "properties": properties or {},
            })

        geometry = geometry or {}
        etype = (entity_type or "").lower()
        start = cls._first_point(geometry, "start_point", "start")
        end = cls._first_point(geometry, "end_point", "end")
        center = cls._first_point(geometry, "center")
        single_point = cls._first_point(
            geometry, "point", "position", "insertion_point", "insert_point"
        )
        vertices = (
            cls._point_list(geometry.get("vertices"))
            or cls._point_list(geometry.get("points"))
            or cls._point_list(geometry.get("fit_points"))
        )
        closed = bool(geometry.get("closed", False))

        if start and end:
            length = geometry.get("length")
            if length is None:
                length = cls._distance(start, end)
            add_primitive("p0", "point", "start", 0, point=start)
            add_primitive("p1", "point", "end", 1, point=end)
            add_primitive("e0", "line", "segment", 0, point=start,
                          point2=end, length=float(length))
            add_relation("e0", "p0", "starts_at")
            add_relation("e0", "p1", "ends_at")

        if center:
            add_primitive("center", "point", "center", 0, point=center)
        if single_point and not center:
            add_primitive("p0", "point", "location", 0, point=single_point)

        if "circle" in etype:
            radius = geometry.get("radius")
            add_primitive("c0", "curve", "circle", 0, point=center,
                          radius=radius, is_closed=True)
            if center:
                add_relation("c0", "center", "has_center")
            if radius is not None:
                area = math.pi * float(radius) ** 2
                add_primitive("s0", "surface", "enclosed_area", 0,
                              parent_key="c0", area=area, is_closed=True)
                add_relation("s0", "c0", "bounded_by")
        elif "arc" in etype:
            add_primitive("c0", "curve", "arc", 0, point=center,
                          radius=geometry.get("radius"),
                          properties={
                              "start_angle": geometry.get("start_angle"),
                              "end_angle": geometry.get("end_angle"),
                          })
            if center:
                add_relation("c0", "center", "has_center")
        elif "ellipse" in etype:
            add_primitive("c0", "curve", "ellipse", 0, point=center,
                          is_closed=True,
                          properties={
                              "major_axis": geometry.get("major_axis"),
                              "radius_ratio": geometry.get("radius_ratio"),
                          })
            if center:
                add_relation("c0", "center", "has_center")

        if vertices:
            for i, point in enumerate(vertices):
                add_primitive(f"p{i}", "point", "vertex", i, point=point)
            edge_count = len(vertices) if closed and len(vertices) > 2 else max(len(vertices) - 1, 0)
            for i in range(edge_count):
                a = vertices[i]
                b = vertices[(i + 1) % len(vertices)]
                add_primitive(f"e{i}", "line", "edge", i,
                              point=a, point2=b, length=cls._distance(a, b))
                add_relation(f"e{i}", f"p{i}", "starts_at", i)
                add_relation(f"e{i}", f"p{(i + 1) % len(vertices)}", "ends_at", i)
            is_surface = (
                closed
                or "solid" in etype
                or "region" in etype
                or "hatch" in etype
                or geometry.get("area") is not None
            )
            if is_surface and len(vertices) >= 3:
                area = geometry.get("area")
                if area is None:
                    area = cls._polygon_area(vertices)
                add_primitive("s0", "surface", "bounded_area", 0,
                              area=float(area), is_closed=True,
                              properties={"vertex_count": len(vertices)})
                for i in range(edge_count):
                    add_relation("s0", f"e{i}", "bounded_by", i)

        if "3dsolid" in etype:
            add_primitive("solid0", "solid", "body", 0, point=center,
                          properties={k: v for k, v in geometry.items()
                                      if k not in {"center", "vertices"}})

        if "hatch" in etype and not any(p["primitive_type"] == "surface" for p in primitives):
            add_primitive("s0", "surface", "hatch_area", 0,
                          area=geometry.get("area"), is_closed=True,
                          properties={"pattern": geometry.get("pattern")})

        point_count = sum(1 for p in primitives if p["primitive_type"] == "point")
        line_count = sum(1 for p in primitives if p["primitive_type"] == "line")
        curve_count = sum(1 for p in primitives if p["primitive_type"] == "curve")
        surface_count = sum(1 for p in primitives if p["primitive_type"] == "surface")
        solid_count = sum(1 for p in primitives if p["primitive_type"] == "solid")
        dimensionality = 3 if solid_count else 2 if surface_count else 1 if (line_count or curve_count) else 0
        length = geometry.get("length")
        if length is None:
            lengths = [p["length"] for p in primitives if p["primitive_type"] == "line" and p.get("length") is not None]
            length = sum(lengths) if lengths else None
        area = geometry.get("area")
        if area is None:
            areas = [p["area"] for p in primitives if p["primitive_type"] == "surface" and p.get("area") is not None]
            area = sum(areas) if areas else None
        is_closed = bool(closed or any(p["is_closed"] for p in primitives))

        summary = {
            "dimensionality": dimensionality,
            "point_count": point_count,
            "line_count": line_count,
            "curve_count": curve_count,
            "surface_count": surface_count,
            "solid_count": solid_count,
            "is_closed": int(is_closed),
            "length": float(length) if length is not None else None,
            "area": float(area) if area is not None else None,
            "summary": (
                f"{entity_type}: {point_count} points, {line_count} lines, "
                f"{curve_count} curves, {surface_count} surfaces, {solid_count} solids"
            ),
        }
        return primitives, relations, summary

    def _replace_entity_topology(self, conn: sqlite3.Connection, handle: str,
                                 entity_type: str,
                                 geometry: Dict[str, Any]):
        primitives, relations, summary = self._derive_topology(entity_type, geometry or {})
        conn.execute("DELETE FROM cad_geometry_relations WHERE entity_handle = ?", (handle,))
        conn.execute("DELETE FROM cad_geometry_primitives WHERE entity_handle = ?", (handle,))
        conn.execute("DELETE FROM cad_topology_summary WHERE entity_handle = ?", (handle,))

        for primitive in primitives:
            conn.execute('''
                INSERT INTO cad_geometry_primitives
                    (entity_handle, primitive_key, primitive_type, role,
                     sequence_index, parent_key, x, y, z, x2, y2, z2,
                     radius, length, area, is_closed, properties)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                handle, primitive["primitive_key"], primitive["primitive_type"],
                primitive["role"], primitive["sequence_index"], primitive["parent_key"],
                primitive["x"], primitive["y"], primitive["z"],
                primitive["x2"], primitive["y2"], primitive["z2"],
                primitive["radius"], primitive["length"], primitive["area"],
                primitive["is_closed"],
                json.dumps(primitive["properties"], ensure_ascii=False),
            ))
        for relation in relations:
            conn.execute('''
                INSERT OR IGNORE INTO cad_geometry_relations
                    (entity_handle, from_key, to_key, relation_type,
                     sequence_index, properties)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                handle, relation["from_key"], relation["to_key"],
                relation["relation_type"], relation["sequence_index"],
                json.dumps(relation["properties"], ensure_ascii=False),
            ))
        conn.execute('''
            INSERT INTO cad_topology_summary
                (entity_handle, dimensionality, point_count, line_count,
                 curve_count, surface_count, solid_count, is_closed,
                 length, area, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            handle, summary["dimensionality"], summary["point_count"],
            summary["line_count"], summary["curve_count"],
            summary["surface_count"], summary["solid_count"],
            summary["is_closed"], summary["length"], summary["area"],
            summary["summary"],
        ))

    def upsert_entity(self, handle: str, name: str, entity_type: str,
                      layer: str = "0", color: int = 256,
                      linetype: str = "ByLayer", properties: Dict = None,
                      geometry: Dict = None,
                      bbox: Optional[Tuple[float,float,float,float]] = None) -> bool:
        try:
            geometry = geometry or {}
            if bbox is None:
                bbox = self._derive_bbox(entity_type, geometry)
            with self._conn() as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO cad_entities
                        (handle, name, type, layer, color, linetype,
                         properties, geometry, bbox_min_x, bbox_min_y,
                         bbox_max_x, bbox_max_y)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(handle) DO UPDATE SET
                        name=excluded.name, type=excluded.type,
                        layer=excluded.layer, color=excluded.color,
                        linetype=excluded.linetype,
                        properties=excluded.properties,
                        geometry=excluded.geometry,
                        bbox_min_x=excluded.bbox_min_x,
                        bbox_min_y=excluded.bbox_min_y,
                        bbox_max_x=excluded.bbox_max_x,
                        bbox_max_y=excluded.bbox_max_y
                ''', (
                    handle, name, entity_type, layer, color, linetype,
                    json.dumps(properties or {}, ensure_ascii=False),
                    json.dumps(geometry or {}, ensure_ascii=False),
                    bbox[0] if bbox else None, bbox[1] if bbox else None,
                    bbox[2] if bbox else None, bbox[3] if bbox else None,
                ))
                self._replace_entity_topology(conn, handle, entity_type, geometry or {})
            return True
        except Exception as e:
            logger.error(f"插入实体 {handle} 失败: {e}")
            return False

    def upsert_entities_batch(self, entities: List[Dict[str, Any]]) -> int:
        """Batch insert/update entities. Returns count of successfully upserted."""
        count = 0
        for ent in entities:
            if self.upsert_entity(
                handle=ent.get("handle", ""),
                name=ent.get("name", ent.get("type", "Unknown")),
                entity_type=ent.get("type", "Unknown"),
                layer=ent.get("layer", "0"),
                color=ent.get("color", 256),
                linetype=ent.get("linetype", "ByLayer"),
                properties=ent.get("properties"),
                geometry=ent.get("geometry"),
                bbox=ent.get("bbox"),
            ):
                count += 1
        return count

    def get_entity(self, handle: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_entities WHERE handle = ?", (handle,)
            ).fetchone()
            if row:
                d = dict(row)
                d['properties'] = json.loads(d.get('properties', '{}'))
                d['geometry'] = json.loads(d.get('geometry', '{}'))
                return d
        return None

    def delete_entity(self, handle: str) -> bool:
        with self._conn() as conn:
            conn.execute("DELETE FROM cad_geometry_relations WHERE entity_handle = ?", (handle,))
            conn.execute("DELETE FROM cad_geometry_primitives WHERE entity_handle = ?", (handle,))
            conn.execute("DELETE FROM cad_topology_summary WHERE entity_handle = ?", (handle,))
            conn.execute("DELETE FROM cad_entities WHERE handle = ?", (handle,))
        return True

    def clear_entities(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM cad_geometry_relations")
            conn.execute("DELETE FROM cad_geometry_primitives")
            conn.execute("DELETE FROM cad_topology_summary")
            conn.execute("DELETE FROM cad_entities")

    def get_entity_topology(self, handle: str) -> Dict[str, Any]:
        with self._conn() as conn:
            summary = conn.execute(
                "SELECT * FROM cad_topology_summary WHERE entity_handle = ?",
                (handle,),
            ).fetchone()
            primitives = conn.execute(
                """SELECT * FROM cad_geometry_primitives
                   WHERE entity_handle = ?
                   ORDER BY primitive_type, sequence_index, primitive_key""",
                (handle,),
            ).fetchall()
            relations = conn.execute(
                """SELECT * FROM cad_geometry_relations
                   WHERE entity_handle = ?
                   ORDER BY sequence_index, relation_type""",
                (handle,),
            ).fetchall()

        def decode(row):
            d = dict(row)
            if "properties" in d:
                d["properties"] = json.loads(d.get("properties") or "{}")
            return d

        return {
            "summary": dict(summary) if summary else None,
            "primitives": [decode(r) for r in primitives],
            "relations": [decode(r) for r in relations],
        }

    def get_topology_summary(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute('''
                SELECT e.handle, e.name, e.type, e.layer,
                       t.dimensionality, t.point_count, t.line_count,
                       t.curve_count, t.surface_count, t.solid_count,
                       t.is_closed, t.length, t.area, t.summary
                FROM cad_topology_summary t
                JOIN cad_entities e ON e.handle = t.entity_handle
                ORDER BY t.dimensionality DESC, e.type, e.handle
                LIMIT ?
            ''', (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ── Queries ─────────────────────────────────────────────────

    def query_entities(self,
                       entity_type: Optional[str] = None,
                       layer: Optional[str] = None,
                       color: Optional[int] = None,
                       text_contains: Optional[str] = None,
                       bbox: Optional[Tuple[float,float,float,float]] = None,
                       limit: int = 1000,
                       offset: int = 0) -> List[Dict[str, Any]]:
        """Flexible entity query with spatial and property filters."""
        conditions = []
        params = []

        if entity_type:
            conditions.append("type = ?")
            params.append(entity_type)
        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        if color is not None:
            conditions.append("color = ?")
            params.append(color)
        if text_contains:
            conditions.append(
                "(json_extract(properties, '$.text_string') LIKE ? "
                "OR json_extract(geometry, '$.text_string') LIKE ? "
                "OR json_extract(geometry, '$.text') LIKE ?)"
            )
            params.extend([f"%{text_contains}%"] * 3)
        if bbox:
            conditions.append(
                "bbox_min_x >= ? AND bbox_min_y >= ? AND bbox_max_x <= ? AND bbox_max_y <= ?")
            params.extend(bbox)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT * FROM cad_entities {where} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def query_near_point(self, x: float, y: float, radius: float,
                         entity_type: Optional[str] = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
        """Find entities within radius of a point (simplified spatial)."""
        # Simple centroid-based approximation
        with self._conn() as conn:
            type_filter = "AND type = ?" if entity_type else ""
            params = [
                x, x, y, y,
                x + radius, x - radius,
                y + radius, y - radius,
            ]
            if entity_type:
                params.append(entity_type)
            params.append(limit)
            query = f'''
                SELECT *,
                       ((bbox_min_x + bbox_max_x)/2 - ?) * ((bbox_min_x + bbox_max_x)/2 - ?) +
                       ((bbox_min_y + bbox_max_y)/2 - ?) * ((bbox_min_y + bbox_max_y)/2 - ?)
                       AS dist_sq
                FROM cad_entities
                WHERE bbox_min_x IS NOT NULL
                  AND bbox_min_x <= ? AND bbox_max_x >= ?
                  AND bbox_min_y <= ? AND bbox_max_y >= ?
                  {type_filter}
                ORDER BY dist_sq
                LIMIT ?
            '''
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                dist_sq = d.pop('dist_sq', 0)
                if dist_sq <= radius * radius:
                    results.append(d)
            return results[:limit]

    def count_entities(self, entity_type: Optional[str] = None,
                       layer: Optional[str] = None) -> int:
        conditions = []
        params = []
        if entity_type:
            conditions.append("type = ?")
            params.append(entity_type)
        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM cad_entities {where}", params).fetchone()
            return row["cnt"] if row else 0

    def get_type_stats(self) -> Dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) as cnt FROM cad_entities GROUP BY type ORDER BY cnt DESC"
            ).fetchall()
            return {r["type"]: r["cnt"] for r in rows}

    def get_layer_stats(self) -> Dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT layer, COUNT(*) as cnt FROM cad_entities GROUP BY layer ORDER BY cnt DESC"
            ).fetchall()
            return {r["layer"]: r["cnt"] for r in rows}

    # ── Layer CRUD ──────────────────────────────────────────────

    def save_layers(self, layers: List[Dict[str, Any]]):
        with self._conn() as conn:
            for layer in layers:
                conn.execute('''
                    INSERT OR REPLACE INTO cad_layers
                        (name, color, linetype, lineweight, is_frozen,
                         is_locked, is_on, is_plottable, description, handle)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    layer.get("name", ""),
                    layer.get("color", 7),
                    layer.get("linetype", "Continuous"),
                    layer.get("lineweight", -1.0),
                    int(layer.get("is_frozen", False)),
                    int(layer.get("is_locked", False)),
                    int(layer.get("is_on", True)),
                    int(layer.get("is_plottable", True)),
                    layer.get("description", ""),
                    layer.get("handle", ""),
                ))

    def get_layers(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM cad_layers ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    # ── Block CRUD ──────────────────────────────────────────────

    def save_blocks(self, blocks: List[Dict[str, Any]]):
        with self._conn() as conn:
            for blk in blocks:
                conn.execute('''
                    INSERT OR REPLACE INTO cad_blocks
                        (name, entity_count, is_layout, is_xref, origin_x, origin_y, origin_z, path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    blk.get("name", ""),
                    blk.get("count", 0),
                    int(blk.get("is_layout", False)),
                    int(blk.get("is_xref", False)),
                    blk.get("origin", [0,0,0])[0] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("origin", [0,0,0])[1] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("origin", [0,0,0])[2] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("path", ""),
                ))

    def get_blocks(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM cad_blocks ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    # ── Text Patterns ───────────────────────────────────────────

    def save_text_pattern(self, pattern: str, count: int, drawing: str = ""):
        with self._conn() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO text_patterns (pattern, count, drawing)
                VALUES (?, ?, ?)
            ''', (pattern, count, drawing))

    def get_text_patterns(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM text_patterns ORDER BY scanned_at DESC").fetchall()
            return [dict(r) for r in rows]

    # ── General SQL ─────────────────────────────────────────────

    @staticmethod
    def _is_read_only_sql(query: str) -> bool:
        stripped = (query or "").strip().lstrip("\ufeff")
        if not stripped:
            return False
        first_token = stripped.split(None, 1)[0].upper()
        return first_token in {"SELECT", "WITH", "PRAGMA", "EXPLAIN"}

    @staticmethod
    def _read_only_authorizer(action: int, arg1: str, arg2: str,
                              db_name: str, source: str) -> int:
        denied_names = {
            "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
            "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE",
            "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
            "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW",
            "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE",
            "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
            "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW",
            "SQLITE_ALTER_TABLE", "SQLITE_REINDEX",
            "SQLITE_ANALYZE", "SQLITE_ATTACH", "SQLITE_DETACH",
            "SQLITE_TRANSACTION",
        }
        denied_actions = {
            getattr(sqlite3, name) for name in denied_names
            if hasattr(sqlite3, name)
        }
        if action in denied_actions:
            return sqlite3.SQLITE_DENY
        if action == getattr(sqlite3, "SQLITE_PRAGMA", -1):
            allowed_pragmas = {
                "table_info", "index_info", "index_list", "foreign_key_list",
                "database_list", "schema_version", "quick_check",
                "integrity_check",
            }
            if (arg1 or "").lower() not in allowed_pragmas:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def execute(self, query: str, params: tuple = (),
                read_only: bool = False) -> Dict[str, Any]:
        """Execute SQL and return rows for result-producing statements.

        Set read_only=True for MCP-facing query tools; writes are denied by
        token screening plus SQLite's authorizer.
        """
        if read_only and not self._is_read_only_sql(query):
            raise ValueError("Only read-only SELECT/WITH/PRAGMA/EXPLAIN SQL is allowed")
        with self._conn() as conn:
            c = conn.cursor()
            if read_only:
                conn.set_authorizer(self._read_only_authorizer)
            try:
                c.execute(query, params)
            finally:
                if read_only:
                    conn.set_authorizer(None)

            if c.description:
                columns = [desc[0] for desc in c.description] if c.description else []
                rows = [dict(zip(columns, row)) for row in c.fetchall()]
                if not read_only:
                    conn.execute(
                        "INSERT INTO query_history (query, result_count) VALUES (?, ?)",
                        (query[:500], len(rows)))
                return {"columns": columns, "rows": rows, "count": len(rows)}
            else:
                if read_only:
                    return {"columns": [], "rows": [], "count": 0}
                affected = c.rowcount
                conn.execute(
                    "INSERT INTO query_history (query, result_count) VALUES (?, ?)",
                    (query[:500], affected))
                return {"affected_rows": affected}

    def get_tables(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return [r["name"] for r in rows]

    def get_table_schema(self, table: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                raise ValueError(f"Unknown table: {table}")
            rows = conn.execute(
                f"PRAGMA table_info({self._quote_identifier(table)})"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Snapshot ─────────────────────────────────────────────────

    def create_snapshot(self, drawing_name: str, entity_count: int,
                        layer_count: int, block_count: int,
                        type_stats: Dict[str, int],
                        snapshot_data: Dict = None) -> int:
        with self._conn() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO drawing_snapshots
                    (drawing_name, entity_count, layer_count, block_count,
                     type_stats, snapshot_data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                drawing_name, entity_count, layer_count, block_count,
                json.dumps(type_stats, ensure_ascii=False),
                json.dumps(snapshot_data or {}, ensure_ascii=False),
            ))
            return c.lastrowid

    def get_recent_snapshots(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM drawing_snapshots ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]


# ── Module-level singleton ──────────────────────────────────────

_db = None

def get_database(db_path: Optional[str] = None) -> CADDatabase:
    global _db
    if _db is None:
        _db = CADDatabase(db_path)
    return _db
