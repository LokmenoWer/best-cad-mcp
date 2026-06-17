"""Dimension annotation to geometry binding over scanned CAD metadata."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    bbox_area,
    bbox_center,
    bbox_dict,
    bbox_from_row,
    circle_center_radius,
    decode_json,
    entity_geometry,
    entity_text,
    entity_type,
    get_db,
    get_entity,
    line_length,
    line_points,
    point3,
    point_distance,
    topology_for_handle,
)
from .result import ToolResult, error_result, ok_result


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _numeric_from_text(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = (
        text.replace("%%c", "")
        .replace("%%C", "")
        .replace("Ø", "")
        .replace("ø", "")
        .replace("R", "")
        .replace("r", "")
    )
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", normalized)
    return _float(match.group(0)) if match else None


def _first_value(*sources: Dict[str, Any], keys: List[str]) -> Any:
    for source in sources:
        for key in keys:
            if isinstance(source, dict) and source.get(key) is not None:
                return source.get(key)
    return None


def _dimension_kind(entity: Dict[str, Any],
                    geometry: Dict[str, Any],
                    properties: Dict[str, Any]) -> str:
    tokens = " ".join([
        entity_type(entity),
        entity_text(entity),
        str(geometry.get("dimension_type") or geometry.get("dim_type") or ""),
        str(properties.get("dimension_type") or properties.get("dim_type") or ""),
        str(entity.get("name") or ""),
    ]).lower()
    if "diametric" in tokens or "diameter" in tokens or "acdbdiametric" in tokens:
        return "diametric"
    if "radial" in tokens or "radius" in tokens or "acdbradial" in tokens:
        return "radial"
    if "angular" in tokens:
        return "angular"
    if "ordinate" in tokens:
        return "ordinate"
    if "aligned" in tokens:
        return "aligned"
    if "linear" in tokens or "rotated" in tokens or "dimension" in tokens:
        return "linear"
    return "unknown"


def _measurement(entity: Dict[str, Any],
                 geometry: Dict[str, Any],
                 properties: Dict[str, Any]) -> Optional[float]:
    for key in ("measurement", "actual_measurement", "value", "distance", "length", "radius", "diameter"):
        value = _first_value(geometry, properties, entity, keys=[key])
        number = _float(value)
        if number is not None:
            return number
    return _numeric_from_text(entity_text(entity))


def _dimension_points(geometry: Dict[str, Any],
                      properties: Dict[str, Any]) -> Dict[str, Any]:
    point_keys = {
        "extension_line_1_point": [
            "extension_line_1_point", "xline1_point", "x_line_1_point",
            "definition_point_1", "def_point_1", "point1",
        ],
        "extension_line_2_point": [
            "extension_line_2_point", "xline2_point", "x_line_2_point",
            "definition_point_2", "def_point_2", "point2",
        ],
        "dimension_line_point": [
            "dimension_line_point", "dim_line_point", "text_position",
            "text_midpoint", "location",
        ],
        "center": ["center", "center_point"],
        "chord_point": ["chord_point", "arc_point"],
    }
    result: Dict[str, Any] = {}
    for name, keys in point_keys.items():
        raw = _first_value(geometry, properties, keys=keys)
        point = point3(raw)
        if point:
            result[name] = point
    raw_points = _first_value(geometry, properties, keys=["definition_points", "points"])
    if isinstance(raw_points, (list, tuple)):
        result["definition_points"] = [point for point in (point3(value) for value in raw_points) if point]
    return result


def extract_dimension_geometry(entity: Dict[str, Any]) -> Dict[str, Any]:
    geometry = entity_geometry(entity)
    properties = decode_json(entity.get("properties"))
    text_override = _first_value(
        geometry,
        properties,
        entity,
        keys=["text_override", "dimension_text", "text", "text_string", "TextOverride"],
    )
    text_value = _numeric_from_text(text_override)
    measurement = _measurement(entity, geometry, properties)
    dimension_type = _dimension_kind(entity, geometry, properties)
    bbox = bbox_from_row(entity)
    associated = _first_value(
        geometry,
        properties,
        keys=["associated_handles", "associative_handles", "target_handles", "measurement_handles"],
    )
    if not isinstance(associated, list):
        associated = []
    return {
        "dimension_handle": str(entity.get("handle")),
        "dimension_type": dimension_type,
        "measurement": measurement,
        "text_value": text_value,
        "text_override": str(text_override) if text_override is not None else None,
        "points": _dimension_points(geometry, properties),
        "associated_handles": [str(handle) for handle in associated],
        "bbox": bbox_dict(bbox),
        "raw_geometry": geometry,
        "raw_properties": properties,
    }


def _score_value(expected: Optional[float],
                 actual: Optional[float],
                 tolerance: float) -> Tuple[float, Dict[str, Any]]:
    if expected is None or actual is None:
        return 0.25, {"reason": "missing expected or actual measurement"}
    delta = abs(float(expected) - float(actual))
    scale = max(abs(float(expected)), abs(float(actual)), 1.0)
    if delta <= tolerance:
        return 1.0, {"delta": delta, "tolerance": tolerance, "reason": "within tolerance"}
    ratio = max(0.0, 1.0 - delta / max(scale, tolerance * 10.0))
    return min(0.85, ratio), {"delta": delta, "tolerance": tolerance, "reason": "value mismatch"}


def _score_bbox_nearness(dimension: Dict[str, Any],
                         entity: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    dim_bbox_raw = dimension.get("bbox") or {}
    dim_center = dim_bbox_raw.get("center") if isinstance(dim_bbox_raw, dict) else None
    ent_bbox = bbox_from_row(entity)
    ent_center = bbox_center(ent_bbox)
    if not dim_center or not ent_center:
        return 0.35, {"reason": "bbox center unavailable"}
    drawing_scale = max(math.sqrt(bbox_area(ent_bbox)), 1.0)
    distance = point_distance(dim_center, ent_center)
    score = max(0.0, 1.0 - distance / (drawing_scale * 12.0))
    return score, {"center_distance": distance, "scale": drawing_scale}


def _score_extension_points(dimension: Dict[str, Any],
                            entity: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    points = dimension.get("points", {})
    p1 = points.get("extension_line_1_point")
    p2 = points.get("extension_line_2_point")
    line = line_points(entity)
    if not p1 or not p2 or not line:
        return 0.35, {"reason": "dimension extension points or line endpoints unavailable"}
    a, b = line
    direct = point_distance(p1, a) + point_distance(p2, b)
    swapped = point_distance(p1, b) + point_distance(p2, a)
    best = min(direct, swapped)
    scale = max(point_distance(a, b), 1.0)
    return max(0.0, 1.0 - best / (scale * 2.0)), {"endpoint_distance_sum": best}


def _primitive_key_for_entity(database: CADDatabase,
                              handle: str,
                              preferred_types: Tuple[str, ...]) -> Optional[str]:
    topology = topology_for_handle(database, handle)
    for primitive in topology.get("primitives", []):
        ptype = str(primitive.get("primitive_type") or "")
        role = str(primitive.get("role") or "")
        if ptype in preferred_types or role in preferred_types:
            return str(primitive.get("primitive_key"))
    return None


def _candidate_for_circle(database: CADDatabase,
                          dimension: Dict[str, Any],
                          entity: Dict[str, Any],
                          tolerance: float) -> Optional[Dict[str, Any]]:
    cr = circle_center_radius(entity)
    if not cr:
        return None
    handle = str(entity.get("handle"))
    _, radius = cr
    actual = radius * 2.0 if dimension["dimension_type"] == "diametric" else radius
    expected = dimension.get("text_value") if dimension.get("text_value") is not None else dimension.get("measurement")
    if dimension["dimension_type"] == "radial" and expected is not None and abs(expected - radius * 2.0) < abs(expected - radius):
        # A scanned radial dimension may expose diameter as measurement; keep it
        # plausible but lower confidence.
        actual = radius * 2.0
    value_score, value_evidence = _score_value(expected, actual, tolerance)
    near_score, near_evidence = _score_bbox_nearness(dimension, entity)
    assoc_bonus = 0.12 if handle in dimension.get("associated_handles", []) else 0.0
    score = min(1.0, 0.72 * value_score + 0.28 * near_score + assoc_bonus)
    return {
        "handle": handle,
        "primitive_key": _primitive_key_for_entity(database, handle, ("curve", "circle", "arc")),
        "target_kind": "circle" if "circle" in entity_type(entity) else "arc",
        "actual": actual,
        "expected": expected,
        "score": round(score, 4),
        "evidence": {
            "entity_type": entity.get("type"),
            "radius": radius,
            "value_score": value_score,
            "value_evidence": value_evidence,
            "spatial_score": near_score,
            "spatial_evidence": near_evidence,
            "associated_handle_bonus": assoc_bonus,
        },
    }


def _candidate_for_line(database: CADDatabase,
                        dimension: Dict[str, Any],
                        entity: Dict[str, Any],
                        tolerance: float) -> Optional[Dict[str, Any]]:
    length = line_length(entity)
    if length is None:
        return None
    handle = str(entity.get("handle"))
    expected = dimension.get("text_value") if dimension.get("text_value") is not None else dimension.get("measurement")
    value_score, value_evidence = _score_value(expected, length, tolerance)
    endpoint_score, endpoint_evidence = _score_extension_points(dimension, entity)
    near_score, near_evidence = _score_bbox_nearness(dimension, entity)
    assoc_bonus = 0.12 if handle in dimension.get("associated_handles", []) else 0.0
    score = min(1.0, 0.62 * value_score + 0.23 * endpoint_score + 0.15 * near_score + assoc_bonus)
    return {
        "handle": handle,
        "primitive_key": _primitive_key_for_entity(database, handle, ("line", "edge", "segment")),
        "target_kind": "line",
        "actual": length,
        "expected": expected,
        "score": round(score, 4),
        "evidence": {
            "entity_type": entity.get("type"),
            "length": length,
            "value_score": value_score,
            "value_evidence": value_evidence,
            "extension_point_score": endpoint_score,
            "extension_point_evidence": endpoint_evidence,
            "spatial_score": near_score,
            "spatial_evidence": near_evidence,
            "associated_handle_bonus": assoc_bonus,
        },
    }


def _rank_candidates(database: CADDatabase,
                     dimension: Dict[str, Any],
                     entities: List[Dict[str, Any]],
                     tolerance: float) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    kind = dimension.get("dimension_type")
    for entity in entities:
        if str(entity.get("handle")) == dimension.get("dimension_handle"):
            continue
        etype = entity_type(entity)
        if "dimension" in etype:
            continue
        candidate = None
        if kind in {"radial", "diametric"} and ("circle" in etype or "arc" in etype):
            candidate = _candidate_for_circle(database, dimension, entity, tolerance)
        elif kind in {"linear", "aligned", "unknown"} and "line" in etype and "polyline" not in etype:
            candidate = _candidate_for_line(database, dimension, entity, tolerance)
        elif kind in {"linear", "aligned", "unknown"} and "polyline" in etype:
            candidate = _candidate_for_line(database, dimension, entity, tolerance)
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: -float(item.get("score") or 0.0))
    return candidates


def bind_dimension_to_geometry_data(handle: str,
                                    database: Optional[CADDatabase] = None,
                                    tolerance: float = 1e-3) -> Dict[str, Any]:
    db = get_db(database)
    entity = get_entity(db, handle)
    if not entity:
        return {
            "dimension_handle": handle,
            "dimension_type": "unknown",
            "measurement": None,
            "text_value": None,
            "text_override": None,
            "candidate_targets": [],
            "best_target": None,
            "status": "unbound",
            "confidence": 0.0,
            "evidence": {"reason": "dimension entity not found"},
        }
    dimension = extract_dimension_geometry(entity)
    entities = all_entities(db)
    candidates = _rank_candidates(db, dimension, entities, tolerance)
    best = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    status = "unbound"
    confidence = 0.0
    warnings: List[str] = []
    if best:
        confidence = float(best.get("score") or 0.0)
        if confidence >= 0.64 and (not second or confidence - float(second.get("score") or 0.0) >= 0.08):
            status = "bound"
        elif confidence >= 0.45:
            status = "ambiguous"
            warnings.append("Multiple plausible geometry targets have similar scores.")
        else:
            status = "unbound"
    else:
        warnings.append("No plausible geometry target was found for this dimension.")
    result = {
        **dimension,
        "candidate_targets": candidates[:10],
        "best_target": best,
        "status": status,
        "confidence": round(confidence, 4),
        "warnings": warnings,
        "evidence": {
            "candidate_count": len(candidates),
            "binding_rule": "value+spatial+association heuristic",
            "tolerance": tolerance,
        },
    }
    return result


def bind_dimension_to_geometry(handle: str,
                               database: Optional[CADDatabase] = None,
                               tolerance: float = 1e-3) -> ToolResult:
    result = bind_dimension_to_geometry_data(handle, database=database, tolerance=tolerance)
    ok = result.get("status") in {"bound", "ambiguous", "unbound"}
    handles = [handle]
    best = result.get("best_target") or {}
    if best.get("handle"):
        handles.append(str(best["handle"]))
    if not ok:
        return error_result(
            f"Could not bind dimension {handle}.",
            data={"binding": result},
            warnings=result.get("warnings", []),
        )
    return ok_result(
        f"Dimension {handle} binding status: {result.get('status')}.",
        data={"binding": result},
        handles=sorted(set(handles)),
        warnings=result.get("warnings", []),
        next_tools=["extract_drawing_constraints", "validate_geometry", "explain_entity"],
    )


def bind_all_dimensions(database: Optional[CADDatabase] = None,
                        tolerance: float = 1e-3) -> ToolResult:
    db = get_db(database)
    bindings = [
        bind_dimension_to_geometry_data(str(entity.get("handle")), database=db, tolerance=tolerance)
        for entity in all_entities(db)
        if "dimension" in entity_type(entity)
    ]
    counts: Dict[str, int] = {}
    for binding in bindings:
        status = str(binding.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    handles = sorted({
        str(binding.get("dimension_handle"))
        for binding in bindings
        if binding.get("dimension_handle")
    } | {
        str((binding.get("best_target") or {}).get("handle"))
        for binding in bindings
        if (binding.get("best_target") or {}).get("handle")
    })
    return ok_result(
        f"Bound {len(bindings)} dimension annotations.",
        data={"bindings": bindings, "status_counts": counts},
        handles=handles,
        warnings=["Ambiguous dimensions remain unknown during constraint checks."],
        next_tools=["extract_drawing_constraints", "check_drawing_constraints", "validate_geometry"],
    )

