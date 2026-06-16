"""Build CAD-IR from the existing scanned SQLite metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .common import (
    all_blocks,
    all_entities,
    all_layers,
    all_topology_primitives,
    all_topology_relations,
    bbox_dict,
    bbox_from_row,
    bbox_union,
    clean_row,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    get_db,
    latest_validation_report,
    now_iso,
    topology_summary,
)
from .ir import BlockIR, CadEntityIR, DrawingIR, LayerIR, to_dict


def _entity_ir(entity: Dict[str, Any], semantic_tags: List[str]) -> Dict[str, Any]:
    bbox = bbox_from_row(entity)
    native_handle = str(entity.get("native_handle") or entity.get("handle") or "")
    handle = str(entity.get("handle") or native_handle)
    entity_type = str(entity.get("type") or entity.get("entity_type") or "Unknown")
    ir = CadEntityIR(
        handle=handle,
        native_handle=native_handle,
        object_name=str(entity.get("name") or entity_type),
        entity_type=entity_type,
        layer=str(entity.get("layer") or "0"),
        color=entity.get("color", 256),
        linetype=str(entity.get("linetype") or "ByLayer"),
        visible=bool(entity.get("visible", 1)),
        bbox=bbox_dict(bbox),
        geometry=decode_json(entity.get("geometry")),
        properties=decode_json(entity.get("properties")),
        topology_refs=[handle],
        semantic_tags=semantic_tags,
        source="cad_entities",
        confidence=1.0,
    )
    return to_dict(ir)


def _layer_ir(layer: Dict[str, Any], entity_counts: Dict[str, int]) -> Dict[str, Any]:
    name = str(layer.get("name") or "")
    return to_dict(LayerIR(
        name=name,
        color=layer.get("color", 7),
        linetype=str(layer.get("linetype") or "Continuous"),
        lineweight=layer.get("lineweight", -1.0),
        is_frozen=bool(layer.get("is_frozen", False)),
        is_locked=bool(layer.get("is_locked", False)),
        is_on=bool(layer.get("is_on", True)),
        is_plottable=bool(layer.get("is_plottable", True)),
        description=str(layer.get("description") or ""),
        handle=str(layer.get("handle") or ""),
        entity_count=int(entity_counts.get(name, 0)),
    ))


def _block_ir(block: Dict[str, Any]) -> Dict[str, Any]:
    origin = [
        float(block.get("origin_x") or 0.0),
        float(block.get("origin_y") or 0.0),
        float(block.get("origin_z") or 0.0),
    ]
    return to_dict(BlockIR(
        name=str(block.get("name") or ""),
        entity_count=int(block.get("entity_count") or block.get("count") or 0),
        is_layout=bool(block.get("is_layout", False)),
        is_xref=bool(block.get("is_xref", False)),
        origin=origin,
        path=str(block.get("path") or ""),
    ))


def _semantic_tags_by_handle(database: CADDatabase) -> Dict[str, List[str]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    tags: Dict[str, List[str]] = {}
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_type, entity_handles
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    for row in rows:
        handles = decode_json(row["entity_handles"], [])
        for handle in handles if isinstance(handles, list) else []:
            tags.setdefault(str(handle), [])
            tag = str(row["object_type"] or "")
            if tag and tag not in tags[str(handle)]:
                tags[str(handle)].append(tag)
    return tags


def _read_semantic_objects(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_id, object_type, label, source, confidence,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   entity_handles, properties, created_at
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY object_type, label, object_id
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    objects = []
    for row in rows:
        item = clean_row(dict(row))
        bbox = None
        if item.get("bbox_min_x") is not None:
            bbox = (
                float(item["bbox_min_x"]),
                float(item["bbox_min_y"]),
                float(item["bbox_max_x"]),
                float(item["bbox_max_y"]),
            )
        item["bbox"] = bbox_dict(bbox)
        for key in ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"):
            item.pop(key, None)
        item["entity_handles"] = decode_json(item.get("entity_handles"), [])
        item["properties"] = decode_json(item.get("properties"))
        objects.append(item)
    return objects


def _read_semantic_relations(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT relation_id, from_object_id, to_object_id, relation_type,
                   confidence, evidence
            FROM cad_semantic_relations
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY relation_type, relation_id
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    relations = []
    for row in rows:
        item = clean_row(dict(row))
        item["evidence"] = decode_json(item.get("evidence"))
        relations.append(item)
    return relations


def _read_constraints(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT constraint_id, constraint_type, source, target_handles,
                   target_object_ids, value, actual, tolerance, unit,
                   confidence, status, evidence, created_at
            FROM cad_constraints
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY constraint_type, constraint_id
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    constraints = []
    for row in rows:
        item = clean_row(dict(row))
        item["target_handles"] = decode_json(item.get("target_handles"), [])
        item["target_object_ids"] = decode_json(item.get("target_object_ids"), [])
        item["evidence"] = decode_json(item.get("evidence"))
        constraints.append(item)
    return constraints


def _read_views(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT snapshot_id, image_path, overlay_image_path,
                   context_json_path, snapshot_data, created_at
            FROM cad_view_snapshots
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY created_at DESC
            LIMIT 20
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    views = []
    for row in rows:
        item = clean_row(dict(row))
        data = decode_json(item.pop("snapshot_data", "{}"))
        if isinstance(data, dict):
            merged = {**data, **item}
            views.append(merged)
        else:
            views.append(item)
    return views


def _maybe_rescan(rescan: bool) -> Optional[str]:
    if not rescan:
        return None
    from src.cad_tools import query_tools

    return query_tools.scan_all_entities(
        clear_db=True,
        max_entities=10000,
        clear_annotations=False,
        detail_level="standard",
        include_bounding_boxes=True,
        derive_topology=True,
        topology_detail="full",
    )


def build_drawing_ir(rescan: bool = False,
                     database: Optional[CADDatabase] = None) -> Dict[str, Any]:
    db = get_db(database)
    ensure_understanding_schema(db)
    scan_message = _maybe_rescan(rescan)
    ctx = db.get_context()

    entities = all_entities(db)
    layer_counts: Dict[str, int] = {}
    for entity in entities:
        layer = str(entity.get("layer") or "0")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    semantic_tags = _semantic_tags_by_handle(db)
    entity_irs = [
        _entity_ir(entity, semantic_tags.get(str(entity.get("handle")), []))
        for entity in entities
    ]

    extents = bbox_dict(bbox_union(bbox_from_row(entity) for entity in entities))
    validation = latest_validation_report(db) or {
        "passed": True,
        "score": 100.0,
        "issue_count": 0,
        "issues": [],
        "generated_at": "",
        "recommended_next_tools": ["validate_geometry"],
    }

    drawing = DrawingIR(
        drawing_id=ctx.drawing_id,
        drawing_name=ctx.drawing_name,
        drawing_path=ctx.drawing_path,
        units="unknown",
        extents=extents,
        entity_count=len(entity_irs),
        layers=[_layer_ir(layer, layer_counts) for layer in all_layers(db)],
        blocks=[_block_ir(block) for block in all_blocks(db)],
        entities=entity_irs,
        topology={
            "summary": topology_summary(db),
            "primitives": all_topology_primitives(db),
            "relations": all_topology_relations(db),
        },
        semantic_objects=_read_semantic_objects(db),
        semantic_relations=_read_semantic_relations(db),
        constraints=_read_constraints(db),
        validation=validation,
        views=_read_views(db),
        generated_at=now_iso(),
    )
    result = to_dict(drawing)
    if scan_message:
        result["scan_message"] = scan_message
    json.dumps(result, ensure_ascii=False, default=str)
    return result


def get_cached_drawing_ir(database: Optional[CADDatabase] = None) -> Dict[str, Any]:
    return build_drawing_ir(rescan=False, database=database)


def export_drawing_ir(filepath: str,
                      rescan: bool = False,
                      database: Optional[CADDatabase] = None) -> Dict[str, Any]:
    drawing_ir = build_drawing_ir(rescan=rescan, database=database)
    target = Path(filepath).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(drawing_ir, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return {"filepath": str(target), "drawing_ir": drawing_ir}


__all__ = ["build_drawing_ir", "get_cached_drawing_ir", "export_drawing_ir"]
