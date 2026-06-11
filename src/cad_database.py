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

    def upsert_entity(self, handle: str, name: str, entity_type: str,
                      layer: str = "0", color: int = 256,
                      linetype: str = "ByLayer", properties: Dict = None,
                      geometry: Dict = None,
                      bbox: Optional[Tuple[float,float,float,float]] = None) -> bool:
        try:
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
            conn.execute("DELETE FROM cad_entities WHERE handle = ?", (handle,))
        return True

    def clear_entities(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM cad_entities")

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
            conditions.append("json_extract(properties, '$.text_string') LIKE ?")
            params.append(f"%{text_contains}%")
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
            params = [x - radius, x + radius, y - radius, y + radius]
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

    def execute(self, query: str, params: tuple = ()) -> Dict[str, Any]:
        """Execute arbitrary SQL. Returns rows + columns for SELECT,
        or affected_rows for other statements."""
        with self._conn() as conn:
            c = conn.cursor()
            c.execute(query, params)

            if query.strip().upper().startswith("SELECT"):
                columns = [desc[0] for desc in c.description] if c.description else []
                rows = [dict(zip(columns, row)) for row in c.fetchall()]
                # Log query
                conn.execute(
                    "INSERT INTO query_history (query, result_count) VALUES (?, ?)",
                    (query[:500], len(rows)))
                return {"columns": columns, "rows": rows, "count": len(rows)}
            else:
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
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
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
