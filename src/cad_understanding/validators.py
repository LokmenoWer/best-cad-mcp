"""Geometry validation reports for scanned CAD metadata."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_blocks,
    all_entities,
    bbox_dict,
    bbox_from_row,
    bbox_intersects,
    circle_center_radius,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    entity_geometry,
    entity_type,
    get_db,
    is_closed_polyline,
    latest_validation_report,
    line_length,
    line_points,
    now_iso,
    point_distance,
    stable_id,
)
from .result import ToolResult, error_result, ok_result

SeverityWeight = {"low": 4, "medium": 10, "high": 18, "critical": 30}


def _issue(issue_type: str,
           severity: str,
           message: str,
           handles: Optional[List[str]] = None,
           expected: Any = None,
           actual: Any = None,
           bbox: Optional[Tuple[float, float, float, float]] = None,
           evidence: Optional[Dict[str, Any]] = None,
           repair_hint: str = "",
           suggested_tools: Optional[List[str]] = None,
           object_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    handles = handles or []
    issue_id = stable_id("val", issue_type, severity, ",".join(sorted(handles)), expected, actual)
    return {
        "issue_id": issue_id,
        "severity": severity,
        "issue_type": issue_type,
        "message": message,
        "handles": sorted(set(handles)),
        "object_ids": sorted(set(object_ids or [])),
        "expected": expected,
        "actual": actual,
        "bbox": bbox_dict(bbox),
        "evidence": evidence or {},
        "repair_hint": repair_hint,
        "suggested_tools": suggested_tools or [],
    }


def _store_report(database: CADDatabase, report: Dict[str, Any]) -> None:
    scope = current_scope(database)
    with database._conn() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO cad_validation_reports
                (report_id, passed, score, issue_count, issues,
                 recommended_next_tools, generated_at, workspace_id, drawing_id,
                 conversation_id, thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            stable_id("report", report.get("generated_at"), report.get("issue_count")),
            int(bool(report.get("passed"))),
            report.get("score"),
            report.get("issue_count"),
            json.dumps(report.get("issues", []), ensure_ascii=False),
            json.dumps(report.get("recommended_next_tools", []), ensure_ascii=False),
            report.get("generated_at"),
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        ))


def _enabled(checks: Optional[List[str]], name: str) -> bool:
    return not checks or name in checks


def _line_signature(entity: Dict[str, Any], precision: int = 6) -> Optional[Tuple[float, float, float, float]]:
    points = line_points(entity)
    if not points:
        return None
    a, b = points
    p1 = (round(a[0], precision), round(a[1], precision))
    p2 = (round(b[0], precision), round(b[1], precision))
    first, second = sorted([p1, p2])
    return (first[0], first[1], second[0], second[1])


def _entity_signature(entity: Dict[str, Any]) -> str:
    etype = entity_type(entity)
    geom = entity_geometry(entity)
    bbox = bbox_from_row(entity)
    if "line" in etype:
        sig = _line_signature(entity)
        if sig:
            return f"line:{sig}"
    if "circle" in etype:
        cr = circle_center_radius(entity)
        if cr:
            center, radius = cr
            return f"circle:{round(center[0], 6)}:{round(center[1], 6)}:{round(radius, 6)}"
    return json.dumps({
        "type": etype,
        "bbox": bbox,
        "geometry": geom,
    }, sort_keys=True, default=str)


def _tiny_gap_issues(lines: List[Dict[str, Any]], tolerance: float = 1e-3) -> List[Dict[str, Any]]:
    endpoints: List[Tuple[str, List[float]]] = []
    for entity in lines:
        points = line_points(entity)
        if points:
            endpoints.append((str(entity.get("handle")), points[0]))
            endpoints.append((str(entity.get("handle")), points[1]))
    issues = []
    for i, (h1, p1) in enumerate(endpoints):
        for h2, p2 in endpoints[i + 1:i + 80]:
            if h1 == h2:
                continue
            dist = point_distance(p1, p2)
            if 0.0 < dist <= tolerance:
                issues.append(_issue(
                    "tiny_gaps_between_endpoints",
                    "medium",
                    f"Line endpoints are separated by a tiny gap of {dist:g}.",
                    [h1, h2],
                    expected=0.0,
                    actual=dist,
                    evidence={"tolerance": tolerance, "p1": p1, "p2": p2},
                    repair_hint="Snap or extend endpoints together after confirming design intent.",
                    suggested_tools=["propose_repair_plan", "dry_run_cad_plan"],
                ))
    return issues


def _overlapping_line_issues(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[Tuple[float, float, float, float], str] = {}
    issues = []
    for entity in lines:
        sig = _line_signature(entity)
        if not sig:
            continue
        handle = str(entity.get("handle"))
        if sig in seen:
            issues.append(_issue(
                "overlapping_lines",
                "medium",
                "Line overlaps another line with identical endpoints.",
                [seen[sig], handle],
                evidence={"line_signature": sig},
                repair_hint="Remove one duplicate line if it is unintended.",
                suggested_tools=["propose_repair_plan", "dry_run_cad_plan"],
            ))
        else:
            seen[sig] = handle
    return issues


def validate_geometry(checks: Optional[List[str]] = None,
                      database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    ensure_understanding_schema(db)
    entities = all_entities(db)
    blocks = all_blocks(db)
    issues: List[Dict[str, Any]] = []
    lines = [entity for entity in entities if "line" in entity_type(entity) and "polyline" not in entity_type(entity)]
    polylines = [entity for entity in entities if "polyline" in entity_type(entity)]

    if _enabled(checks, "zero_length_lines"):
        for entity in lines:
            length = line_length(entity)
            if length is not None and length <= 1e-9:
                issues.append(_issue(
                    "zero_length_lines",
                    "high",
                    "Line has zero or near-zero length.",
                    [str(entity.get("handle"))],
                    expected="length > 0",
                    actual=length,
                    bbox=bbox_from_row(entity),
                    repair_hint="Delete or redraw the line after confirming it is not intentional.",
                    suggested_tools=["propose_repair_plan", "dry_run_cad_plan"],
                ))

    if _enabled(checks, "duplicate_entities"):
        seen: Dict[str, str] = {}
        for entity in entities:
            sig = _entity_signature(entity)
            handle = str(entity.get("handle"))
            if sig in seen:
                issues.append(_issue(
                    "duplicate_entities",
                    "medium",
                    "Entity appears to duplicate another entity.",
                    [seen[sig], handle],
                    bbox=bbox_from_row(entity),
                    evidence={"signature": sig[:300]},
                    repair_hint="Remove the duplicate if it is not intended.",
                    suggested_tools=["propose_repair_plan", "dry_run_cad_plan"],
                ))
            else:
                seen[sig] = handle

    if _enabled(checks, "tiny_gaps_between_endpoints"):
        issues.extend(_tiny_gap_issues(lines))

    if _enabled(checks, "unclosed_polylines"):
        for entity in polylines:
            geom = entity_geometry(entity)
            if not is_closed_polyline(entity) and geom.get("closed") is not None:
                issues.append(_issue(
                    "unclosed_polylines",
                    "medium",
                    "Polyline is not closed but may represent a profile.",
                    [str(entity.get("handle"))],
                    expected="closed profile when used as boundary",
                    actual={"closed": bool(geom.get("closed"))},
                    bbox=bbox_from_row(entity),
                    repair_hint="Close the polyline only if it is intended as a boundary.",
                    suggested_tools=["propose_repair_plan"],
                ))

    if _enabled(checks, "overlapping_lines"):
        issues.extend(_overlapping_line_issues(lines))

    if _enabled(checks, "missing_dimensions_candidate"):
        dimensional = [entity for entity in entities if "dimension" in entity_type(entity)]
        if len(entities) >= 5 and not dimensional:
            issues.append(_issue(
                "missing_dimensions_candidate",
                "low",
                "Drawing has geometry but no scanned dimension entities.",
                [],
                expected="at least one dimension for production drawings",
                actual=0,
                repair_hint="Add dimensions where required by the drawing standard.",
                suggested_tools=["add_linear_dimension", "add_radial_dimension"],
            ))

    if _enabled(checks, "dimension_value_mismatch"):
        with db._conn() as conn:
            rows = conn.execute('''
                SELECT constraint_id, target_handles, value, actual, tolerance, status
                FROM cad_constraints
                WHERE workspace_id = ? AND drawing_id = ?
                  AND conversation_id = ? AND thread_id = ?
                  AND status = 'violated'
            ''', tuple(current_scope(db).values())).fetchall()
        for row in rows:
            handles = decode_json(row["target_handles"], [])
            issues.append(_issue(
                "dimension_value_mismatch",
                "high",
                "Constraint value does not match actual geometry.",
                [str(handle) for handle in handles],
                expected=row["value"],
                actual=row["actual"],
                evidence={"constraint_id": row["constraint_id"], "tolerance": row["tolerance"]},
                repair_hint="Inspect the dimension binding before editing geometry or annotation.",
                suggested_tools=["get_drawing_constraints", "explain_entity"],
            ))

    if _enabled(checks, "wrong_or_empty_layer"):
        for entity in entities:
            layer = str(entity.get("layer") or "")
            if not layer:
                issues.append(_issue(
                    "wrong_or_empty_layer",
                    "medium",
                    "Entity has an empty layer name.",
                    [str(entity.get("handle"))],
                    expected="non-empty layer",
                    actual=layer,
                    bbox=bbox_from_row(entity),
                    repair_hint="Move the entity to an appropriate layer.",
                    suggested_tools=["set_entity_properties"],
                ))

    if _enabled(checks, "out_of_extents_geometry"):
        valid_bboxes = [bbox_from_row(entity) for entity in entities if bbox_from_row(entity)]
        if valid_bboxes:
            min_x = min(b[0] for b in valid_bboxes)
            min_y = min(b[1] for b in valid_bboxes)
            max_x = max(b[2] for b in valid_bboxes)
            max_y = max(b[3] for b in valid_bboxes)
            width = max(max_x - min_x, 1e-9)
            height = max(max_y - min_y, 1e-9)
            padded = (min_x - width * 10, min_y - height * 10, max_x + width * 10, max_y + height * 10)
            for entity in entities:
                bbox = bbox_from_row(entity)
                if bbox and not bbox_intersects(bbox, padded):
                    issues.append(_issue(
                        "out_of_extents_geometry",
                        "low",
                        "Entity lies far outside the main extents.",
                        [str(entity.get("handle"))],
                        bbox=bbox,
                        evidence={"main_extents": [min_x, min_y, max_x, max_y]},
                        repair_hint="Review whether this is stray geometry.",
                        suggested_tools=["explain_entity"],
                    ))

    if _enabled(checks, "unresolved_or_empty_blocks"):
        for block in blocks:
            if not block.get("is_layout") and int(block.get("entity_count") or 0) == 0:
                issues.append(_issue(
                    "unresolved_or_empty_blocks",
                    "low",
                    "Block definition has no scanned entities.",
                    [],
                    expected="entity_count > 0",
                    actual=block.get("entity_count"),
                    evidence={"block": block.get("name"), "path": block.get("path", "")},
                    repair_hint="Purge unused blocks or reload unresolved references if needed.",
                    suggested_tools=["get_all_blocks", "purge_drawing"],
                ))

    penalty = sum(SeverityWeight.get(issue["severity"], 4) for issue in issues)
    score = max(0.0, 100.0 - penalty)
    report = {
        "passed": not any(issue["severity"] in {"high", "critical"} for issue in issues),
        "score": round(score, 2),
        "issue_count": len(issues),
        "issues": issues,
        "generated_at": now_iso(),
        "recommended_next_tools": ["propose_repair_plan", "export_view_image_with_mapping"] if issues else ["summarize_drawing"],
    }
    _store_report(db, report)
    return ok_result(
        f"Validation completed with {len(issues)} issues.",
        data={"validation_report": report},
        handles=sorted({handle for issue in issues for handle in issue.get("handles", [])}),
        next_tools=report["recommended_next_tools"],
    )


def get_validation_report(database: Optional[CADDatabase] = None) -> ToolResult:
    report = latest_validation_report(get_db(database))
    if not report:
        return ok_result(
            "No validation report is cached yet.",
            data={"validation_report": None},
            warnings=["Run validate_geometry first to create a report."],
            next_tools=["validate_geometry"],
        )
    return ok_result(
        f"Loaded validation report with {report.get('issue_count', 0)} issues.",
        data={"validation_report": report},
        handles=sorted({handle for issue in report.get("issues", []) for handle in issue.get("handles", [])}),
        next_tools=["propose_repair_plan", "build_drawing_ir"],
    )


def propose_repair_plan(issue_ids: List[str],
                        database: Optional[CADDatabase] = None) -> ToolResult:
    report = latest_validation_report(get_db(database))
    if not report:
        return error_result("No validation report is available.", next_tools=["validate_geometry"])
    selected = [
        issue for issue in report.get("issues", [])
        if not issue_ids or issue.get("issue_id") in issue_ids
    ]
    steps = []
    affected: List[str] = []
    alternatives: List[Dict[str, Any]] = []
    risk_levels: List[str] = []
    for index, issue in enumerate(selected, start=1):
        handles = issue.get("handles", [])
        affected.extend(handles)
        issue_type = issue.get("issue_type")
        op = "draw_text"
        args: Dict[str, Any] = {"issue_id": issue.get("issue_id"), "handles": handles}
        writes = False
        step_risk = "low"
        rationale = issue.get("repair_hint", "")
        if issue_type == "zero_length_lines" and handles:
            op = "delete_entity"
            args = {"handle": handles[-1]}
            writes = True
            step_risk = "high"
            alternatives.append({
                "issue_id": issue.get("issue_id"),
                "option": "redraw_line",
                "reason": "Delete is high risk when the tiny line encodes intentional construction geometry.",
            })
        elif issue_type == "duplicate_entities" and handles:
            op = "delete_entity" if len(handles) == 1 else "delete_entities"
            args = {"handle": handles[-1]} if len(handles) == 1 else {"handles": handles[1:]}
            writes = True
            step_risk = "high"
            alternatives.append({
                "issue_id": issue.get("issue_id"),
                "option": "keep_duplicate",
                "reason": "Duplicate geometry may be intentional for stacked disciplines or plotting weights.",
            })
        elif issue_type == "tiny_gaps_between_endpoints" and len(handles) >= 2:
            op = "move_entity"
            evidence = issue.get("evidence", {})
            args = {
                "handle": handles[-1],
                "from_point": evidence.get("p2") or [0, 0, 0],
                "to_point": evidence.get("p1") or [0, 0, 0],
            }
            writes = True
            step_risk = "medium"
            alternatives.append({
                "issue_id": issue.get("issue_id"),
                "option": "extend_endpoint",
                "reason": "Extending a segment can preserve alignment better than moving the whole entity.",
            })
        elif issue_type == "unclosed_polylines" and handles:
            op = "set_entity_properties"
            args = {"handle": handles[-1], "closed": True}
            writes = True
            step_risk = "medium"
        elif issue_type == "dimension_value_mismatch" and handles:
            op = "set_entity_properties"
            args = {
                "handle": handles[0],
                "requires_dimension_binding_review": True,
                "expected": issue.get("expected"),
                "actual": issue.get("actual"),
            }
            writes = False
            step_risk = "medium"
            alternatives.append({
                "issue_id": issue.get("issue_id"),
                "option": "edit_geometry_or_annotation",
                "reason": "The correct repair depends on whether the dimension or measured geometry is authoritative.",
            })
        elif issue_type == "wrong_or_empty_layer" and handles:
            op = "set_entity_properties"
            args = {"handle": handles[-1], "layer": "0"}
            writes = True
            step_risk = "low"
        elif issue_type == "missing_dimensions_candidate":
            op = "add_linear_dimension"
            args = {
                "x1": 0,
                "y1": 0,
                "x2": 0,
                "y2": 0,
                "text_x": 0,
                "text_y": 0,
            }
            writes = False
            step_risk = "low"
            rationale = "Replace placeholder points with production-critical edges or holes before execution."
        elif issue_type == "unresolved_or_empty_blocks":
            op = "create_block"
            args = {
                "requires_manual_block_review": True,
                "issue_id": issue.get("issue_id"),
            }
            writes = False
            step_risk = "medium"
            alternatives.append({
                "issue_id": issue.get("issue_id"),
                "option": "purge_or_reload",
                "reason": "Purging/reloading blocks can affect drawing references and needs explicit user approval.",
            })
        else:
            args = {
                "text": f"Review issue {issue.get('issue_id')}",
                "insert_x": 0,
                "insert_y": 0,
                "layer": "TEXT-NOTE",
            }
            rationale = "No automatic repair is safe; manual review placeholder only."
        steps.append({
            "step_id": f"repair_{index}",
            "op": op,
            "args": args,
            "expect": {"issue_type": issue.get("issue_type")},
            "depends_on": [],
            "writes": writes,
            "risk_level": step_risk,
            "rationale": rationale,
        })
        risk_levels.append(step_risk)
    risk = "high" if "high" in risk_levels else "medium" if "medium" in risk_levels else "low"
    if any(issue.get("severity") in {"high", "critical"} for issue in selected) and risk == "low":
        risk = "medium"
    plan = {
        "plan_id": stable_id("plan", "repair", ",".join(issue_ids or []), report.get("generated_at")),
        "description": "Repair plan proposed from validation issues.",
        "units": "drawing_units",
        "steps": steps,
        "constraints": [],
        "risk_level": risk,
        "requires_confirmation": True,
        "dry_run_available": True,
        "affected_handles": sorted(set(affected)),
        "alternatives": alternatives,
    }
    return ok_result(
        f"Proposed repair plan with {len(steps)} steps.",
        data={"plan": plan, "issues": selected},
        handles=plan["affected_handles"],
        warnings=["This tool only proposes repairs; it does not modify the DWG."],
        next_tools=["validate_cad_plan", "dry_run_cad_plan"],
    )
