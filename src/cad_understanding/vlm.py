"""Structured VLM review output validation and persistence."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    bbox_dict,
    bbox_intersects,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    get_db,
    json_text,
    latest_validation_report,
    now_iso,
    stable_id,
)
from .result import ToolResult, error_result, ok_result
from . import view_grounding


VALID_FINDING_STATUSES = {
    "validated",
    "grounded",
    "ambiguous",
    "rejected",
    "promoted",
}
VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}


def _load_snapshot(database: CADDatabase, snapshot_id: str) -> Optional[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        row = conn.execute('''
            SELECT snapshot_data
            FROM cad_view_snapshots
            WHERE snapshot_id = ? AND workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            snapshot_id,
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchone()
    return decode_json(row["snapshot_data"], {}) if row else None


def _review_findings(review: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    if isinstance(review, str):
        try:
            review = json.loads(review)
        except Exception as exc:
            return [], [f"review is not valid JSON: {exc}"]
    if isinstance(review, list):
        raw_findings = review
    elif isinstance(review, dict):
        raw_findings = review.get("findings", [])
        if not raw_findings and any(key in review for key in ("issue_type", "bbox", "overlay_id")):
            raw_findings = [review]
    else:
        return [], ["review must be a JSON object or list"]
    if not isinstance(raw_findings, list):
        return [], ["review.findings must be a list"]
    findings = [item for item in raw_findings if isinstance(item, dict)]
    skipped = len(raw_findings) - len(findings)
    warnings = [f"Skipped {skipped} non-object finding(s)."] if skipped else []
    return findings, warnings


def _normalize_bbox(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in value[:4]]
    except Exception:
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    if x2 - x1 <= 0.0 or y2 - y1 <= 0.0:
        return None
    return [x1, y1, x2, y2]


def _normalize_handles(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _normalize_confidence(value: Any) -> Optional[float]:
    try:
        confidence = float(value)
    except Exception:
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def _evidence_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    if value is None:
        return {}
    return {"text": str(value)}


def _overlay_ids(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    if not snapshot:
        return []
    return [
        str(item.get("overlay_id")).strip().upper()
        for item in snapshot.get("overlay_items", [])
        if item.get("overlay_id")
    ]


def _visible_handles(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    if not snapshot:
        return []
    handles = snapshot.get("visible_handles", [])
    return [str(handle) for handle in handles if handle]


def _bbox_in_image(bbox: List[float], snapshot: Optional[Dict[str, Any]]) -> bool:
    if not snapshot:
        return True
    image = snapshot.get("image", {})
    width = float(image.get("width") or 0.0)
    height = float(image.get("height") or 0.0)
    if width <= 0.0 or height <= 0.0:
        return True
    x1, y1, x2, y2 = bbox
    return x2 >= 0.0 and y2 >= 0.0 and x1 <= width and y1 <= height


def validate_vlm_review_output(review: Any,
                               snapshot_id: Optional[str] = None,
                               database: Optional[CADDatabase] = None) -> ToolResult:
    """Validate and normalize a VLM drawing review payload."""
    db = get_db(database)
    snapshot = _load_snapshot(db, snapshot_id) if snapshot_id else None
    if snapshot_id and not snapshot:
        return error_result(
            f"Unknown view snapshot: {snapshot_id}",
            next_tools=["export_view_image_with_mapping"],
        )

    raw_findings, warnings = _review_findings(review)
    normalized: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    available_overlay_ids = set(_overlay_ids(snapshot))
    visible_handles = set(_visible_handles(snapshot))

    for index, raw in enumerate(raw_findings, start=1):
        item_errors: List[str] = []
        issue_type = str(raw.get("issue_type") or raw.get("type") or "").strip()
        if not issue_type:
            item_errors.append("issue_type is required")
        confidence = _normalize_confidence(raw.get("confidence"))
        if confidence is None:
            item_errors.append("confidence must be a number in [0, 1]")
            confidence = 0.0
        overlay_id = str(raw.get("overlay_id") or "").strip().upper()
        if overlay_id and snapshot and overlay_id not in available_overlay_ids:
            item_errors.append(f"overlay_id {overlay_id} is not in snapshot")
        bbox = _normalize_bbox(raw.get("bbox") or raw.get("pixel_bbox"))
        if bbox and not _bbox_in_image(bbox, snapshot):
            item_errors.append("bbox is outside the snapshot image bounds")
        claimed_handles = _normalize_handles(
            raw.get("handles") or raw.get("claimed_handles") or raw.get("handle")
        )
        if snapshot and claimed_handles:
            missing_handles = [handle for handle in claimed_handles if handle not in visible_handles]
            if missing_handles:
                item_errors.append(
                    f"claimed handle(s) not visible in snapshot: {', '.join(missing_handles[:5])}"
                )
        if not overlay_id and not bbox and not claimed_handles:
            item_errors.append("one of overlay_id, bbox, or claimed_handles is required")
        severity = str(raw.get("severity") or "medium").lower().strip()
        if severity not in VALID_SEVERITIES:
            item_errors.append(f"severity must be one of {sorted(VALID_SEVERITIES)}")
            severity = "medium"
        evidence = _evidence_payload(raw.get("evidence"))
        # Evidence is helpful but not mandatory: a finding that is already
        # localized by overlay_id / bbox / claimed handles is actionable even
        # when the VLM omits supporting text (the overlay itself is evidence).
        # Only require it when nothing else pins the finding to the drawing.
        if not evidence and not overlay_id and not bbox and not claimed_handles:
            item_errors.append(
                "evidence or a localization (overlay_id, bbox, or claimed_handles) is required"
            )

        finding_id = str(raw.get("finding_id") or "").strip()
        if not finding_id:
            finding_id = stable_id("vlm", snapshot_id or "", index, issue_type, overlay_id, bbox, claimed_handles)
        normalized_item = {
            "finding_id": finding_id,
            "snapshot_id": snapshot_id or str(raw.get("snapshot_id") or ""),
            "issue_type": issue_type,
            "severity": severity,
            "confidence": round(confidence, 4),
            "overlay_id": overlay_id,
            "bbox": bbox,
            "claimed_handles": claimed_handles,
            "evidence": evidence,
            "raw_finding": raw,
        }
        if item_errors:
            errors.append({"index": index, "finding": normalized_item, "errors": item_errors})
        else:
            normalized.append(normalized_item)

    if errors and not normalized:
        return error_result(
            f"VLM review output failed validation for all {len(errors)} finding(s).",
            data={
                "findings": [],
                "errors": errors,
                "rejected_findings": errors,
                "snapshot_id": snapshot_id,
            },
            warnings=warnings,
            next_tools=["export_view_image_with_mapping", "validate_vlm_review_output"],
        )
    rejection_warnings = [
        f"Rejected {len(errors)} finding(s) with validation errors: "
        + "; ".join(
            f"finding #{e['index']}: {', '.join(e['errors'])}"
            for e in errors[:3]
        )
        + ("..." if len(errors) > 3 else "")
    ] if errors else []
    return ok_result(
        f"Validated {len(normalized)} VLM finding(s)"
        + (f"; rejected {len(errors)} invalid finding(s)." if errors else "."),
        data={
            "findings": normalized,
            "rejected_findings": errors,
            "snapshot_id": snapshot_id,
            "available_overlay_ids": sorted(available_overlay_ids),
        },
        handles=sorted({h for item in normalized for h in item.get("claimed_handles", [])}),
        warnings=warnings + rejection_warnings,
        next_tools=["submit_vlm_review", "ground_vlm_region", "ground_vlm_overlay_id"],
    )


def _ground_finding(database: CADDatabase,
                    finding: Dict[str, Any],
                    top_k: int) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    snapshot_id = str(finding.get("snapshot_id") or "")
    warnings: List[str] = []
    candidates: List[Dict[str, Any]] = []
    world_bbox: Dict[str, Any] = {}
    if finding.get("overlay_id"):
        grounded = view_grounding.ground_vlm_overlay_id(
            snapshot_id,
            str(finding["overlay_id"]),
            database=database,
        )
        warnings.extend(grounded.get("warnings", []))
        if grounded.get("ok"):
            candidates = [grounded["data"]["candidate"]]
    elif finding.get("bbox"):
        grounded = view_grounding.ground_vlm_region(
            snapshot_id,
            list(finding["bbox"]),
            top_k=top_k,
            database=database,
        )
        warnings.extend(grounded.get("warnings", []))
        if grounded.get("ok"):
            candidates = grounded["data"].get("candidates", [])
            world_bbox = grounded["data"].get("world_region") or {}
    if finding.get("bbox") and not world_bbox:
        world_region = view_grounding.map_pixel_region_to_world_bbox(
            snapshot_id,
            list(finding["bbox"]),
            database=database,
        )
        warnings.extend(world_region.get("warnings", []))
        if world_region.get("ok"):
            world_bbox = world_region["data"].get("world_bbox") or {}
    return candidates, sorted(set(warnings)), world_bbox


def _store_findings(database: CADDatabase,
                    findings: Iterable[Dict[str, Any]],
                    source_model: str,
                    prompt_version: str) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        for item in findings:
            conn.execute('''
                INSERT OR REPLACE INTO cad_vlm_findings
                    (finding_id, snapshot_id, source_model, prompt_version,
                     issue_type, severity, status, confidence, overlay_id,
                     pixel_bbox, world_bbox, claimed_handles, grounded_handles,
                     grounding_candidates, evidence, raw_finding,
                     workspace_id, drawing_id, conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item["finding_id"],
                item.get("snapshot_id", ""),
                source_model,
                prompt_version,
                item.get("issue_type", ""),
                item.get("severity", "medium"),
                item.get("status", "validated"),
                item.get("confidence", 0.0),
                item.get("overlay_id", ""),
                json_text(item.get("bbox") or []),
                json_text(item.get("world_bbox") or {}),
                json_text(item.get("claimed_handles") or []),
                json_text(item.get("grounded_handles") or []),
                json_text(item.get("grounding_candidates") or []),
                json_text(item.get("evidence") or {}),
                json_text(item.get("raw_finding") or {}),
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))


def submit_vlm_review(snapshot_id: str,
                      review: Any,
                      source_model: str = "unknown",
                      prompt_version: str = "vlm_review_drawing/v2",
                      top_k: int = 10,
                      database: Optional[CADDatabase] = None) -> ToolResult:
    """Validate, ground, and persist VLM review findings."""
    db = get_db(database)
    validation = validate_vlm_review_output(review, snapshot_id=snapshot_id, database=db)
    if not validation.get("ok"):
        return validation
    findings: List[Dict[str, Any]] = []
    warnings: List[str] = list(validation.get("warnings", []))
    for item in validation["data"].get("findings", []):
        candidates, grounding_warnings, world_bbox = _ground_finding(db, item, top_k)
        grounded_handles = sorted({
            str(candidate.get("handle"))
            for candidate in candidates
            if candidate.get("handle")
        })
        claimed_handles = set(item.get("claimed_handles", []))
        status = "grounded" if grounded_handles else "validated"
        if grounded_handles and claimed_handles and not claimed_handles.intersection(grounded_handles):
            status = "ambiguous"
            warnings.append(
                f"Finding {item['finding_id']} claimed handle(s) do not match grounding candidates."
            )
        warnings.extend(grounding_warnings)
        findings.append({
            **item,
            "status": status,
            "grounded_handles": grounded_handles,
            "grounding_candidates": candidates,
            "world_bbox": world_bbox,
        })
    _store_findings(db, findings, source_model, prompt_version)
    return ok_result(
        f"Stored {len(findings)} VLM finding(s).",
        data={"findings": findings, "snapshot_id": snapshot_id},
        handles=sorted({h for item in findings for h in item.get("grounded_handles", [])}),
        warnings=sorted(set(warnings)),
        next_tools=["get_vlm_findings", "promote_vlm_finding_to_validation_issue", "explain_entity"],
    )


def _row_to_finding(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    for key in (
        "pixel_bbox",
        "world_bbox",
        "claimed_handles",
        "grounded_handles",
        "grounding_candidates",
        "evidence",
        "raw_finding",
    ):
        item[key] = decode_json(item.get(key), [] if key.endswith("handles") or key == "pixel_bbox" else {})
    return item


def get_vlm_findings(snapshot_id: Optional[str] = None,
                     status: Optional[str] = None,
                     issue_type: Optional[str] = None,
                     limit: int = 100,
                     database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    ensure_understanding_schema(db)
    scope = current_scope(db)
    params: List[Any] = [
        scope["workspace_id"], scope["drawing_id"],
        scope["conversation_id"], scope["thread_id"],
    ]
    filters = [
        "workspace_id = ?",
        "drawing_id = ?",
        "conversation_id = ?",
        "thread_id = ?",
    ]
    if snapshot_id:
        filters.append("snapshot_id = ?")
        params.append(snapshot_id)
    if status:
        filters.append("status = ?")
        params.append(status)
    if issue_type:
        filters.append("LOWER(issue_type) = LOWER(?)")
        params.append(issue_type)
    try:
        normalized_limit = max(1, min(int(limit or 100), 1000))
    except Exception:
        normalized_limit = 100
    params.append(normalized_limit)
    with db._conn() as conn:
        rows = conn.execute(f'''
            SELECT finding_id, snapshot_id, source_model, prompt_version,
                   issue_type, severity, status, confidence, overlay_id,
                   pixel_bbox, world_bbox, claimed_handles, grounded_handles,
                   grounding_candidates, evidence, raw_finding, created_at
            FROM cad_vlm_findings
            WHERE {' AND '.join(filters)}
            ORDER BY created_at DESC, finding_id
            LIMIT ?
        ''', params).fetchall()
    findings = [_row_to_finding(dict(row)) for row in rows]
    return ok_result(
        f"Loaded {len(findings)} VLM finding(s).",
        data={"findings": findings},
        handles=sorted({h for item in findings for h in item.get("grounded_handles", [])}),
        next_tools=["promote_vlm_finding_to_validation_issue", "explain_entity", "build_drawing_ir"],
    )


def _selected_findings(database: CADDatabase,
                       finding_ids: Optional[List[str]],
                       min_confidence: float) -> List[Dict[str, Any]]:
    result = get_vlm_findings(database=database, limit=1000)
    findings = result["data"].get("findings", []) if result.get("ok") else []
    selected_ids = {str(item) for item in (finding_ids or []) if item}
    return [
        item for item in findings
        if (not selected_ids or item.get("finding_id") in selected_ids)
        and float(item.get("confidence") or 0.0) >= float(min_confidence or 0.0)
        and item.get("status") in {"grounded", "validated", "ambiguous"}
    ]


def _validation_issue_from_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    handles = finding.get("grounded_handles") or finding.get("claimed_handles") or []
    bbox = None
    world_bbox = finding.get("world_bbox") or {}
    if isinstance(world_bbox, dict) and isinstance(world_bbox.get("min"), list) and isinstance(world_bbox.get("max"), list):
        bbox = (
            float(world_bbox["min"][0]),
            float(world_bbox["min"][1]),
            float(world_bbox["max"][0]),
            float(world_bbox["max"][1]),
        )
    issue_type = f"vlm_{finding.get('issue_type') or 'review_issue'}"
    issue_id = stable_id("val", issue_type, finding.get("finding_id"))
    return {
        "issue_id": issue_id,
        "severity": finding.get("severity") or "medium",
        "issue_type": issue_type,
        "message": f"VLM finding: {finding.get('issue_type')}",
        "handles": handles,
        "object_ids": [],
        "expected": None,
        "actual": None,
        "bbox": bbox_dict(bbox),
        "evidence": {
            "source": "vlm_review",
            "finding_id": finding.get("finding_id"),
            "snapshot_id": finding.get("snapshot_id"),
            "confidence": finding.get("confidence"),
            "overlay_id": finding.get("overlay_id"),
            "pixel_bbox": finding.get("pixel_bbox"),
            "evidence": finding.get("evidence"),
            "grounding_candidates": finding.get("grounding_candidates", [])[:5],
        },
        "repair_hint": "Inspect the grounded handle(s) and convert the confirmed issue to a CADPlan repair.",
        "suggested_tools": ["get_vlm_findings", "explain_entity", "propose_repair_plan"],
    }


def _store_validation_report(database: CADDatabase,
                             report: Dict[str, Any]) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO cad_validation_reports
                (report_id, passed, score, issue_count, issues,
                 recommended_next_tools, generated_at, workspace_id, drawing_id,
                 conversation_id, thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            stable_id("report", report.get("generated_at"), report.get("issue_count"), "vlm"),
            int(bool(report.get("passed"))),
            report.get("score"),
            report.get("issue_count"),
            json_text(report.get("issues", [])),
            json_text(report.get("recommended_next_tools", [])),
            report.get("generated_at"),
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        ))


def _mark_findings(database: CADDatabase,
                   finding_ids: Iterable[str],
                   status: str) -> None:
    if status not in VALID_FINDING_STATUSES:
        return
    ids = [str(item) for item in finding_ids if item]
    if not ids:
        return
    scope = current_scope(database)
    with database._conn() as conn:
        for finding_id in ids:
            conn.execute('''
                UPDATE cad_vlm_findings
                SET status = ?
                WHERE finding_id = ? AND workspace_id = ? AND drawing_id = ?
                  AND conversation_id = ? AND thread_id = ?
            ''', (
                status, finding_id,
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))


def _public_bbox_to_tuple(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(value, dict) and isinstance(value.get("min"), list) and isinstance(value.get("max"), list):
        try:
            return (
                float(value["min"][0]),
                float(value["min"][1]),
                float(value["max"][0]),
                float(value["max"][1]),
            )
        except Exception:
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        except Exception:
            return None
    return None


def _semantic_type_from_finding(finding: Dict[str, Any]) -> str:
    raw = finding.get("raw_finding") or {}
    evidence = finding.get("evidence") or {}
    for key in ("semantic_type", "object_type", "detected_object_type"):
        value = raw.get(key) if isinstance(raw, dict) else None
        if value:
            return str(value).strip().lower()
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if value:
            return str(value).strip().lower()
    issue = str(finding.get("issue_type") or "").lower()
    if "title" in issue:
        return "title_block"
    if "bom" in issue or "parts" in issue:
        return "bom_table"
    if "revision" in issue or "rev" in issue:
        return "revision_table"
    if "dimension" in issue or "diameter" in issue or "radius" in issue:
        return "dimension_annotation"
    if "gdt" in issue or "tolerance" in issue:
        return "gdt_annotation"
    if "roughness" in issue or "surface" in issue:
        return "surface_roughness"
    if "section" in issue:
        return "section_marker"
    return "vlm_review_finding"


def _insert_vlm_semantics(database: CADDatabase,
                          objects: List[Dict[str, Any]],
                          relations: List[Dict[str, Any]]) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        conn.execute('''
            DELETE FROM cad_semantic_relations
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              AND relation_id LIKE 'rel_vlm_%'
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        ))
        conn.execute('''
            DELETE FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              AND source LIKE 'vlm:%'
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
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
                json_text(obj.get("entity_handles", [])),
                json_text(obj.get("properties", {})),
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
                rel["relation_type"], rel["confidence"], json_text(rel.get("evidence", {})),
                scope["workspace_id"], scope["drawing_id"],
                scope["conversation_id"], scope["thread_id"],
            ))


def _existing_semantic_objects(database: CADDatabase) -> List[Dict[str, Any]]:
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_id, object_type, source, confidence,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   entity_handles
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
              AND source NOT LIKE 'vlm:%'
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["entity_handles"] = decode_json(item.get("entity_handles"), [])
        bbox = None
        if item.get("bbox_min_x") is not None:
            bbox = (
                float(item["bbox_min_x"]),
                float(item["bbox_min_y"]),
                float(item["bbox_max_x"]),
                float(item["bbox_max_y"]),
            )
        item["bbox"] = bbox
        result.append(item)
    return result


def fuse_vlm_findings_into_semantic_graph(finding_ids: Optional[List[str]] = None,
                                          min_confidence: float = 0.5,
                                          database: Optional[CADDatabase] = None) -> ToolResult:
    """Materialize VLM findings as semantic graph objects and overlap relations."""
    db = get_db(database)
    findings = _selected_findings(db, finding_ids, min_confidence)
    existing_objects = _existing_semantic_objects(db)
    objects: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    for finding in findings:
        object_type = _semantic_type_from_finding(finding)
        handles = finding.get("grounded_handles") or finding.get("claimed_handles") or []
        bbox = _public_bbox_to_tuple(finding.get("world_bbox"))
        source_model = str(finding.get("source_model") or "unknown")
        object_id = stable_id("sem", "vlm", finding.get("finding_id"))
        label = str(
            (finding.get("raw_finding") or {}).get("label")
            or finding.get("issue_type")
            or object_type
        )[:120]
        obj = {
            "object_id": object_id,
            "object_type": object_type,
            "label": label,
            "source": f"vlm:{source_model}",
            "confidence": round(float(finding.get("confidence") or 0.0), 3),
            "bbox": bbox,
            "entity_handles": handles,
            "properties": {
                "finding_id": finding.get("finding_id"),
                "snapshot_id": finding.get("snapshot_id"),
                "issue_type": finding.get("issue_type"),
                "severity": finding.get("severity"),
                "evidence": finding.get("evidence"),
                "prompt_version": finding.get("prompt_version"),
                "grounding_candidates": finding.get("grounding_candidates", [])[:5],
            },
        }
        objects.append(obj)
        handle_set = set(str(handle) for handle in handles)
        for existing in existing_objects:
            existing_handles = set(str(handle) for handle in existing.get("entity_handles", []))
            overlap = bool(handle_set and existing_handles and handle_set.intersection(existing_handles))
            if not overlap and bbox and existing.get("bbox"):
                overlap = bbox_intersects(bbox, existing.get("bbox"))
            if not overlap:
                continue
            relation_type = (
                "conflicts_with"
                if object_type != existing.get("object_type")
                else "supports"
            )
            relations.append({
                "relation_id": stable_id("rel_vlm", relation_type, object_id, existing.get("object_id")),
                "from_object_id": object_id,
                "to_object_id": existing.get("object_id"),
                "relation_type": relation_type,
                "confidence": min(
                    float(obj["confidence"]),
                    float(existing.get("confidence") or 0.5),
                ),
                "evidence": {
                    "reason": "VLM semantic object overlaps an existing semantic object.",
                    "finding_id": finding.get("finding_id"),
                    "vlm_object_type": object_type,
                    "existing_object_type": existing.get("object_type"),
                },
            })
    _insert_vlm_semantics(db, objects, relations)
    return ok_result(
        f"Fused {len(objects)} VLM finding(s) into the semantic graph.",
        data={"semantic_objects": objects, "semantic_relations": relations},
        handles=sorted({h for obj in objects for h in obj.get("entity_handles", [])}),
        warnings=[
            "VLM semantic objects are evidence-bearing hypotheses; keep low-confidence conflicts unresolved until reviewed."
        ] if objects else [],
        next_tools=["get_semantic_graph", "find_semantic_objects", "build_drawing_ir"],
    )


def _candidate_handles(finding: Dict[str, Any], top_k: int) -> List[str]:
    candidates = finding.get("grounding_candidates") or []
    handles = []
    for candidate in candidates[:max(1, int(top_k or 1))]:
        handle = candidate.get("handle")
        if handle:
            handles.append(str(handle))
    if not handles:
        handles = [str(handle) for handle in (finding.get("grounded_handles") or []) if handle]
    return handles


def _match_ground_truth(finding: Dict[str, Any],
                        expected: Dict[str, Any]) -> bool:
    if expected.get("finding_id") and expected.get("finding_id") == finding.get("finding_id"):
        return True
    if expected.get("overlay_id") and str(expected.get("overlay_id")).upper() != str(finding.get("overlay_id") or "").upper():
        return False
    if expected.get("issue_type") and str(expected.get("issue_type")).lower() != str(finding.get("issue_type") or "").lower():
        return False
    return bool(expected.get("overlay_id") or expected.get("issue_type"))


def evaluate_vlm_grounding(ground_truth: List[Dict[str, Any]],
                           snapshot_id: Optional[str] = None,
                           top_k: int = 3,
                           database: Optional[CADDatabase] = None) -> ToolResult:
    """Score persisted VLM findings against expected handles and issue types."""
    db = get_db(database)
    findings_result = get_vlm_findings(snapshot_id=snapshot_id, database=db, limit=1000)
    findings = findings_result["data"].get("findings", []) if findings_result.get("ok") else []
    expected_items = [item for item in (ground_truth or []) if isinstance(item, dict)]
    used_findings = set()
    cases = []
    top1_hits = 0
    topk_hits = 0
    issue_hits = 0
    matched_count = 0
    for index, expected in enumerate(expected_items, start=1):
        expected_handles = set(_normalize_handles(expected.get("expected_handles") or expected.get("handles")))
        match_index = None
        matched_finding: Optional[Dict[str, Any]] = None
        for finding_index, finding in enumerate(findings):
            if finding_index in used_findings:
                continue
            if _match_ground_truth(finding, expected):
                match_index = finding_index
                matched_finding = finding
                break
        if matched_finding is not None and match_index is not None:
            used_findings.add(match_index)
            matched_count += 1
        candidate_top1 = _candidate_handles(matched_finding or {}, 1)
        candidate_topk = _candidate_handles(matched_finding or {}, top_k)
        top1 = bool(expected_handles and candidate_top1 and expected_handles.intersection(candidate_top1[:1]))
        topk = bool(expected_handles and candidate_topk and expected_handles.intersection(candidate_topk))
        issue_match = bool(
            matched_finding is not None
            and (
                not expected.get("issue_type")
                or str(expected.get("issue_type")).lower() == str(matched_finding.get("issue_type") or "").lower()
            )
        )
        top1_hits += int(top1)
        topk_hits += int(topk)
        issue_hits += int(issue_match)
        cases.append({
            "case_id": expected.get("case_id") or f"case_{index}",
            "expected_handles": sorted(expected_handles),
            "matched_finding_id": (matched_finding or {}).get("finding_id"),
            "candidate_top1": candidate_top1[:1],
            "candidate_topk": candidate_topk,
            "top1_hit": top1,
            "topk_hit": topk,
            "issue_type_hit": issue_match,
        })
    total = len(expected_items)
    finding_count = len(findings)
    metrics = {
        "case_count": total,
        "finding_count": finding_count,
        "matched_case_count": matched_count,
        "handle_top1_accuracy": round(top1_hits / total, 4) if total else 0.0,
        "handle_topk_accuracy": round(topk_hits / total, 4) if total else 0.0,
        "issue_type_recall": round(issue_hits / total, 4) if total else 0.0,
        "issue_precision": round(matched_count / finding_count, 4) if finding_count else 0.0,
        "json_valid_rate": 1.0 if findings else 0.0,
        "top_k": max(1, int(top_k or 3)),
    }
    return ok_result(
        "Evaluated VLM grounding findings.",
        data={"metrics": metrics, "cases": cases},
        handles=sorted({h for case in cases for h in case.get("candidate_topk", [])}),
        warnings=[
            "Metrics are computed from persisted findings; invalid VLM JSON rejected before submit is not counted."
        ],
        next_tools=["get_vlm_findings", "export_view_image_with_mapping"],
    )


def promote_vlm_finding_to_validation_issue(finding_ids: Optional[List[str]] = None,
                                             min_confidence: float = 0.0,
                                             database: Optional[CADDatabase] = None) -> ToolResult:
    """Copy grounded VLM findings into the latest validation report."""
    db = get_db(database)
    findings = _selected_findings(db, finding_ids, min_confidence)
    if not findings:
        return ok_result(
            "No VLM findings matched the promotion criteria.",
            data={"promoted_issues": []},
            next_tools=["get_vlm_findings", "submit_vlm_review"],
        )
    existing = latest_validation_report(db) or {
        "passed": True,
        "score": 100.0,
        "issue_count": 0,
        "issues": [],
        "recommended_next_tools": [],
        "generated_at": now_iso(),
    }
    existing_issues = list(existing.get("issues", []) or [])
    existing_ids = {issue.get("issue_id") for issue in existing_issues}
    promoted = []
    for finding in findings:
        issue = _validation_issue_from_finding(finding)
        if issue["issue_id"] in existing_ids:
            continue
        existing_issues.append(issue)
        promoted.append(issue)
    recommended = list(existing.get("recommended_next_tools", []) or [])
    for tool in ("propose_repair_plan", "export_view_image_with_mapping", "get_vlm_findings"):
        if tool not in recommended:
            recommended.append(tool)
    penalty = sum(18 if issue.get("severity") in {"high", "critical"} else 10 for issue in promoted)
    report = {
        **existing,
        "passed": bool(existing.get("passed", True)) and not any(
            issue.get("severity") in {"high", "critical"} for issue in promoted
        ),
        "score": max(0.0, float(existing.get("score") or 100.0) - penalty),
        "issue_count": len(existing_issues),
        "issues": existing_issues,
        "recommended_next_tools": recommended,
        "generated_at": now_iso(),
    }
    _store_validation_report(db, report)
    _mark_findings(db, [finding["finding_id"] for finding in findings], "promoted")
    return ok_result(
        f"Promoted {len(promoted)} VLM finding(s) to validation issue(s).",
        data={"promoted_issues": promoted, "validation_report": report},
        handles=sorted({h for issue in promoted for h in issue.get("handles", [])}),
        next_tools=["get_validation_report", "propose_repair_plan", "explain_entity"],
    )


__all__ = [
    "validate_vlm_review_output",
    "submit_vlm_review",
    "get_vlm_findings",
    "fuse_vlm_findings_into_semantic_graph",
    "evaluate_vlm_grounding",
    "promote_vlm_finding_to_validation_issue",
]
