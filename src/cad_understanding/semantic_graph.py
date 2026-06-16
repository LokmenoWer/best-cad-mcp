"""Rule-based semantic object detection over scanned CAD metadata."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    bbox_contains,
    bbox_dict,
    bbox_from_row,
    bbox_union,
    circle_center_radius,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    entity_geometry,
    entity_text,
    entity_type,
    get_db,
    is_closed_polyline,
    point_distance,
    stable_id,
)
from .result import ToolResult, ok_result


def _object_row(object_type: str,
                label: str,
                handles: List[str],
                confidence: float,
                properties: Optional[Dict[str, Any]] = None,
                source: str = "rule:generic",
                object_id: Optional[str] = None,
                bbox: Optional[Tuple[float, float, float, float]] = None) -> Dict[str, Any]:
    object_id = object_id or stable_id("sem", object_type, label, ",".join(sorted(handles)))
    return {
        "object_id": object_id,
        "object_type": object_type,
        "label": label,
        "source": source,
        "confidence": round(float(confidence), 3),
        "bbox": bbox,
        "entity_handles": sorted(set(handles)),
        "properties": properties or {},
    }


def _relation_row(relation_type: str,
                  from_object_id: str,
                  to_object_id: str,
                  confidence: float,
                  evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "relation_id": stable_id("rel", relation_type, from_object_id, to_object_id),
        "from_object_id": from_object_id,
        "to_object_id": to_object_id,
        "relation_type": relation_type,
        "confidence": round(float(confidence), 3),
        "evidence": evidence or {},
    }


def _insert_graph(database: CADDatabase,
                  objects: List[Dict[str, Any]],
                  relations: List[Dict[str, Any]],
                  source_prefix: str) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        conn.execute('''
            DELETE FROM cad_semantic_relations
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              AND relation_id LIKE 'rel_%'
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        ))
        conn.execute('''
            DELETE FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              AND source LIKE ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
            f"{source_prefix}%",
        ))
        for obj in objects:
            bbox = obj.get("bbox")
            conn.execute('''
                INSERT OR REPLACE INTO cad_semantic_objects
                    (object_id, object_type, label, source, confidence,
                     bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                     entity_handles, properties, workspace_id, drawing_id,
                     conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                obj["object_id"], obj["object_type"], obj["label"],
                obj["source"], obj["confidence"],
                bbox[0] if bbox else None, bbox[1] if bbox else None,
                bbox[2] if bbox else None, bbox[3] if bbox else None,
                json.dumps(obj.get("entity_handles", []), ensure_ascii=False),
                json.dumps(obj.get("properties", {}), ensure_ascii=False),
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))
        for rel in relations:
            conn.execute('''
                INSERT OR REPLACE INTO cad_semantic_relations
                    (relation_id, from_object_id, to_object_id, relation_type,
                     confidence, evidence, workspace_id, drawing_id,
                     conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                rel["relation_id"], rel["from_object_id"], rel["to_object_id"],
                rel["relation_type"], rel["confidence"],
                json.dumps(rel.get("evidence", {}), ensure_ascii=False),
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))


def _read_graph(database: CADDatabase) -> Dict[str, List[Dict[str, Any]]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        obj_rows = conn.execute('''
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
        rel_rows = conn.execute('''
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
    objects = []
    for row in obj_rows:
        item = dict(row)
        bbox = None
        if item.get("bbox_min_x") is not None:
            bbox = (
                float(item["bbox_min_x"]),
                float(item["bbox_min_y"]),
                float(item["bbox_max_x"]),
                float(item["bbox_max_y"]),
            )
        for key in ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"):
            item.pop(key, None)
        item["bbox"] = bbox_dict(bbox)
        item["entity_handles"] = decode_json(item.get("entity_handles"), [])
        item["properties"] = decode_json(item.get("properties"))
        objects.append(item)
    relations = []
    for row in rel_rows:
        item = dict(row)
        item["evidence"] = decode_json(item.get("evidence"))
        relations.append(item)
    return {"semantic_objects": objects, "semantic_relations": relations}


def _detect_circle_patterns(circles: List[Dict[str, Any]],
                            domain: str) -> List[Dict[str, Any]]:
    groups: Dict[float, List[Dict[str, Any]]] = {}
    for entity in circles:
        cr = circle_center_radius(entity)
        if not cr:
            continue
        _, radius = cr
        key = round(radius, 3)
        groups.setdefault(key, []).append(entity)
    patterns = []
    for radius, members in groups.items():
        if len(members) < 3:
            continue
        centers = [circle_center_radius(member)[0] for member in members if circle_center_radius(member)]
        centroid = [
            sum(center[0] for center in centers) / len(centers),
            sum(center[1] for center in centers) / len(centers),
        ]
        distances = [point_distance(center, centroid) for center in centers]
        mean_distance = sum(distances) / len(distances) if distances else 0.0
        variance = sum((d - mean_distance) ** 2 for d in distances) / len(distances) if distances else 0.0
        pattern_type = "bolt_circle_pattern" if domain == "mechanical" and mean_distance > radius else "hole_pattern"
        confidence = 0.82 if variance <= max(radius, 1.0) else 0.68
        patterns.append(_object_row(
            pattern_type,
            f"{len(members)}x radius {radius:g}",
            [str(member.get("handle")) for member in members],
            confidence,
            bbox=bbox_union(bbox_from_row(member) for member in members),
            properties={
                "count": len(members),
                "radius": radius,
                "estimated_pattern_center": centroid,
                "center_distance_variance": variance,
            },
            source=f"rule:{domain}",
        ))
    return patterns


def detect_semantic_objects(domain: str = "generic",
                            database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    domain = (domain or "generic").lower().strip()
    entities = all_entities(db)
    objects: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    circles: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []

    for entity in entities:
        handle = str(entity.get("handle") or "")
        etype = entity_type(entity)
        text = entity_text(entity)
        bbox = bbox_from_row(entity)
        geom = entity_geometry(entity)
        if "polyline" in etype and is_closed_polyline(entity):
            obj_type = "closed_profile"
            confidence = 0.78
            objects.append(_object_row(
                obj_type, f"closed profile {handle}", [handle], confidence,
                bbox=bbox, properties={"closed": True}, source=f"rule:{domain}",
            ))
            profiles.append(entity)
        elif "circle" in etype:
            circles.append(entity)
            obj_type = "hole" if domain == "mechanical" else "circle_feature"
            objects.append(_object_row(
                obj_type,
                f"{obj_type} {handle}",
                [handle],
                0.72 if domain == "mechanical" else 0.62,
                bbox=bbox,
                properties={"radius": geom.get("radius")},
                source=f"rule:{domain}",
            ))
        elif "hatch" in etype:
            obj_type = "section_region" if domain == "mechanical" else "filled_region"
            objects.append(_object_row(
                obj_type, f"{obj_type} {handle}", [handle], 0.7,
                bbox=bbox, properties={"pattern": geom.get("pattern")}, source=f"rule:{domain}",
            ))
        elif "dimension" in etype:
            objects.append(_object_row(
                "dimension_annotation", f"dimension {handle}", [handle], 0.8,
                bbox=bbox, properties=geom, source=f"rule:{domain}",
            ))
        elif "text" in etype:
            obj_type = "room_label" if domain == "architecture" else "text_annotation"
            objects.append(_object_row(
                obj_type,
                str(geom.get("text") or geom.get("text_string") or f"text {handle}")[:80],
                [handle],
                0.76,
                bbox=bbox,
                properties={"text": geom.get("text") or geom.get("text_string")},
                source=f"rule:{domain}",
            ))
        elif "block" in etype:
            label = str(geom.get("block_name") or entity.get("name") or handle)
            lower_label = label.lower() + " " + text
            obj_type = "block_instance"
            if domain == "architecture":
                if "door" in lower_label:
                    obj_type = "door"
                elif "window" in lower_label:
                    obj_type = "window"
            elif domain == "electrical":
                obj_type = "component_symbol"
            objects.append(_object_row(
                obj_type, label, [handle], 0.74,
                bbox=bbox, properties={"block_name": label}, source=f"rule:{domain}",
            ))
        elif domain == "mechanical" and ("center" in text or "center" in str(entity.get("linetype", "")).lower()):
            objects.append(_object_row(
                "centerline", f"centerline {handle}", [handle], 0.65,
                bbox=bbox, properties={"layer": entity.get("layer")}, source=f"rule:{domain}",
            ))
        elif domain == "architecture" and "line" in etype and "wall" in text:
            objects.append(_object_row(
                "wall_candidate", f"wall line {handle}", [handle], 0.55,
                bbox=bbox, properties={"layer": entity.get("layer")}, source=f"rule:{domain}",
            ))
        elif domain == "electrical" and ("line" in etype or "polyline" in etype) and any(k in text for k in ("wire", "cable", "circuit")):
            objects.append(_object_row(
                "wire_candidate", f"wire {handle}", [handle], 0.58,
                bbox=bbox, properties={"layer": entity.get("layer")}, source=f"rule:{domain}",
            ))

    objects.extend(_detect_circle_patterns(circles, domain))

    if profiles:
        largest = max(profiles, key=lambda row: bbox_area_safe(bbox_from_row(row)))
        largest_handle = str(largest.get("handle"))
        objects.append(_object_row(
            "outer_profile" if domain == "mechanical" else "outer_closed_profile",
            f"outer profile {largest_handle}",
            [largest_handle],
            0.7,
            bbox=bbox_from_row(largest),
            properties={"selection": "largest closed profile by bounding box"},
            source=f"rule:{domain}",
        ))

    by_handle: Dict[str, List[Dict[str, Any]]] = {}
    for obj in objects:
        for handle in obj.get("entity_handles", []):
            by_handle.setdefault(handle, []).append(obj)
    profile_objects = [obj for obj in objects if "profile" in obj["object_type"]]
    hole_objects = [obj for obj in objects if obj["object_type"] in {"hole", "circle_feature"}]
    for profile in profile_objects:
        profile_bbox = profile.get("bbox")
        for hole in hole_objects:
            hole_bbox = hole.get("bbox")
            if bbox_contains(profile_bbox, hole_bbox):
                relations.append(_relation_row(
                    "contains",
                    profile["object_id"],
                    hole["object_id"],
                    0.62,
                    {"reason": "hole bbox lies inside profile bbox"},
                ))

    _insert_graph(db, objects, relations, f"rule:{domain}")
    graph = _read_graph(db)
    return ok_result(
        f"Detected {len(objects)} semantic objects with rule-based {domain} detector.",
        data=graph,
        handles=sorted({h for obj in objects for h in obj.get("entity_handles", [])}),
        warnings=["Semantic detection is deterministic and rule-based; confidence reflects heuristic evidence."],
        next_tools=["get_semantic_graph", "find_semantic_objects", "extract_drawing_constraints"],
    )


def bbox_area_safe(bbox: Optional[Tuple[float, float, float, float]]) -> float:
    if not bbox:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def get_semantic_graph(database: Optional[CADDatabase] = None) -> ToolResult:
    graph = _read_graph(get_db(database))
    return ok_result(
        f"Loaded semantic graph with {len(graph['semantic_objects'])} objects.",
        data=graph,
        handles=sorted({
            handle
            for obj in graph["semantic_objects"]
            for handle in obj.get("entity_handles", [])
        }),
        next_tools=["find_semantic_objects", "build_drawing_ir"],
    )


def find_semantic_objects(object_type: Optional[str] = None,
                          label_query: Optional[str] = None,
                          top_k: int = 20,
                          database: Optional[CADDatabase] = None) -> ToolResult:
    graph = _read_graph(get_db(database))
    object_type_norm = (object_type or "").lower().strip()
    label_norm = (label_query or "").lower().strip()
    matches = []
    for obj in graph["semantic_objects"]:
        score = 0.0
        if object_type_norm and object_type_norm in str(obj.get("object_type", "")).lower():
            score += 0.6
        if label_norm and label_norm in str(obj.get("label", "")).lower():
            score += 0.4
        if not object_type_norm and not label_norm:
            score = float(obj.get("confidence") or 0.0)
        if score > 0:
            matches.append({**obj, "score": round(min(score, 1.0), 3)})
    matches.sort(key=lambda item: (-item["score"], -float(item.get("confidence") or 0.0), item.get("label", "")))
    matches = matches[:max(1, min(int(top_k or 20), 100))]
    return ok_result(
        f"Found {len(matches)} semantic objects.",
        data={"semantic_objects": matches},
        handles=sorted({h for obj in matches for h in obj.get("entity_handles", [])}),
        next_tools=["explain_entity", "get_semantic_graph"],
    )

