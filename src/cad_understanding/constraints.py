"""Constraint extraction and checking over scanned CAD metadata."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .common import (
    all_entities,
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
    line_length,
    line_points,
    point_distance,
    stable_id,
)
from .result import ToolResult, ok_result


def _constraint(constraint_type: str,
                source: str,
                handles: List[str],
                value: Optional[float],
                actual: Optional[float],
                tolerance: Optional[float],
                unit: str,
                confidence: float,
                status: str,
                evidence: Optional[Dict[str, Any]] = None,
                object_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "constraint_id": stable_id("con", constraint_type, source, ",".join(sorted(handles)), value, actual),
        "constraint_type": constraint_type,
        "source": source,
        "target_handles": sorted(set(handles)),
        "target_object_ids": sorted(set(object_ids or [])),
        "value": value,
        "actual": actual,
        "tolerance": tolerance,
        "unit": unit,
        "confidence": round(float(confidence), 3),
        "status": status,
        "evidence": evidence or {},
    }


def _replace_constraints(database: CADDatabase,
                         constraints: List[Dict[str, Any]],
                         source_prefixes: List[str]) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        for source_prefix in source_prefixes:
            conn.execute('''
                DELETE FROM cad_constraints
                WHERE workspace_id = ? AND drawing_id = ?
                  AND conversation_id = ? AND thread_id = ?
                  AND source LIKE ?
            ''', (
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
                f"{source_prefix}%",
            ))
        for item in constraints:
            conn.execute('''
                INSERT OR REPLACE INTO cad_constraints
                    (constraint_id, constraint_type, source, target_handles,
                     target_object_ids, value, actual, tolerance, unit,
                     confidence, status, evidence, workspace_id, drawing_id,
                     conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item["constraint_id"], item["constraint_type"], item["source"],
                json.dumps(item.get("target_handles", []), ensure_ascii=False),
                json.dumps(item.get("target_object_ids", []), ensure_ascii=False),
                item.get("value"), item.get("actual"), item.get("tolerance"),
                item.get("unit", ""), item.get("confidence", 0.0),
                item.get("status", "unknown"),
                json.dumps(item.get("evidence", {}), ensure_ascii=False),
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))


def _read_constraints(database: CADDatabase,
                      status: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    params: List[Any] = [
        scope["workspace_id"], scope["drawing_id"],
        scope["conversation_id"], scope["thread_id"],
    ]
    status_sql = ""
    if status:
        status_sql = "AND status = ?"
        params.append(status)
    with database._conn() as conn:
        rows = conn.execute(f'''
            SELECT constraint_id, constraint_type, source, target_handles,
                   target_object_ids, value, actual, tolerance, unit,
                   confidence, status, evidence, created_at
            FROM cad_constraints
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              {status_sql}
            ORDER BY constraint_type, constraint_id
        ''', params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["target_handles"] = decode_json(item.get("target_handles"), [])
        item["target_object_ids"] = decode_json(item.get("target_object_ids"), [])
        item["evidence"] = decode_json(item.get("evidence"))
        result.append(item)
    return result


def _dimension_value(entity: Dict[str, Any]) -> Optional[float]:
    geom = entity_geometry(entity)
    props = decode_json(entity.get("properties"))
    for key in ("measurement", "actual_measurement", "value", "distance", "radius", "diameter"):
        for source in (geom, props):
            try:
                value = source.get(key)
                if value is not None:
                    return float(value)
            except Exception:
                pass
    text = entity_text(entity)
    numeric = ""
    for char in text:
        if char.isdigit() or char in ".-":
            numeric += char
        elif numeric:
            break
    try:
        return float(numeric) if numeric else None
    except Exception:
        return None


def extract_dimension_constraints(database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    constraints = []
    for entity in all_entities(db):
        if "dimension" not in entity_type(entity):
            continue
        handle = str(entity.get("handle"))
        value = _dimension_value(entity)
        ctype = "dimension_value"
        text = entity_text(entity)
        if "radial" in text or "radius" in text:
            ctype = "radius"
        elif "diametric" in text or "diameter" in text:
            ctype = "diameter"
        constraints.append(_constraint(
            ctype,
            "dimension:scanned",
            [handle],
            value=value,
            actual=None,
            tolerance=None,
            unit="drawing_units",
            confidence=0.55 if value is not None else 0.35,
            status="unknown",
            evidence={
                "reason": "Dimension binding to measured geometry is not known from scanned metadata.",
                "dimension_text": text[:120],
            },
        ))
    _replace_constraints(db, constraints, ["dimension:"])
    return ok_result(
        f"Extracted {len(constraints)} dimension constraints with unknown binding status.",
        data={"constraints": constraints},
        handles=[h for c in constraints for h in c["target_handles"]],
        warnings=["Dimension constraints are status=unknown until their measured geometry can be bound confidently."],
        next_tools=["infer_geometric_constraints", "check_drawing_constraints"],
    )


def infer_geometric_constraints(database: Optional[CADDatabase] = None,
                                tolerance: float = 1e-6) -> ToolResult:
    db = get_db(database)
    entities = all_entities(db)
    constraints: List[Dict[str, Any]] = []
    lines = [entity for entity in entities if "line" in entity_type(entity) and "polyline" not in entity_type(entity)]
    circles = [entity for entity in entities if "circle" in entity_type(entity)]

    for entity in circles:
        cr = circle_center_radius(entity)
        if not cr:
            continue
        handle = str(entity.get("handle"))
        _, radius = cr
        constraints.append(_constraint(
            "radius", "geometry:circle", [handle], radius, radius,
            tolerance, "drawing_units", 0.95, "satisfied",
            {"reason": "Radius read from circle geometry or bbox."},
        ))
        constraints.append(_constraint(
            "diameter", "geometry:circle", [handle], radius * 2.0, radius * 2.0,
            tolerance, "drawing_units", 0.95, "satisfied",
            {"reason": "Diameter inferred as 2x radius."},
        ))

    for entity in lines:
        length = line_length(entity)
        if length is None:
            continue
        handle = str(entity.get("handle"))
        constraints.append(_constraint(
            "distance", "geometry:line_length", [handle], length, length,
            tolerance, "drawing_units", 0.9, "satisfied",
            {"reason": "Line endpoint distance."},
        ))

    for i, first in enumerate(lines):
        a1 = line_angle(first)
        if a1 is None:
            continue
        for second in lines[i + 1:i + 100]:
            a2 = line_angle(second)
            if a2 is None:
                continue
            delta = abs((a1 - a2 + math.pi / 2.0) % math.pi - math.pi / 2.0)
            handles = [str(first.get("handle")), str(second.get("handle"))]
            if delta <= 1e-5:
                constraints.append(_constraint(
                    "parallel", "geometry:line_pair", handles, 0.0, delta,
                    1e-5, "radians", 0.82, "satisfied",
                    {"angle_delta_radians": delta},
                ))
            perp_delta = abs(delta - math.pi / 2.0)
            if perp_delta <= 1e-5:
                constraints.append(_constraint(
                    "perpendicular", "geometry:line_pair", handles, math.pi / 2.0,
                    abs(a1 - a2), 1e-5, "radians", 0.8, "satisfied",
                    {"angle_delta_radians": abs(a1 - a2)},
                ))

    endpoints: List[tuple[str, List[float]]] = []
    for entity in lines:
        points = line_points(entity)
        if points:
            endpoints.append((str(entity.get("handle")), points[0]))
            endpoints.append((str(entity.get("handle")), points[1]))
    for i, (h1, p1) in enumerate(endpoints):
        for h2, p2 in endpoints[i + 1:i + 80]:
            if h1 == h2:
                continue
            dist = point_distance(p1, p2)
            if dist <= tolerance:
                constraints.append(_constraint(
                    "coincident_endpoint", "geometry:endpoint_pair",
                    [h1, h2], 0.0, dist, tolerance, "drawing_units",
                    0.78, "satisfied", {"distance": dist},
                ))

    circle_centers = [(entity, circle_center_radius(entity)) for entity in circles]
    for i, (first, cr1) in enumerate(circle_centers):
        if not cr1:
            continue
        for second, cr2 in circle_centers[i + 1:i + 50]:
            if not cr2:
                continue
            dist = point_distance(cr1[0], cr2[0])
            if dist <= tolerance:
                constraints.append(_constraint(
                    "concentric", "geometry:circle_pair",
                    [str(first.get("handle")), str(second.get("handle"))],
                    0.0, dist, tolerance, "drawing_units",
                    0.83, "satisfied", {"center_distance": dist},
                ))

    for entity in entities:
        if is_closed_polyline(entity):
            handle = str(entity.get("handle"))
            constraints.append(_constraint(
                "closed_profile", "geometry:polyline", [handle], 1.0, 1.0,
                tolerance, "boolean", 0.9, "satisfied",
                {"reason": "Polyline closed flag or first/last vertex match."},
            ))

    radius_groups: Dict[float, List[str]] = {}
    for entity in circles:
        cr = circle_center_radius(entity)
        if not cr:
            continue
        radius_groups.setdefault(round(cr[1], 3), []).append(str(entity.get("handle")))
    for radius, handles in radius_groups.items():
        if len(handles) >= 3:
            constraints.append(_constraint(
                "repeated_pattern_count", "geometry:circle_radius_group",
                handles, float(len(handles)), float(len(handles)), 0.0, "count",
                0.72, "satisfied",
                {"radius": radius, "reason": "Repeated circles with matching radius."},
            ))

    _replace_constraints(db, constraints, ["geometry:"])
    return ok_result(
        f"Inferred {len(constraints)} geometric constraints.",
        data={"constraints": constraints},
        handles=[h for c in constraints for h in c["target_handles"]],
        next_tools=["check_drawing_constraints", "validate_geometry"],
    )


def check_constraint_satisfaction(tolerance: float = 1e-6,
                                  database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    constraints = _read_constraints(db)
    checked = []
    for item in constraints:
        status = item.get("status") or "unknown"
        value = item.get("value")
        actual = item.get("actual")
        tol = item.get("tolerance")
        if value is not None and actual is not None:
            try:
                tol_value = float(tol if tol is not None else tolerance)
                status = "satisfied" if abs(float(value) - float(actual)) <= tol_value else "violated"
            except Exception:
                status = "unknown"
        elif status != "satisfied":
            status = "unknown"
        checked_item = {**item, "status": status}
        checked.append(checked_item)
    _replace_constraints(db, checked, ["geometry:", "dimension:"])
    counts: Dict[str, int] = {}
    for item in checked:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return ok_result(
        f"Checked {len(checked)} constraints.",
        data={"constraints": checked, "status_counts": counts},
        handles=[h for c in checked for h in c["target_handles"]],
        warnings=["Unknown status means the scan did not provide enough binding evidence."],
        next_tools=["get_drawing_constraints", "validate_geometry"],
    )


def get_constraints(status: Optional[str] = None,
                    database: Optional[CADDatabase] = None) -> ToolResult:
    constraints = _read_constraints(get_db(database), status=status)
    return ok_result(
        f"Loaded {len(constraints)} constraints.",
        data={"constraints": constraints},
        handles=[h for c in constraints for h in c["target_handles"]],
        next_tools=["check_drawing_constraints", "validate_geometry"],
    )


def extract_drawing_constraints(database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    dim = extract_dimension_constraints(db)
    geom = infer_geometric_constraints(db)
    constraints = _read_constraints(db)
    warnings = []
    warnings.extend(dim.get("warnings", []))
    warnings.extend(geom.get("warnings", []))
    return ok_result(
        f"Extracted {len(constraints)} total drawing constraints.",
        data={"constraints": constraints},
        handles=[h for c in constraints for h in c["target_handles"]],
        warnings=warnings,
        next_tools=["check_drawing_constraints", "validate_geometry"],
    )

