"""Rule-based semantic object detection over scanned CAD metadata."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    bbox_area,
    bbox_center,
    bbox_contains,
    bbox_dict,
    bbox_from_row,
    bbox_intersects,
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
    line_angle,
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
                bbox: Optional[Tuple[float, float, float, float]] = None,
                rule_name: Optional[str] = None,
                assumptions: Optional[List[str]] = None,
                warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    object_id = object_id or stable_id("sem", object_type, label, ",".join(sorted(handles)))
    props = dict(properties or {})
    props.setdefault("evidence_handles", sorted(set(handles)))
    props.setdefault("rule_name", rule_name or source)
    props.setdefault("assumptions", assumptions or [])
    props.setdefault("warnings", warnings or ([] if confidence >= 0.65 else ["low_confidence_candidate"]))
    return {
        "object_id": object_id,
        "object_type": object_type,
        "label": label,
        "source": source,
        "confidence": round(float(confidence), 3),
        "bbox": bbox,
        "entity_handles": sorted(set(handles)),
        "properties": props,
    }


def _relation_row(relation_type: str,
                  from_object_id: str,
                  to_object_id: str,
                  confidence: float,
                  evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    relation_evidence = dict(evidence or {})
    relation_evidence.setdefault("rule_name", f"relation:{relation_type}")
    relation_evidence.setdefault("assumptions", [])
    relation_evidence.setdefault("warnings", [] if confidence >= 0.65 else ["low_confidence_relation"])
    return {
        "relation_id": stable_id("rel", relation_type, from_object_id, to_object_id),
        "from_object_id": from_object_id,
        "to_object_id": to_object_id,
        "relation_type": relation_type,
        "confidence": round(float(confidence), 3),
        "evidence": relation_evidence,
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


def _layer_or_text(entity: Dict[str, Any]) -> str:
    return f"{entity_text(entity)} {entity.get('layer', '')}".lower()


def _is_long_thin(bbox: Optional[Tuple[float, float, float, float]],
                  ratio: float = 3.0) -> bool:
    if not bbox:
        return False
    width = abs(bbox[2] - bbox[0])
    height = abs(bbox[3] - bbox[1])
    short = max(min(width, height), 1e-9)
    return max(width, height) / short >= ratio


def _drafting_object_type(entity: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    text = _layer_or_text(entity)
    etype = entity_type(entity)
    if "revision" in text or "rev" in text:
        return "revision_table", 0.66
    if "bom" in text or "parts list" in text or "bill of material" in text:
        return "bom_table", 0.7
    if "title" in text or "title_block" in text:
        return "title_block", 0.72
    if "border" in text or ("polyline" in etype and "sheet" in text):
        return "border", 0.68
    if "callout" in text or "leader" in text or "mleader" in etype:
        return "callout", 0.65
    if "section" in text:
        return "section_marker", 0.66
    if "detail" in text:
        return "detail_marker", 0.66
    return None


def _domain_specific_object(entity: Dict[str, Any],
                            domain: str,
                            etype: str,
                            text: str,
                            bbox: Optional[Tuple[float, float, float, float]]) -> Optional[Tuple[str, float, Dict[str, Any]]]:
    if domain == "mechanical":
        if "slot" in text or ("polyline" in etype and _is_long_thin(bbox, 2.8)):
            return "slot", 0.62, {"reason": "long thin closed/profile geometry or slot layer/text"}
        if "shaft" in text:
            return "shaft", 0.58, {"reason": "shaft lexical evidence"}
        if "flange" in text:
            return "flange_candidate", 0.58, {"reason": "flange lexical evidence"}
        if "bracket" in text:
            return "bracket_candidate", 0.56, {"reason": "bracket lexical evidence"}
    if domain == "architecture":
        if "wall" in text and ("line" in etype or "polyline" in etype):
            return "wall_candidate", 0.6, {"reason": "wall layer/text on linework"}
        if "opening" in text:
            return "opening", 0.58, {"reason": "opening lexical evidence"}
        if "stair" in text:
            return "stair_candidate", 0.55, {"reason": "stair lexical evidence"}
        if "room" in text and "text" in etype:
            return "room_label", 0.72, {"reason": "room text label"}
    if domain == "electrical":
        if ("wire" in text or "cable" in text or "conduit" in text) and ("line" in etype or "polyline" in etype):
            return ("cable" if "cable" in text else "wire"), 0.64, {"reason": "electrical path lexical evidence"}
        if "terminal" in text:
            return "terminal", 0.61, {"reason": "terminal lexical evidence"}
        if "device" in text or ("text" in etype and any(k in text for k in ("sw", "lt", "panel"))):
            return "device_label", 0.58, {"reason": "device label lexical evidence"}
    if domain == "drafting":
        drafting = _drafting_object_type(entity)
        if drafting:
            dtype, confidence = drafting
            return dtype, confidence, {"reason": "drafting layer/text/block evidence"}
    return None


def _add_spatial_relations(objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    relations: List[Dict[str, Any]] = []
    for i, first in enumerate(objects):
        first_bbox = first.get("bbox")
        if not first_bbox:
            continue
        for second in objects[i + 1:i + 80]:
            second_bbox = second.get("bbox")
            if not second_bbox:
                continue
            if bbox_contains(first_bbox, second_bbox):
                relations.append(_relation_row(
                    "contains", first["object_id"], second["object_id"], 0.6,
                    {"reason": "bbox containment", "evidence_handles": first.get("entity_handles", []) + second.get("entity_handles", [])},
                ))
            elif bbox_contains(second_bbox, first_bbox):
                relations.append(_relation_row(
                    "inside", first["object_id"], second["object_id"], 0.6,
                    {"reason": "bbox containment", "evidence_handles": first.get("entity_handles", []) + second.get("entity_handles", [])},
                ))
            elif bbox_intersects(first_bbox, second_bbox):
                relations.append(_relation_row(
                    "adjacent_to", first["object_id"], second["object_id"], 0.42,
                    {"reason": "bbox intersection/adjacency candidate"},
                ))
    return relations


def detect_semantic_objects(domain: str = "generic",
                            database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    domain = (domain or "generic").lower().strip()
    entities = all_entities(db)
    objects: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    circles: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []
    dimensions: List[Dict[str, Any]] = []
    texts: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []

    for entity in entities:
        handle = str(entity.get("handle") or "")
        etype = entity_type(entity)
        text = entity_text(entity)
        bbox = bbox_from_row(entity)
        geom = entity_geometry(entity)
        if "line" in etype and "polyline" not in etype:
            lines.append(entity)
        if "polyline" in etype and is_closed_polyline(entity):
            obj_type = "closed_profile"
            confidence = 0.78
            objects.append(_object_row(
                obj_type, f"closed profile {handle}", [handle], confidence,
                bbox=bbox, properties={"closed": True}, source=f"rule:{domain}",
                rule_name="closed_polyline_profile",
            ))
            profiles.append(entity)
            specific = _domain_specific_object(entity, domain, etype, text, bbox)
            if specific:
                obj_type, spec_confidence, spec_props = specific
                objects.append(_object_row(
                    obj_type, f"{obj_type} {handle}", [handle], spec_confidence,
                    bbox=bbox, properties=spec_props, source=f"rule:{domain}",
                    rule_name=f"{domain}_{obj_type}",
                ))
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
                rule_name="circle_feature",
            ))
        elif "hatch" in etype:
            obj_type = "section_region" if domain == "mechanical" else "filled_region"
            objects.append(_object_row(
                obj_type, f"{obj_type} {handle}", [handle], 0.7,
                bbox=bbox, properties={"pattern": geom.get("pattern")}, source=f"rule:{domain}",
                rule_name="hatch_region",
            ))
        elif "dimension" in etype:
            dimensions.append(entity)
            objects.append(_object_row(
                "dimension_annotation", f"dimension {handle}", [handle], 0.8,
                bbox=bbox, properties=geom, source=f"rule:{domain}",
                rule_name="dimension_annotation",
            ))
        elif "text" in etype:
            texts.append(entity)
            obj_type = "room_label" if domain == "architecture" else "text_annotation"
            drafting = _drafting_object_type(entity)
            if domain == "drafting" and drafting:
                obj_type = drafting[0]
            objects.append(_object_row(
                obj_type,
                str(geom.get("text") or geom.get("text_string") or f"text {handle}")[:80],
                [handle],
                0.76 if obj_type != "text_annotation" else 0.72,
                bbox=bbox,
                properties={"text": geom.get("text") or geom.get("text_string")},
                source=f"rule:{domain}",
                rule_name=f"{domain}_{obj_type}",
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
                obj_type = "terminal" if "terminal" in lower_label else "component_symbol"
            elif domain == "drafting":
                drafting = _drafting_object_type(entity)
                obj_type = drafting[0] if drafting else obj_type
            objects.append(_object_row(
                obj_type, label, [handle], 0.74,
                bbox=bbox, properties={"block_name": label}, source=f"rule:{domain}",
                rule_name=f"{domain}_{obj_type}",
            ))
        elif domain == "mechanical" and ("center" in text or "center" in str(entity.get("linetype", "")).lower()):
            objects.append(_object_row(
                "centerline", f"centerline {handle}", [handle], 0.65,
                bbox=bbox, properties={"layer": entity.get("layer")}, source=f"rule:{domain}",
                rule_name="mechanical_centerline",
            ))
        else:
            specific = _domain_specific_object(entity, domain, etype, text, bbox)
            if specific:
                obj_type, spec_confidence, spec_props = specific
                objects.append(_object_row(
                    obj_type, f"{obj_type} {handle}", [handle], spec_confidence,
                    bbox=bbox, properties={**spec_props, "layer": entity.get("layer")},
                    source=f"rule:{domain}",
                    rule_name=f"{domain}_{obj_type}",
                ))

    pattern_objects = _detect_circle_patterns(circles, domain)
    objects.extend(pattern_objects)

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
            rule_name="largest_profile_outer_candidate",
        ))
        outer_bbox = bbox_from_row(largest)
        for profile in profiles:
            handle = str(profile.get("handle"))
            if handle == largest_handle:
                continue
            profile_bbox = bbox_from_row(profile)
            if bbox_contains(outer_bbox, profile_bbox):
                objects.append(_object_row(
                    "inner_profile" if domain == "mechanical" else "inner_closed_profile",
                    f"inner profile {handle}",
                    [handle],
                    0.62,
                    bbox=profile_bbox,
                    properties={"selection": "closed profile inside largest profile"},
                    source=f"rule:{domain}",
                    rule_name="inner_profile_candidate",
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
    for pattern in pattern_objects:
        for handle in pattern.get("entity_handles", []):
            for member in by_handle.get(handle, []):
                if member["object_id"] != pattern["object_id"]:
                    relations.append(_relation_row(
                        "pattern_member_of",
                        member["object_id"],
                        pattern["object_id"],
                        0.72,
                        {"reason": "circle member of repeated radius group", "evidence_handles": [handle]},
                    ))

    for dimension in dimensions:
        dim_bbox = bbox_from_row(dimension)
        if not dim_bbox:
            continue
        dim_objects = by_handle.get(str(dimension.get("handle")), [])
        if not dim_objects:
            continue
        nearby = [
            entity for entity in entities
            if str(entity.get("handle")) != str(dimension.get("handle"))
            and bbox_intersects(dim_bbox, bbox_from_row(entity))
        ][:5]
        for entity in nearby:
            for target in by_handle.get(str(entity.get("handle")), []):
                relations.append(_relation_row(
                    "dimension_of",
                    dim_objects[0]["object_id"],
                    target["object_id"],
                    0.42,
                    {"reason": "dimension bbox intersects target bbox", "evidence_handles": [str(dimension.get("handle")), str(entity.get("handle"))]},
                ))

    for text_entity in texts:
        text_bbox = bbox_from_row(text_entity)
        text_center = bbox_center(text_bbox)
        if not text_center:
            continue
        text_objs = by_handle.get(str(text_entity.get("handle")), [])
        if not text_objs:
            continue
        nearest_obj = None
        nearest_dist = float("inf")
        for obj in objects:
            if obj["object_id"] == text_objs[0]["object_id"]:
                continue
            center = bbox_center(obj.get("bbox"))
            if center:
                dist = point_distance(text_center, center)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_obj = obj
        if nearest_obj and nearest_dist < max(math.sqrt(bbox_area_safe(nearest_obj.get("bbox"))), 1.0) * 4.0:
            relations.append(_relation_row(
                "label_of",
                text_objs[0]["object_id"],
                nearest_obj["object_id"],
                0.45,
                {"reason": "nearest semantic object to text label", "distance": nearest_dist},
            ))

    for i, first in enumerate(lines[:120]):
        a1 = line_angle(first)
        if a1 is None:
            continue
        for second in lines[i + 1:i + 40]:
            a2 = line_angle(second)
            if a2 is None:
                continue
            delta = abs((a1 - a2 + math.pi / 2.0) % math.pi - math.pi / 2.0)
            first_objs = by_handle.get(str(first.get("handle")), [])
            second_objs = by_handle.get(str(second.get("handle")), [])
            if not first_objs or not second_objs:
                continue
            if delta <= 1e-5:
                relations.append(_relation_row(
                    "parallel_to", first_objs[0]["object_id"], second_objs[0]["object_id"], 0.68,
                    {"angle_delta_radians": delta},
                ))
            elif abs(delta - math.pi / 2.0) <= 1e-5:
                relations.append(_relation_row(
                    "perpendicular_to", first_objs[0]["object_id"], second_objs[0]["object_id"], 0.66,
                    {"angle_delta_radians": abs(a1 - a2)},
                ))

    circle_refs = [(entity, circle_center_radius(entity)) for entity in circles]
    for i, (first, cr1) in enumerate(circle_refs):
        if not cr1:
            continue
        for second, cr2 in circle_refs[i + 1:i + 40]:
            if not cr2:
                continue
            dist = point_distance(cr1[0], cr2[0])
            if dist <= 1e-6:
                first_objs = by_handle.get(str(first.get("handle")), [])
                second_objs = by_handle.get(str(second.get("handle")), [])
                if first_objs and second_objs:
                    relations.append(_relation_row(
                        "concentric_with", first_objs[0]["object_id"], second_objs[0]["object_id"], 0.7,
                        {"center_distance": dist},
                    ))

    relations.extend(_add_spatial_relations(objects))

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


def _bbox_from_public(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(value, dict) and isinstance(value.get("min"), list) and isinstance(value.get("max"), list):
        return (
            float(value["min"][0]),
            float(value["min"][1]),
            float(value["max"][0]),
            float(value["max"][1]),
        )
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    return None


def find_semantic_objects(object_type: Optional[str] = None,
                          label_query: Optional[str] = None,
                          handle: Optional[str] = None,
                          bbox_region: Optional[List[float]] = None,
                          domain: Optional[str] = None,
                          confidence_threshold: float = 0.0,
                          top_k: int = 20,
                          database: Optional[CADDatabase] = None) -> ToolResult:
    graph = _read_graph(get_db(database))
    object_type_norm = (object_type or "").lower().strip()
    label_norm = (label_query or "").lower().strip()
    handle_norm = (handle or "").strip()
    domain_norm = (domain or "").lower().strip()
    query_bbox = _bbox_from_public(bbox_region)
    matches = []
    for obj in graph["semantic_objects"]:
        score = 0.0
        confidence = float(obj.get("confidence") or 0.0)
        if confidence < float(confidence_threshold or 0.0):
            continue
        if handle_norm and handle_norm not in [str(h) for h in obj.get("entity_handles", [])]:
            continue
        if domain_norm and domain_norm not in str(obj.get("source", "")).lower():
            continue
        if query_bbox and not bbox_intersects(_bbox_from_public(obj.get("bbox")), query_bbox):
            continue
        if object_type_norm and object_type_norm in str(obj.get("object_type", "")).lower():
            score += 0.6
        if label_norm and label_norm in str(obj.get("label", "")).lower():
            score += 0.4
        if handle_norm:
            score += 0.25
        if domain_norm:
            score += 0.15
        if query_bbox:
            score += 0.2
        if not any([object_type_norm, label_norm, handle_norm, domain_norm, query_bbox]):
            score = confidence
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
