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
from .dimension_binding import bind_all_dimensions, bind_dimension_to_geometry_data
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
    warnings = []
    for entity in all_entities(db):
        if "dimension" not in entity_type(entity):
            continue
        handle = str(entity.get("handle"))
        binding = bind_dimension_to_geometry_data(handle, database=db)
        value = (
            binding.get("text_value")
            if binding.get("text_value") is not None
            else binding.get("measurement")
        )
        best_target = binding.get("best_target") or {}
        actual = best_target.get("actual") if binding.get("status") == "bound" else None
        tolerance = 1e-3 if binding.get("status") == "bound" else None
        status = "unknown"
        if binding.get("status") == "bound" and value is not None and actual is not None:
            status = "satisfied" if abs(float(value) - float(actual)) <= float(tolerance or 1e-3) else "violated"
        elif binding.get("status") in {"ambiguous", "unbound"}:
            status = "unknown"
        ctype_map = {
            "radial": "radius",
            "diametric": "diameter",
            "linear": "distance",
            "aligned": "distance",
            "angular": "angle",
            "ordinate": "ordinate",
        }
        ctype = ctype_map.get(str(binding.get("dimension_type") or ""), "dimension_value")
        handles = [handle]
        if best_target.get("handle"):
            handles.append(str(best_target["handle"]))
        if binding.get("status") != "bound":
            warnings.append(f"Dimension {handle} binding status is {binding.get('status')}; constraint remains unknown.")
        constraints.append(_constraint(
            ctype,
            "dimension:bound" if binding.get("status") == "bound" else "dimension:scanned",
            handles,
            value=value,
            actual=actual,
            tolerance=tolerance,
            unit="drawing_units",
            confidence=float(binding.get("confidence") or (0.55 if value is not None else 0.35)),
            status=status,
            evidence={
                "reason": (
                    "Dimension binding was resolved to geometry."
                    if binding.get("status") == "bound"
                    else "Dimension binding to measured geometry is ambiguous or unavailable."
                ),
                "dimension_binding": binding,
            },
        ))
    _replace_constraints(db, constraints, ["dimension:"])
    return ok_result(
        f"Extracted {len(constraints)} dimension constraints with geometry binding evidence.",
        data={"constraints": constraints},
        handles=[h for c in constraints for h in c["target_handles"]],
        warnings=warnings,
        next_tools=["bind_all_dimensions", "extract_drawing_constraints", "check_drawing_constraints"],
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


def propose_constraint_repair_plan(constraint_ids: Optional[List[str]] = None,
                                   database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    selected_ids = set(str(item) for item in (constraint_ids or []) if item)
    constraints = [
        item for item in _read_constraints(db)
        if item.get("status") == "violated"
        and (not selected_ids or item.get("constraint_id") in selected_ids)
    ]
    steps: List[Dict[str, Any]] = []
    affected: List[str] = []
    alternatives: List[Dict[str, Any]] = []
    high_risk = False
    for index, constraint in enumerate(constraints, start=1):
        handles = [str(handle) for handle in constraint.get("target_handles", []) if handle]
        affected.extend(handles)
        ctype = str(constraint.get("constraint_type") or "")
        expected = constraint.get("value")
        actual = constraint.get("actual")
        issue = {
            "constraint_id": constraint.get("constraint_id"),
            "constraint_type": ctype,
            "handles": handles,
            "expected": expected,
            "actual": actual,
        }
        if ctype in {"radius", "diameter"} and len(handles) >= 2 and expected and actual:
            target = handles[-1]
            scale = float(expected) / float(actual) if float(actual) else 1.0
            if ctype == "diameter":
                scale = float(expected) / float(actual) if float(actual) else 1.0
            steps.append({
                "step_id": f"constraint_repair_{index}",
                "op": "scale_entity",
                "args": {"handle": target, "base_point": [0, 0, 0], "scale": scale},
                "writes": True,
                "expect": {"constraint_id": constraint.get("constraint_id"), "status": "satisfied"},
                "postconditions": [
                    {"type": ctype, "target": target, "value": expected, "tolerance": constraint.get("tolerance") or 1e-3}
                ],
                "rationale": "Adjust candidate geometry to match the bound dimension value after dry-run review.",
            })
            high_risk = True
            alternatives.append({**issue, "alternative": "Edit the dimension text/annotation instead if geometry is authoritative."})
        elif ctype == "distance" and handles:
            target = handles[-1]
            steps.append({
                "step_id": f"constraint_repair_{index}",
                "op": "move_entity",
                "args": {
                    "handle": target,
                    "from_point": [0, 0, 0],
                    "to_point": [0, 0, 0],
                    "requires_manual_points": True,
                },
                "writes": True,
                "expect": {"constraint_id": constraint.get("constraint_id"), "status": "satisfied"},
                "postconditions": [
                    {"type": "distance", "target": target, "value": expected, "tolerance": constraint.get("tolerance") or 1e-3}
                ],
                "rationale": "Move/extend the measured geometry after selecting the intended endpoint correction.",
            })
            high_risk = True
            alternatives.append({**issue, "alternative": "Use endpoint-level repair when primitive binding identifies the measured endpoint."})
        elif ctype == "concentric" and len(handles) >= 2:
            steps.append({
                "step_id": f"constraint_repair_{index}",
                "op": "move_entity",
                "args": {
                    "handle": handles[-1],
                    "from_point": [0, 0, 0],
                    "to_point": [0, 0, 0],
                    "requires_manual_center_points": True,
                },
                "writes": True,
                "expect": {"constraint_id": constraint.get("constraint_id"), "status": "satisfied"},
                "rationale": "Move one candidate center onto the other only after confirming design intent.",
            })
            high_risk = True
        elif ctype == "closed_profile" and handles:
            steps.append({
                "step_id": f"constraint_repair_{index}",
                "op": "set_entity_properties",
                "args": {"handle": handles[-1], "properties": {"closed": True}},
                "writes": True,
                "expect": {"constraint_id": constraint.get("constraint_id"), "status": "satisfied"},
                "rationale": "Close the profile only if the boundary is intended to be closed.",
            })
        else:
            alternatives.append({**issue, "alternative": "Constraint type is ambiguous; inspect binding evidence before editing."})
    plan = {
        "plan_id": stable_id("plan", "constraint_repair", ",".join(selected_ids), len(constraints)),
        "description": "Constraint-solving repair plan proposed from violated constraints.",
        "units": "drawing_units",
        "steps": steps,
        "constraints": constraints,
        "risk_level": "high" if high_risk else "medium" if steps else "low",
        "requires_confirmation": True,
        "dry_run_available": True,
        "affected_handles": sorted(set(affected)),
        "alternatives": alternatives,
    }
    return ok_result(
        f"Proposed constraint repair plan with {len(steps)} executable steps and {len(alternatives)} alternatives.",
        data={"plan": plan, "constraints": constraints},
        handles=plan["affected_handles"],
        warnings=[
            "This tool only proposes repairs; it never modifies the DWG.",
            "Always validate and dry-run the plan before execute_cad_plan(allow_modify=True).",
        ],
        next_tools=["validate_cad_plan", "dry_run_cad_plan"],
    )
