"""Read-only drawing understanding helpers for agent-facing MCP tools."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_annotations,
    all_blocks,
    all_entities,
    all_layers,
    bbox_area,
    bbox_center,
    bbox_dict,
    bbox_from_row,
    bbox_union,
    clean_row,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    entity_geometry,
    entity_text,
    entity_type,
    get_db,
    get_entity,
    latest_validation_report,
    line_length,
    point_distance,
    topology_for_handle,
    topology_summary,
)
from .ir_builder import build_drawing_ir
from .result import ToolResult, error_result, ok_result

DOMAIN_KEYWORDS = {
    "mechanical": {
        "bolt", "hole", "shaft", "bearing", "gear", "plate", "section",
        "center", "centerline", "thread", "diameter", "radius", "mach",
    },
    "architecture": {
        "wall", "door", "window", "room", "floor", "ceiling", "stair",
        "grid", "a-", "plan", "elevation", "section",
    },
    "electrical": {
        "wire", "circuit", "panel", "switch", "outlet", "device", "light",
        "fixture", "cable", "conduit", "e-",
    },
    "structural": {
        "beam", "column", "foundation", "rebar", "steel", "brace", "slab",
        "s-", "grid",
    },
}


def _type_stats(entities: List[Dict[str, Any]]) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    for entity in entities:
        etype = str(entity.get("type") or "Unknown")
        stats[etype] = stats.get(etype, 0) + 1
    return dict(sorted(stats.items(), key=lambda item: (-item[1], item[0])))


def _layer_summary(layers: List[Dict[str, Any]],
                   entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for entity in entities:
        layer = str(entity.get("layer") or "0")
        counts[layer] = counts.get(layer, 0) + 1
    names = {str(layer.get("name") or "") for layer in layers}
    names.update(counts.keys())
    return [
        {
            "name": name,
            "entity_count": counts.get(name, 0),
            "color": next((layer.get("color") for layer in layers if layer.get("name") == name), None),
            "linetype": next((layer.get("linetype") for layer in layers if layer.get("name") == name), None),
        }
        for name in sorted(names)
    ]


def _block_summary(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": block.get("name"),
            "entity_count": block.get("entity_count") or block.get("count") or 0,
            "is_xref": bool(block.get("is_xref")),
            "is_layout": bool(block.get("is_layout")),
            "path": block.get("path", ""),
        }
        for block in blocks
    ]


def _domain_scores(entities: List[Dict[str, Any]],
                   layers: List[Dict[str, Any]],
                   blocks: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    corpus = " ".join(
        [entity_text(entity) for entity in entities]
        + [str(layer.get("name", "")).lower() for layer in layers]
        + [str(block.get("name", "")).lower() for block in blocks]
    )
    entity_mix = _type_stats(entities)
    scores: Dict[str, float] = {domain: 0.0 for domain in DOMAIN_KEYWORDS}
    evidence: Dict[str, List[str]] = {domain: [] for domain in DOMAIN_KEYWORDS}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            count = corpus.count(keyword)
            if count:
                scores[domain] += count
                evidence[domain].append(keyword)
    circle_count = sum(c for t, c in entity_mix.items() if "circle" in t.lower())
    dim_count = sum(c for t, c in entity_mix.items() if "dimension" in t.lower())
    block_count = sum(c for t, c in entity_mix.items() if "block" in t.lower())
    line_count = sum(c for t, c in entity_mix.items() if "line" in t.lower())
    if circle_count >= 2 and dim_count:
        scores["mechanical"] += 2.0
        evidence["mechanical"].append("circle+dimension mix")
    if block_count and "wire" in corpus:
        scores["electrical"] += 2.0
        evidence["electrical"].append("block+wire mix")
    if line_count > circle_count * 3 and any("wall" in str(layer.get("name", "")).lower() for layer in layers):
        scores["architecture"] += 2.0
        evidence["architecture"].append("wall layers with linework")

    ranked = []
    max_score = max(scores.values()) if scores else 0.0
    for domain, score in sorted(scores.items(), key=lambda item: -item[1]):
        confidence = (score / max_score) if max_score else 0.0
        ranked.append({
            "domain": domain,
            "score": round(score, 3),
            "confidence": round(confidence, 3),
            "evidence": evidence[domain][:10],
        })
    best = ranked[0]["domain"] if ranked and ranked[0]["score"] > 0 else "generic"
    return best, ranked


def summarize_drawing(level: str = "normal",
                      database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    entities = all_entities(db)
    layers = all_layers(db)
    blocks = all_blocks(db)
    domain, ranked_domains = _domain_scores(entities, layers, blocks)
    drawing_ir = build_drawing_ir(False, db) if (level or "").lower() == "deep" else None
    data: Dict[str, Any] = {
        "drawing": {
            "context": clean_row(db.get_context_dict()),
            "entity_count": len(entities),
            "layer_count": len(layers),
            "block_count": len(blocks),
            "extents": bbox_dict(bbox_union(bbox_from_row(entity) for entity in entities)),
        },
        "entity_statistics": _type_stats(entities),
        "layer_summary": _layer_summary(layers, entities),
        "block_summary": _block_summary(blocks),
        "topology_summary": {
            "sample": topology_summary(db, limit=50),
            "summary_count": len(topology_summary(db, limit=100000)),
        },
        "possible_domains": ranked_domains,
        "recommended_next_tools": [
            "detect_semantic_objects",
            "extract_drawing_constraints",
            "validate_geometry",
            "export_view_image_with_mapping",
        ],
    }
    if drawing_ir is not None:
        data["drawing_ir"] = drawing_ir
    return ok_result(
        f"Summarized {len(entities)} scanned CAD entities; inferred domain: {domain}.",
        data=data,
        handles=[str(entity.get("handle")) for entity in entities[:50] if entity.get("handle")],
        next_tools=data["recommended_next_tools"],
    )


def _semantic_guess(entity: Dict[str, Any]) -> Dict[str, Any]:
    etype = entity_type(entity)
    layer = str(entity.get("layer") or "").lower()
    geom = entity_geometry(entity)
    if "circle" in etype:
        radius = geom.get("radius")
        label = "hole candidate" if radius is not None else "circle feature"
        return {
            "object_type": "hole" if radius is not None else "circle_feature",
            "label": label,
            "confidence": 0.65 if radius is not None else 0.5,
            "evidence": ["circle entity", f"layer={layer}"],
        }
    if "polyline" in etype:
        closed = bool(geom.get("closed"))
        return {
            "object_type": "closed_profile" if closed else "path",
            "label": "closed profile" if closed else "polyline path",
            "confidence": 0.7 if closed else 0.45,
            "evidence": ["polyline", f"closed={closed}"],
        }
    if "text" in etype:
        return {
            "object_type": "text_annotation",
            "label": str(geom.get("text") or geom.get("text_string") or "text"),
            "confidence": 0.8,
            "evidence": ["text entity"],
        }
    if "block" in etype:
        return {
            "object_type": "block_instance",
            "label": str(geom.get("block_name") or entity.get("name") or "block"),
            "confidence": 0.75,
            "evidence": ["block reference"],
        }
    if "dimension" in etype:
        return {
            "object_type": "dimension_annotation",
            "label": "dimension",
            "confidence": 0.8,
            "evidence": ["dimension entity"],
        }
    return {
        "object_type": "cad_entity",
        "label": str(entity.get("type") or entity.get("name") or "entity"),
        "confidence": 0.35,
        "evidence": [f"type={entity.get('type')}"],
    }


def explain_entity(handle: str,
                   database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    entity = get_entity(db, handle)
    if not entity:
        return error_result(
            f"No scanned entity found for handle {handle}.",
            next_tools=["scan_all_entities", "build_drawing_ir"],
        )
    bbox = bbox_from_row(entity)
    center = bbox_center(bbox)
    radius = max(1.0, math.sqrt(bbox_area(bbox)) * 2.0) if bbox else 10.0
    nearby = []
    if center:
        nearby = [
            {
                "handle": row.get("handle"),
                "entity_type": row.get("type"),
                "layer": row.get("layer"),
                "bbox": bbox_dict(bbox_from_row(row)),
            }
            for row in db.query_near_point(center[0], center[1], radius, limit=20)
            if row.get("handle") != handle
        ]
    annotations = [
        annotation for annotation in all_annotations(db)
        if annotation.get("entity_handle") == handle
    ]
    related_dimensions = [
        {
            "handle": row.get("handle"),
            "entity_type": row.get("type"),
            "layer": row.get("layer"),
            "bbox": bbox_dict(bbox_from_row(row)),
        }
        for row in nearby
        if "dimension" in str(row.get("entity_type", "")).lower()
    ]
    data = {
        "entity": {
            **clean_row(entity),
            "bbox": bbox_dict(bbox),
            "geometry": decode_json(entity.get("geometry")),
            "properties": decode_json(entity.get("properties")),
        },
        "topology": topology_for_handle(db, handle),
        "nearby_entities": nearby,
        "related_annotations": annotations,
        "related_dimensions": related_dimensions,
        "semantic_guess": _semantic_guess(entity),
    }
    return ok_result(
        f"Explained entity {handle}.",
        data=data,
        handles=[handle] + [str(row.get("handle")) for row in nearby if row.get("handle")],
        next_tools=["find_entities_by_description", "detect_semantic_objects", "validate_geometry"],
    )


def _query_keywords(query: str) -> List[str]:
    words = []
    for raw in (query or "").lower().replace(",", " ").split():
        word = raw.strip(" .;:()[]{}")
        if word:
            words.append(word)
    return words


def _direction_bonus(query_terms: List[str], entity: Dict[str, Any],
                     extents: Optional[Tuple[float, float, float, float]]) -> Tuple[float, List[str]]:
    if not extents:
        return 0.0, []
    center = bbox_center(bbox_from_row(entity))
    if not center:
        return 0.0, []
    min_x, min_y, max_x, max_y = extents
    width = max(max_x - min_x, 1e-9)
    height = max(max_y - min_y, 1e-9)
    nx = (center[0] - min_x) / width
    ny = (center[1] - min_y) / height
    score = 0.0
    reasons: List[str] = []
    checks = {
        "left": 1.0 - nx,
        "right": nx,
        "bottom": 1.0 - ny,
        "lower": 1.0 - ny,
        "top": ny,
        "upper": ny,
    }
    for term, value in checks.items():
        if term in query_terms:
            score += max(0.0, value) * 0.2
            reasons.append(f"{term} position")
    return score, reasons


def _semantic_matches_by_handle(database: CADDatabase,
                                terms: List[str]) -> Dict[str, Tuple[float, List[str]]]:
    if not terms:
        return {}
    ensure_understanding_schema(database)
    scope = current_scope(database)
    result: Dict[str, Tuple[float, List[str]]] = {}
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_type, label, source, confidence, entity_handles, properties
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    for row in rows:
        haystack = " ".join([
            str(row["object_type"] or ""),
            str(row["label"] or ""),
            str(row["source"] or ""),
            str(row["properties"] or ""),
        ]).lower()
        matched = [term for term in terms if term in haystack]
        if not matched:
            continue
        confidence = float(row["confidence"] or 0.0)
        score = min(0.35, 0.12 * len(matched) + 0.18 * confidence)
        handles = decode_json(row["entity_handles"], [])
        for handle in handles if isinstance(handles, list) else []:
            old_score, reasons = result.get(str(handle), (0.0, []))
            result[str(handle)] = (old_score + score, reasons + [f"semantic:{','.join(matched[:3])}"])
    return result


def _validation_matches_by_handle(database: CADDatabase,
                                  terms: List[str]) -> Dict[str, Tuple[float, List[str]]]:
    report = latest_validation_report(database)
    result: Dict[str, Tuple[float, List[str]]] = {}
    if not report:
        return result
    for issue in report.get("issues", []):
        haystack = " ".join([
            str(issue.get("issue_type", "")),
            str(issue.get("message", "")),
            str(issue.get("severity", "")),
        ]).lower()
        matched = [term for term in terms if term in haystack]
        if not matched and not any(term in {"issue", "problem", "error", "violated"} for term in terms):
            continue
        for handle in issue.get("handles", []):
            old_score, reasons = result.get(str(handle), (0.0, []))
            result[str(handle)] = (
                old_score + 0.22,
                reasons + [f"validation:{issue.get('issue_type')}"],
            )
    return result


def _constraint_matches_by_handle(database: CADDatabase,
                                  terms: List[str]) -> Dict[str, Tuple[float, List[str]]]:
    if not terms:
        return {}
    ensure_understanding_schema(database)
    scope = current_scope(database)
    result: Dict[str, Tuple[float, List[str]]] = {}
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT constraint_type, status, target_handles, evidence
            FROM cad_constraints
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    for row in rows:
        haystack = " ".join([
            str(row["constraint_type"] or ""),
            str(row["status"] or ""),
            str(row["evidence"] or ""),
        ]).lower()
        matched = [term for term in terms if term in haystack]
        if not matched and not any(term in {"constraint", "dimension", "violated"} for term in terms):
            continue
        handles = decode_json(row["target_handles"], [])
        for handle in handles if isinstance(handles, list) else []:
            old_score, reasons = result.get(str(handle), (0.0, []))
            result[str(handle)] = (
                old_score + 0.2,
                reasons + [f"constraint:{row['constraint_type']}:{row['status']}"],
            )
    return result


def find_entities_by_description(query: str,
                                 top_k: int = 20,
                                 database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    entities = all_entities(db)
    terms = _query_keywords(query)
    extents = bbox_union(bbox_from_row(entity) for entity in entities)
    semantic_scores = _semantic_matches_by_handle(db, terms)
    validation_scores = _validation_matches_by_handle(db, terms)
    constraint_scores = _constraint_matches_by_handle(db, terms)
    candidates = []
    for entity in entities:
        text = entity_text(entity)
        etype = entity_type(entity)
        geom = entity_geometry(entity)
        handle = str(entity.get("handle") or "")
        score = 0.0
        reasons: List[str] = []
        for term in terms:
            aliases = {
                "hole": ["circle", "hole"],
                "rectangle": ["polyline", "rect"],
                "centerline": ["center", "centerline"],
                "dimension": ["dimension", "dim"],
                "text": ["text", "mtext"],
                "block": ["block"],
                "hatch": ["hatch"],
                "outer": ["outer", "profile", "outline"],
                "inner": ["inner", "inside"],
                "wall": ["wall"],
                "wire": ["wire", "cable", "conduit"],
                "title": ["title", "title_block"],
                "bom": ["bom", "parts list"],
                "violated": ["violated", "mismatch"],
            }.get(term, [term])
            if any(alias in text or alias in etype for alias in aliases):
                score += 0.25
                reasons.append(f"matched '{term}'")
        if "circle" in terms and "circle" in etype:
            score += 0.35
            reasons.append("circle entity")
        if "line" in terms and "line" in etype:
            score += 0.25
            reasons.append("line entity")
        if "polyline" in terms and "polyline" in etype:
            score += 0.25
            reasons.append("polyline entity")
        if "closed" in terms and bool(geom.get("closed")):
            score += 0.25
            reasons.append("closed geometry")
        bonus, direction_reasons = _direction_bonus(terms, entity, extents)
        score += bonus
        reasons.extend(direction_reasons)
        if extents and "center" in terms:
            center = bbox_center(bbox_from_row(entity))
            if center:
                min_x, min_y, max_x, max_y = extents
                drawing_center = [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0]
                diag = max(point_distance([min_x, min_y], [max_x, max_y]), 1e-9)
                center_score = max(0.0, 1.0 - point_distance(center, drawing_center) / diag)
                score += center_score * 0.2
                reasons.append("center position")
        for extra_scores in (semantic_scores, validation_scores, constraint_scores):
            if handle in extra_scores:
                extra_score, extra_reasons = extra_scores[handle]
                score += extra_score
                reasons.extend(extra_reasons)
        area = bbox_area(bbox_from_row(entity))
        radius = geom.get("radius")
        sort_metric = float(radius) if radius is not None else area
        if score > 0:
            candidates.append((score, sort_metric, entity, reasons))

    if "largest" in terms:
        candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
    elif "smallest" in terms:
        candidates.sort(key=lambda item: (item[1], -item[0]))
    else:
        candidates.sort(key=lambda item: item[0], reverse=True)

    results = []
    for score, _, entity, reasons in candidates[:max(1, min(int(top_k or 20), 100))]:
        results.append({
            "handle": entity.get("handle"),
            "score": round(min(score, 1.0), 3),
            "reason": "; ".join(reasons[:5]),
            "bbox": bbox_dict(bbox_from_row(entity)),
            "entity_type": entity.get("type"),
            "layer": entity.get("layer"),
        })
    warnings = []
    if not results:
        warnings.append("No lexical/rule-based candidates matched the description.")
    warnings.append("Entity description search is lexical and rule-based; embedding search is not implemented yet.")
    return ok_result(
        f"Found {len(results)} rule-based candidates for: {query}",
        data={"candidates": results, "query": query},
        handles=[str(row["handle"]) for row in results if row.get("handle")],
        warnings=warnings,
        next_tools=["explain_entity", "export_view_image_with_mapping"],
    )


def analyze_drawing_intent(domain_hint: Optional[str] = None,
                           database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    entities = all_entities(db)
    layers = all_layers(db)
    blocks = all_blocks(db)
    inferred, ranked = _domain_scores(entities, layers, blocks)
    if domain_hint:
        hint = domain_hint.lower().strip()
        for item in ranked:
            if item["domain"] == hint:
                item["score"] = round(float(item["score"]) + 2.0, 3)
                item["evidence"].append("user domain_hint")
        ranked.sort(key=lambda item: -float(item["score"]))
        inferred = ranked[0]["domain"] if ranked and ranked[0]["score"] > 0 else hint
    data = {
        "inferred_domain": inferred,
        "ranked_domains": ranked,
        "entity_mix": _type_stats(entities),
        "layer_evidence": [layer.get("name") for layer in layers[:50]],
        "block_evidence": [block.get("name") for block in blocks[:50]],
    }
    return ok_result(
        f"Inferred drawing domain: {inferred}.",
        data=data,
        next_tools=["detect_semantic_objects", "extract_drawing_constraints", "validate_geometry"],
    )
