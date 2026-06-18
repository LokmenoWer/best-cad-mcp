"""Multi-stage engineering drawing interpretation over cached CAD evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    bbox_dict,
    bbox_from_row,
    bbox_union,
    entity_geometry,
    entity_text,
    entity_type,
    get_db,
)
from .result import ToolResult, ok_result
from .semantic_graph import get_semantic_graph
from .vlm import get_vlm_findings


LAYOUT_TYPES = {
    "title_block",
    "bom_table",
    "revision_table",
    "border",
    "section_marker",
    "detail_marker",
}


def _region(label: str,
            region_type: str,
            bbox: Any,
            handles: Optional[List[str]] = None,
            confidence: float = 0.5,
            source: str = "rule") -> Dict[str, Any]:
    return {
        "region_id": f"{source}:{region_type}:{label}".replace(" ", "_")[:120],
        "region_type": region_type,
        "label": label,
        "bbox": bbox_dict(bbox),
        "handles": handles or [],
        "confidence": round(float(confidence), 3),
        "source": source,
    }


def _layout_stage(database: CADDatabase,
                  semantic_objects: List[Dict[str, Any]]) -> Dict[str, Any]:
    entities = all_entities(database)
    regions: List[Dict[str, Any]] = []
    for obj in semantic_objects:
        object_type = str(obj.get("object_type") or "")
        if object_type not in LAYOUT_TYPES:
            continue
        regions.append(_region(
            str(obj.get("label") or object_type),
            object_type,
            _public_bbox_to_tuple(obj.get("bbox")),
            [str(handle) for handle in obj.get("entity_handles", [])],
            float(obj.get("confidence") or 0.0),
            str(obj.get("source") or "semantic"),
        ))
    all_bbox = bbox_union(bbox_from_row(entity) for entity in entities)
    if all_bbox:
        regions.append(_region(
            "main drawing extent",
            "drawing_extent",
            all_bbox,
            [str(entity.get("handle")) for entity in entities if entity.get("handle")],
            0.7,
            "derived:extent",
        ))
    return {
        "stage": "layout_segmentation",
        "regions": regions,
        "counts": {
            "regions": len(regions),
            "semantic_layout_regions": sum(1 for item in regions if item["source"].startswith(("rule", "vlm"))),
        },
    }


def _public_bbox_to_tuple(value: Any):
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


def _annotation_kind(entity: Dict[str, Any]) -> Optional[str]:
    etype = entity_type(entity)
    text = entity_text(entity)
    geom = entity_geometry(entity)
    if "dimension" in etype:
        return "dimension"
    if any(token in text for token in ("gdt", "datum", "tolerance", "flatness", "position")):
        return "gdt"
    if any(token in text for token in ("roughness", "surface", "ra ", "ra", "finish")):
        return "surface_roughness"
    if "mleader" in etype or "leader" in etype:
        return "callout"
    if "text" in etype and (geom.get("text") or geom.get("text_string")):
        return "text_note"
    return None


def _annotation_stage(database: CADDatabase) -> Dict[str, Any]:
    annotations = []
    for entity in all_entities(database):
        kind = _annotation_kind(entity)
        if not kind:
            continue
        annotations.append({
            "annotation_id": f"ann:{entity.get('handle')}",
            "annotation_type": kind,
            "handle": entity.get("handle"),
            "entity_type": entity.get("type"),
            "layer": entity.get("layer"),
            "bbox": bbox_dict(bbox_from_row(entity)),
            "text": entity_text(entity)[:200],
            "confidence": 0.82 if kind == "dimension" else 0.62,
            "source": "cad_metadata",
        })
    counts: Dict[str, int] = {}
    for item in annotations:
        counts[item["annotation_type"]] = counts.get(item["annotation_type"], 0) + 1
    return {
        "stage": "annotation_detection",
        "annotations": annotations,
        "counts": counts,
    }


def _vlm_stage(database: CADDatabase,
               snapshot_id: Optional[str]) -> Dict[str, Any]:
    findings_result = get_vlm_findings(snapshot_id=snapshot_id, database=database, limit=1000)
    findings = findings_result["data"].get("findings", []) if findings_result.get("ok") else []
    parsed_items = []
    for finding in findings:
        raw = finding.get("raw_finding") or {}
        parsed_items.append({
            "finding_id": finding.get("finding_id"),
            "snapshot_id": finding.get("snapshot_id"),
            "issue_type": finding.get("issue_type"),
            "semantic_type": raw.get("semantic_type") or raw.get("object_type"),
            "severity": finding.get("severity"),
            "confidence": finding.get("confidence"),
            "status": finding.get("status"),
            "handles": finding.get("grounded_handles") or finding.get("claimed_handles") or [],
            "overlay_id": finding.get("overlay_id"),
            "evidence": finding.get("evidence"),
        })
    return {
        "stage": "vlm_semantic_parsing",
        "findings": parsed_items,
        "counts": {
            "findings": len(parsed_items),
            "grounded": sum(1 for item in parsed_items if item.get("handles")),
            "high_confidence": sum(1 for item in parsed_items if float(item.get("confidence") or 0.0) >= 0.8),
        },
    }


def _reconciliation_stage(layout: Dict[str, Any],
                          annotations: Dict[str, Any],
                          vlm: Dict[str, Any],
                          semantic_objects: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues = []
    next_tools = ["build_drawing_ir", "export_view_image_with_mapping"]
    if not layout["regions"]:
        issues.append({
            "issue_type": "layout_not_detected",
            "severity": "info",
            "message": "No layout regions were detected from semantics or extents.",
        })
        next_tools.append("detect_semantic_objects")
    if not annotations["annotations"]:
        issues.append({
            "issue_type": "annotations_not_detected",
            "severity": "info",
            "message": "No dimension, GD&T, surface roughness, callout, or note annotations were detected.",
        })
    ambiguous_vlm = [
        finding for finding in vlm["findings"]
        if finding.get("status") == "ambiguous"
    ]
    if ambiguous_vlm:
        issues.append({
            "issue_type": "ambiguous_vlm_grounding",
            "severity": "medium",
            "message": f"{len(ambiguous_vlm)} VLM finding(s) have ambiguous grounding.",
            "finding_ids": [item.get("finding_id") for item in ambiguous_vlm],
        })
        next_tools.extend(["get_vlm_findings", "explain_entity"])
    if any(str(obj.get("source", "")).startswith("vlm:") for obj in semantic_objects):
        next_tools.append("get_semantic_graph")
    return {
        "stage": "reconciliation",
        "issues": issues,
        "recommended_next_tools": sorted(set(next_tools)),
    }


def analyze_engineering_drawing_stages(snapshot_id: Optional[str] = None,
                                       domain: str = "mechanical",
                                       database: Optional[CADDatabase] = None) -> ToolResult:
    """Return a multi-stage interpretation JSON for engineering drawings."""
    db = get_db(database)
    semantic = get_semantic_graph(db)
    semantic_objects = semantic["data"].get("semantic_objects", []) if semantic.get("ok") else []
    layout = _layout_stage(db, semantic_objects)
    annotations = _annotation_stage(db)
    vlm = _vlm_stage(db, snapshot_id)
    reconciliation = _reconciliation_stage(layout, annotations, vlm, semantic_objects)
    unified_json = {
        "schema_version": "engineering-drawing-interpretation/v1",
        "domain": domain or "mechanical",
        "snapshot_id": snapshot_id,
        "stages": {
            "layout_segmentation": layout,
            "annotation_detection": annotations,
            "vlm_semantic_parsing": vlm,
            "reconciliation": reconciliation,
        },
        "summary": {
            "layout_region_count": layout["counts"]["regions"],
            "annotation_count": len(annotations["annotations"]),
            "vlm_finding_count": vlm["counts"]["findings"],
            "issue_count": len(reconciliation["issues"]),
        },
    }
    return ok_result(
        "Built multi-stage engineering drawing interpretation.",
        data={"interpretation": unified_json},
        handles=sorted({
            handle
            for item in annotations["annotations"]
            for handle in [item.get("handle")]
            if handle
        } | {
            handle
            for finding in vlm["findings"]
            for handle in finding.get("handles", [])
        }),
        warnings=[
            "This interpretation is a staged evidence bundle; it does not modify the DWG."
        ],
        next_tools=reconciliation["recommended_next_tools"],
    )


__all__ = ["analyze_engineering_drawing_stages"]
