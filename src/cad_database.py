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
import uuid
import hashlib
import contextvars
import time
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple, Union
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 数据库存储在 Agent 的工作目录（MCP 客户端的 cwd），而非 MCP 程序目录
# 这样每个 Agent 实例的数据库互相隔离
# 使用函数而非模块级常量，确保每次获取时都是当前 cwd
_KEY_SEPARATOR = "\x1f"
_DEFAULT_SQL_MAX_ROWS = 1000
_DEFAULT_SQL_TIMEOUT_MS = 5000
_DEFAULT_SQL_MAX_RESULT_BYTES = 1_000_000
_MAX_SQL_MAX_ROWS = 50_000
_MAX_SQL_TIMEOUT_MS = 60_000
_MAX_SQL_MAX_RESULT_BYTES = 20_000_000

_DRAWING_SCOPED_TABLES = {
    "cad_entities",
    "cad_geometry_primitives",
    "cad_geometry_relations",
    "cad_topology_summary",
    "cad_layers",
    "cad_blocks",
    "cad_drawings",
}
_THREAD_SCOPED_TABLES = {
    "cad_spatial_annotations",
    "text_patterns",
    "query_history",
    "drawing_snapshots",
    "cad_semantic_objects",
    "cad_semantic_relations",
    "cad_constraints",
    "cad_validation_reports",
    "cad_view_snapshots",
    "cad_image_traces",
}
_WORKSPACE_SCOPED_TABLES = {
    "cad_workspaces",
    "cad_conversations",
    "cad_threads",
}
_READ_ONLY_SCOPED_TABLES = (
    _DRAWING_SCOPED_TABLES | _THREAD_SCOPED_TABLES | _WORKSPACE_SCOPED_TABLES
)
_READ_ONLY_SCOPED_VIEWS = set(_READ_ONLY_SCOPED_TABLES)
_READ_ONLY_DENIED_MAIN_TABLES = _READ_ONLY_SCOPED_TABLES | {"sqlite_sequence"}


@dataclass(frozen=True)
class CADWorkspaceContext:
    """Logical database scope for one MCP workspace/thread/drawing."""

    workspace_id: str
    workspace_root: str
    conversation_id: str
    thread_id: str
    drawing_id: str
    drawing_name: str = "active"
    drawing_path: str = ""


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _env_int(name: str, default: int,
             minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _get_default_workspace_root() -> str:
    configured = _env_first("CAD_MCP_WORKSPACE_ROOT", default=os.getcwd())
    return str(Path(configured).resolve())


def _get_default_db_path() -> str:
    workspace_root = Path(_get_default_workspace_root())
    data_dir = workspace_root / ".cad_mcp"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "workspace.db")


def _default_context() -> CADWorkspaceContext:
    workspace_root = _get_default_workspace_root()
    workspace_id = _env_first(
        "CAD_MCP_WORKSPACE_ID",
        "MCP_WORKSPACE_ID",
        default=_stable_id("ws", workspace_root.lower()),
    )
    conversation_id = _env_first(
        "CAD_MCP_CONVERSATION_ID",
        "MCP_CONVERSATION_ID",
        "CONVERSATION_ID",
        default="default-conversation",
    )
    thread_id = _env_first(
        "CAD_MCP_THREAD_ID",
        "MCP_THREAD_ID",
        "THREAD_ID",
        default="default-thread",
    )
    drawing_name = _env_first("CAD_MCP_DRAWING_NAME", default="active")
    drawing_path = _env_first("CAD_MCP_DRAWING_PATH", default="")
    drawing_id = _env_first(
        "CAD_MCP_DRAWING_ID",
        default=_stable_id("dwg", (drawing_path or drawing_name).lower()),
    )
    return CADWorkspaceContext(
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        conversation_id=conversation_id,
        thread_id=thread_id,
        drawing_id=drawing_id,
        drawing_name=drawing_name,
        drawing_path=drawing_path,
    )


