"""Build CAD-IR from the existing scanned SQLite metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_blocks,
    all_entities,
    all_layers,
    all_topology_primitives,
    all_topology_relations,
    bbox_dict,
    bbox_from_row,
    bbox_union,
    clean_row,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    get_db,
    latest_validation_report,
    now_iso,
    topology_summary,
)
from .ir import (
    BlockIR,
    CAD_IR_SCHEMA_VERSION,
    CadIRManifest,
    CadIRQuality,
    DrawingIRV2,
    DrawingOverviewIR,
    LayerIR,
    to_dict,
)

IR_SECTIONS = [
    "overview",
    "entities",
    "layers",
    "blocks",
    "topology",
    "semantics",
    "constraints",
    "validation",
    "views",
    "vlm_findings",
    "quality",
]
DEFAULT_ENTITY_LIMIT = 1000
MAX_ENTITY_LIMIT = 100000


def _layer_ir(layer: Dict[str, Any], entity_counts: Dict[str, int]) -> Dict[str, Any]:
    name = str(layer.get("name") or "")
    return to_dict(LayerIR(
        name=name,
        color=layer.get("color", 7),
        linetype=str(layer.get("linetype") or "Continuous"),
        lineweight=layer.get("lineweight", -1.0),
        is_frozen=bool(layer.get("is_frozen", False)),
        is_locked=bool(layer.get("is_locked", False)),
        is_on=bool(layer.get("is_on", True)),
        is_plottable=bool(layer.get("is_plottable", True)),
        description=str(layer.get("description") or ""),
        handle=str(layer.get("handle") or ""),
        entity_count=int(entity_counts.get(name, 0)),
    ))


def _implicit_layer_ir(name: str, entity_count: int) -> Dict[str, Any]:
    return to_dict(LayerIR(name=name, entity_count=int(entity_count)))


def _block_ir(block: Dict[str, Any]) -> Dict[str, Any]:
    origin = [
        float(block.get("origin_x") or 0.0),
        float(block.get("origin_y") or 0.0),
        float(block.get("origin_z") or 0.0),
    ]
    return to_dict(BlockIR(
        name=str(block.get("name") or ""),
        entity_count=int(block.get("entity_count") or block.get("count") or 0),
        is_layout=bool(block.get("is_layout", False)),
        is_xref=bool(block.get("is_xref", False)),
        origin=origin,
        path=str(block.get("path") or ""),
    ))


def _semantic_tags_by_handle(database: CADDatabase) -> Dict[str, List[str]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    tags: Dict[str, List[str]] = {}
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_type, entity_handles
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    for row in rows:
        handles = decode_json(row["entity_handles"], [])
        for handle in handles if isinstance(handles, list) else []:
            tags.setdefault(str(handle), [])
            tag = str(row["object_type"] or "")
            if tag and tag not in tags[str(handle)]:
                tags[str(handle)].append(tag)
    return tags


def _read_semantic_objects(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
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
    objects = []
    for row in rows:
        item = clean_row(dict(row))
        bbox = None
        if item.get("bbox_min_x") is not None:
            bbox = (
                float(item["bbox_min_x"]),
                float(item["bbox_min_y"]),
                float(item["bbox_max_x"]),
                float(item["bbox_max_y"]),
            )
        item["bbox"] = bbox_dict(bbox)
        for key in ("bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y"):
            item.pop(key, None)
        item["entity_handles"] = decode_json(item.get("entity_handles"), [])
        item["properties"] = decode_json(item.get("properties"))
        objects.append(item)
    return objects


def _read_semantic_relations(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
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
    relations = []
    for row in rows:
        item = clean_row(dict(row))
        item["evidence"] = decode_json(item.get("evidence"))
        relations.append(item)
    return relations


def _read_constraints(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT constraint_id, constraint_type, source, target_handles,
                   target_object_ids, value, actual, tolerance, unit,
                   confidence, status, evidence, created_at
            FROM cad_constraints
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY constraint_type, constraint_id
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    constraints = []
    for row in rows:
        item = clean_row(dict(row))
        item["target_handles"] = decode_json(item.get("target_handles"), [])
        item["target_object_ids"] = decode_json(item.get("target_object_ids"), [])
        item["evidence"] = decode_json(item.get("evidence"))
        constraints.append(item)
    return constraints


def _read_views(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT snapshot_id, image_path, overlay_image_path,
                   context_json_path, snapshot_data, created_at
            FROM cad_view_snapshots
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY created_at DESC
            LIMIT 20
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    views = []
    for row in rows:
        item = clean_row(dict(row))
        data = decode_json(item.pop("snapshot_data", "{}"))
        if isinstance(data, dict):
            merged = {**data, **item}
            views.append(merged)
        else:
            views.append(item)
    return views


def _read_vlm_findings(database: CADDatabase) -> List[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT finding_id, snapshot_id, source_model, prompt_version,
                   issue_type, severity, status, confidence, overlay_id,
                   pixel_bbox, world_bbox, claimed_handles, grounded_handles,
                   grounding_candidates, evidence, raw_finding, created_at
            FROM cad_vlm_findings
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY created_at DESC, finding_id
            LIMIT 200
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    findings = []
    for row in rows:
        item = clean_row(dict(row))
        for key in (
            "pixel_bbox",
            "world_bbox",
            "claimed_handles",
            "grounded_handles",
            "grounding_candidates",
            "evidence",
            "raw_finding",
        ):
            default = [] if key in {"pixel_bbox", "claimed_handles", "grounded_handles", "grounding_candidates"} else {}
            item[key] = decode_json(item.get(key), default)
        findings.append(item)
    return findings


def _maybe_rescan(rescan: bool) -> Optional[str]:
    if not rescan:
        return None
    from src.cad_tools import query_tools

    return query_tools.scan_all_entities(
        clear_db=True,
        max_entities=10000,
        clear_annotations=False,
        detail_level="standard",
        include_bounding_boxes=True,
        derive_topology=True,
        topology_detail="full",
    )


def _normalize_sections(sections: Optional[Any]) -> Tuple[List[str], List[str]]:
    if sections is None:
        return list(IR_SECTIONS), []
    if isinstance(sections, str):
        raw_sections = [
            section.strip() for section in sections.replace(";", ",").split(",")
        ]
    else:
        raw_sections = [str(section).strip() for section in sections]

    aliases = {
        "all": "all",
        "entity": "entities",
        "entity_index": "entities",
        "semantic": "semantics",
        "semantic_objects": "semantics",
        "constraint": "constraints",
        "validation_report": "validation",
        "view": "views",
        "vlm": "vlm_findings",
        "vlm_review": "vlm_findings",
        "vlm_findings": "vlm_findings",
    }
    normalized: List[str] = []
    ignored: List[str] = []
    for raw in raw_sections:
        key = aliases.get(raw.lower(), raw.lower())
        if not key:
            continue
        if key == "all":
            return list(IR_SECTIONS), []
        if key not in IR_SECTIONS:
            ignored.append(raw)
            continue
        if key not in normalized:
            normalized.append(key)
    warnings = [
        f"Ignored unknown CAD-IR section(s): {', '.join(ignored)}."
    ] if ignored else []
    return normalized, warnings


def _normalize_entity_limit(entity_limit: Optional[int]) -> int:
    if entity_limit is None:
        return DEFAULT_ENTITY_LIMIT
    try:
        value = int(entity_limit)
    except Exception:
        return DEFAULT_ENTITY_LIMIT
    return max(0, min(value, MAX_ENTITY_LIMIT))


def _handle(entity: Dict[str, Any]) -> str:
    return str(entity.get("handle") or entity.get("native_handle") or "")


def _count_by(items: Sequence[Dict[str, Any]], key: str,
              default: str = "Unknown") -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or default)
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def _counts_by_value(items: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _quality_issue(severity: str, issue_type: str, message: str,
                   handles: Optional[List[str]] = None,
                   evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    issue = {
        "severity": severity,
        "issue_type": issue_type,
        "message": message,
    }
    if handles:
        issue["handles"] = handles[:20]
    if evidence:
        issue["evidence"] = evidence
    return issue


def _unique_append(values: List[str], *items: str) -> None:
    for item in items:
        if item and item not in values:
            values.append(item)


def _topology_maps(
    topology_rows: Sequence[Dict[str, Any]],
    primitives: Sequence[Dict[str, Any]],
    relations: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int], Dict[str, int]]:
    summaries = {
        str(row.get("handle") or row.get("entity_handle") or ""): row
        for row in topology_rows
        if row.get("handle") or row.get("entity_handle")
    }
    primitive_counts: Dict[str, int] = {}
    relation_counts: Dict[str, int] = {}
    for primitive in primitives:
        handle = str(primitive.get("entity_handle") or "")
        primitive_counts[handle] = primitive_counts.get(handle, 0) + 1
    for relation in relations:
        handle = str(relation.get("entity_handle") or "")
        relation_counts[handle] = relation_counts.get(handle, 0) + 1
    return summaries, primitive_counts, relation_counts


def _constraint_flags_by_handle(
    constraints: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    flags: Dict[str, Dict[str, Any]] = {}
    for constraint in constraints:
        status = str(constraint.get("status") or "unknown")
        ctype = str(constraint.get("constraint_type") or "constraint")
        for handle in constraint.get("target_handles", []) or []:
            item = flags.setdefault(str(handle), {
                "count": 0,
                "statuses": set(),
                "types": set(),
            })
            item["count"] += 1
            item["statuses"].add(status)
            item["types"].add(ctype)
    return {
        handle: {
            "count": item["count"],
            "statuses": sorted(item["statuses"]),
            "types": sorted(item["types"]),
        }
        for handle, item in flags.items()
    }


def _validation_flags_by_handle(
    validation: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    flags: Dict[str, Dict[str, Any]] = {}
    for issue in validation.get("issues", []) or []:
        severity = str(issue.get("severity") or "unknown")
        issue_type = str(issue.get("issue_type") or "validation_issue")
        for handle in issue.get("handles", []) or []:
            item = flags.setdefault(str(handle), {
                "count": 0,
                "severities": set(),
                "issue_types": set(),
            })
            item["count"] += 1
            item["severities"].add(severity)
            item["issue_types"].add(issue_type)
    return {
        handle: {
            "count": item["count"],
            "severities": sorted(item["severities"]),
            "issue_types": sorted(item["issue_types"]),
        }
        for handle, item in flags.items()
    }


def _entity_index_item(
    entity: Dict[str, Any],
    semantic_tags: List[str],
    topology_summaries: Dict[str, Dict[str, Any]],
    primitive_counts: Dict[str, int],
    relation_counts: Dict[str, int],
    constraint_flags: Dict[str, Dict[str, Any]],
    validation_flags: Dict[str, Dict[str, Any]],
    include_raw: bool,
) -> Dict[str, Any]:
    handle = _handle(entity)
    entity_type = str(entity.get("type") or entity.get("entity_type") or "Unknown")
    bbox = bbox_from_row(entity)
    primitive_count = primitive_counts.get(handle, 0)
    relation_count = relation_counts.get(handle, 0)
    has_summary = handle in topology_summaries
    topology_detail = (
        "full" if primitive_count or relation_count
        else "summary" if has_summary
        else "none"
    )
    item: Dict[str, Any] = {
        "handle": handle,
        "entity_type": entity_type,
        "object_name": str(entity.get("name") or entity_type),
        "layer": str(entity.get("layer") or "0"),
        "bbox": bbox_dict(bbox),
        "semantic_tags": semantic_tags,
        "topology": {
            "detail": topology_detail,
            "has_summary": has_summary,
            "primitive_count": primitive_count,
            "relation_count": relation_count,
        },
        "flags": {
            "has_bbox": bbox is not None,
            "has_topology": has_summary,
            "has_semantics": bool(semantic_tags),
            "has_constraints": handle in constraint_flags,
            "has_validation_issues": handle in validation_flags,
        },
    }
    if handle in constraint_flags:
        item["constraint_flags"] = constraint_flags[handle]
    if handle in validation_flags:
        item["validation_flags"] = validation_flags[handle]
    if include_raw:
        item.update({
            "color": entity.get("color", 256),
            "linetype": str(entity.get("linetype") or "ByLayer"),
            "visible": bool(entity.get("visible", 1)),
            "geometry": decode_json(entity.get("geometry")),
            "properties": decode_json(entity.get("properties")),
        })
    return item


def _layer_section(layers: Sequence[Dict[str, Any]],
                   layer_counts: Dict[str, int]) -> Dict[str, Any]:
    by_name = {
        str(layer.get("name") or ""): _layer_ir(layer, layer_counts)
        for layer in layers
    }
    for name, count in layer_counts.items():
        by_name.setdefault(name, _implicit_layer_ir(name, count))
    items = [by_name[name] for name in sorted(by_name)]
    return {"count": len(items), "items": items}


def _default_validation() -> Dict[str, Any]:
    return {
        "passed": True,
        "score": 100.0,
        "issue_count": 0,
        "issues": [],
        "generated_at": "",
        "recommended_next_tools": ["validate_geometry"],
    }


def _build_quality(
    entities: Sequence[Dict[str, Any]],
    topology_summaries: Dict[str, Dict[str, Any]],
    primitives: Sequence[Dict[str, Any]],
    relations: Sequence[Dict[str, Any]],
    semantic_tags: Dict[str, List[str]],
    semantic_objects: Sequence[Dict[str, Any]],
    semantic_relations: Sequence[Dict[str, Any]],
    constraints: Sequence[Dict[str, Any]],
    validation: Dict[str, Any],
    views: Sequence[Dict[str, Any]],
    vlm_findings: Sequence[Dict[str, Any]],
    entity_limit: int,
    included_entity_count: int,
    entity_truncated: bool,
) -> Dict[str, Any]:
    total_entities = len(entities)
    entity_handles = [_handle(entity) for entity in entities]
    missing_bbox = [
        handle for entity, handle in zip(entities, entity_handles)
        if bbox_from_row(entity) is None
    ]
    missing_topology = [
        handle for handle in entity_handles if handle not in topology_summaries
    ]
    topology_detail = (
        "none" if not topology_summaries
        else "full_or_partial" if primitives or relations
        else "summary"
    )
    validation_has_report = bool(validation.get("generated_at"))
    issue_count = int(validation.get("issue_count") or len(validation.get("issues", []) or []))
    recommended_next_tools: List[str] = []
    issues: List[Dict[str, Any]] = []

    if total_entities == 0:
        issues.append(_quality_issue(
            "warning",
            "empty_scan",
            "CAD-IR contains no scanned entities.",
        ))
        _unique_append(recommended_next_tools, "scan_all_entities")
    if missing_bbox:
        issues.append(_quality_issue(
            "warning",
            "missing_bbox",
            f"{len(missing_bbox)} scanned entity/entities do not have a usable bounding box.",
            missing_bbox,
        ))
        _unique_append(recommended_next_tools, "scan_all_entities")
    if missing_topology and total_entities:
        issues.append(_quality_issue(
            "warning",
            "missing_topology_summary",
            f"{len(missing_topology)} scanned entity/entities do not have topology summaries.",
            missing_topology,
        ))
        _unique_append(recommended_next_tools, "scan_all_entities")
    elif topology_detail == "summary" and total_entities:
        issues.append(_quality_issue(
            "info",
            "summary_only_topology",
            "Topology summaries exist, but primitive/relation topology was not captured.",
            evidence={"recommended_topology_detail": "full"},
        ))
    if not semantic_objects and total_entities:
        issues.append(_quality_issue(
            "info",
            "semantics_not_detected",
            "No semantic objects are cached for this thread.",
        ))
        _unique_append(recommended_next_tools, "detect_semantic_objects")
    if not constraints and total_entities:
        issues.append(_quality_issue(
            "info",
            "constraints_not_extracted",
            "No drawing constraints are cached for this thread.",
        ))
        _unique_append(recommended_next_tools, "bind_all_dimensions", "extract_drawing_constraints")
    if not validation_has_report and total_entities:
        issues.append(_quality_issue(
            "info",
            "validation_not_run",
            "No cached validation report exists for this thread.",
        ))
        _unique_append(recommended_next_tools, "validate_geometry")
    if not views and total_entities:
        issues.append(_quality_issue(
            "info",
            "no_view_snapshots",
            "No mapped view snapshots are cached for visual grounding.",
        ))
        _unique_append(recommended_next_tools, "export_view_image_with_mapping")
    if entity_truncated:
        issues.append(_quality_issue(
            "warning",
            "entity_index_truncated",
            f"Entity section contains {included_entity_count} of {total_entities} scanned entities.",
            evidence={"entity_limit": entity_limit},
        ))

    scan_state = (
        "empty"
        if total_entities == 0
        else "scanned_with_gaps"
        if missing_bbox or missing_topology or entity_truncated
        else "scanned"
    )
    coverage = {
        "entities": {
            "total": total_entities,
            "included_in_ir": included_entity_count,
            "truncated": entity_truncated,
        },
        "bbox": {
            "with_bbox": total_entities - len(missing_bbox),
            "missing": len(missing_bbox),
            "ratio": round((total_entities - len(missing_bbox)) / total_entities, 3)
            if total_entities else 0.0,
        },
        "topology": {
            "detail_level": topology_detail,
            "summary_count": len(topology_summaries),
            "missing_summary_count": len(missing_topology),
            "primitive_count": len(primitives),
            "relation_count": len(relations),
            "ratio": round(len(topology_summaries) / total_entities, 3)
            if total_entities else 0.0,
        },
        "semantics": {
            "object_count": len(semantic_objects),
            "relation_count": len(semantic_relations),
            "tagged_entity_count": len(semantic_tags),
            "ratio": round(len(semantic_tags) / total_entities, 3)
            if total_entities else 0.0,
        },
        "constraints": {
            "constraint_count": len(constraints),
            "status_counts": _counts_by_value(constraints, "status"),
        },
        "validation": {
            "has_report": validation_has_report,
            "passed": bool(validation.get("passed", True)),
            "issue_count": issue_count,
            "score": validation.get("score", 100.0),
        },
        "views": {
            "snapshot_count": len(views),
        },
        "vlm_findings": {
            "finding_count": len(vlm_findings),
            "status_counts": _counts_by_value(vlm_findings, "status"),
            "grounded_count": sum(1 for item in vlm_findings if item.get("grounded_handles")),
        },
    }
    return to_dict(CadIRQuality(
        scan_state=scan_state,
        coverage=coverage,
        issues=issues,
        recommended_next_tools=recommended_next_tools,
    ))


def build_drawing_ir(rescan: bool = False,
                     database: Optional[CADDatabase] = None,
                     profile: str = "agent",
                     sections: Optional[Any] = None,
                     entity_limit: int = DEFAULT_ENTITY_LIMIT,
                     include_raw: bool = False) -> Dict[str, Any]:
    db = get_db(database)
    ensure_understanding_schema(db)
    scan_message = _maybe_rescan(rescan)
    ctx = db.get_context()

    requested_sections, section_warnings = _normalize_sections(sections)
    limit = _normalize_entity_limit(entity_limit)
    entities = all_entities(db)
    layer_counts: Dict[str, int] = {}
    for entity in entities:
        layer = str(entity.get("layer") or "0")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    layers = _layer_section(all_layers(db), layer_counts)
    block_items = [_block_ir(block) for block in all_blocks(db)]
    topology_rows = topology_summary(db, limit=100000)
    primitives = all_topology_primitives(db)
    topology_relations = all_topology_relations(db)
    topology_summaries, primitive_counts, relation_counts = _topology_maps(
        topology_rows,
        primitives,
        topology_relations,
    )
    semantic_tags = _semantic_tags_by_handle(db)
    semantic_objects = _read_semantic_objects(db)
    semantic_relations = _read_semantic_relations(db)
    constraints = _read_constraints(db)
    validation = latest_validation_report(db) or _default_validation()
    views = _read_views(db)
    vlm_findings = _read_vlm_findings(db)
    constraint_flags = _constraint_flags_by_handle(constraints)
    validation_flags = _validation_flags_by_handle(validation)

    include_entities = "entities" in requested_sections
    included_entity_count = min(len(entities), limit) if include_entities else 0
    entity_truncated = include_entities and included_entity_count < len(entities)
    entity_index = [
        _entity_index_item(
            entity,
            semantic_tags.get(_handle(entity), []),
            topology_summaries,
            primitive_counts,
            relation_counts,
            constraint_flags,
            validation_flags,
            include_raw,
        )
        for entity in entities[:included_entity_count]
    ]

    extents = bbox_dict(bbox_union(bbox_from_row(entity) for entity in entities))
    drawing = to_dict(DrawingOverviewIR(
        name=ctx.drawing_name,
        path=ctx.drawing_path,
        units="unknown",
        extents=extents,
        counts={
            "entities": len(entities),
            "layers": layers["count"],
            "blocks": len(block_items),
            "topology_summaries": len(topology_rows),
            "topology_primitives": len(primitives),
            "topology_relations": len(topology_relations),
            "semantic_objects": len(semantic_objects),
            "semantic_relations": len(semantic_relations),
            "constraints": len(constraints),
            "validation_issues": int(validation.get("issue_count") or 0),
            "views": len(views),
            "vlm_findings": len(vlm_findings),
        },
    ))
    quality = _build_quality(
        entities,
        topology_summaries,
        primitives,
        topology_relations,
        semantic_tags,
        semantic_objects,
        semantic_relations,
        constraints,
        validation,
        views,
        vlm_findings,
        limit,
        included_entity_count,
        entity_truncated,
    )

    payload_sections: Dict[str, Any] = {}
    if "overview" in requested_sections:
        payload_sections["overview"] = {
            "entity_type_counts": _count_by(entities, "type"),
            "layer_entity_counts": dict(sorted(layer_counts.items())),
            "artifact_counts": drawing["counts"],
            "recommended_next_tools": quality["recommended_next_tools"],
        }
    if include_entities:
        payload_sections["entities"] = {
            "total": len(entities),
            "count": len(entity_index),
            "truncated": entity_truncated,
            "include_raw": bool(include_raw),
            "items": entity_index,
        }
    if "layers" in requested_sections:
        payload_sections["layers"] = layers
    if "blocks" in requested_sections:
        payload_sections["blocks"] = {
            "count": len(block_items),
            "items": block_items,
        }
    if "topology" in requested_sections:
        payload_sections["topology"] = {
            "summary": topology_rows,
            "primitives": primitives,
            "relations": topology_relations,
            "counts": {
                "summary": len(topology_rows),
                "primitives": len(primitives),
                "relations": len(topology_relations),
            },
        }
    if "semantics" in requested_sections:
        payload_sections["semantics"] = {
            "objects": semantic_objects,
            "relations": semantic_relations,
            "counts": {
                "objects": len(semantic_objects),
                "relations": len(semantic_relations),
            },
        }
    if "constraints" in requested_sections:
        payload_sections["constraints"] = {
            "items": constraints,
            "count": len(constraints),
            "status_counts": _counts_by_value(constraints, "status"),
            "type_counts": _counts_by_value(constraints, "constraint_type"),
        }
    if "validation" in requested_sections:
        payload_sections["validation"] = validation
    if "views" in requested_sections:
        payload_sections["views"] = {
            "count": len(views),
            "items": views,
        }
    if "vlm_findings" in requested_sections:
        payload_sections["vlm_findings"] = {
            "count": len(vlm_findings),
            "items": vlm_findings,
            "status_counts": _counts_by_value(vlm_findings, "status"),
        }
    if "quality" in requested_sections:
        payload_sections["quality"] = quality

    warnings = list(section_warnings)
    if scan_message:
        warnings.append(f"Rescan completed before building CAD-IR: {scan_message}")
    if entity_truncated:
        warnings.append(
            f"Entity section truncated to {included_entity_count} of {len(entities)} scanned entities."
        )

    manifest = to_dict(CadIRManifest(
        profile=str(profile or "agent"),
        sections=requested_sections,
        counts=drawing["counts"],
        limits={
            "entity_limit": limit,
            "max_entity_limit": MAX_ENTITY_LIMIT,
            "include_raw": bool(include_raw),
        },
        warnings=warnings,
    ))
    result = to_dict(DrawingIRV2(
        schema_version=CAD_IR_SCHEMA_VERSION,
        generated_at=now_iso(),
        manifest=manifest,
        drawing=drawing,
        quality=quality,
        sections=payload_sections,
    ))
    json.dumps(result, ensure_ascii=False, default=str)
    return result


def get_cached_drawing_ir(database: Optional[CADDatabase] = None) -> Dict[str, Any]:
    return build_drawing_ir(rescan=False, database=database)


def export_drawing_ir(filepath: str,
                      rescan: bool = False,
                      database: Optional[CADDatabase] = None,
                      profile: str = "agent",
                      sections: Optional[Any] = None,
                      entity_limit: int = DEFAULT_ENTITY_LIMIT,
                      include_raw: bool = False) -> Dict[str, Any]:
    drawing_ir = build_drawing_ir(
        rescan=rescan,
        database=database,
        profile=profile,
        sections=sections,
        entity_limit=entity_limit,
        include_raw=include_raw,
    )
    target = Path(filepath).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(drawing_ir, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return {"filepath": str(target), "drawing_ir": drawing_ir}


__all__ = ["build_drawing_ir", "get_cached_drawing_ir", "export_drawing_ir"]
