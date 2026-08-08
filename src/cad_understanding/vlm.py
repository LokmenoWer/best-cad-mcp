"""Structured VLM review output validation and persistence."""

from __future__ import annotations

import json
import math
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
MIN_GROUNDING_SCORE = 0.35
# These intents assert a property of a complete semantic shape that no member
# entity can satisfy on its own. Annotation-oriented semantic_type values are
# intentionally excluded because they may describe the issue rather than the
# geometry being localized (for example a missing dimension on a circle).
EXACT_SEMANTIC_SHAPE_INTENTS = {"closed_profile"}


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
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    width = x2 - x1
    height = y2 - y1
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0.0
        or height <= 0.0
    ):
        return None
    return [x1, y1, x2, y2]


def _positive_image_dimensions(value: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(value, dict):
        return None
    try:
        width = float(value.get("width"))
        height = float(value.get("height"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(component) and component > 0.0 for component in (width, height)):
        return None
    if (
        not math.isclose(width, round(width), rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(height, round(height), rel_tol=0.0, abs_tol=1e-9)
    ):
        return None
    return float(round(width)), float(round(height))


def _finite_affine_matrix(value: Any) -> Optional[List[List[float]]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    matrix: List[List[float]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return None
        try:
            numeric_row = [float(component) for component in row]
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(component) for component in numeric_row):
            return None
        matrix.append(numeric_row)
    if not all(math.isclose(matrix[2][index], expected, rel_tol=0.0, abs_tol=1e-12)
               for index, expected in enumerate((0.0, 0.0, 1.0))):
        return None
    return matrix


def _matrix_multiply(left: List[List[float]],
                     right: List[List[float]]) -> List[List[float]]:
    return [
        [
            float(sum(left[row][index] * right[index][column] for index in range(3)))
            for column in range(3)
        ]
        for row in range(3)
    ]


def _matrices_close(actual: Any,
                    expected: List[List[float]]) -> bool:
    matrix = _finite_affine_matrix(actual)
    return bool(matrix) and all(
        math.isclose(
            matrix[row][column], expected[row][column],
            rel_tol=1e-10, abs_tol=1e-10,
        )
        for row in range(3)
        for column in range(3)
    )


def _axis_aligned_transform_bbox(bbox: List[float],
                                 matrix: List[List[float]]) -> Optional[List[float]]:
    # Visual resize/crop contracts may scale and translate only. Reject skew,
    # rotation, reflection, and perspective rather than guessing box extents.
    if (
        matrix[0][0] <= 0.0 or matrix[1][1] <= 0.0
        or not math.isclose(matrix[0][1], 0.0, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(matrix[1][0], 0.0, rel_tol=0.0, abs_tol=1e-12)
    ):
        return None
    transformed = [
        matrix[0][0] * bbox[0] + matrix[0][2],
        matrix[1][1] * bbox[1] + matrix[1][2],
        matrix[0][0] * bbox[2] + matrix[0][2],
        matrix[1][1] * bbox[3] + matrix[1][2],
    ]
    return _normalize_bbox(transformed)


def _tile_coordinate_contract(tile: Dict[str, Any]) -> Optional[
        Tuple[float, float, List[List[float]]]
]:
    global_bbox = _normalize_bbox(
        tile.get("global_pixel_bbox") or tile.get("pixel_bbox")
    )
    dimensions = _positive_image_dimensions(tile.get("image"))
    if global_bbox is None or dimensions is None:
        return None
    width, height = dimensions
    if (
        not math.isclose(global_bbox[2] - global_bbox[0], width, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(global_bbox[3] - global_bbox[1], height, rel_tol=0.0, abs_tol=1e-9)
    ):
        return None
    expected = [
        [1.0, 0.0, global_bbox[0]],
        [0.0, 1.0, global_bbox[1]],
        [0.0, 0.0, 1.0],
    ]
    if tile.get("local_to_global") is not None and not _matrices_close(
        tile.get("local_to_global"), expected
    ):
        return None
    return width, height, expected


def _normalize_observed_image_bbox(finding: Dict[str, Any],
                                   source_ref: Dict[str, Any],
                                   snapshot: Optional[Dict[str, Any]]) -> Tuple[
                                       Dict[str, Any], List[str]
                                   ]:
    if not snapshot:
        return finding, ["observed-image source_ref requires a mapped snapshot"]
    if str(source_ref.get("schema_version") or "") != "VisualSourceRef/v1":
        return finding, ["observed-image source_ref must use schema_version VisualSourceRef/v1"]
    snapshot_id = str(source_ref.get("snapshot_id") or "")
    expected_snapshot_id = str(snapshot.get("snapshot_id") or "")
    if not snapshot_id or snapshot_id != expected_snapshot_id:
        return finding, ["source_ref.snapshot_id does not match the mapped snapshot"]
    observed_dimensions = _positive_image_dimensions(source_ref.get("observed_image"))
    declared_source_dimensions = _positive_image_dimensions(source_ref.get("source_image"))
    snapshot_dimensions = _positive_image_dimensions(snapshot.get("image"))
    if observed_dimensions is None or declared_source_dimensions is None or snapshot_dimensions is None:
        return finding, ["source_ref image dimensions must be finite positive numbers"]
    tile_id = str(source_ref.get("tile_id") or "").strip().upper()
    tile: Optional[Dict[str, Any]] = None
    if tile_id:
        tile = next((
            item for item in snapshot.get("tiles", [])
            if isinstance(item, dict)
            and str(item.get("tile_id") or "").strip().upper() == tile_id
        ), None)
        if tile is None:
            return finding, [f"unknown source_ref.tile_id {tile_id}; it is not in the snapshot"]
        tile_contract = _tile_coordinate_contract(tile)
        if tile_contract is None:
            return finding, [f"snapshot tile {tile_id} has an invalid coordinate contract"]
        source_width, source_height, source_to_global = tile_contract
        expected_source_space = "tile_local"
    else:
        source_width, source_height = snapshot_dimensions
        source_to_global = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        expected_source_space = "snapshot_global"
    declared_width, declared_height = declared_source_dimensions
    if (
        not math.isclose(declared_width, source_width, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(declared_height, source_height, rel_tol=0.0, abs_tol=1e-9)
    ):
        return finding, ["source_ref source dimensions do not match the mapped artifact"]
    if str(source_ref.get("source_coordinate_space") or "") != expected_source_space:
        return finding, [f"source_ref.source_coordinate_space must be {expected_source_space}"]
    if str(source_ref.get("global_coordinate_space") or "") != "snapshot_global":
        return finding, ["source_ref.global_coordinate_space must be snapshot_global"]
    observed_width, observed_height = observed_dimensions
    if observed_width > source_width or observed_height > source_height:
        return finding, ["observed image dimensions cannot exceed mapped source dimensions"]
    width_ratio = observed_width / source_width
    height_ratio = observed_height / source_height
    rounding_tolerance = max(1.0 / source_width, 1.0 / source_height) + 1e-12
    if abs(width_ratio - height_ratio) > rounding_tolerance:
        return finding, ["observed image dimensions are not a valid aspect-preserving resize"]
    observed_to_source = [
        [source_width / observed_width, 0.0, 0.0],
        [0.0, source_height / observed_height, 0.0],
        [0.0, 0.0, 1.0],
    ]
    observed_to_global = _matrix_multiply(source_to_global, observed_to_source)
    for key, expected in (
        ("observed_to_source", observed_to_source),
        ("source_to_global", source_to_global),
        ("observed_to_global", observed_to_global),
    ):
        if not _matrices_close(source_ref.get(key), expected):
            return finding, [f"source_ref.{key} is missing or inconsistent with image dimensions"]
    bbox = _normalize_bbox(finding.get("bbox") or finding.get("pixel_bbox"))
    if bbox is None:
        finding["source_ref"] = source_ref
        return finding, []
    if (
        bbox[0] < 0.0 or bbox[1] < 0.0
        or bbox[2] > observed_width or bbox[3] > observed_height
    ):
        return finding, ["observed-image bbox is outside the embedded image bounds"]
    source_bbox = _axis_aligned_transform_bbox(bbox, observed_to_source)
    global_bbox = _axis_aligned_transform_bbox(bbox, observed_to_global)
    if source_bbox is None or global_bbox is None:
        return finding, ["source_ref transforms are not safe axis-aligned image transforms"]
    snapshot_width, snapshot_height = snapshot_dimensions
    if (
        global_bbox[0] < 0.0 or global_bbox[1] < 0.0
        or global_bbox[2] > snapshot_width or global_bbox[3] > snapshot_height
    ):
        return finding, ["normalized bbox is outside the snapshot image bounds"]
    finding["bbox"] = global_bbox
    finding["pixel_bbox"] = global_bbox
    normalized_ref = dict(source_ref)
    normalized_ref["observed_coordinate_space"] = "observed_image"
    normalized_ref["coordinate_space"] = "snapshot_global"
    finding["source_ref"] = normalized_ref
    finding["coordinate_normalization"] = {
        "normalized_coordinate_space": "snapshot_global",
        "observed_coordinate_space": "observed_image",
        "tile_id": tile_id,
        "observed_pixel_bbox": bbox,
        "source_pixel_bbox": source_bbox,
        "global_pixel_bbox": global_bbox,
        "observed_to_source": observed_to_source,
        "source_to_global": source_to_global,
        "observed_to_global": observed_to_global,
    }
    return finding, []


def _normalize_finding_coordinate_frame(raw: Dict[str, Any],
                                        snapshot: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Rebase snapshot tile-local VLM boxes into snapshot-global pixels."""
    finding = dict(raw)
    source_ref = finding.get("source_ref")
    if source_ref in (None, ""):
        return finding, []
    if not isinstance(source_ref, dict):
        return finding, ["source_ref must be an object"]
    source_ref = dict(source_ref)
    coordinate_space = str(source_ref.get("coordinate_space") or "").strip().lower()
    tile_id = str(source_ref.get("tile_id") or "").strip().upper()
    if coordinate_space in {"observed_image", "model_image", "rendered_image"}:
        return _normalize_observed_image_bbox(finding, source_ref, snapshot)
    if tile_id and not coordinate_space:
        return finding, ["source_ref.coordinate_space is required when tile_id is provided"]
    if coordinate_space in {"", "snapshot_global", "image_global", "global"}:
        source_ref["coordinate_space"] = "snapshot_global"
        finding["source_ref"] = source_ref
        return finding, []
    if coordinate_space not in {"tile_local", "local"}:
        return finding, ["source_ref.coordinate_space must be snapshot_global or tile_local"]
    if not tile_id:
        return finding, ["source_ref.tile_id is required for tile_local coordinates"]
    tile = next((
        item for item in (snapshot or {}).get("tiles", [])
        if isinstance(item, dict)
        and str(item.get("tile_id") or "").strip().upper() == tile_id
    ), None)
    if tile is None:
        return finding, [f"unknown source_ref.tile_id {tile_id}; it is not in the snapshot"]
    bbox = _normalize_bbox(finding.get("bbox") or finding.get("pixel_bbox"))
    if bbox is None:
        finding["source_ref"] = source_ref
        return finding, []
    tile_contract = _tile_coordinate_contract(tile)
    if tile_contract is None:
        return finding, [f"snapshot tile {tile_id} has an invalid coordinate contract"]
    local_width, local_height, local_to_global = tile_contract
    if (
        bbox[0] < 0.0 or bbox[1] < 0.0
        or bbox[2] > local_width or bbox[3] > local_height
    ):
        return finding, [f"tile-local bbox is outside {tile_id} bounds"]
    rebased = _axis_aligned_transform_bbox(bbox, local_to_global)
    if rebased is None:
        return finding, [f"snapshot tile {tile_id} has an unsafe coordinate transform"]
    finding["bbox"] = rebased
    finding["pixel_bbox"] = rebased
    source_ref["observed_coordinate_space"] = "tile_local"
    source_ref["coordinate_space"] = "snapshot_global"
    finding["source_ref"] = source_ref
    finding["coordinate_normalization"] = {
        "normalized_coordinate_space": "snapshot_global",
        "observed_coordinate_space": "tile_local",
        "tile_id": tile_id,
        "local_pixel_bbox": bbox,
        "global_pixel_bbox": rebased,
        "local_to_global": local_to_global,
    }
    return finding, []


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
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
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
    return (
        x1 >= 0.0 and y1 >= 0.0
        and x2 <= width and y2 <= height
    )


def validate_vlm_review_output(review: Any,
                               snapshot_id: Optional[str] = None,
                               database: Optional[CADDatabase] = None) -> ToolResult:
    """Validate and normalize a VLM drawing review payload."""
    db = get_db(database)
    ensure_understanding_schema(db)
    snapshot = _load_snapshot(db, snapshot_id) if snapshot_id else None
    if snapshot_id and not snapshot:
        return error_result(
            f"Unknown view snapshot: {snapshot_id}",
            next_tools=["export_view_image_with_mapping"],
        )

    raw_findings, warnings = _review_findings(review)
    assumed_global_bbox_count = sum(
        1 for finding in raw_findings
        if (finding.get("bbox") is not None or finding.get("pixel_bbox") is not None)
        and (
            not isinstance(finding.get("source_ref"), dict)
            or not finding.get("source_ref")
        )
    )
    if snapshot and assumed_global_bbox_count:
        warnings.append(
            f"{assumed_global_bbox_count} bbox finding(s) omitted source_ref; coordinates were assumed to be snapshot-global. Echo the image's source_ref_template whenever pixels came from a tile or downscaled embedded image."
        )
    normalized: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    available_overlay_ids = set(_overlay_ids(snapshot))
    visible_handles = set(_visible_handles(snapshot))
    explicit_id_counts: Dict[str, int] = {}
    for raw in raw_findings:
        explicit_id = str(raw.get("finding_id") or "").strip()
        if explicit_id:
            explicit_id_counts[explicit_id] = explicit_id_counts.get(explicit_id, 0) + 1
    scope = current_scope(db)
    with db._conn() as conn:
        existing_id_scopes = {
            str(row["finding_id"]): (
                str(row["workspace_id"] or ""),
                str(row["drawing_id"] or ""),
                str(row["conversation_id"] or ""),
                str(row["thread_id"] or ""),
            )
            for row in conn.execute('''
                SELECT finding_id, workspace_id, drawing_id,
                       conversation_id, thread_id
                FROM cad_vlm_findings
            ''').fetchall()
        }
    active_scope = (
        scope["workspace_id"], scope["drawing_id"],
        scope["conversation_id"], scope["thread_id"],
    )

    for index, raw in enumerate(raw_findings, start=1):
        item_errors: List[str] = []
        original_raw = raw
        raw, coordinate_errors = _normalize_finding_coordinate_frame(raw, snapshot)
        item_errors.extend(coordinate_errors)
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
        raw_bbox = raw.get("bbox") or raw.get("pixel_bbox")
        bbox = _normalize_bbox(raw_bbox)
        if raw_bbox is not None and bbox is None:
            item_errors.append(
                "bbox must contain four finite numbers with positive width and height"
            )
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

        semantic_type = view_grounding._normalized_semantic_type(
            raw.get("semantic_type")
            or raw.get("object_type")
            or raw.get("detected_object_type")
        )
        finding_id = str(raw.get("finding_id") or "").strip()
        if not finding_id:
            finding_id = stable_id(
                "vlm", snapshot_id or "", index, issue_type, semantic_type,
                overlay_id, bbox, claimed_handles,
            )
        elif explicit_id_counts.get(finding_id, 0) > 1:
            item_errors.append(
                f"finding_id {finding_id} is duplicated within this review payload"
            )
        existing_scope = existing_id_scopes.get(finding_id)
        if existing_scope is not None and existing_scope != active_scope:
            item_errors.append(
                f"finding_id {finding_id} is already used in a different drawing scope"
            )
        normalized_item = {
            "finding_id": finding_id,
            "snapshot_id": snapshot_id or str(raw.get("snapshot_id") or ""),
            "issue_type": issue_type,
            "severity": severity,
            "confidence": round(confidence, 4),
            "overlay_id": overlay_id,
            "bbox": bbox,
            "claimed_handles": claimed_handles,
            "semantic_type": semantic_type,
            "source_ref": raw.get("source_ref") or {},
            "coordinate_normalization": raw.get("coordinate_normalization") or {},
            "evidence": evidence,
            "raw_finding": original_raw,
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


def _semantic_membership_candidates(database: CADDatabase,
                                    snapshot: Dict[str, Any],
                                    semantic_type: str,
                                    member_handles: Iterable[str]) -> List[Dict[str, Any]]:
    expected = view_grounding._normalized_semantic_type(semantic_type)
    members = {str(handle) for handle in member_handles if handle}
    if not expected or not members:
        return []
    items = [
        item for item in (
            (snapshot.get("semantic_overlay_items") or [])
            or [
                entry for entry in snapshot.get("overlay_items", [])
                if str(entry.get("item_kind") or "").lower() == "semantic"
            ]
        )
        if view_grounding._normalized_semantic_type(item.get("object_type")) == expected
        and members.issubset({str(handle) for handle in item.get("handles", []) if handle})
    ]
    candidates: List[Dict[str, Any]] = []
    for item in items:
        query_bbox = view_grounding._pixel_bbox_with_min_size(
            item.get("pixel_bbox") or [0, 0, 0, 0],
            min_size=8.0,
        )
        candidate = view_grounding._candidate_from_overlay_item(
            database, item, query_bbox, snapshot
        )
        semantic_confidence = float(item.get("confidence") or 0.0)
        candidate.update({
            "candidate_type": "semantic_shape",
            "score": round(min(0.99, 0.91 + 0.08 * semantic_confidence), 4),
            "semantic_confidence": round(semantic_confidence, 4),
            "semantic_intent": expected,
            "semantic_intent_match": True,
            "membership_evidence": {
                "member_handles": sorted(members),
                "reason": "Referenced handles are members of this known semantic shape.",
            },
        })
        candidates.append(candidate)
    candidates.sort(key=lambda item: (
        -float(item.get("score") or 0.0),
        str(item.get("object_id") or ""),
    ))
    return candidates


def _verified_claim_group_candidate(database: CADDatabase,
                                    snapshot: Dict[str, Any],
                                    claimed_handles: Iterable[str]) -> Optional[Dict[str, Any]]:
    visible = {str(handle) for handle in snapshot.get("visible_handles", []) if handle}
    overlay_items = snapshot.get("overlay_items") or []
    verified_members: List[Dict[str, Any]] = []
    for raw_handle in claimed_handles:
        handle = str(raw_handle)
        if handle not in visible:
            continue
        item = next((
            entry for entry in overlay_items
            if str(entry.get("item_kind") or "entity") == "entity"
            and str(entry.get("handle") or "") == handle
        ), None)
        if item is None:
            pixel_bbox = (snapshot.get("entity_screen_bboxes") or {}).get(handle)
            item = {
                "overlay_id": "",
                "item_kind": "entity",
                "handle": handle,
                "native_handle": handle,
                "pixel_bbox": pixel_bbox or [0, 0, 0, 0],
                "confidence": 1.0,
            }
        query_bbox = view_grounding._pixel_bbox_with_min_size(
            item.get("pixel_bbox") or [0, 0, 0, 0], min_size=2.0
        )
        candidate = view_grounding._candidate_from_overlay_item(
            database, item, query_bbox, snapshot
        )
        candidate.update({
            "score": 1.0,
            "confidence": round(float(snapshot.get("confidence", 0.5) or 0.5), 4),
            "evidence": {
                **(candidate.get("evidence") or {}),
                "reason": "Claimed handle was verified as visible in the snapshot.",
            },
        })
        verified_members.append(candidate)
    if not verified_members:
        return None
    verified_handles = sorted({
        str(candidate.get("handle"))
        for candidate in verified_members if candidate.get("handle")
    })
    pixel_boxes = [
        candidate.get("pixel_bbox") for candidate in verified_members
        if isinstance(candidate.get("pixel_bbox"), list)
        and len(candidate.get("pixel_bbox")) >= 4
    ]
    group_bbox = [
        min(box[0] for box in pixel_boxes),
        min(box[1] for box in pixel_boxes),
        max(box[2] for box in pixel_boxes),
        max(box[3] for box in pixel_boxes),
    ] if pixel_boxes else []
    return {
        **verified_members[0],
        "candidate_type": "verified_claim_group",
        "handle": verified_handles[0] if verified_handles else "",
        "native_handle": verified_handles[0] if verified_handles else "",
        "handles": verified_handles,
        "pixel_bbox": group_bbox,
        "score": 1.0,
        "confidence": min(
            float(candidate.get("confidence") or 0.0)
            for candidate in verified_members
        ),
        "candidate_primitives": [],
        "evidence": {
            "reason": "All claimed handles were verified as one visible handle group.",
            "member_candidates": verified_members,
        },
    }


def _ground_finding(database: CADDatabase,
                    finding: Dict[str, Any],
                    top_k: int) -> Tuple[
                        List[Dict[str, Any]], List[str], Dict[str, Any], Dict[str, Any]
                    ]:
    """Ground and reconcile every localization source in one finding."""
    snapshot_id = str(finding.get("snapshot_id") or "")
    snapshot = _load_snapshot(database, snapshot_id) or {}
    warnings: List[str] = []
    world_bbox: Dict[str, Any] = {}
    bbox_candidates: List[Dict[str, Any]] = []
    overlay_candidates: List[Dict[str, Any]] = []
    membership_candidates: List[Dict[str, Any]] = []
    claim_candidates: List[Dict[str, Any]] = []
    semantic_type = view_grounding._normalized_semantic_type(
        finding.get("semantic_type")
    )
    exact_semantic_shape_required = bool(
        finding.get("bbox") and semantic_type in EXACT_SEMANTIC_SHAPE_INTENTS
    )
    exact_semantic_shape_satisfied = not exact_semantic_shape_required
    bbox_grounding_selection: Dict[str, Any] = {}
    claimed_group = sorted({
        str(handle) for handle in finding.get("claimed_handles", []) if handle
    })
    overlay_group: List[str] = []
    bbox_group: List[str] = []
    overlay_bbox_supported = True

    if finding.get("bbox"):
        grounded = view_grounding.ground_vlm_region(
            snapshot_id,
            list(finding["bbox"]),
            top_k=max(2, int(top_k or 10)),
            database=database,
            semantic_type=semantic_type or None,
        )
        warnings.extend(grounded.get("warnings", []))
        if grounded.get("ok"):
            data = grounded.get("data") or {}
            raw_bbox_selection = data.get("selection") or {}
            # Keep decision provenance without retaining candidate object
            # references. Candidate dictionaries are reused below and later
            # receive grounding_decision; embedding them here would create a
            # selection -> candidate -> selection cycle during JSON storage.
            bbox_grounding_selection = {
                key: raw_bbox_selection.get(key)
                for key in (
                    "strategy",
                    "semantic_intent",
                    "semantic_intent_applied",
                    "score_margin",
                    "ambiguous",
                    "ambiguity_threshold",
                    "minimum_grounding_score",
                    "acceptable_candidate_count",
                    "decision_pool",
                    "recommended_handle_group",
                    "runner_up_handle_group",
                )
                if key in raw_bbox_selection
            }
            recommended_candidate = data.get("recommended_candidate") or {}
            if exact_semantic_shape_required:
                exact_semantic_shape_satisfied = bool(
                    str(recommended_candidate.get("candidate_type") or "")
                    == "semantic_shape"
                    and view_grounding._normalized_semantic_type(
                        recommended_candidate.get("object_type")
                    ) == semantic_type
                    and float(recommended_candidate.get("score") or 0.0)
                    >= MIN_GROUNDING_SCORE
                    and _candidate_has_region_support(recommended_candidate)
                )
            combined = [
                *data.get("shape_candidates", []),
                *data.get("candidates", []),
            ]
            ordered = [
                *((data.get("selection") or {}).get("decision_candidates") or []),
                *combined,
            ]
            seen = set()
            for candidate in ordered:
                key = (
                    str(candidate.get("candidate_type") or candidate.get("item_kind") or ""),
                    str(candidate.get("object_id") or candidate.get("handle") or ""),
                    tuple(_handles_for_grounding_candidate(candidate)),
                )
                if key in seen:
                    continue
                seen.add(key)
                bbox_candidates.append(candidate)
            if bbox_candidates:
                bbox_group = _handles_for_grounding_candidate(bbox_candidates[0])
            world_bbox = data.get("world_region") or {}

    overlay_item: Optional[Dict[str, Any]] = None
    if finding.get("overlay_id"):
        overlay_norm = str(finding.get("overlay_id") or "").strip().upper()
        overlay_item = next((
            entry for entry in snapshot.get("overlay_items", [])
            if str(entry.get("overlay_id") or "").strip().upper() == overlay_norm
        ), None)
        if overlay_item is not None:
            overlay_group = _handles_for_grounding_candidate(overlay_item)
            query_bbox = (
                list(finding["bbox"])
                if finding.get("bbox")
                else view_grounding._pixel_bbox_with_min_size(
                    overlay_item.get("pixel_bbox") or [0, 0, 0, 0], min_size=8.0
                )
            )
            candidate = view_grounding._candidate_from_overlay_item(
                database,
                overlay_item,
                query_bbox,
                snapshot,
                direct_reference=not bool(finding.get("bbox")),
            )
            if finding.get("bbox"):
                overlay_bbox_supported = bool(
                    float(candidate.get("overlap_score") or 0.0) > 0.0
                    or float(candidate.get("score") or 0.0) > 0.1
                )
                pixel_polygon = overlay_item.get("pixel_polygon") or []
                if (
                    str(overlay_item.get("item_kind") or "").lower() == "semantic"
                    and len(pixel_polygon) >= 3
                ):
                    polygon_support = view_grounding._pixel_polygon_support(
                        query_bbox,
                        pixel_polygon,
                        float(
                            ((candidate.get("evidence") or {}).get("spatial_support") or {}).get(
                                "stroke_padding_px", 2.0
                            )
                        ),
                    )
                    candidate["polygon_support"] = polygon_support
                    overlay_bbox_supported = bool(
                        overlay_bbox_supported and polygon_support.get("supported")
                    )
            else:
                candidate["score"] = 1.0
                candidate["confidence"] = round(min(
                    1.0,
                    float(snapshot.get("confidence", 0.5) or 0.5)
                    * float(overlay_item.get("confidence", 1.0) or 0.0),
                ), 4)
            candidate["overlay_reference"] = {
                "overlay_id": overlay_norm,
                "identity_verified": True,
                "score_forced": not bool(finding.get("bbox")),
                "bbox_spatial_supported": overlay_bbox_supported,
            }
            if overlay_bbox_supported:
                overlay_candidates.append(candidate)
            if semantic_type:
                membership_candidates = _semantic_membership_candidates(
                    database, snapshot, semantic_type, overlay_group
                )

    if claimed_group and not finding.get("bbox"):
        claim_candidate = _verified_claim_group_candidate(
            database, snapshot, claimed_group
        )
        if claim_candidate:
            claim_candidates.append(claim_candidate)
        if semantic_type and not membership_candidates:
            membership_candidates = _semantic_membership_candidates(
                database, snapshot, semantic_type, claimed_group
            )

    membership_groups = [
        _handles_for_grounding_candidate(candidate)
        for candidate in membership_candidates
    ]
    if membership_candidates:
        for candidate in overlay_candidates + claim_candidates:
            if any(
                set(_handles_for_grounding_candidate(candidate)) < set(group)
                for group in membership_groups
            ):
                candidate["unadjusted_score"] = candidate.get("score")
                candidate["score"] = round(min(float(candidate.get("score") or 0.0), 0.86), 4)

    conflicts: List[Dict[str, Any]] = []
    agreements: List[Dict[str, Any]] = []
    bbox_set = set(bbox_group)
    overlay_set = set(overlay_group)
    claimed_set = set(claimed_group)
    bbox_top = bbox_candidates[0] if bbox_candidates else {}
    bbox_is_semantic = str(bbox_top.get("candidate_type") or "") == "semantic_shape"
    if finding.get("bbox") and overlay_item is not None and not overlay_bbox_supported:
        conflicts.append({
            "sources": ["bbox", "overlay_id"],
            "reason": "overlay_item_has_no_spatial_support_in_bbox",
            "overlay_group": sorted(overlay_set),
        })
    if bbox_set and overlay_set:
        if overlay_set == bbox_set or (bbox_is_semantic and overlay_set < bbox_set):
            agreements.append({
                "sources": ["bbox", "overlay_id"],
                "relation": "exact_group" if overlay_set == bbox_set else "overlay_member_of_shape",
                "handle_group": sorted(bbox_set),
            })
        else:
            conflicts.append({
                "sources": ["bbox", "overlay_id"],
                "reason": "overlay_group_conflicts_with_bbox_group",
                "bbox_group": sorted(bbox_set),
                "overlay_group": sorted(overlay_set),
            })
    if bbox_set and claimed_set:
        if claimed_set == bbox_set:
            agreements.append({
                "sources": ["bbox", "claimed_handles"],
                "relation": "exact_group",
                "handle_group": sorted(bbox_set),
            })
        else:
            conflicts.append({
                "sources": ["bbox", "claimed_handles"],
                "reason": "claimed_group_does_not_exactly_match_bbox_group",
                "bbox_group": sorted(bbox_set),
                "claimed_group": sorted(claimed_set),
            })
    if overlay_set and claimed_set:
        if overlay_set == claimed_set:
            agreements.append({
                "sources": ["overlay_id", "claimed_handles"],
                "relation": "exact_group",
                "handle_group": sorted(overlay_set),
            })
        elif not (overlay_set < claimed_set or claimed_set < overlay_set):
            conflicts.append({
                "sources": ["overlay_id", "claimed_handles"],
                "reason": "overlay_group_conflicts_with_claimed_group",
                "overlay_group": sorted(overlay_set),
                "claimed_group": sorted(claimed_set),
            })
    if claimed_set and any(claimed_set < set(group) for group in membership_groups):
        conflicts.append({
            "sources": ["claimed_handles", "semantic_type"],
            "reason": "claimed_group_is_incomplete_for_known_semantic_shape",
            "claimed_group": sorted(claimed_set),
            "candidate_groups": membership_groups,
        })
    distinct_membership_groups = {tuple(group) for group in membership_groups}
    if not finding.get("bbox") and len(distinct_membership_groups) > 1:
        conflicts.append({
            "sources": ["overlay_id" if overlay_set else "claimed_handles", "semantic_type"],
            "reason": "referenced_members_belong_to_multiple_semantic_shapes",
            "candidate_groups": [list(group) for group in sorted(distinct_membership_groups)],
        })

    ordered_candidates = (
        [*bbox_candidates, *overlay_candidates]
        if bbox_candidates
        else [*membership_candidates, *overlay_candidates, *claim_candidates]
    )
    candidates: List[Dict[str, Any]] = []
    seen_candidate_keys = set()
    for candidate in ordered_candidates:
        candidate_key = (
            str(candidate.get("candidate_type") or candidate.get("item_kind") or ""),
            str(candidate.get("object_id") or candidate.get("handle") or ""),
            tuple(_handles_for_grounding_candidate(candidate)),
        )
        if candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        candidates.append(candidate)
        if len(candidates) >= max(2, int(top_k or 10)):
            break

    if finding.get("bbox") and not world_bbox:
        world_region = view_grounding.map_pixel_region_to_world_bbox(
            snapshot_id,
            list(finding["bbox"]),
            database=database,
        )
        warnings.extend(world_region.get("warnings", []))
        if world_region.get("ok"):
            world_bbox = world_region["data"].get("world_bbox") or {}
    reconciliation = {
        "sources": [
            source for source, present in (
                ("overlay_id", bool(finding.get("overlay_id"))),
                ("bbox", bool(finding.get("bbox"))),
                ("claimed_handles", bool(claimed_group)),
                ("semantic_type", bool(semantic_type)),
            ) if present
        ],
        "semantic_type": semantic_type,
        "bbox_handle_group": bbox_group,
        "overlay_handle_group": overlay_group,
        "claimed_handle_group": claimed_group,
        "semantic_membership_groups": membership_groups,
        "exact_semantic_shape_required": exact_semantic_shape_required,
        "exact_semantic_shape_satisfied": exact_semantic_shape_satisfied,
        "bbox_grounding_selection": bbox_grounding_selection,
        "agreements": agreements,
        "conflicts": conflicts,
    }
    if exact_semantic_shape_required and not exact_semantic_shape_satisfied:
        warnings.append(
            f"Semantic intent {semantic_type!r} had no acceptable exact semantic shape; "
            "entity-only candidates are retained as evidence but cannot satisfy grounding."
        )
    if conflicts:
        warnings.append(
            "Localization sources disagree or describe an incomplete semantic group; grounding remains ambiguous."
        )
    return candidates, sorted(set(warnings)), world_bbox, reconciliation


def _handles_for_grounding_candidate(candidate: Dict[str, Any]) -> List[str]:
    values = candidate.get("handles")
    if not isinstance(values, list) or not values:
        values = [candidate.get("handle")]
    return sorted({str(handle) for handle in values if handle})


def _candidate_has_region_support(candidate: Dict[str, Any]) -> bool:
    polygon_support = candidate.get("polygon_support")
    if isinstance(polygon_support, dict):
        return bool(polygon_support.get("supported"))
    spatial = (candidate.get("evidence") or {}).get("spatial_support") or {}
    support_mode = str(spatial.get("support_mode") or candidate.get("support_mode") or "")
    if support_mode in {"path", "reconstructed_path"}:
        return float(spatial.get("path_length_in_query_px") or 0.0) > 0.0
    return (
        float(spatial.get("overlap_score") or candidate.get("overlap_score") or 0.0) > 0.0
        and float(spatial.get("query_coverage") or candidate.get("query_coverage") or 0.0) > 0.0
    )


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
                      prompt_version: str = "vlm_review_drawing/v3",
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
        candidates, grounding_warnings, world_bbox, reconciliation = _ground_finding(
            db, item, top_k
        )
        top_candidate = candidates[0] if candidates else None
        top_handles = set(_handles_for_grounding_candidate(top_candidate or {}))
        runner_up = next((
            candidate for candidate in candidates[1:]
            if set(_handles_for_grounding_candidate(candidate)) != top_handles
        ), None)
        score_margin = (
            float((top_candidate or {}).get("score") or 0.0)
            - float((runner_up or {}).get("score") or 0.0)
            if top_candidate else 0.0
        )
        claimed_handles = set(item.get("claimed_handles", []))
        top_score = float((top_candidate or {}).get("score") or 0.0)
        region_support = bool(
            top_candidate and (
                not item.get("bbox")
                or _candidate_has_region_support(top_candidate)
            )
        )
        exact_semantic_shape_required = bool(
            reconciliation.get("exact_semantic_shape_required")
        )
        top_candidate_semantic_contract = bool(
            not exact_semantic_shape_required
            or (
                reconciliation.get("exact_semantic_shape_satisfied")
                and str((top_candidate or {}).get("candidate_type") or "")
                == "semantic_shape"
                and view_grounding._normalized_semantic_type(
                    (top_candidate or {}).get("object_type")
                ) == view_grounding._normalized_semantic_type(
                    item.get("semantic_type")
                )
            )
        )
        top_candidate_acceptable = bool(
            top_candidate
            and top_score >= MIN_GROUNDING_SCORE
            and region_support
            and top_candidate_semantic_contract
        )
        runner_up_score = float((runner_up or {}).get("score") or 0.0)
        runner_up_region_support = bool(
            runner_up and (
                not item.get("bbox")
                or _candidate_has_region_support(runner_up)
            )
        )
        runner_up_semantic_contract = bool(
            not exact_semantic_shape_required
            or (
                str((runner_up or {}).get("candidate_type") or "")
                == "semantic_shape"
                and view_grounding._normalized_semantic_type(
                    (runner_up or {}).get("object_type")
                ) == view_grounding._normalized_semantic_type(
                    item.get("semantic_type")
                )
            )
        )
        runner_up_acceptable = bool(
            runner_up
            and runner_up_score >= MIN_GROUNDING_SCORE
            and runner_up_region_support
            and runner_up_semantic_contract
        )
        # A close pair is ambiguous only when both are viable decisions. Two
        # sub-threshold spatial hypotheses are evidence for abstention, not an
        # ambiguity claim; this mirrors ground_vlm_region's recommended=None
        # contract for semantic_shape_matches_below_threshold.
        candidate_margin_ambiguous = bool(
            top_candidate_acceptable
            and runner_up_acceptable
            and score_margin < 0.08
        )
        ambiguous = bool(
            candidate_margin_ambiguous
            or reconciliation.get("conflicts")
        )
        if item.get("bbox") and claimed_handles and not top_handles:
            ambiguous = True
            warnings.append(
                f"Finding {item['finding_id']} claimed visible handles but its bbox has no spatially supported CAD candidate."
            )
        if top_handles and claimed_handles and claimed_handles != top_handles:
            ambiguous = True
            missing_from_candidate = sorted(claimed_handles - top_handles)
            unclaimed_in_candidate = sorted(top_handles - claimed_handles)
            warnings.append(
                f"Finding {item['finding_id']} claimed handle group does not exactly match "
                f"the top grounding group (missing={missing_from_candidate}, "
                f"unclaimed={unclaimed_in_candidate})."
            )
        if top_candidate and not top_candidate_acceptable:
            warnings.append(
                f"Finding {item['finding_id']} top candidate did not meet the absolute grounding floor "
                f"(score={top_score:.4f}, required={MIN_GROUNDING_SCORE:.2f}, "
                f"region_support={region_support})."
            )
            if claimed_handles or item.get("overlay_id"):
                ambiguous = True
        if ambiguous:
            status = "ambiguous"
            grounded_handles: List[str] = []
            if reconciliation.get("conflicts"):
                warnings.append(
                    f"Finding {item['finding_id']} remains ambiguous because localization sources conflict."
                )
            else:
                warnings.append(
                    f"Finding {item['finding_id']} remains ambiguous (top candidate margin {score_margin:.4f})."
                )
        elif top_handles and top_candidate_acceptable:
            status = "grounded"
            grounded_handles = sorted(top_handles)
        else:
            status = "validated"
            grounded_handles = []
        selection_payload = {
            "score_margin": round(score_margin, 4),
            "ambiguity_threshold": 0.08,
            "minimum_grounding_score": MIN_GROUNDING_SCORE,
            "top_candidate_region_supported": region_support,
            "top_candidate_semantic_contract_satisfied": top_candidate_semantic_contract,
            "top_candidate_acceptable": top_candidate_acceptable,
            "runner_up_region_supported": runner_up_region_support,
            "runner_up_semantic_contract_satisfied": runner_up_semantic_contract,
            "runner_up_acceptable": runner_up_acceptable,
            "candidate_margin_ambiguous": candidate_margin_ambiguous,
            "status": status,
            "reconciliation": reconciliation,
        }
        if top_candidate is not None:
            top_candidate["grounding_decision"] = selection_payload
        warnings.extend(grounding_warnings)
        grounding_selection = {
            **selection_payload,
            "selected_candidate": top_candidate,
        }
        grounding_audit = {
            "schema_version": "VLMGroundingAudit/v1",
            "semantic_type": item.get("semantic_type") or "",
            "source_ref": item.get("source_ref") or {},
            "coordinate_normalization": item.get("coordinate_normalization") or {},
            "grounding_selection": grounding_selection,
        }
        persisted_evidence = {
            **(item.get("evidence") or {}),
            "grounding_reconciliation": reconciliation,
            "grounding_audit": grounding_audit,
        }
        findings.append({
            **item,
            "status": status,
            "grounded_handles": grounded_handles,
            "grounding_candidates": candidates,
            "grounding_selection": grounding_selection,
            "world_bbox": world_bbox,
            "evidence": persisted_evidence,
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
    item["bbox"] = list(item.get("pixel_bbox") or [])
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    audit = evidence.get("grounding_audit") if isinstance(evidence, dict) else {}
    if isinstance(audit, dict):
        for key in (
            "semantic_type",
            "source_ref",
            "coordinate_normalization",
            "grounding_selection",
        ):
            if key in audit:
                item[key] = audit[key]
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
        and item.get("status") == "grounded"
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
        value = finding.get(key)
        if value:
            return view_grounding._normalized_semantic_type(value)
        value = raw.get(key) if isinstance(raw, dict) else None
        if value:
            return view_grounding._normalized_semantic_type(value)
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if value:
            return view_grounding._normalized_semantic_type(value)
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


def _candidate_handle_group_records(finding: Dict[str, Any],
                                    top_k: int) -> List[Dict[str, Any]]:
    """Return canonical decision groups without discarding their scores."""
    records: List[Dict[str, Any]] = []
    seen = set()
    for candidate in finding.get("grounding_candidates") or []:
        group = tuple(_handles_for_grounding_candidate(candidate))
        if not group or group in seen:
            continue
        seen.add(group)
        raw_score = candidate.get("score")
        try:
            numeric_score = float(raw_score)
            score_available = raw_score is not None and math.isfinite(numeric_score)
            score = round(numeric_score, 8) if score_available else 0.0
        except (TypeError, ValueError, OverflowError):
            score_available = False
            score = 0.0
        records.append({
            "handles": list(group),
            "score": score,
            "score_available": score_available,
            "score_valid_probability": bool(
                score_available and 0.0 <= score <= 1.0
            ),
            "candidate_type": str(
                candidate.get("candidate_type")
                or candidate.get("item_kind")
                or ""
            ),
        })
        if len(records) >= max(1, int(top_k or 1)):
            break
    if not records:
        grounded = sorted({
            str(handle) for handle in (finding.get("grounded_handles") or []) if handle
        })
        if grounded:
            records = [{
                "handles": grounded,
                "score": 1.0,
                # This score is a compatibility fallback, not a model output;
                # excluding it avoids spuriously perfect calibration metrics.
                "score_available": False,
                "score_valid_probability": False,
                "candidate_type": "persisted_grounded_group",
            }]
    return records


def _normalize_alternative_handle_groups(value: Any) -> List[List[str]]:
    groups: List[List[str]] = []
    seen = set()
    if not isinstance(value, (list, tuple)):
        return groups
    for raw_group in value:
        group = tuple(_normalize_handles(raw_group))
        if not group or group in seen:
            continue
        seen.add(group)
        groups.append(list(group))
    return groups


def _match_ground_truth(finding: Dict[str, Any],
                        expected: Dict[str, Any]) -> bool:
    if expected.get("finding_id"):
        return expected.get("finding_id") == finding.get("finding_id")
    if expected.get("overlay_id") and str(expected.get("overlay_id")).upper() != str(finding.get("overlay_id") or "").upper():
        return False
    if expected.get("issue_type") and str(expected.get("issue_type")).lower() != str(finding.get("issue_type") or "").lower():
        return False
    return bool(expected.get("overlay_id") or expected.get("issue_type"))


def _expected_ground_truth_spec(expected: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize exact, equivalent, and deliberately ambiguous shape groups."""
    primary = _normalize_handles(
        expected.get("expected_handle_group")
        or expected.get("expected_handles")
        or expected.get("handles")
    )
    equivalent = _normalize_alternative_handle_groups(
        expected.get("expected_equivalent_handle_groups")
        or expected.get("equivalent_handle_groups")
    )
    alternatives = _normalize_alternative_handle_groups(
        expected.get("expected_alternative_groups")
    )

    exact_groups: List[List[str]] = []
    seen = set()
    for group in ([primary] if primary else []) + equivalent:
        canonical = tuple(group)
        if canonical and canonical not in seen:
            seen.add(canonical)
            exact_groups.append(list(canonical))

    acceptable_groups: List[List[str]] = []
    acceptable_seen = set()
    for group in exact_groups + alternatives:
        canonical = tuple(group)
        if canonical and canonical not in acceptable_seen:
            acceptable_seen.add(canonical)
            acceptable_groups.append(list(canonical))

    return {
        "raw": expected,
        "primary_group": primary,
        "equivalent_groups": equivalent,
        "alternative_groups": alternatives,
        "exact_groups": exact_groups,
        "exact_group_sets": [set(group) for group in exact_groups],
        "alternative_group_sets": [set(group) for group in alternatives],
        "acceptable_group_sets": [set(group) for group in acceptable_groups],
        "expected_union": {
            handle for group in acceptable_groups for handle in group
        },
        "expected_status": str(expected.get("expected_status") or "").strip().lower(),
        "expected_issue_type": str(expected.get("issue_type") or "").strip().lower(),
    }


def _finding_grounding_features(finding: Dict[str, Any],
                                top_k: int) -> Dict[str, Any]:
    records = _candidate_handle_group_records(finding, top_k)
    groups = [record["handles"] for record in records]
    status = str(finding.get("status") or "").strip().lower()
    # Promotion is downstream of a grounded decision. Treat it as committed
    # grounding while retaining the raw persisted status in case details.
    decision_status = "grounded" if status == "promoted" else status
    return {
        "records": records,
        "groups": groups,
        "group_sets": [set(group) for group in groups],
        "top1_group": set(groups[0]) if groups else set(),
        "grounded_group": set(_normalize_handles(finding.get("grounded_handles"))),
        "status": status,
        "decision_status": decision_status,
    }


def _best_group_overlap(actual: set,
                        expected_groups: List[set]) -> Tuple[float, float, float, List[str]]:
    """Return the best complete-group comparison across valid representations."""
    if not expected_groups:
        return 0.0, 0.0, 0.0, []
    best: Optional[Tuple[Tuple[float, float, float, int, Tuple[str, ...]],
                         Tuple[float, float, float, List[str]]]] = None
    for expected in expected_groups:
        intersection_count = len(actual.intersection(expected))
        precision = intersection_count / len(actual) if actual else 0.0
        recall = intersection_count / len(expected) if expected else 0.0
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall > 0.0 else 0.0
        )
        canonical = tuple(sorted(expected))
        # Prefer the highest F1, then recall/precision. The final terms make
        # ties deterministic without changing any metric value.
        key = (f1, recall, precision, -len(expected), canonical)
        result = (precision, recall, f1, list(canonical))
        if best is None or key > best[0]:
            best = (key, result)
    return best[1] if best is not None else (0.0, 0.0, 0.0, [])


def _ground_truth_pair_quality(spec: Dict[str, Any],
                               finding: Dict[str, Any],
                               features: Dict[str, Any]) -> Optional[int]:
    """Quality for globally optimal pairing when identity keys are non-unique."""
    expected = spec["raw"]
    if not _match_ground_truth(finding, expected):
        return None

    acceptable = spec["acceptable_group_sets"]
    top1_group = features["top1_group"]
    candidate_groups = features["group_sets"]
    grounded_group = features["grounded_group"]
    quality = 0

    # When a benchmark identifies multiple observations only by issue type,
    # use full-shape agreement to pair them, analogous to optimal IoU matching
    # in object-detection evaluation. Exact identity keys still constrain the
    # eligible edge through `_match_ground_truth`.
    if acceptable:
        if grounded_group and any(grounded_group == group for group in acceptable):
            quality += 800_000
        if top1_group and any(top1_group == group for group in acceptable):
            quality += 500_000
        elif any(candidate == group for candidate in candidate_groups for group in acceptable):
            quality += 250_000
        _, _, top1_f1, _ = _best_group_overlap(top1_group, acceptable)
        quality += int(round(top1_f1 * 100_000))

    expected_status = spec["expected_status"]
    if expected_status:
        raw_status = features["status"]
        status_match = (
            raw_status == expected_status
            or (expected_status == "grounded" and raw_status == "promoted")
        )
        quality += 40_000 if status_match else 0

    expected_issue = spec["expected_issue_type"]
    actual_issue = str(finding.get("issue_type") or "").strip().lower()
    if expected_issue and actual_issue == expected_issue:
        quality += 20_000
    if expected.get("overlay_id"):
        quality += 10_000
    if expected.get("finding_id"):
        quality += 20_000
    return quality


def _maximum_weight_assignment(weights: List[List[int]]) -> List[Optional[int]]:
    """Exact rectangular Hungarian assignment (rows <= columns)."""
    if not weights:
        return []
    row_count = len(weights)
    column_count = len(weights[0]) if weights[0] else 0
    if not column_count or any(len(row) != column_count for row in weights):
        return [None] * row_count
    if row_count > column_count:
        raise ValueError("assignment requires at least as many columns as rows")

    max_weight = max(max(row) for row in weights)
    # Hungarian minimizes costs. All values are integer and finite.
    costs = [[max_weight - value for value in row] for row in weights]
    u = [0] * (row_count + 1)
    v = [0] * (column_count + 1)
    matched_row_for_column = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)

    for row_index in range(1, row_count + 1):
        matched_row_for_column[0] = row_index
        current_column = 0
        minimum_slack = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[current_column] = True
            current_row = matched_row_for_column[current_column]
            delta = math.inf
            next_column = 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][column_index - 1]
                    - u[current_row]
                    - v[column_index]
                )
                if reduced_cost < minimum_slack[column_index]:
                    minimum_slack[column_index] = reduced_cost
                    previous_column[column_index] = current_column
                if minimum_slack[column_index] < delta:
                    delta = minimum_slack[column_index]
                    next_column = column_index
            for column_index in range(column_count + 1):
                if used[column_index]:
                    u[matched_row_for_column[column_index]] += delta
                    v[column_index] -= delta
                else:
                    minimum_slack[column_index] -= delta
            current_column = next_column
            if matched_row_for_column[current_column] == 0:
                break
        while True:
            next_column = previous_column[current_column]
            matched_row_for_column[current_column] = matched_row_for_column[next_column]
            current_column = next_column
            if current_column == 0:
                break

    assignment: List[Optional[int]] = [None] * row_count
    for column_index in range(1, column_count + 1):
        row_index = matched_row_for_column[column_index]
        if row_index:
            assignment[row_index - 1] = column_index - 1
    return assignment


def _optimal_ground_truth_matches(specs: List[Dict[str, Any]],
                                  findings: List[Dict[str, Any]],
                                  finding_features: List[Dict[str, Any]]) -> Dict[int, int]:
    """Maximize match cardinality first, then total shape/decision agreement."""
    if not specs or not findings:
        return {}
    raw_qualities: List[List[Optional[int]]] = []
    maximum_quality = 0
    for spec in specs:
        row: List[Optional[int]] = []
        for finding, features in zip(findings, finding_features):
            quality = _ground_truth_pair_quality(spec, finding, features)
            row.append(quality)
            if quality is not None:
                maximum_quality = max(maximum_quality, quality)
        raw_qualities.append(row)

    # One additional valid edge must outweigh every possible secondary-quality
    # gain, so the result is maximum-cardinality and then maximum-quality.
    cardinality_weight = (maximum_quality + 1) * (len(specs) + 1)
    weights: List[List[int]] = []
    for row in raw_qualities:
        encoded = [
            cardinality_weight + int(quality) if quality is not None else 0
            for quality in row
        ]
        # The rectangular solver needs only enough zero-cost columns to reach
        # row_count; unused incompatible real columns also act as abstentions.
        encoded.extend([0] * max(0, len(specs) - len(findings)))
        weights.append(encoded)

    assignment = _maximum_weight_assignment(weights)
    matches: Dict[int, int] = {}
    finding_count = len(findings)
    for case_index, column_index in enumerate(assignment):
        if (
            column_index is not None
            and column_index < finding_count
            and raw_qualities[case_index][column_index] is not None
        ):
            matches[case_index] = column_index
    return matches


def _score_calibration(samples: List[Tuple[float, bool]],
                       bin_count: int = 10) -> Dict[str, Any]:
    """Summarize whether top-candidate scores track exact-shape correctness."""
    if not samples:
        return {
            "case_count": 0,
            "mean_score": None,
            "correct_mean_score": None,
            "incorrect_mean_score": None,
            "brier_score": None,
            "expected_calibration_error": None,
            "bins": [],
        }
    bins: List[List[Tuple[float, bool]]] = [[] for _ in range(bin_count)]
    for score, correct in samples:
        index = min(bin_count - 1, int(score * bin_count))
        bins[index].append((score, correct))
    populated_bins = []
    weighted_gap = 0.0
    for index, values in enumerate(bins):
        if not values:
            continue
        mean_score = sum(score for score, _ in values) / len(values)
        accuracy = sum(int(correct) for _, correct in values) / len(values)
        gap = abs(mean_score - accuracy)
        weighted_gap += gap * len(values) / len(samples)
        populated_bins.append({
            "lower": round(index / bin_count, 4),
            "upper": round((index + 1) / bin_count, 4),
            "count": len(values),
            "mean_score": round(mean_score, 4),
            "exact_group_accuracy": round(accuracy, 4),
            "calibration_gap": round(gap, 4),
        })
    correct_scores = [score for score, correct in samples if correct]
    incorrect_scores = [score for score, correct in samples if not correct]
    return {
        "case_count": len(samples),
        "mean_score": round(sum(score for score, _ in samples) / len(samples), 4),
        "correct_mean_score": (
            round(sum(correct_scores) / len(correct_scores), 4)
            if correct_scores else None
        ),
        "incorrect_mean_score": (
            round(sum(incorrect_scores) / len(incorrect_scores), 4)
            if incorrect_scores else None
        ),
        "brier_score": round(
            sum((score - float(correct)) ** 2 for score, correct in samples) / len(samples),
            4,
        ),
        "expected_calibration_error": round(weighted_gap, 4),
        "bins": populated_bins,
    }


def evaluate_vlm_grounding(ground_truth: List[Dict[str, Any]],
                           snapshot_id: Optional[str] = None,
                           top_k: int = 3,
                           database: Optional[CADDatabase] = None,
                           ground_truth_exhaustive: bool = True) -> ToolResult:
    """Score persisted findings with globally optimal one-to-one matching.

    ``ground_truth_exhaustive=False`` is intended for sampled annotations: in
    that mode unmatched persisted findings are unknown rather than false
    positives, so precision metrics that require exhaustive labels are ``None``.
    """
    db = get_db(database)
    findings_result = get_vlm_findings(snapshot_id=snapshot_id, database=db, limit=1000)
    findings = (
        [
            finding for finding in findings_result["data"].get("findings", [])
            if isinstance(finding, dict)
        ]
        if findings_result.get("ok") else []
    )
    expected_items = [item for item in (ground_truth or []) if isinstance(item, dict)]
    try:
        evaluated_top_k = max(1, int(top_k or 3))
    except (TypeError, ValueError, OverflowError):
        evaluated_top_k = 3
    expected_specs = [_expected_ground_truth_spec(item) for item in expected_items]
    finding_features = [
        _finding_grounding_features(finding, evaluated_top_k)
        for finding in findings
    ]
    optimal_matches = _optimal_ground_truth_matches(
        expected_specs, findings, finding_features
    )
    used_findings = set(optimal_matches.values())
    cases = []
    top1_hits = 0
    topk_hits = 0
    issue_hits = 0
    matched_count = 0
    exact_top1_hits = 0
    exact_topk_hits = 0
    group_precision_sum = 0.0
    group_recall_sum = 0.0
    group_f1_sum = 0.0
    handle_case_count = 0
    overlap_case_count = 0
    alternative_case_count = 0
    alternative_top1_hits = 0
    alternative_coverage_sum = 0.0
    alternative_full_coverage_hits = 0
    status_case_count = 0
    status_hits = 0
    ambiguity_true_positive = 0
    ambiguity_false_positive = 0
    ambiguity_false_negative = 0
    matched_exact_group_case_count = 0
    localization_decision_case_count = 0
    grounding_commit_count = 0
    grounding_abstention_count = 0
    correct_commit_count = 0
    incorrect_commit_count = 0
    commit_on_expected_ambiguity_count = 0
    required_commit_case_count = 0
    missed_required_commit_count = 0
    expected_abstention_case_count = 0
    correct_expected_abstention_count = 0
    score_samples: List[Tuple[float, bool]] = []
    missing_score_count = 0
    invalid_score_count = 0
    score_grounded_values: List[float] = []
    score_abstained_values: List[float] = []
    issue_type_case_count = 0
    matched_issue_type_case_count = 0

    for zero_index, spec in enumerate(expected_specs):
        index = zero_index + 1
        expected = spec["raw"]
        expected_group = set(spec["primary_group"])
        expected_equivalent_groups = spec["equivalent_groups"]
        expected_exact_sets = spec["exact_group_sets"]
        expected_alternative_groups = spec["alternative_groups"]
        expected_alternative_sets = spec["alternative_group_sets"]
        expected_acceptable_sets = spec["acceptable_group_sets"]
        expected_union = spec["expected_union"]

        match_index = optimal_matches.get(zero_index)
        matched_finding = findings[match_index] if match_index is not None else None
        matched_features = (
            finding_features[match_index]
            if match_index is not None
            else _finding_grounding_features({}, evaluated_top_k)
        )
        if matched_finding is not None:
            matched_count += 1
        candidate_group_records = matched_features["records"]
        candidate_groups = matched_features["groups"]
        candidate_top1 = list(candidate_groups[0]) if candidate_groups else []
        candidate_topk: List[str] = []
        for candidate_group in candidate_groups:
            for handle in candidate_group:
                if handle not in candidate_topk:
                    candidate_topk.append(handle)
        top1_group = matched_features["top1_group"]
        top1 = bool(expected_union and top1_group and expected_union.intersection(top1_group))
        topk = bool(expected_union and candidate_topk and expected_union.intersection(candidate_topk))
        exact_top1 = bool(
            expected_exact_sets
            and any(top1_group == group for group in expected_exact_sets)
        )
        exact_topk = bool(
            expected_exact_sets
            and any(
                candidate_group == expected_group_set
                for candidate_group in matched_features["group_sets"]
                for expected_group_set in expected_exact_sets
            )
        )
        if expected_union:
            overlap_case_count += 1
        if expected_exact_sets:
            handle_case_count += 1
            if matched_finding is not None:
                matched_exact_group_case_count += 1
            group_precision, group_recall, group_f1, best_expected_group = (
                _best_group_overlap(top1_group, expected_exact_sets)
            )
            group_precision_sum += group_precision
            group_recall_sum += group_recall
            group_f1_sum += group_f1
            exact_top1_hits += int(exact_top1)
            exact_topk_hits += int(exact_topk)
        else:
            group_precision = group_recall = group_f1 = 0.0
            best_expected_group = []

        candidate_group_sets = matched_features["group_sets"]
        alternative_matches = [
            group in candidate_group_sets for group in expected_alternative_sets
        ]
        alternative_top1_hit = bool(
            expected_alternative_sets
            and any(top1_group == group for group in expected_alternative_sets)
        )
        alternative_coverage = (
            sum(alternative_matches) / len(expected_alternative_sets)
            if expected_alternative_sets else 0.0
        )
        alternative_full_coverage = bool(
            expected_alternative_sets and all(alternative_matches)
        )
        if expected_alternative_sets:
            alternative_case_count += 1
            alternative_top1_hits += int(alternative_top1_hit)
            alternative_coverage_sum += alternative_coverage
            alternative_full_coverage_hits += int(alternative_full_coverage)

        expected_status = spec["expected_status"]
        actual_status = matched_features["status"]
        actual_decision_status = matched_features["decision_status"]
        status_match = bool(
            expected_status
            and (
                actual_status == expected_status
                or (expected_status == "grounded" and actual_status == "promoted")
            )
        )
        if expected_status:
            status_case_count += 1
            status_hits += int(status_match)
            expected_ambiguous = expected_status == "ambiguous"
            predicted_ambiguous = actual_status == "ambiguous"
            ambiguity_true_positive += int(expected_ambiguous and predicted_ambiguous)
            ambiguity_false_positive += int(not expected_ambiguous and predicted_ambiguous)
            ambiguity_false_negative += int(expected_ambiguous and not predicted_ambiguous)

        expected_issue_type = spec["expected_issue_type"]
        actual_issue_type = str(
            (matched_finding or {}).get("issue_type") or ""
        ).strip().lower()
        issue_match: Optional[bool]
        if expected_issue_type:
            issue_type_case_count += 1
            issue_match = bool(
                matched_finding is not None
                and expected_issue_type == actual_issue_type
            )
            if matched_finding is not None:
                matched_issue_type_case_count += 1
            issue_hits += int(issue_match)
        else:
            issue_match = None

        grounded_group = matched_features["grounded_group"]
        committed = bool(
            matched_finding is not None and actual_decision_status == "grounded"
        )
        committed_group_correct = bool(
            committed
            and grounded_group
            and any(grounded_group == group for group in expected_acceptable_sets)
        )
        abstained: Optional[bool] = None
        if matched_finding is not None and expected_union:
            localization_decision_case_count += 1
            abstained = not committed
            if committed:
                grounding_commit_count += 1
                correct_commit_count += int(committed_group_correct)
                incorrect_commit_count += int(not committed_group_correct)
                commit_on_expected_ambiguity_count += int(
                    expected_status == "ambiguous"
                )
            else:
                grounding_abstention_count += 1
        if expected_status == "grounded":
            required_commit_case_count += 1
            missed_required_commit_count += int(not committed)
        elif expected_status:
            expected_abstention_case_count += 1
            correct_expected_abstention_count += int(
                matched_finding is not None and not committed
            )

        top1_score: Optional[float] = None
        top1_score_valid = False
        if candidate_group_records:
            top_record = candidate_group_records[0]
            if top_record.get("score_available"):
                top1_score = float(top_record["score"])
                top1_score_valid = bool(top_record.get("score_valid_probability"))
        if expected_exact_sets and candidate_group_records:
            if top1_score_valid and top1_score is not None:
                score_samples.append((top1_score, exact_top1))
                if committed:
                    score_grounded_values.append(top1_score)
                else:
                    score_abstained_values.append(top1_score)
            elif top_record.get("score_available"):
                invalid_score_count += 1
            else:
                missing_score_count += 1

        top1_hits += int(top1)
        topk_hits += int(topk)
        cases.append({
            "case_id": expected.get("case_id") or f"case_{index}",
            "equivalence_group": expected.get("equivalence_group"),
            "expected_handle_group": sorted(expected_group),
            "expected_handles": sorted(expected_group),
            "expected_equivalent_handle_groups": expected_equivalent_groups,
            "expected_alternative_groups": expected_alternative_groups,
            "matched_finding_id": (matched_finding or {}).get("finding_id"),
            "matched_finding_index": match_index,
            "matching_strategy": "global_optimal_one_to_one",
            "matching_quality": (
                _ground_truth_pair_quality(spec, matched_finding, matched_features)
                if matched_finding is not None else None
            ),
            "candidate_top1": candidate_top1,
            "candidate_topk": candidate_topk,
            "candidate_top1_group": sorted(top1_group),
            "candidate_topk_groups": candidate_groups,
            "candidate_topk_group_records": candidate_group_records,
            "top1_hit": top1,
            "topk_hit": topk,
            "top1_exact_group_hit": exact_top1,
            "topk_exact_group_hit": exact_topk,
            "top1_group_precision": round(group_precision, 4),
            "top1_group_recall": round(group_recall, 4),
            "top1_group_f1": round(group_f1, 4),
            "best_matching_expected_group": best_expected_group,
            "candidate_top1_score": top1_score,
            "candidate_top1_score_valid_probability": top1_score_valid,
            "alternative_top1_hit": alternative_top1_hit if expected_alternative_sets else None,
            "alternative_topk_coverage": (
                round(alternative_coverage, 4) if expected_alternative_sets else None
            ),
            "alternative_topk_full_coverage": (
                alternative_full_coverage if expected_alternative_sets else None
            ),
            "issue_type_hit": issue_match,
            "expected_status": expected_status or None,
            "actual_status": actual_status or None,
            "actual_decision_status": actual_decision_status or None,
            "status_hit": status_match if expected_status else None,
            "committed_handle_group": sorted(grounded_group),
            "committed_group_correct": (
                committed_group_correct if matched_finding is not None and expected_union else None
            ),
            "abstained": abstained,
        })
    total = len(expected_items)
    finding_count = len(findings)
    # When status labels are provided, unmatched persisted ambiguous findings
    # are false positives under the evaluator's exhaustive-ground-truth model.
    if status_case_count and ground_truth_exhaustive:
        ambiguity_false_positive += sum(
            1
            for finding_index, finding in enumerate(findings)
            if finding_index not in used_findings
            and str(finding.get("status") or "").strip().lower() == "ambiguous"
        )
    ambiguity_precision = (
        ambiguity_true_positive / (ambiguity_true_positive + ambiguity_false_positive)
        if ambiguity_true_positive + ambiguity_false_positive else None
    )
    ambiguity_recall = (
        ambiguity_true_positive / (ambiguity_true_positive + ambiguity_false_negative)
        if ambiguity_true_positive + ambiguity_false_negative else None
    )
    equivalence_groups: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        group = str(case.get("equivalence_group") or "").strip()
        if group:
            equivalence_groups.setdefault(group, []).append(case)
    declared_comparable_groups = [
        group for group in equivalence_groups.values() if len(group) >= 2
    ]
    comparable_groups = [
        group for group in declared_comparable_groups
        if all(case.get("matched_finding_id") for case in group)
    ]
    equivalence_case_count = sum(len(group) for group in declared_comparable_groups)
    matched_equivalence_case_count = sum(
        bool(case.get("matched_finding_id"))
        for group in declared_comparable_groups
        for case in group
    )
    invariant_groups = sum(
        1
        for group in comparable_groups
        if len({
            json_text({
                "candidate_topk_groups": case.get("candidate_topk_groups") or [],
                "candidate_topk_group_scores": [
                    {
                        "handles": record.get("handles") or [],
                        "score": record.get("score"),
                    }
                    for record in case.get("candidate_topk_group_records") or []
                ],
                "actual_status": case.get("actual_status"),
            })
            for case in group
        }) == 1
    )
    ranking_invariant_groups = sum(
        1
        for group in comparable_groups
        if len({
            json_text({
                "candidate_topk_groups": case.get("candidate_topk_groups") or [],
                "actual_decision_status": case.get("actual_decision_status"),
            })
            for case in group
        }) == 1
    )
    decision_invariant_groups = sum(
        1
        for group in comparable_groups
        if len({
            json_text({
                "candidate_top1_group": case.get("candidate_top1_group") or [],
                "committed_handle_group": case.get("committed_handle_group") or [],
                "actual_decision_status": case.get("actual_decision_status"),
            })
            for case in group
        }) == 1
    )
    unmatched_finding_indices = [
        index for index in range(finding_count) if index not in used_findings
    ]
    score_calibration = _score_calibration(score_samples)
    issue_type_accuracy_on_labeled_matches = (
        issue_hits / matched_issue_type_case_count
        if matched_issue_type_case_count else None
    )
    exhaustive_precision = (
        matched_count / finding_count
        if ground_truth_exhaustive and finding_count else
        (0.0 if ground_truth_exhaustive else None)
    )
    metrics = {
        "case_count": total,
        "finding_count": finding_count,
        "matched_case_count": matched_count,
        "unmatched_case_count": total - matched_count,
        "unmatched_finding_count": len(unmatched_finding_indices),
        "ground_truth_exhaustive": bool(ground_truth_exhaustive),
        "finding_match_recall": round(matched_count / total, 4) if total else 0.0,
        "finding_match_precision": (
            round(exhaustive_precision, 4)
            if exhaustive_precision is not None else None
        ),
        "handle_top1_accuracy": (
            round(top1_hits / overlap_case_count, 4) if overlap_case_count else 0.0
        ),
        "handle_topk_accuracy": (
            round(topk_hits / overlap_case_count, 4) if overlap_case_count else 0.0
        ),
        "top1_exact_group_accuracy": (
            round(exact_top1_hits / handle_case_count, 4) if handle_case_count else 0.0
        ),
        "topk_exact_group_recall": (
            round(exact_topk_hits / handle_case_count, 4) if handle_case_count else 0.0
        ),
        "top1_group_precision": (
            round(group_precision_sum / handle_case_count, 4) if handle_case_count else 0.0
        ),
        "top1_group_recall": (
            round(group_recall_sum / handle_case_count, 4) if handle_case_count else 0.0
        ),
        "top1_group_f1": (
            round(group_f1_sum / handle_case_count, 4) if handle_case_count else 0.0
        ),
        "exact_group_case_count": handle_case_count,
        "matched_exact_group_case_count": matched_exact_group_case_count,
        "grounding_decision_case_count": localization_decision_case_count,
        "grounding_commit_count": grounding_commit_count,
        "grounding_abstention_count": grounding_abstention_count,
        "grounding_coverage": (
            round(grounding_commit_count / localization_decision_case_count, 4)
            if localization_decision_case_count else 0.0
        ),
        "grounding_abstention_rate": (
            round(grounding_abstention_count / localization_decision_case_count, 4)
            if localization_decision_case_count else 0.0
        ),
        "selective_exact_group_accuracy": (
            round(correct_commit_count / grounding_commit_count, 4)
            if grounding_commit_count else None
        ),
        "correct_commit_count": correct_commit_count,
        "incorrect_commit_count": incorrect_commit_count,
        "commit_on_expected_ambiguity_count": commit_on_expected_ambiguity_count,
        "required_commit_case_count": required_commit_case_count,
        "missed_required_commit_count": missed_required_commit_count,
        "expected_abstention_case_count": expected_abstention_case_count,
        "correct_expected_abstention_count": correct_expected_abstention_count,
        "alternative_case_count": alternative_case_count,
        "alternative_top1_accuracy": (
            round(alternative_top1_hits / alternative_case_count, 4)
            if alternative_case_count else 0.0
        ),
        "alternative_topk_coverage": (
            round(alternative_coverage_sum / alternative_case_count, 4)
            if alternative_case_count else 0.0
        ),
        "alternative_topk_full_coverage_rate": (
            round(alternative_full_coverage_hits / alternative_case_count, 4)
            if alternative_case_count else 0.0
        ),
        "ambiguity_true_positive": ambiguity_true_positive,
        "ambiguity_false_positive": ambiguity_false_positive,
        "ambiguity_false_negative": ambiguity_false_negative,
        "ambiguity_support": ambiguity_true_positive + ambiguity_false_negative,
        "ambiguity_precision": (
            round(ambiguity_precision, 4) if ambiguity_precision is not None else None
        ),
        "ambiguity_recall": (
            round(ambiguity_recall, 4) if ambiguity_recall is not None else None
        ),
        "decision_accuracy": (
            round(status_hits / status_case_count, 4) if status_case_count else 0.0
        ),
        "tile_invariance_rate": (
            round(invariant_groups / len(comparable_groups), 4)
            if comparable_groups else 0.0
        ),
        "equivalence_ranking_invariance_rate": (
            round(ranking_invariant_groups / len(comparable_groups), 4)
            if comparable_groups else 0.0
        ),
        "equivalence_decision_invariance_rate": (
            round(decision_invariant_groups / len(comparable_groups), 4)
            if comparable_groups else 0.0
        ),
        "equivalence_group_count": len(comparable_groups),
        "declared_equivalence_group_count": len(declared_comparable_groups),
        "equivalence_case_coverage": (
            round(matched_equivalence_case_count / equivalence_case_count, 4)
            if equivalence_case_count else 0.0
        ),
        "issue_type_case_count": issue_type_case_count,
        "issue_type_recall": (
            round(issue_hits / issue_type_case_count, 4)
            if issue_type_case_count else 0.0
        ),
        "issue_type_accuracy_on_labeled_matches": (
            round(issue_type_accuracy_on_labeled_matches, 4)
            if issue_type_accuracy_on_labeled_matches is not None else None
        ),
        # Compatibility name: this measures whether persisted findings can be
        # paired to an expected case, not multiclass issue-type precision.
        "issue_precision": (
            round(exhaustive_precision, 4)
            if exhaustive_precision is not None else None
        ),
        "top1_score_case_count": score_calibration["case_count"],
        "top1_score_missing_count": missing_score_count,
        "top1_score_invalid_count": invalid_score_count,
        "top1_score_mean": score_calibration["mean_score"],
        "top1_score_correct_mean": score_calibration["correct_mean_score"],
        "top1_score_incorrect_mean": score_calibration["incorrect_mean_score"],
        "top1_score_brier": score_calibration["brier_score"],
        "top1_score_ece": score_calibration["expected_calibration_error"],
        "top1_score_mean_when_grounded": (
            round(sum(score_grounded_values) / len(score_grounded_values), 4)
            if score_grounded_values else None
        ),
        "top1_score_mean_when_abstained": (
            round(sum(score_abstained_values) / len(score_abstained_values), 4)
            if score_abstained_values else None
        ),
        "top1_score_calibration": score_calibration,
        # Compatibility field: this was historically named json_valid_rate,
        # but it only indicates that persisted findings exist. Validation
        # rejects never reach this table and therefore cannot be scored here.
        "json_valid_rate": 1.0 if findings else 0.0,
        "has_persisted_findings": bool(findings),
        "top_k": evaluated_top_k,
    }
    warnings = [
        "Metrics are computed from persisted findings; invalid VLM JSON rejected before submit is not counted.",
        "json_valid_rate is retained for compatibility and only reports whether persisted findings exist; use has_persisted_findings.",
        "handle_top1_accuracy and handle_topk_accuracy are legacy any-overlap metrics; prefer exact-group and equivalent/alternative-group metrics.",
        "issue_precision is retained as exhaustive finding-match precision; it is not multiclass issue-type precision.",
        "Candidate-score calibration treats a score in [0,1] as confidence that the top candidate is the exact complete shape.",
    ]
    if not ground_truth_exhaustive:
        warnings.append(
            "Ground truth is non-exhaustive: unmatched findings were left unscored and precision requiring exhaustive annotations is null."
        )
    return ok_result(
        "Evaluated VLM grounding findings.",
        data={
            "metrics": metrics,
            "cases": cases,
            "unmatched_finding_ids": [
                findings[index].get("finding_id") for index in unmatched_finding_indices
            ],
        },
        handles=sorted({h for case in cases for h in case.get("candidate_topk", [])}),
        warnings=warnings,
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