class CADDatabase:
    """Manages the SQLite database for CAD metadata persistence."""

    def __init__(self, db_path: Optional[str] = None):
        self._using_default_db_path = db_path is None
        self.db_path = db_path or _get_default_db_path()
        self._active_context = _default_context()
        self._context_var: contextvars.ContextVar[CADWorkspaceContext] = (
            contextvars.ContextVar("cad_workspace_context")
        )
        self._init_schema()
        with self._conn() as conn:
            self._migrate_workspace_schema(conn)
            self._ensure_context_rows(conn)
        self._warn_if_legacy_database_present()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._register_context(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _register_context(self, conn: sqlite3.Connection) -> None:
        ctx = self.get_context()
        conn.create_function("cad_current_workspace_id", 0, lambda: ctx.workspace_id)
        conn.create_function("cad_current_drawing_id", 0, lambda: ctx.drawing_id)
        conn.create_function("cad_current_conversation_id", 0, lambda: ctx.conversation_id)
        conn.create_function("cad_current_thread_id", 0, lambda: ctx.thread_id)

    def get_context(self) -> CADWorkspaceContext:
        try:
            return self._context_var.get()
        except LookupError:
            return self._active_context

    def get_context_dict(self) -> Dict[str, Any]:
        data = asdict(self.get_context())
        data["db_path"] = self.db_path
        return data

    def get_legacy_database_status(self) -> Dict[str, Any]:
        legacy_path = Path(self.get_context().workspace_root) / "autocad_data.db"
        active_path = Path(self.db_path).resolve()
        return {
            "legacy_path": str(legacy_path),
            "active_db_path": str(active_path),
            "exists": legacy_path.exists(),
            "is_active_db": legacy_path.exists() and legacy_path.resolve() == active_path,
            "size_bytes": legacy_path.stat().st_size if legacy_path.exists() else 0,
            "recommendation": (
                "Archive or delete autocad_data.db after confirming no old MCP "
                "process still uses it; the active workspace database is "
                ".cad_mcp/workspace.db."
                if legacy_path.exists() and legacy_path.resolve() != active_path
                else ""
            ),
        }

    def _warn_if_legacy_database_present(self) -> None:
        status = self.get_legacy_database_status()
        if self._using_default_db_path and status["exists"] and not status["is_active_db"]:
            logger.warning(
                "Legacy CAD database found at %s; active database is %s. "
                "Archive or delete the legacy file after migration checks.",
                status["legacy_path"],
                status["active_db_path"],
            )

    def configure_context(self,
                          workspace_root: Optional[str] = None,
                          workspace_id: Optional[str] = None,
                          conversation_id: Optional[str] = None,
                          thread_id: Optional[str] = None,
                          drawing_id: Optional[str] = None,
                          drawing_name: Optional[str] = None,
                          drawing_path: Optional[str] = None) -> Dict[str, Any]:
        old = self.get_context()
        new_root = str(Path(workspace_root).resolve()) if workspace_root else old.workspace_root
        new_workspace_id = workspace_id or old.workspace_id
        if workspace_root and not workspace_id:
            new_workspace_id = _stable_id("ws", new_root.lower())
        new_drawing_name = drawing_name if drawing_name is not None else old.drawing_name
        new_drawing_path = drawing_path if drawing_path is not None else old.drawing_path
        new_drawing_id = drawing_id or old.drawing_id
        if (drawing_name is not None or drawing_path is not None) and drawing_id is None:
            new_drawing_id = self._make_drawing_id(new_drawing_name, new_drawing_path)
        ctx = CADWorkspaceContext(
            workspace_id=new_workspace_id,
            workspace_root=new_root,
            conversation_id=conversation_id or old.conversation_id,
            thread_id=thread_id or old.thread_id,
            drawing_id=new_drawing_id,
            drawing_name=new_drawing_name or "active",
            drawing_path=new_drawing_path or "",
        )
        self._active_context = ctx
        self._context_var.set(ctx)
        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
        return self.get_context_dict()

    def activate_drawing(self, name: str = "active",
                         path: str = "",
                         drawing_id: Optional[str] = None) -> Dict[str, Any]:
        return self.configure_context(
            drawing_id=drawing_id,
            drawing_name=name or "active",
            drawing_path=path or "",
        )

    def list_workspace_drawings(self, limit: int = 100) -> List[Dict[str, Any]]:
        ctx = self.get_context()
        with self._conn() as conn:
            rows = conn.execute('''
                SELECT drawing_id, drawing_name, drawing_path, active,
                       created_at, updated_at
                FROM cad_drawings
                WHERE workspace_id = ?
                ORDER BY updated_at DESC, drawing_name
                LIMIT ?
            ''', (ctx.workspace_id, max(1, min(int(limit or 100), 1000)))).fetchall()
            return [dict(row) for row in rows]

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set:
        rows = conn.execute(
            f"PRAGMA table_info({self._quote_identifier(table)})"
        ).fetchall()
        return {row["name"] for row in rows}

    def _ensure_column(self, conn: sqlite3.Connection, table: str,
                       column: str, definition: str) -> None:
        if column not in self._table_columns(conn, table):
            try:
                conn.execute(
                    f"ALTER TABLE {self._quote_identifier(table)} "
                    f"ADD COLUMN {self._quote_identifier(column)} {definition}"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _migrate_workspace_schema(self, conn: sqlite3.Connection) -> None:
        ctx = self.get_context()
        for column, definition in {
            "workspace_id": "TEXT DEFAULT ''",
            "drawing_id": "TEXT DEFAULT ''",
            "native_handle": "TEXT DEFAULT ''",
        }.items():
            self._ensure_column(conn, "cad_entities", column, definition)

        for table in ("cad_spatial_annotations", "text_patterns",
                      "query_history", "drawing_snapshots"):
            self._ensure_column(conn, table, "workspace_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, table, "drawing_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, table, "conversation_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, table, "thread_id", "TEXT DEFAULT ''")

        self._ensure_column(conn, "query_history", "duration_ms", "REAL")
        self._ensure_column(conn, "query_history", "truncated", "INTEGER DEFAULT 0")
        self._ensure_column(conn, "query_history", "error", "TEXT DEFAULT ''")

        self._ensure_column(conn, "cad_spatial_annotations",
                            "native_annotation_id", "TEXT DEFAULT ''")
        self._ensure_column(conn, "cad_spatial_annotations",
                            "native_entity_handle", "TEXT DEFAULT ''")

        for table in ("cad_layers", "cad_blocks"):
            self._ensure_column(conn, table, "workspace_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, table, "drawing_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, table, "native_name", "TEXT DEFAULT ''")

        conn.execute('CREATE INDEX IF NOT EXISTS idx_entities_scope ON cad_entities(workspace_id, drawing_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_entities_native_handle ON cad_entities(workspace_id, drawing_id, native_handle)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_annotations_scope ON cad_spatial_annotations(workspace_id, drawing_id, conversation_id, thread_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_layers_scope ON cad_layers(workspace_id, drawing_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_blocks_scope ON cad_blocks(workspace_id, drawing_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_text_patterns_scope ON text_patterns(workspace_id, drawing_id, conversation_id, thread_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_query_history_scope ON query_history(workspace_id, drawing_id, conversation_id, thread_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_scope ON drawing_snapshots(workspace_id, drawing_id, conversation_id, thread_id)')

        self._adopt_legacy_rows(conn, ctx)

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _adopt_legacy_rows(self, conn: sqlite3.Connection,
                           ctx: CADWorkspaceContext) -> None:
        entity_rows = conn.execute('''
            SELECT handle FROM cad_entities
            WHERE workspace_id = '' OR drawing_id = '' OR native_handle = ''
        ''').fetchall()
        for row in entity_rows:
            old_handle = row["handle"]
            if _KEY_SEPARATOR in old_handle:
                native_handle = old_handle.split(_KEY_SEPARATOR)[-1]
                new_handle = old_handle
            else:
                native_handle = old_handle
                new_handle = self._entity_key(native_handle, ctx)
            if new_handle != old_handle:
                conn.execute('''
                    INSERT OR IGNORE INTO cad_entities
                        (handle, name, type, layer, color, linetype,
                         linetype_scale, lineweight, visible,
                         bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                         properties, geometry, scanned_at,
                         workspace_id, drawing_id, native_handle)
                    SELECT ?, name, type, layer, color, linetype,
                           linetype_scale, lineweight, visible,
                           bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                           properties, geometry, scanned_at,
                           ?, ?, ?
                    FROM cad_entities WHERE handle = ?
                ''', (
                    new_handle, ctx.workspace_id, ctx.drawing_id,
                    native_handle, old_handle,
                ))
                for table in (
                    "cad_geometry_primitives",
                    "cad_geometry_relations",
                    "cad_topology_summary",
                ):
                    conn.execute(
                        f"UPDATE {table} SET entity_handle = ? WHERE entity_handle = ?",
                        (new_handle, old_handle),
                    )
                conn.execute("DELETE FROM cad_entities WHERE handle = ?", (old_handle,))
            else:
                conn.execute('''
                    UPDATE cad_entities
                    SET workspace_id = ?, drawing_id = ?, native_handle = ?
                    WHERE handle = ?
                ''', (ctx.workspace_id, ctx.drawing_id,
                      native_handle, old_handle))

        for table in ("cad_layers", "cad_blocks"):
            for row in conn.execute(
                f"SELECT name FROM {table} "
                "WHERE workspace_id = '' OR drawing_id = '' OR native_name = ''"
            ).fetchall():
                old_name = row["name"]
                native_name = (
                    old_name.split(_KEY_SEPARATOR)[-1]
                    if _KEY_SEPARATOR in old_name else old_name
                )
                conn.execute(
                    f"UPDATE {table} SET name = ?, workspace_id = ?, "
                    "drawing_id = ?, native_name = ? WHERE name = ?",
                    (self._name_key(native_name, ctx), ctx.workspace_id,
                     ctx.drawing_id, native_name, old_name),
                )

        for table in ("text_patterns", "query_history", "drawing_snapshots"):
            conn.execute(
                f"UPDATE {table} SET workspace_id = ?, drawing_id = ?, "
                "conversation_id = ?, thread_id = ? "
                "WHERE workspace_id = '' OR drawing_id = '' "
                "OR conversation_id = '' OR thread_id = ''",
                (ctx.workspace_id, ctx.drawing_id,
                 ctx.conversation_id, ctx.thread_id),
            )

        annotation_rows = conn.execute('''
            SELECT annotation_id, entity_handle FROM cad_spatial_annotations
            WHERE workspace_id = '' OR drawing_id = '' OR conversation_id = ''
               OR thread_id = '' OR native_annotation_id = ''
               OR native_entity_handle = ''
        ''').fetchall()
        for row in annotation_rows:
            old_id = row["annotation_id"]
            native_id = (
                old_id.split(_KEY_SEPARATOR)[-1]
                if _KEY_SEPARATOR in old_id else old_id
            )
            native_entity = (
                row["entity_handle"].split(_KEY_SEPARATOR)[-1]
                if row["entity_handle"] else ""
            )
            scoped_entity = self._entity_key(native_entity, ctx) if native_entity else ""
            conn.execute('''
                UPDATE cad_spatial_annotations
                SET annotation_id = ?, workspace_id = ?, drawing_id = ?,
                    conversation_id = ?, thread_id = ?,
                    native_annotation_id = ?, native_entity_handle = ?,
                    entity_handle = ?
                WHERE annotation_id = ?
            ''', (
                self._annotation_key(native_id, ctx),
                ctx.workspace_id, ctx.drawing_id,
                ctx.conversation_id, ctx.thread_id,
                native_id, native_entity, scoped_entity, old_id,
            ))

    @staticmethod
    def _make_drawing_id(name: str, path: str = "") -> str:
        identity = (path or name or "active").lower()
        return _stable_id("dwg", identity)

    def _scope_key(self, *parts: str) -> str:
        return _KEY_SEPARATOR.join(str(part or "") for part in parts)

    def _entity_key(self, handle: str,
                    ctx: Optional[CADWorkspaceContext] = None) -> str:
        ctx = ctx or self.get_context()
        return self._scope_key(ctx.workspace_id, ctx.drawing_id, handle)

    def _annotation_key(self, annotation_id: str,
                        ctx: Optional[CADWorkspaceContext] = None) -> str:
        ctx = ctx or self.get_context()
        return self._scope_key(
            ctx.workspace_id, ctx.drawing_id,
            ctx.conversation_id, ctx.thread_id, annotation_id,
        )

    def _name_key(self, name: str,
                  ctx: Optional[CADWorkspaceContext] = None) -> str:
        ctx = ctx or self.get_context()
        return self._scope_key(ctx.workspace_id, ctx.drawing_id, name)

    def _ensure_context_rows(self, conn: sqlite3.Connection,
                             ctx: Optional[CADWorkspaceContext] = None) -> None:
        ctx = ctx or self.get_context()
        conn.execute('''
            INSERT INTO cad_workspaces (workspace_id, workspace_root)
            VALUES (?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                workspace_root=excluded.workspace_root,
                updated_at=CURRENT_TIMESTAMP
        ''', (ctx.workspace_id, ctx.workspace_root))
        conn.execute('''
            INSERT INTO cad_conversations
                (workspace_id, conversation_id)
            VALUES (?, ?)
            ON CONFLICT(workspace_id, conversation_id) DO UPDATE SET
                updated_at=CURRENT_TIMESTAMP
        ''', (ctx.workspace_id, ctx.conversation_id))
        conn.execute('''
            INSERT INTO cad_threads
                (workspace_id, conversation_id, thread_id)
            VALUES (?, ?, ?)
            ON CONFLICT(workspace_id, conversation_id, thread_id) DO UPDATE SET
                updated_at=CURRENT_TIMESTAMP
        ''', (ctx.workspace_id, ctx.conversation_id, ctx.thread_id))
        conn.execute('''
            UPDATE cad_drawings
            SET active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE workspace_id = ? AND drawing_id <> ?
        ''', (ctx.workspace_id, ctx.drawing_id))
        conn.execute('''
            INSERT INTO cad_drawings
                (workspace_id, drawing_id, drawing_name, drawing_path, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(workspace_id, drawing_id) DO UPDATE SET
                drawing_name=excluded.drawing_name,
                drawing_path=excluded.drawing_path,
                active=1,
                updated_at=CURRENT_TIMESTAMP
        ''', (ctx.workspace_id, ctx.drawing_id, ctx.drawing_name, ctx.drawing_path))

    def _init_schema(self):
        with self._conn() as conn:
            c = conn.cursor()

            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    workspace_root TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, conversation_id)
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, conversation_id, thread_id)
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_drawings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    drawing_id TEXT NOT NULL,
                    drawing_name TEXT NOT NULL DEFAULT 'active',
                    drawing_path TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, drawing_id)
                )
            ''')

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

            # Model-private annotations inspired by pointer-based CAD workflows.
            # These marks are stored only in SQLite; no AutoCAD entities, layers,
            # XData, or dictionaries are created in the user's drawing.
            c.execute('''
                CREATE TABLE IF NOT EXISTS cad_spatial_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    annotation_id TEXT UNIQUE NOT NULL,
                    target_kind TEXT NOT NULL DEFAULT 'entity',
                    label TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    entity_handle TEXT DEFAULT '',
                    primitive_key TEXT DEFAULT '',
                    x REAL,
                    y REAL,
                    z REAL,
                    x2 REAL,
                    y2 REAL,
                    z2 REAL,
                    bbox_min_x REAL,
                    bbox_min_y REAL,
                    bbox_max_x REAL,
                    bbox_max_y REAL,
                    confidence REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'model',
                    hidden INTEGER DEFAULT 1,
                    properties TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_annotations_id ON cad_spatial_annotations(annotation_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_annotations_label ON cad_spatial_annotations(label)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_annotations_target ON cad_spatial_annotations(target_kind)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_annotations_handle ON cad_spatial_annotations(entity_handle)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_annotations_bbox ON cad_spatial_annotations(bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y)')

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

        if not primitives:
            if "3dsolid" in etype:
                solid_count = 1
            elif "hatch" in etype or "region" in etype or "surface" in etype:
                surface_count = 1
                is_closed = True
            elif "circle" in etype:
                curve_count = 1
                is_closed = True
            elif "arc" in etype or "ellipse" in etype or "spline" in etype:
                curve_count = 1
            elif "polyline" in etype or "line" in etype or "ray" in etype:
                line_count = 1
            elif "text" in etype or "block" in etype or "point" in etype:
                point_count = 1
            dimensionality = 3 if solid_count else 2 if surface_count else 1 if (line_count or curve_count) else 0

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

    def _entity_scope_clause(self, alias: str = "") -> Tuple[str, Tuple[Any, ...]]:
        ctx = self.get_context()
        prefix = f"{alias}." if alias else ""
        return (
            f"{prefix}workspace_id = ? AND {prefix}drawing_id = ?",
            (ctx.workspace_id, ctx.drawing_id),
        )

    def _thread_scope_clause(self, alias: str = "") -> Tuple[str, Tuple[Any, ...]]:
        ctx = self.get_context()
        prefix = f"{alias}." if alias else ""
        return (
            f"{prefix}workspace_id = ? AND {prefix}drawing_id = ? "
            f"AND {prefix}conversation_id = ? AND {prefix}thread_id = ?",
            (ctx.workspace_id, ctx.drawing_id, ctx.conversation_id, ctx.thread_id),
        )

    @staticmethod
    def _decode_json(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value or "{}")
        except Exception:
            return {}

    @staticmethod
    def _normalize_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            return None
        try:
            return (
                float(value[0]),
                float(value[1]),
                float(value[2]),
                float(value[3]),
            )
        except (TypeError, ValueError):
            return None

    def _public_entity_row(self, row: Union[sqlite3.Row, Dict[str, Any]]) -> Dict[str, Any]:
        item = dict(row)
        if item.get("native_handle"):
            item["handle"] = item["native_handle"]
        item["properties"] = self._decode_json(item.get("properties"))
        item["geometry"] = self._decode_json(item.get("geometry"))
        return item

    def _public_annotation_row(self, row: Union[sqlite3.Row, Dict[str, Any]]) -> Dict[str, Any]:
        item = dict(row)
        if item.get("native_annotation_id"):
            item["annotation_id"] = item["native_annotation_id"]
        if item.get("native_entity_handle"):
            item["entity_handle"] = item["native_entity_handle"]
        item["hidden"] = bool(item.get("hidden", 1))
        item["properties"] = self._decode_json(item.get("properties"))
        return item

    @staticmethod
    def _public_name_row(row: Union[sqlite3.Row, Dict[str, Any]]) -> Dict[str, Any]:
        item = dict(row)
        if item.get("native_name"):
            item["name"] = item["native_name"]
        return item

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

    @staticmethod
    def _normalize_topology_detail(value: Optional[str]) -> str:
        detail = (value or "full").strip().lower()
        if detail in {"none", "off", "false", "0"}:
            return "none"
        if detail in {"summary", "summaries", "light", "lite"}:
            return "summary"
        return "full"

    def _insert_topology_summary(self, conn: sqlite3.Connection, handle: str,
                                 summary: Dict[str, Any]) -> None:
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

    def _replace_entity_topology_summary(self, conn: sqlite3.Connection,
                                         handle: str, entity_type: str,
                                         geometry: Dict[str, Any]) -> None:
        _, _, summary = self._derive_topology(entity_type, geometry or {})
        conn.execute("DELETE FROM cad_geometry_relations WHERE entity_handle = ?", (handle,))
        conn.execute("DELETE FROM cad_geometry_primitives WHERE entity_handle = ?", (handle,))
        conn.execute("DELETE FROM cad_topology_summary WHERE entity_handle = ?", (handle,))
        self._insert_topology_summary(conn, handle, summary)

    @staticmethod
    def _delete_topology_for_handles(conn: sqlite3.Connection,
                                     handles: List[str]) -> None:
        if not handles:
            return
        for i in range(0, len(handles), 900):
            chunk = handles[i:i + 900]
            placeholders = ",".join("?" for _ in chunk)
            for table in (
                "cad_geometry_relations",
                "cad_geometry_primitives",
                "cad_topology_summary",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE entity_handle IN ({placeholders})",
                    chunk,
                )

    def upsert_entity(self, handle: str, name: str, entity_type: str,
                      layer: str = "0", color: int = 256,
                      linetype: str = "ByLayer", properties: Dict = None,
                      geometry: Dict = None,
                      bbox: Optional[Tuple[float,float,float,float]] = None,
                      derive_topology: bool = True,
                      derive_bbox: bool = True,
                      topology_detail: str = "full") -> bool:
        try:
            if not handle:
                return False
            ctx = self.get_context()
            scoped_handle = self._entity_key(handle, ctx)
            geometry = geometry or {}
            bbox = self._normalize_bbox(bbox)
            if bbox is None:
                bbox = self._derive_bbox(entity_type, geometry) if derive_bbox else None
            with self._conn() as conn:
                self._ensure_context_rows(conn, ctx)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO cad_entities
                        (handle, name, type, layer, color, linetype,
                         properties, geometry, bbox_min_x, bbox_min_y,
                         bbox_max_x, bbox_max_y, workspace_id, drawing_id,
                         native_handle)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(handle) DO UPDATE SET
                        name=excluded.name, type=excluded.type,
                        layer=excluded.layer, color=excluded.color,
                        linetype=excluded.linetype,
                        properties=excluded.properties,
                        geometry=excluded.geometry,
                        bbox_min_x=excluded.bbox_min_x,
                        bbox_min_y=excluded.bbox_min_y,
                        bbox_max_x=excluded.bbox_max_x,
                        bbox_max_y=excluded.bbox_max_y,
                        workspace_id=excluded.workspace_id,
                        drawing_id=excluded.drawing_id,
                        native_handle=excluded.native_handle
                ''', (
                    scoped_handle, name, entity_type, layer, color, linetype,
                    json.dumps(properties or {}, ensure_ascii=False),
                    json.dumps(geometry or {}, ensure_ascii=False),
                    bbox[0] if bbox else None, bbox[1] if bbox else None,
                    bbox[2] if bbox else None, bbox[3] if bbox else None,
                    ctx.workspace_id, ctx.drawing_id, handle,
                ))
                topology_mode = self._normalize_topology_detail(topology_detail)
                if not derive_topology or topology_mode == "none":
                    self._delete_topology_for_handles(conn, [scoped_handle])
                elif topology_mode == "summary":
                    self._replace_entity_topology_summary(conn, scoped_handle, entity_type, geometry or {})
                else:
                    self._replace_entity_topology(conn, scoped_handle, entity_type, geometry or {})
            return True
        except Exception as e:
            logger.error(f"插入实体 {handle} 失败: {e}")
            return False

    def upsert_entities_batch(self, entities: List[Dict[str, Any]],
                              derive_topology: bool = True,
                              derive_bbox: bool = True,
                              topology_detail: str = "full") -> int:
        """Batch insert/update entities. Returns count of successfully upserted."""
        if not entities:
            return 0

        ctx = self.get_context()
        rows: List[Tuple[Any, ...]] = []
        topology_rows: List[Tuple[str, str, Dict[str, Any]]] = []
        skipped = 0
        for ent in entities:
            try:
                handle = ent.get("handle", "")
                if not handle:
                    skipped += 1
                    continue
                entity_type = ent.get("type", "Unknown")
                geometry = ent.get("geometry") or {}
                bbox = self._normalize_bbox(ent.get("bbox"))
                if bbox is None:
                    bbox = self._derive_bbox(entity_type, geometry) if derive_bbox else None
                scoped_handle = self._entity_key(handle, ctx)
                rows.append((
                    scoped_handle,
                    ent.get("name", entity_type),
                    entity_type,
                    ent.get("layer", "0"),
                    ent.get("color", 256),
                    ent.get("linetype", "ByLayer"),
                    json.dumps(ent.get("properties") or {}, ensure_ascii=False),
                    json.dumps(geometry, ensure_ascii=False),
                    bbox[0] if bbox else None,
                    bbox[1] if bbox else None,
                    bbox[2] if bbox else None,
                    bbox[3] if bbox else None,
                    ctx.workspace_id,
                    ctx.drawing_id,
                    handle,
                ))
                topology_rows.append((scoped_handle, entity_type, geometry))
            except Exception as e:
                skipped += 1
                logger.error("Failed to prepare entity for batch upsert: %s", e)

        if not rows:
            return 0

        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            conn.executemany('''
                INSERT INTO cad_entities
                    (handle, name, type, layer, color, linetype,
                     properties, geometry, bbox_min_x, bbox_min_y,
                     bbox_max_x, bbox_max_y, workspace_id, drawing_id,
                     native_handle)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    name=excluded.name, type=excluded.type,
                    layer=excluded.layer, color=excluded.color,
                    linetype=excluded.linetype,
                    properties=excluded.properties,
                    geometry=excluded.geometry,
                    bbox_min_x=excluded.bbox_min_x,
                    bbox_min_y=excluded.bbox_min_y,
                    bbox_max_x=excluded.bbox_max_x,
                    bbox_max_y=excluded.bbox_max_y,
                    workspace_id=excluded.workspace_id,
                    drawing_id=excluded.drawing_id,
                    native_handle=excluded.native_handle
            ''', rows)
            topology_mode = self._normalize_topology_detail(topology_detail)
            if not derive_topology or topology_mode == "none":
                self._delete_topology_for_handles(
                    conn,
                    [scoped_handle for scoped_handle, _, _ in topology_rows],
                )
            elif topology_mode == "summary":
                for scoped_handle, entity_type, geometry in topology_rows:
                    self._replace_entity_topology_summary(conn, scoped_handle, entity_type, geometry)
            else:
                for scoped_handle, entity_type, geometry in topology_rows:
                    self._replace_entity_topology(conn, scoped_handle, entity_type, geometry)
        if skipped:
            logger.info("Skipped %s invalid entities during batch upsert", skipped)
        return len(rows)

    def get_entity(self, handle: str) -> Optional[Dict[str, Any]]:
        scoped_handle = self._entity_key(handle)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_entities WHERE handle = ?", (scoped_handle,)
            ).fetchone()
            if row:
                return self._public_entity_row(row)
        return None

    def delete_entity(self, handle: str) -> bool:
        scoped_handle = self._entity_key(handle)
        with self._conn() as conn:
            conn.execute("DELETE FROM cad_geometry_relations WHERE entity_handle = ?", (scoped_handle,))
            conn.execute("DELETE FROM cad_geometry_primitives WHERE entity_handle = ?", (scoped_handle,))
            conn.execute("DELETE FROM cad_topology_summary WHERE entity_handle = ?", (scoped_handle,))
            conn.execute("DELETE FROM cad_entities WHERE handle = ?", (scoped_handle,))
        return True

    def clear_entities(self, clear_annotations: bool = False,
                       clear_understanding: bool = False):
        scope_sql, scope_params = self._entity_scope_clause()
        with self._conn() as conn:
            handle_subquery = f"SELECT handle FROM cad_entities WHERE {scope_sql}"
            for table in (
                "cad_geometry_relations",
                "cad_geometry_primitives",
                "cad_topology_summary",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE entity_handle IN ({handle_subquery})",
                    scope_params,
                )
            conn.execute(f"DELETE FROM cad_entities WHERE {scope_sql}", scope_params)
            if clear_annotations:
                thread_sql, thread_params = self._thread_scope_clause()
                conn.execute(f"DELETE FROM cad_spatial_annotations WHERE {thread_sql}", thread_params)
            if clear_understanding:
                self._clear_understanding_cache_conn(conn)

    def _clear_understanding_cache_conn(self, conn: sqlite3.Connection) -> Dict[str, int]:
        thread_sql, thread_params = self._thread_scope_clause()
        deleted: Dict[str, int] = {}
        for table in (
            "cad_semantic_relations",
            "cad_semantic_objects",
            "cad_constraints",
            "cad_validation_reports",
            "cad_view_snapshots",
            "cad_image_traces",
        ):
            if not self._table_exists(conn, table):
                deleted[table] = 0
                continue
            cursor = conn.execute(f"DELETE FROM {table} WHERE {thread_sql}", thread_params)
            deleted[table] = max(cursor.rowcount, 0)
        return deleted

    def clear_understanding_cache(self) -> Dict[str, int]:
        with self._conn() as conn:
            return self._clear_understanding_cache_conn(conn)

    @staticmethod
    def _scope_group_columns() -> str:
        return "workspace_id, drawing_id, conversation_id, thread_id"

    def _prune_scoped_cache_table(self, conn: sqlite3.Connection, table: str,
                                  pk_column: str, order_column: str,
                                  keep_per_scope: int) -> int:
        if keep_per_scope < 1 or not self._table_exists(conn, table):
            return 0
        groups = conn.execute(
            f"SELECT {self._scope_group_columns()} FROM {table} "
            f"GROUP BY {self._scope_group_columns()}"
        ).fetchall()
        deleted = 0
        for group in groups:
            params = (
                group["workspace_id"], group["drawing_id"],
                group["conversation_id"], group["thread_id"],
                keep_per_scope,
            )
            rows = conn.execute(f'''
                SELECT {pk_column}
                FROM {table}
                WHERE workspace_id = ? AND drawing_id = ?
                  AND conversation_id = ? AND thread_id = ?
                ORDER BY {order_column} DESC, {pk_column} DESC
                LIMIT -1 OFFSET ?
            ''', params).fetchall()
            ids = [row[pk_column] for row in rows]
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE {pk_column} IN ({placeholders})",
                    chunk,
                )
                deleted += max(cursor.rowcount, 0)
        return deleted

    def get_maintenance_status(self) -> Dict[str, Any]:
        with self._conn() as conn:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
            auto_vacuum = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            counts: Dict[str, int] = {}
            for table in sorted(_READ_ONLY_SCOPED_TABLES):
                if self._table_exists(conn, table):
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {self._quote_identifier(table)}"
                    ).fetchone()[0]
            large_payloads: Dict[str, Dict[str, Any]] = {}
            for table, column in (
                ("cad_view_snapshots", "snapshot_data"),
                ("cad_image_traces", "spec_json"),
                ("cad_validation_reports", "issues"),
                ("cad_semantic_objects", "entity_handles"),
                ("cad_entities", "geometry"),
            ):
                if not self._table_exists(conn, table):
                    continue
                row = conn.execute(f'''
                    SELECT COUNT(*) AS rows,
                           COALESCE(SUM(length({column})), 0) AS bytes,
                           COALESCE(MAX(length({column})), 0) AS max_bytes
                    FROM {table}
                ''').fetchone()
                large_payloads[table] = dict(row)
        return {
            "db_path": self.db_path,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "freelist_bytes": page_size * freelist_count,
            "file_bytes_estimate": page_size * page_count,
            "auto_vacuum": auto_vacuum,
            "table_counts": counts,
            "large_payloads": large_payloads,
            "legacy_database": self.get_legacy_database_status(),
        }

    def maintain(self,
                 max_view_snapshots_per_scope: int = 20,
                 max_validation_reports_per_scope: int = 20,
                 incremental_vacuum_pages: int = 1000,
                 vacuum: bool = False) -> Dict[str, Any]:
        before = self.get_maintenance_status()
        deleted: Dict[str, int] = {}
        with self._conn() as conn:
            deleted["cad_view_snapshots"] = self._prune_scoped_cache_table(
                conn,
                "cad_view_snapshots",
                "snapshot_id",
                "created_at",
                max(1, int(max_view_snapshots_per_scope or 20)),
            )
            deleted["cad_validation_reports"] = self._prune_scoped_cache_table(
                conn,
                "cad_validation_reports",
                "report_id",
                "generated_at",
                max(1, int(max_validation_reports_per_scope or 20)),
            )
            if incremental_vacuum_pages and not vacuum:
                conn.execute(
                    f"PRAGMA incremental_vacuum({max(1, int(incremental_vacuum_pages))})"
                )
        if vacuum:
            conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
                conn.execute("VACUUM")
            finally:
                conn.close()
        after = self.get_maintenance_status()
        return {
            "before": before,
            "after": after,
            "deleted": deleted,
            "vacuum": bool(vacuum),
            "incremental_vacuum_pages": 0 if vacuum else int(incremental_vacuum_pages or 0),
        }

    @staticmethod
    def _coerce_optional_point(value: Any) -> Optional[Tuple[float, float, float]]:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError("point must be a list/tuple with at least x and y")
        return (
            float(value[0]),
            float(value[1]),
            float(value[2]) if len(value) > 2 else 0.0,
        )

    @staticmethod
    def _coerce_optional_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            raise ValueError("bbox must contain [min_x, min_y, max_x, max_y]")
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))

    def upsert_spatial_annotation(self, label: str, target_kind: str = "entity",
                                  annotation_id: Optional[str] = None,
                                  description: str = "",
                                  entity_handle: Optional[str] = None,
                                  primitive_key: Optional[str] = None,
                                  point: Optional[List[float]] = None,
                                  point2: Optional[List[float]] = None,
                                  bbox: Optional[List[float]] = None,
                                  confidence: float = 1.0,
                                  source: str = "model",
                                  properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Store a model-only spatial mark without modifying the DWG."""
        clean_label = (label or "").strip()
        if not clean_label:
            raise ValueError("label is required")
        clean_kind = (target_kind or "entity").strip().lower()
        if clean_kind not in {"entity", "primitive", "point", "bbox", "area", "view", "group"}:
            raise ValueError("target_kind must be entity, primitive, point, bbox, area, view, or group")
        if not any([entity_handle, primitive_key, point, bbox, clean_kind in {"view", "group"}]):
            raise ValueError("provide an entity handle, primitive key, point, bbox, view, or group target")

        p1 = self._coerce_optional_point(point)
        p2 = self._coerce_optional_point(point2)
        bb = self._coerce_optional_bbox(bbox)
        ann_id = (annotation_id or f"ann_{uuid.uuid4().hex[:12]}").strip()
        ctx = self.get_context()
        scoped_ann_id = self._annotation_key(ann_id, ctx)
        scoped_entity_handle = self._entity_key(entity_handle, ctx) if entity_handle else ""
        props = properties or {}

        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            conn.execute('''
                INSERT INTO cad_spatial_annotations
                    (annotation_id, target_kind, label, description,
                     entity_handle, primitive_key, x, y, z, x2, y2, z2,
                     bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                     confidence, source, hidden, properties,
                     workspace_id, drawing_id, conversation_id, thread_id,
                     native_annotation_id, native_entity_handle)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(annotation_id) DO UPDATE SET
                    target_kind=excluded.target_kind,
                    label=excluded.label,
                    description=excluded.description,
                    entity_handle=excluded.entity_handle,
                    primitive_key=excluded.primitive_key,
                    x=excluded.x,
                    y=excluded.y,
                    z=excluded.z,
                    x2=excluded.x2,
                    y2=excluded.y2,
                    z2=excluded.z2,
                    bbox_min_x=excluded.bbox_min_x,
                    bbox_min_y=excluded.bbox_min_y,
                    bbox_max_x=excluded.bbox_max_x,
                    bbox_max_y=excluded.bbox_max_y,
                    confidence=excluded.confidence,
                    source=excluded.source,
                    hidden=1,
                    properties=excluded.properties,
                    workspace_id=excluded.workspace_id,
                    drawing_id=excluded.drawing_id,
                    conversation_id=excluded.conversation_id,
                    thread_id=excluded.thread_id,
                    native_annotation_id=excluded.native_annotation_id,
                    native_entity_handle=excluded.native_entity_handle,
                    updated_at=CURRENT_TIMESTAMP
            ''', (
                scoped_ann_id, clean_kind, clean_label, description or "",
                scoped_entity_handle, primitive_key or "",
                p1[0] if p1 else None, p1[1] if p1 else None, p1[2] if p1 else None,
                p2[0] if p2 else None, p2[1] if p2 else None, p2[2] if p2 else None,
                bb[0] if bb else None, bb[1] if bb else None,
                bb[2] if bb else None, bb[3] if bb else None,
                float(confidence), source or "model",
                json.dumps(props, ensure_ascii=False),
                ctx.workspace_id, ctx.drawing_id, ctx.conversation_id, ctx.thread_id,
                ann_id, entity_handle or "",
            ))
        return self.get_spatial_annotation(ann_id) or {}

    def get_spatial_annotation(self, annotation_id: str) -> Optional[Dict[str, Any]]:
        rows = self.list_spatial_annotations(annotation_id=annotation_id, limit=1)
        return rows[0] if rows else None

    def list_spatial_annotations(self, annotation_id: Optional[str] = None,
                                 label: Optional[str] = None,
                                 target_kind: Optional[str] = None,
                                 entity_handle: Optional[str] = None,
                                 limit: int = 100) -> List[Dict[str, Any]]:
        conditions = []
        thread_sql, thread_params = self._thread_scope_clause()
        conditions.append(thread_sql)
        params: List[Any] = list(thread_params)
        if annotation_id:
            conditions.append("annotation_id = ?")
            params.append(self._annotation_key(annotation_id))
        if label:
            conditions.append("label = ?")
            params.append(label)
        if target_kind:
            conditions.append("target_kind = ?")
            params.append(target_kind)
        if entity_handle:
            conditions.append("entity_handle = ?")
            params.append(self._entity_key(entity_handle))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        safe_limit = max(1, min(int(limit or 100), 1000))
        with self._conn() as conn:
            rows = conn.execute(f'''
                SELECT * FROM cad_spatial_annotations
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
            ''', (*params, safe_limit)).fetchall()

        results = []
        for row in rows:
            results.append(self._public_annotation_row(row))
        return results

    def delete_spatial_annotations(self, annotation_id: Optional[str] = None,
                                   label: Optional[str] = None,
                                   target_kind: Optional[str] = None,
                                   entity_handle: Optional[str] = None) -> int:
        conditions = []
        thread_sql, thread_params = self._thread_scope_clause()
        conditions.append(thread_sql)
        params: List[Any] = list(thread_params)
        if annotation_id:
            conditions.append("annotation_id = ?")
            params.append(self._annotation_key(annotation_id))
        if label:
            conditions.append("label = ?")
            params.append(label)
        if target_kind:
            conditions.append("target_kind = ?")
            params.append(target_kind)
        if entity_handle:
            conditions.append("entity_handle = ?")
            params.append(self._entity_key(entity_handle))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as conn:
            cursor = conn.execute(f"DELETE FROM cad_spatial_annotations {where}", params)
            return cursor.rowcount

    def get_entity_topology(self, handle: str) -> Dict[str, Any]:
        scoped_handle = self._entity_key(handle)
        with self._conn() as conn:
            summary = conn.execute(
                "SELECT * FROM cad_topology_summary WHERE entity_handle = ?",
                (scoped_handle,),
            ).fetchone()
            primitives = conn.execute(
                """SELECT * FROM cad_geometry_primitives
                   WHERE entity_handle = ?
                   ORDER BY primitive_type, sequence_index, primitive_key""",
                (scoped_handle,),
            ).fetchall()
            relations = conn.execute(
                """SELECT * FROM cad_geometry_relations
                   WHERE entity_handle = ?
                   ORDER BY sequence_index, relation_type""",
                (scoped_handle,),
            ).fetchall()

        def decode(row):
            d = dict(row)
            if d.get("entity_handle") == scoped_handle:
                d["entity_handle"] = handle
            if "properties" in d:
                d["properties"] = json.loads(d.get("properties") or "{}")
            return d

        summary_row = dict(summary) if summary else None
        if summary_row and summary_row.get("entity_handle") == scoped_handle:
            summary_row["entity_handle"] = handle
        return {
            "summary": summary_row,
            "primitives": [decode(r) for r in primitives],
            "relations": [decode(r) for r in relations],
        }

    def get_topology_summary(self, limit: int = 100) -> List[Dict[str, Any]]:
        scope_sql, scope_params = self._entity_scope_clause("e")
        with self._conn() as conn:
            rows = conn.execute('''
                SELECT e.native_handle AS handle, e.name, e.type, e.layer,
                       t.dimensionality, t.point_count, t.line_count,
                       t.curve_count, t.surface_count, t.solid_count,
                       t.is_closed, t.length, t.area, t.summary
                FROM cad_topology_summary t
                JOIN cad_entities e ON e.handle = t.entity_handle
                WHERE {scope_sql}
                ORDER BY t.dimensionality DESC, e.type, e.handle
                LIMIT ?
            '''.format(scope_sql=scope_sql), (*scope_params, limit)).fetchall()
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
        scope_sql, scope_params = self._entity_scope_clause()
        conditions = [scope_sql]
        params = list(scope_params)

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
            return [self._public_entity_row(r) for r in rows]

    def query_near_point(self, x: float, y: float, radius: float,
                         entity_type: Optional[str] = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
        """Find entities within radius of a point (simplified spatial)."""
        # Simple centroid-based approximation
        with self._conn() as conn:
            ctx = self.get_context()
            type_filter = "AND type = ?" if entity_type else ""
            params = [
                x, x, y, y,
                x + radius, x - radius,
                y + radius, y - radius,
                ctx.workspace_id, ctx.drawing_id,
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
                  AND workspace_id = ? AND drawing_id = ?
                  {type_filter}
                ORDER BY dist_sq
                LIMIT ?
            '''
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                d = self._public_entity_row(r)
                dist_sq = d.pop('dist_sq', 0)
                if dist_sq <= radius * radius:
                    results.append(d)
            return results[:limit]

    def count_entities(self, entity_type: Optional[str] = None,
                       layer: Optional[str] = None) -> int:
        scope_sql, scope_params = self._entity_scope_clause()
        conditions = [scope_sql]
        params = list(scope_params)
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
        scope_sql, scope_params = self._entity_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT type, COUNT(*) as cnt FROM cad_entities WHERE {scope_sql} GROUP BY type ORDER BY cnt DESC",
                scope_params,
            ).fetchall()
            return {r["type"]: r["cnt"] for r in rows}

    def get_layer_stats(self) -> Dict[str, int]:
        scope_sql, scope_params = self._entity_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT layer, COUNT(*) as cnt FROM cad_entities WHERE {scope_sql} GROUP BY layer ORDER BY cnt DESC",
                scope_params,
            ).fetchall()
            return {r["layer"]: r["cnt"] for r in rows}

    # ── Layer CRUD ──────────────────────────────────────────────

    def save_layers(self, layers: List[Dict[str, Any]]):
        ctx = self.get_context()
        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            for layer in layers:
                native_name = layer.get("name", "")
                conn.execute('''
                    INSERT OR REPLACE INTO cad_layers
                        (name, color, linetype, lineweight, is_frozen,
                         is_locked, is_on, is_plottable, description, handle,
                         workspace_id, drawing_id, native_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    self._name_key(native_name, ctx),
                    layer.get("color", 7),
                    layer.get("linetype", "Continuous"),
                    layer.get("lineweight", -1.0),
                    int(layer.get("is_frozen", False)),
                    int(layer.get("is_locked", False)),
                    int(layer.get("is_on", True)),
                    int(layer.get("is_plottable", True)),
                    layer.get("description", ""),
                    layer.get("handle", ""),
                    ctx.workspace_id,
                    ctx.drawing_id,
                    native_name,
                ))

    def get_layers(self) -> List[Dict[str, Any]]:
        scope_sql, scope_params = self._entity_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM cad_layers WHERE {scope_sql} ORDER BY native_name, name",
                scope_params,
            ).fetchall()
            return [self._public_name_row(r) for r in rows]

    # ── Block CRUD ──────────────────────────────────────────────

    def save_blocks(self, blocks: List[Dict[str, Any]]):
        ctx = self.get_context()
        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            for blk in blocks:
                native_name = blk.get("name", "")
                conn.execute('''
                    INSERT OR REPLACE INTO cad_blocks
                        (name, entity_count, is_layout, is_xref, origin_x,
                         origin_y, origin_z, path, workspace_id, drawing_id,
                         native_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    self._name_key(native_name, ctx),
                    blk.get("count", 0),
                    int(blk.get("is_layout", False)),
                    int(blk.get("is_xref", False)),
                    blk.get("origin", [0,0,0])[0] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("origin", [0,0,0])[1] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("origin", [0,0,0])[2] if isinstance(blk.get("origin"), list) else 0,
                    blk.get("path", ""),
                    ctx.workspace_id,
                    ctx.drawing_id,
                    native_name,
                ))

    def get_blocks(self) -> List[Dict[str, Any]]:
        scope_sql, scope_params = self._entity_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM cad_blocks WHERE {scope_sql} ORDER BY native_name, name",
                scope_params,
            ).fetchall()
            return [self._public_name_row(r) for r in rows]

    # ── Text Patterns ───────────────────────────────────────────

    def save_text_pattern(self, pattern: str, count: int, drawing: str = ""):
        ctx = self.get_context()
        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            conn.execute('''
                INSERT INTO text_patterns
                    (pattern, count, drawing, workspace_id, drawing_id,
                     conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                pattern, count, drawing, ctx.workspace_id, ctx.drawing_id,
                ctx.conversation_id, ctx.thread_id,
            ))

    def get_text_patterns(self) -> List[Dict[str, Any]]:
        thread_sql, thread_params = self._thread_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM text_patterns WHERE {thread_sql} ORDER BY scanned_at DESC",
                thread_params,
            ).fetchall()
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
        if action == getattr(sqlite3, "SQLITE_READ", -1):
            table = (arg1 or "").lower()
            database = (db_name or "").lower()
            view_source = (source or "").lower()
            if (
                database == "main"
                and table in _READ_ONLY_DENIED_MAIN_TABLES
                and view_source not in _READ_ONLY_SCOPED_VIEWS
            ):
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

    def _install_scoped_read_views(self, conn: sqlite3.Connection) -> None:
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_workspaces AS
            SELECT workspace_id, workspace_root, created_at, updated_at
            FROM main.cad_workspaces
            WHERE workspace_id = cad_current_workspace_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_conversations AS
            SELECT id, workspace_id, conversation_id, title, created_at, updated_at
            FROM main.cad_conversations
            WHERE workspace_id = cad_current_workspace_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_threads AS
            SELECT id, workspace_id, conversation_id, thread_id, title,
                   created_at, updated_at
            FROM main.cad_threads
            WHERE workspace_id = cad_current_workspace_id()
              AND conversation_id = cad_current_conversation_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_drawings AS
            SELECT id, workspace_id, drawing_id, drawing_name, drawing_path,
                   active, created_at, updated_at
            FROM main.cad_drawings
            WHERE workspace_id = cad_current_workspace_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_entities AS
            SELECT id, native_handle AS handle, name, type, layer, color,
                   linetype, linetype_scale, lineweight, visible,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   properties, geometry, scanned_at,
                   workspace_id, drawing_id, native_handle
            FROM main.cad_entities
            WHERE workspace_id = cad_current_workspace_id()
              AND drawing_id = cad_current_drawing_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_geometry_primitives AS
            SELECT gp.id, e.native_handle AS entity_handle,
                   gp.primitive_key, gp.primitive_type, gp.role,
                   gp.sequence_index, gp.parent_key, gp.x, gp.y, gp.z,
                   gp.x2, gp.y2, gp.z2, gp.radius, gp.length, gp.area,
                   gp.is_closed, gp.source, gp.properties
            FROM main.cad_geometry_primitives gp
            JOIN main.cad_entities e ON e.handle = gp.entity_handle
            WHERE e.workspace_id = cad_current_workspace_id()
              AND e.drawing_id = cad_current_drawing_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_geometry_relations AS
            SELECT gr.id, e.native_handle AS entity_handle,
                   gr.from_key, gr.to_key, gr.relation_type,
                   gr.sequence_index, gr.properties
            FROM main.cad_geometry_relations gr
            JOIN main.cad_entities e ON e.handle = gr.entity_handle
            WHERE e.workspace_id = cad_current_workspace_id()
              AND e.drawing_id = cad_current_drawing_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_topology_summary AS
            SELECT e.native_handle AS entity_handle, t.dimensionality,
                   t.point_count, t.line_count, t.curve_count,
                   t.surface_count, t.solid_count, t.is_closed,
                   t.length, t.area, t.summary, t.updated_at
            FROM main.cad_topology_summary t
            JOIN main.cad_entities e ON e.handle = t.entity_handle
            WHERE e.workspace_id = cad_current_workspace_id()
              AND e.drawing_id = cad_current_drawing_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_spatial_annotations AS
            SELECT id, native_annotation_id AS annotation_id, target_kind,
                   label, description, native_entity_handle AS entity_handle,
                   primitive_key, x, y, z, x2, y2, z2,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   confidence, source, hidden, properties,
                   created_at, updated_at, workspace_id, drawing_id,
                   conversation_id, thread_id
            FROM main.cad_spatial_annotations
            WHERE workspace_id = cad_current_workspace_id()
              AND drawing_id = cad_current_drawing_id()
              AND conversation_id = cad_current_conversation_id()
              AND thread_id = cad_current_thread_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_layers AS
            SELECT id, native_name AS name, color, linetype, lineweight,
                   is_frozen, is_locked, is_on, is_plottable,
                   description, handle, workspace_id, drawing_id
            FROM main.cad_layers
            WHERE workspace_id = cad_current_workspace_id()
              AND drawing_id = cad_current_drawing_id()
        ''')
        conn.execute('''
            CREATE TEMP VIEW IF NOT EXISTS cad_blocks AS
            SELECT id, native_name AS name, entity_count, is_layout, is_xref,
                   origin_x, origin_y, origin_z, path, workspace_id, drawing_id
            FROM main.cad_blocks
            WHERE workspace_id = cad_current_workspace_id()
              AND drawing_id = cad_current_drawing_id()
        ''')
        for table in ("text_patterns", "query_history", "drawing_snapshots"):
            conn.execute(f'''
                CREATE TEMP VIEW IF NOT EXISTS {table} AS
                SELECT *
                FROM main.{table}
                WHERE workspace_id = cad_current_workspace_id()
                  AND drawing_id = cad_current_drawing_id()
                  AND conversation_id = cad_current_conversation_id()
                  AND thread_id = cad_current_thread_id()
            ''')
        for table in (
            "cad_semantic_objects",
            "cad_semantic_relations",
            "cad_constraints",
            "cad_validation_reports",
            "cad_view_snapshots",
            "cad_image_traces",
        ):
            if not self._table_exists(conn, table):
                continue
            conn.execute(f'''
                CREATE TEMP VIEW IF NOT EXISTS {table} AS
                SELECT *
                FROM main.{table}
                WHERE workspace_id = cad_current_workspace_id()
                  AND drawing_id = cad_current_drawing_id()
                  AND conversation_id = cad_current_conversation_id()
                  AND thread_id = cad_current_thread_id()
            ''')

    @staticmethod
    def _coerce_limit(value: Optional[int], env_name: str,
                      default: int, maximum: int) -> int:
        if value is None:
            return _env_int(env_name, default, 1, maximum)
        try:
            return max(1, min(int(value), maximum))
        except (TypeError, ValueError):
            return _env_int(env_name, default, 1, maximum)

    @staticmethod
    def _row_result_size(row: Dict[str, Any]) -> int:
        return len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))

    def _fetch_limited_rows(self, cursor: sqlite3.Cursor,
                            columns: List[str],
                            max_rows: int,
                            max_result_bytes: int) -> Tuple[List[Dict[str, Any]], bool, int]:
        rows: List[Dict[str, Any]] = []
        truncated = False
        result_bytes = 0
        while True:
            batch = cursor.fetchmany(100)
            if not batch:
                break
            for index, raw_row in enumerate(batch):
                row = dict(zip(columns, raw_row))
                row_bytes = self._row_result_size(row)
                if rows and result_bytes + row_bytes > max_result_bytes:
                    truncated = True
                    return rows, truncated, result_bytes
                rows.append(row)
                result_bytes += row_bytes
                if len(rows) >= max_rows:
                    truncated = (index + 1 < len(batch)) or cursor.fetchone() is not None
                    return rows, truncated, result_bytes
        return rows, truncated, result_bytes

    def _record_query_history(self, conn: sqlite3.Connection, query: str,
                              result_count: int, duration_ms: float,
                              truncated: bool = False,
                              error: str = "") -> None:
        ctx = self.get_context()
        conn.execute(
            """INSERT INTO main.query_history
               (query, result_count, executed_at, workspace_id, drawing_id,
                conversation_id, thread_id, duration_ms, truncated, error)
               VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)""",
            (
                query[:500], result_count, ctx.workspace_id, ctx.drawing_id,
                ctx.conversation_id, ctx.thread_id, duration_ms,
                int(bool(truncated)), error[:500],
            ),
        )

    def execute(self, query: str, params: tuple = (),
                read_only: bool = False,
                max_rows: Optional[int] = None,
                timeout_ms: Optional[int] = None,
                max_result_bytes: Optional[int] = None) -> Dict[str, Any]:
        """Execute SQL and return rows for result-producing statements.

        Set read_only=True for MCP-facing query tools; writes are denied by
        token screening plus SQLite's authorizer. Read-only queries are scoped
        through temporary views and are bounded by row, byte, and time limits.
        """
        if read_only and not self._is_read_only_sql(query):
            raise ValueError("Only read-only SELECT/WITH/PRAGMA/EXPLAIN SQL is allowed")
        effective_max_rows = self._coerce_limit(
            max_rows, "CAD_MCP_SQL_MAX_ROWS",
            _DEFAULT_SQL_MAX_ROWS, _MAX_SQL_MAX_ROWS,
        )
        effective_timeout_ms = self._coerce_limit(
            timeout_ms, "CAD_MCP_SQL_TIMEOUT_MS",
            _DEFAULT_SQL_TIMEOUT_MS, _MAX_SQL_TIMEOUT_MS,
        )
        effective_max_result_bytes = self._coerce_limit(
            max_result_bytes, "CAD_MCP_SQL_MAX_RESULT_BYTES",
            _DEFAULT_SQL_MAX_RESULT_BYTES, _MAX_SQL_MAX_RESULT_BYTES,
        )
        with self._conn() as conn:
            c = conn.cursor()
            scoped_read = self._is_read_only_sql(query)
            if scoped_read:
                self._install_scoped_read_views(conn)
            guards_active = False

            def _clear_read_guards() -> None:
                nonlocal guards_active
                if guards_active:
                    conn.set_authorizer(None)
                    conn.set_progress_handler(None, 0)
                    guards_active = False

            if read_only:
                conn.set_authorizer(self._read_only_authorizer)
                deadline = time.monotonic() + (effective_timeout_ms / 1000.0)

                def _progress_handler() -> int:
                    return 1 if time.monotonic() > deadline else 0

                conn.set_progress_handler(_progress_handler, 1000)
                guards_active = True
            start = time.monotonic()
            try:
                c.execute(query, params)
                if c.description:
                    columns = [desc[0] for desc in c.description] if c.description else []
                    if read_only:
                        rows, truncated, result_bytes = self._fetch_limited_rows(
                            c, columns, effective_max_rows,
                            effective_max_result_bytes,
                        )
                    else:
                        rows = [dict(zip(columns, row)) for row in c.fetchall()]
                        truncated = False
                        result_bytes = sum(self._row_result_size(row) for row in rows)
                    duration_ms = round((time.monotonic() - start) * 1000.0, 3)
                    _clear_read_guards()
                    self._record_query_history(
                        conn, query, len(rows), duration_ms, truncated=truncated,
                    )
                    result = {
                        "columns": columns,
                        "rows": rows,
                        "count": len(rows),
                        "truncated": truncated,
                        "result_bytes": result_bytes,
                    }
                    if read_only:
                        result["limits"] = {
                            "max_rows": effective_max_rows,
                            "timeout_ms": effective_timeout_ms,
                            "max_result_bytes": effective_max_result_bytes,
                        }
                    return result

                if read_only:
                    duration_ms = round((time.monotonic() - start) * 1000.0, 3)
                    _clear_read_guards()
                    self._record_query_history(conn, query, 0, duration_ms)
                    return {"columns": [], "rows": [], "count": 0, "truncated": False}
                affected = c.rowcount
                duration_ms = round((time.monotonic() - start) * 1000.0, 3)
                _clear_read_guards()
                self._record_query_history(conn, query, affected, duration_ms)
                return {"affected_rows": affected}
            except Exception as exc:
                duration_ms = round((time.monotonic() - start) * 1000.0, 3)
                _clear_read_guards()
                try:
                    self._record_query_history(
                        conn, query, 0, duration_ms, error=str(exc),
                    )
                except Exception:
                    logger.debug("Failed to record query error in history", exc_info=True)
                raise
            finally:
                _clear_read_guards()

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
        ctx = self.get_context()
        with self._conn() as conn:
            self._ensure_context_rows(conn, ctx)
            c = conn.cursor()
            c.execute('''
                INSERT INTO drawing_snapshots
                    (drawing_name, entity_count, layer_count, block_count,
                     type_stats, snapshot_data, workspace_id, drawing_id,
                     conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                drawing_name, entity_count, layer_count, block_count,
                json.dumps(type_stats, ensure_ascii=False),
                json.dumps(snapshot_data or {}, ensure_ascii=False),
                ctx.workspace_id, ctx.drawing_id,
                ctx.conversation_id, ctx.thread_id,
            ))
            return c.lastrowid

    def get_recent_snapshots(self, limit: int = 5) -> List[Dict[str, Any]]:
        thread_sql, thread_params = self._thread_scope_clause()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM drawing_snapshots WHERE {thread_sql} ORDER BY created_at DESC LIMIT ?",
                (*thread_params, limit)).fetchall()
            return [dict(r) for r in rows]


# ── Module-level singleton ──────────────────────────────────────

_db = None

def get_database(db_path: Optional[str] = None) -> CADDatabase:
    global _db
    if _db is None:
        _db = CADDatabase(db_path)
    return _db
