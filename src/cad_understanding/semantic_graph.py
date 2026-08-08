"""Rule-based semantic object detection over scanned CAD metadata."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
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
from .drawing_graph import infer_cross_entity_closed_profiles
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


def _median(values: List[float]) -> float:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _spatial_circle_components(members: List[Dict[str, Any]],
                               radius: float) -> List[List[Dict[str, Any]]]:
    """Split equal-radius circles into locally connected candidate patterns."""
    centers = [circle_center_radius(member)[0] for member in members]
    nearest: List[float] = []
    for index, center in enumerate(centers):
        distances = [
            point_distance(center, other)
            for other_index, other in enumerate(centers)
            if other_index != index and point_distance(center, other) > 1e-9
        ]
        if distances:
            nearest.append(min(distances))
    local_pitch = _median(nearest)
    if local_pitch <= 0.0:
        return []
    link_limit = max(radius * 4.0, local_pitch * 2.5)
    unvisited = set(range(len(members)))
    components: List[List[Dict[str, Any]]] = []
    while unvisited:
        pending = [unvisited.pop()]
        indices: List[int] = []
        while pending:
            index = pending.pop()
            indices.append(index)
            neighbors = [
                other_index for other_index in list(unvisited)
                if point_distance(centers[index], centers[other_index]) <= link_limit
            ]
            for other_index in neighbors:
                unvisited.remove(other_index)
                pending.append(other_index)
        if len(indices) >= 3:
            components.append([members[index] for index in sorted(indices)])
    return components


def _linear_pattern_quality(centers: List[Tuple[float, float]],
                            radius: float) -> Optional[float]:
    farthest: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
    span = 0.0
    for index, first in enumerate(centers):
        for second in centers[index + 1:]:
            distance = point_distance(first, second)
            if distance > span:
                span = distance
                farthest = (first, second)
    if farthest is None or span <= 1e-9:
        return None
    start, end = farthest
    dx = (end[0] - start[0]) / span
    dy = (end[1] - start[1]) / span
    perpendicular_limit = max(radius * 0.35, span * 0.01, 1e-6)
    projections: List[float] = []
    for center in centers:
        rel_x = center[0] - start[0]
        rel_y = center[1] - start[1]
        if abs(rel_x * dy - rel_y * dx) > perpendicular_limit:
            return None
        projections.append(rel_x * dx + rel_y * dy)
    projections.sort()
    gaps = [
        second - first for first, second in zip(projections, projections[1:])
        if second - first > 1e-9
    ]
    mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
    if mean_gap <= 0.0 or len(gaps) != len(centers) - 1:
        return None
    gap_deviation = math.sqrt(sum(
        (gap - mean_gap) ** 2 for gap in gaps
    ) / len(gaps)) / mean_gap
    return gap_deviation if gap_deviation <= 0.15 else None


def _axis_grid_pattern_quality(centers: List[Tuple[float, float]],
                               radius: float) -> Optional[float]:
    tolerance = max(radius * 0.35, 1e-6)

    def clustered(values: List[float]) -> List[float]:
        groups: List[List[float]] = []
        for value in sorted(values):
            if not groups or abs(value - sum(groups[-1]) / len(groups[-1])) > tolerance:
                groups.append([value])
            else:
                groups[-1].append(value)
        return [sum(group) / len(group) for group in groups]

    xs = clustered([center[0] for center in centers])
    ys = clustered([center[1] for center in centers])
    if len(xs) < 2 or len(ys) < 2 or len(xs) * len(ys) != len(centers):
        return None
    occupied = set()
    for x, y in centers:
        x_index = min(range(len(xs)), key=lambda index: abs(xs[index] - x))
        y_index = min(range(len(ys)), key=lambda index: abs(ys[index] - y))
        if abs(xs[x_index] - x) > tolerance or abs(ys[y_index] - y) > tolerance:
            return None
        occupied.add((x_index, y_index))
    if len(occupied) != len(centers):
        return None
    normalized_deviations: List[float] = []
    for coordinates in (xs, ys):
        gaps = [second - first for first, second in zip(coordinates, coordinates[1:])]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
        if mean_gap <= 0.0:
            return None
        normalized_deviations.append(math.sqrt(sum(
            (gap - mean_gap) ** 2 for gap in gaps
        ) / len(gaps)) / mean_gap)
    quality = max(normalized_deviations, default=0.0)
    return quality if quality <= 0.15 else None


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
    for radius, radius_members in groups.items():
        for members in _spatial_circle_components(radius_members, radius):
            centers = [
                circle_center_radius(member)[0]
                for member in members if circle_center_radius(member)
            ]
            centroid = [
                sum(center[0] for center in centers) / len(centers),
                sum(center[1] for center in centers) / len(centers),
            ]
            distances = [point_distance(center, centroid) for center in centers]
            mean_distance = sum(distances) / len(distances) if distances else 0.0
            radial_deviation = (
                math.sqrt(sum(
                    (distance - mean_distance) ** 2 for distance in distances
                ) / len(distances)) / mean_distance
                if mean_distance > 1e-9 else float("inf")
            )
            linear_quality = _linear_pattern_quality(centers, radius)
            grid_quality = _axis_grid_pattern_quality(centers, radius)
            radial_pattern = mean_distance > radius and radial_deviation <= 0.15
            if not radial_pattern and linear_quality is None and grid_quality is None:
                continue
            pattern_type = (
                "bolt_circle_pattern"
                if domain == "mechanical" and radial_pattern else "hole_pattern"
            )
            quality = (
                radial_deviation if radial_pattern
                else min(
                    value for value in (linear_quality, grid_quality)
                    if value is not None
                )
            )
            confidence = max(0.68, min(0.84, 0.84 - 0.5 * quality))
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
                    "radial_deviation_ratio": radial_deviation,
                    "linear_spacing_deviation_ratio": linear_quality,
                    "grid_spacing_deviation_ratio": grid_quality,
                    "spatial_component_count": len(members),
                },
                source=f"rule:{domain}",
                rule_name="coherent_equal_radius_pattern",
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


def _finite_profile_polygon(value: Any) -> List[List[float]]:
    """Return a small, finite 2D profile polygon for nesting checks.

    Semantic nesting is only a prior.  Invalid, unbounded, or degenerate
    geometry therefore fails closed instead of turning an AABB coincidence
    into an inner/outer-profile claim.
    """
    if not isinstance(value, list) or len(value) > 8192:
        return []
    points: List[List[float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return []
        try:
            point = [float(raw[0]), float(raw[1])]
        except (TypeError, ValueError, OverflowError):
            return []
        if not all(math.isfinite(component) for component in point):
            return []
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 3 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        return []
    scale = max(
        1.0,
        *(abs(component) for point in points for component in point),
    )
    area_twice = abs(math.fsum(
        (points[index][0] / scale) * (points[(index + 1) % len(points)][1] / scale)
        - (points[(index + 1) % len(points)][0] / scale) * (points[index][1] / scale)
        for index in range(len(points))
    ))
    return points if math.isfinite(area_twice) and area_twice > 1e-12 else []


def _point_on_profile_boundary(point: List[float],
                               start: List[float],
                               end: List[float],
                               tolerance: float) -> bool:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= tolerance * tolerance:
        return math.hypot(point[0] - start[0], point[1] - start[1]) <= tolerance
    parameter = min(1.0, max(0.0, (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / length_squared))
    return math.hypot(
        point[0] - (start[0] + parameter * dx),
        point[1] - (start[1] + parameter * dy),
    ) <= tolerance


def _profile_polygon_contains_point(polygon: List[List[float]],
                                    point: List[float]) -> bool:
    scale = max(
        1.0,
        abs(point[0]),
        abs(point[1]),
        *(abs(component) for vertex in polygon for component in vertex),
    )
    tolerance = scale * 1e-9
    for start, end in zip(polygon, [*polygon[1:], polygon[0]]):
        if _point_on_profile_boundary(point, start, end, tolerance):
            return True
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (previous[1] > point[1]) != (current[1] > point[1]):
            crossing_x = previous[0] + (
                (point[1] - previous[1])
                * (current[0] - previous[0])
                / (current[1] - previous[1])
            )
            if crossing_x >= point[0] - tolerance:
                inside = not inside
        previous = current
    return inside


def _profile_containment_method(outer: Dict[str, Any],
                                inner: Dict[str, Any]) -> Optional[str]:
    """Return the evidence type when one reconstructed profile contains another."""
    outer_bbox = outer.get("bbox")
    inner_bbox = inner.get("bbox")
    if not outer_bbox or not inner_bbox or not bbox_contains(outer_bbox, inner_bbox):
        return None
    outer_area = bbox_area_safe(outer_bbox)
    inner_area = bbox_area_safe(inner_bbox)
    scale = max(1.0, outer_area, inner_area)
    if outer_area - inner_area <= scale * 1e-9:
        return None

    outer_polygon = _finite_profile_polygon(
        (outer.get("properties") or {}).get("vertices")
    )
    inner_polygon = _finite_profile_polygon(
        (inner.get("properties") or {}).get("vertices")
    )
    if not outer_polygon:
        return "bbox_fallback"

    if inner_polygon:
        probes: List[List[float]] = []
        for start, end in zip(inner_polygon, [*inner_polygon[1:], inner_polygon[0]]):
            probes.extend([
                start,
                [0.75 * start[0] + 0.25 * end[0], 0.75 * start[1] + 0.25 * end[1]],
                [0.5 * start[0] + 0.5 * end[0], 0.5 * start[1] + 0.5 * end[1]],
                [0.25 * start[0] + 0.75 * end[0], 0.25 * start[1] + 0.75 * end[1]],
            ])
    else:
        min_x, min_y, max_x, max_y = inner_bbox
        probes = [
            [min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y],
            [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0],
        ]
    return (
        "polygon_containment"
        if probes and all(
            _profile_polygon_contains_point(outer_polygon, point)
            for point in probes
        )
        else None
    )


def _profile_role_objects(objects: List[Dict[str, Any]],
                          domain: str) -> List[Dict[str, Any]]:
    """Classify every distinct closed loop by geometric nesting depth."""
    profiles_by_handles: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for obj in objects:
        if obj.get("object_type") != "closed_profile":
            continue
        handles = tuple(sorted({
            str(handle) for handle in obj.get("entity_handles", []) if handle
        }))
        if not handles or not obj.get("bbox"):
            continue
        current = profiles_by_handles.get(handles)
        current_vertices = _finite_profile_polygon(
            ((current or {}).get("properties") or {}).get("vertices")
        )
        vertices = _finite_profile_polygon(
            (obj.get("properties") or {}).get("vertices")
        )
        if current is None or (vertices and not current_vertices):
            profiles_by_handles[handles] = obj

    profiles = list(profiles_by_handles.values())
    roles: List[Dict[str, Any]] = []
    for profile in profiles:
        containers: List[Tuple[Dict[str, Any], str]] = []
        for possible_outer in profiles:
            if possible_outer is profile:
                continue
            method = _profile_containment_method(possible_outer, profile)
            if method:
                containers.append((possible_outer, method))
        containers.sort(key=lambda item: (
            bbox_area_safe(item[0].get("bbox")),
            str(item[0].get("object_id") or ""),
        ))
        depth = len(containers)
        is_mechanical = domain == "mechanical"
        object_type = (
            "outer_profile" if depth == 0 else "inner_profile"
        ) if is_mechanical else (
            "outer_closed_profile" if depth == 0 else "inner_closed_profile"
        )
        methods = sorted({method for _, method in containers})
        uses_bbox_fallback = "bbox_fallback" in methods
        source_confidence = float(profile.get("confidence") or 0.0)
        confidence = min(
            max(0.0, source_confidence - 0.02),
            0.58 if uses_bbox_fallback else (0.72 if depth == 0 else 0.7),
        )
        profile_properties = dict(profile.get("properties") or {})
        roles.append(_object_row(
            object_type,
            f"{'outer' if depth == 0 else 'inner'} profile {profile.get('object_id')}",
            list(profile.get("entity_handles") or []),
            confidence,
            bbox=profile.get("bbox"),
            properties={
                "selection": "closed profile classified by geometric nesting depth",
                "profile_object_id": profile.get("object_id"),
                "nesting_depth": depth,
                "container_object_ids": [
                    container.get("object_id") for container, _ in containers
                ],
                "containment_methods": methods,
                "vertices": profile_properties.get("vertices") or [],
            },
            source=f"rule:{domain}",
            rule_name="profile_geometric_nesting",
            warnings=(
                ["Profile nesting uses bounding-box fallback because an exact contour was unavailable."]
                if uses_bbox_fallback else []
            ),
        ))
    return roles


def detect_semantic_objects(domain: str = "generic",
                            database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    domain = (domain or "generic").lower().strip()
    entities = all_entities(db)
    objects: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    circles: List[Dict[str, Any]] = []
    dimensions: List[Dict[str, Any]] = []
    texts: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []

    for entity in entities:
        handle = str(entity.get("handle") or "")
        etype = entity_type(entity)
        text = entity_text(entity)
        bbox = bbox_from_row(entity)
        geom = entity_geometry(entity)
        if etype.replace(" ", "") in {"line", "acdbline"}:
            lines.append(entity)
        if "polyline" in etype and is_closed_polyline(entity):
            obj_type = "closed_profile"
            confidence = 0.78
            objects.append(_object_row(
                obj_type, f"closed profile {handle}", [handle], confidence,
                bbox=bbox, properties={
                    "closed": True,
                    "vertices": geom.get("vertices") or geom.get("points") or [],
                }, source=f"rule:{domain}",
                rule_name="closed_polyline_profile",
            ))
            specific = _domain_specific_object(entity, domain, etype, text, bbox)
            if specific:
                obj_type, spec_confidence, spec_props = specific
                objects.append(_object_row(
                    obj_type, f"{obj_type} {handle}", [handle], spec_confidence,
                    bbox=bbox, properties=spec_props, source=f"rule:{domain}",
                    rule_name=f"{domain}_{obj_type}",
                ))
        elif (
            (
                "ellipse" in etype
                and (
                    geom.get("is_arc") is False
                    or (
                        (geom.get("closed") is True or geom.get("is_closed") is True)
                        and geom.get("is_arc") is not True
                    )
                )
            )
            or ("spline" in etype and bool(geom.get("closed") or geom.get("is_closed")))
        ):
            curve_kind = "ellipse" if "ellipse" in etype else "spline"
            approximate = curve_kind == "spline" and not bool(geom.get("sampled_points"))
            objects.append(_object_row(
                "closed_profile",
                f"closed {curve_kind} profile {handle}",
                [handle],
                0.76 if curve_kind == "ellipse" else 0.64,
                bbox=bbox,
                properties={
                    "closed": True,
                    "curve_kind": curve_kind,
                    "approximate": approximate,
                },
                source=f"rule:{domain}",
                rule_name=f"closed_{curve_kind}_profile",
                warnings=(
                    ["Spline profile has no explicit sampled_points; topology is closed but path fidelity is approximate."]
                    if approximate else []
                ),
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

    cross_entity_profiles = infer_cross_entity_closed_profiles(db)
    for profile in cross_entity_profiles:
        objects.append(_object_row(
            "closed_profile",
            f"cross-entity closed profile {profile['profile_id']}",
            profile["entity_handles"],
            profile["confidence"],
            bbox=tuple(profile["bbox"]),
            properties={
                "selection": "drawing-level planar graph bounded face",
                "member_primitives": profile["member_primitives"],
                "vertices": profile["vertices"],
                "junction_vertices": profile.get("junction_vertices", []),
                "area": profile["area"],
                "perimeter": profile["perimeter"],
                "segment_count": profile["segment_count"],
                "boundary_edge_count": profile.get("boundary_edge_count", profile["segment_count"]),
                "branch_node_count": profile.get("branch_node_count", 0),
                "adjacent_bridge_count": profile.get("adjacent_bridge_count", 0),
                "curve_count": profile.get("curve_count", 0),
                "approximate_curve_count": profile.get("approximate_curve_count", 0),
                "max_curve_sampling_error": profile.get(
                    "max_curve_sampling_error"
                ),
                "max_certified_curve_sampling_error": profile.get(
                    "max_certified_curve_sampling_error", 0.0
                ),
                "source_coverage": profile.get("source_coverage", ()),
                "endpoint_tolerance": profile["endpoint_tolerance"],
                "max_endpoint_gap": profile["max_endpoint_gap"],
                "closure_quality": profile["closure_quality"],
                "topology_evidence": profile.get("topology_evidence", {}),
            },
            source=f"rule:{domain}",
            object_id=profile["profile_id"],
            # Keep the established rule name stable for downstream filters;
            # topology_evidence records the stronger bounded-face method.
            rule_name="cross_entity_endpoint_cycle",
        ))

    pattern_objects = _detect_circle_patterns(circles, domain)
    objects.extend(pattern_objects)

    # Keep topology (closed_profile) and role (outer/inner) as separate semantic
    # objects.  This lets consumers ask for a generic loop while preserving the
    # nesting evidence needed to disambiguate dense, concentric linework.
    objects.extend(_profile_role_objects(objects, domain))

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
            if first_objs[0]["object_id"] == second_objs[0]["object_id"]:
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
        warnings=[
            "Semantic detection is deterministic and rule-based; confidence reflects heuristic evidence.",
            "Cross-entity profiles atomically planarize LINE/POLYLINE crossings with analytic circular and elliptic boundaries; unresolved spline and non-circular curve/curve contact regions fail closed.",
        ],
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
        exact_type_match = False
        confidence = float(obj.get("confidence") or 0.0)
        if confidence < float(confidence_threshold or 0.0):
            continue
        if handle_norm and handle_norm not in [str(h) for h in obj.get("entity_handles", [])]:
            continue
        if domain_norm and domain_norm not in str(obj.get("source", "")).lower():
            continue
        if query_bbox and not bbox_intersects(_bbox_from_public(obj.get("bbox")), query_bbox):
            continue
        candidate_type = str(obj.get("object_type", "")).lower()
        if object_type_norm and object_type_norm in candidate_type:
            score += 0.6
            exact_type_match = candidate_type == object_type_norm
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
            matches.append({
                **obj,
                "score": round(min(score, 1.0), 3),
                "_exact_type_match": exact_type_match,
            })
    matches.sort(key=lambda item: (
        -int(bool(item.get("_exact_type_match"))),
        -item["score"],
        -float(item.get("confidence") or 0.0),
        item.get("label", ""),
    ))
    matches = matches[:max(1, min(int(top_k or 20), 100))]
    for item in matches:
        item.pop("_exact_type_match", None)
    return ok_result(
        f"Found {len(matches)} semantic objects.",
        data={"semantic_objects": matches},
        handles=sorted({h for obj in matches for h in obj.get("entity_handles", [])}),
        next_tools=["explain_entity", "get_semantic_graph"],
    )
