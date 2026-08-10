"""Agent-readable CAD understanding resources."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .analysis import summarize_drawing
from .common import (
    all_topology_primitives,
    all_topology_relations,
    get_db,
    latest_validation_report,
    topology_summary,
)
from .constraints import get_constraints
from .engineering_review import analyze_engineering_drawing_stages
from .ir_builder import build_drawing_ir
from .semantic_graph import get_semantic_graph
from .vlm import get_vlm_findings
from .result import ToolResult, error_result, ok_result

CAD_RESOURCE_URIS = [
    "cad://workspace/context",
    "cad://drawing/current/summary",
    "cad://drawing/current/ir",
    "cad://drawing/current/ir/overview",
    "cad://drawing/current/ir/entities",
    "cad://drawing/current/topology",
    "cad://drawing/current/semantic-graph",
    "cad://drawing/current/constraints",
    "cad://drawing/current/validation-report",
    "cad://drawing/current/vlm-findings",
    "cad://drawing/current/engineering-interpretation",
    "cad://drawing/current/tool-guide",
]


def _payload(uri: str, database: Optional[CADDatabase] = None) -> Dict[str, Any]:
    db = get_db(database)
    if uri == "cad://workspace/context":
        return {"context": db.get_context_dict()}
    if uri == "cad://drawing/current/summary":
        return summarize_drawing("normal", db)["data"]
    if uri == "cad://drawing/current/ir":
        return {"drawing_ir": build_drawing_ir(False, db)}
    if uri == "cad://drawing/current/ir/overview":
        return {"drawing_ir": build_drawing_ir(False, db, sections=["overview"])}
    if uri == "cad://drawing/current/ir/entities":
        return {"drawing_ir": build_drawing_ir(False, db, sections=["entities"])}
    if uri == "cad://drawing/current/topology":
        return {
            "summary": topology_summary(db),
            "primitives": all_topology_primitives(db),
            "relations": all_topology_relations(db),
        }
    if uri == "cad://drawing/current/semantic-graph":
        return get_semantic_graph(db)["data"]
    if uri == "cad://drawing/current/constraints":
        return get_constraints(database=db)["data"]
    if uri == "cad://drawing/current/validation-report":
        return {"validation_report": latest_validation_report(db)}
    if uri == "cad://drawing/current/vlm-findings":
        return get_vlm_findings(database=db)["data"]
    if uri == "cad://drawing/current/engineering-interpretation":
        return analyze_engineering_drawing_stages(database=db)["data"]
    if uri == "cad://drawing/current/tool-guide":
        return {
            "closed_loop": {
                "phases": [
                    "observe document/space/units plus structured and visual baseline",
                    "define plan-external acceptance criteria",
                    "validate_cad_plan -> dry_run_cad_plan without modifying DWG",
                    "execute an authorized transactional modification batch",
                    "rescan -> rebuild/rebind/recheck -> validate -> fresh visual review",
                ],
                "evidence": {
                    "structured": "handles, topology, CAD-IR, semantics, units, dimensions, constraints",
                    "visual": "composition, overlap, clipping, spacing, legibility, hatch and sheet layout",
                },
                "acceptance": (
                    "Never accept a modification from its success response alone; "
                    "compare fresh structured and visual evidence with the external acceptance criteria."
                ),
                "automatic_repair_limit": 2,
            },
            "intent_routes": {
                "understand_existing_or_complex": [
                    "inspect document, active space, units, and visual baseline when useful",
                    "scan_all_entities(topology_detail='summary')",
                    "build_drawing_ir",
                    "summarize_drawing",
                    "analyze_drawing_intent",
                    "detect_semantic_objects",
                    "bind_all_dimensions",
                    "extract_drawing_constraints",
                    "check_drawing_constraints",
                    "validate_geometry",
                    "render_drawing_view when the model must see; mapped export when grounding is needed",
                ],
                "engineering_drawing_or_assembly": [
                    "export_view_image_with_mapping(include_overlay=True, overlay_granularity='both')",
                    "analyze_engineering_drawing_stages",
                    "fuse_vlm_findings_into_semantic_graph when grounded findings add semantic objects",
                    "build_drawing_ir(sections=['overview', 'entities'])",
                ],
                "copy_mechanical_drawing_from_image": [
                    "prepare_image_trace(image_path, domain='mechanical')",
                    "prepare_visual_semantic_context(image_id) before component-level VLM recognition",
                    "Agent-side VLM uses prompt copy_drawing_from_image and returns ImageDrawingSpec/v1 JSON",
                    "validate_image_drawing_spec -> submit_image_drawing_spec",
                    "compile_image_spec_to_cad_plan -> validate_image_fidelity_contract",
                    "validate_cad_plan -> dry_run_cad_plan -> execute_cad_plan only with allow_modify=True",
                    "scan_all_entities -> validate_geometry -> export_view_image_with_mapping for visual diff",
                ],
                "new_complex_drawing": [
                    "recommend_cad_tools(intent)",
                    "define units and plan-external acceptance criteria",
                    "validate_cad_plan",
                    "dry_run_cad_plan",
                    "execute_cad_plan with allow_modify=True and transactional=True when permission is present",
                    "scan_all_entities",
                    "rebuild/rebind/recheck -> validate_geometry -> fresh visual review",
                ],
                "repair": [
                    "capture structured and visual before-state evidence",
                    "validate_geometry",
                    "explain_entity for exact target handles",
                    "propose_repair_plan or propose_constraint_repair_plan",
                    "validate_cad_plan",
                    "dry_run_cad_plan",
                    "execute when modification permission is present",
                    "rescan -> rebind/recheck -> validate_geometry -> fresh visual review",
                ],
            },
            "workflow": [
                "scan_all_entities",
                "build_drawing_ir",
                "summarize_drawing",
                "detect_semantic_objects",
                "extract_drawing_constraints",
                "validate_geometry",
                "export_view_image_with_mapping",
                "validate_vlm_review_output",
                "submit_vlm_review",
                "fuse_vlm_findings_into_semantic_graph",
                "analyze_engineering_drawing_stages",
                "evaluate_vlm_grounding",
                "ground_vlm_region",
                "prepare_image_trace",
                "prepare_visual_semantic_context",
                "validate_image_drawing_spec",
                "submit_image_drawing_spec",
                "compile_image_spec_to_cad_plan",
                "validate_image_fidelity_contract",
                "propose_repair_plan",
                "dry_run_cad_plan",
                "execute_cad_plan only with allow_modify=True",
            ],
            "safety": [
                "Understanding tools read scanned metadata and do not modify the DWG.",
                "Semantic, constraint, validation, and view grounding metadata is stored in SQLite.",
                "Plan execution refuses to modify AutoCAD unless allow_modify=True.",
                "Review renders and mapped snapshots are read-only artifacts, not deliverable exports.",
                "After a failed phase, rescan and rebuild the plan instead of replaying stale handles.",
            ],
            "fidelity": [
                "Do not flatten assemblies, section views, exploded views, BOMs, title blocks, or dimensions into generic lines and text.",
                "Use blocks/arrays for repeated parts, CAD tables for BOMs, associative dimensions for measurements, hatches for sections, and solids/regions/booleans for 3D intent.",
                "Image trace must preserve feature-level geometry: chamfers, fillets, holes, slots, patterns, hatches, dimensions, and tables must not silently degrade to plain rectangles, loose lines, or text.",
                "When no exposed tool preserves the requested feature, state the gap and request guidance instead of silently simplifying.",
            ],
        }
    raise KeyError(uri)


def get_resource_json(uri: str,
                      database: Optional[CADDatabase] = None) -> str:
    return json.dumps(_payload(uri, database), indent=2, ensure_ascii=False, default=str)


def list_cad_resources(database: Optional[CADDatabase] = None) -> ToolResult:
    return ok_result(
        "Listed CAD understanding resources.",
        data={"resources": CAD_RESOURCE_URIS},
        next_tools=["get_cad_resource"],
    )


def get_cad_resource(uri: str,
                     database: Optional[CADDatabase] = None) -> ToolResult:
    try:
        payload = _payload(uri, database)
    except KeyError:
        return error_result(
            f"Unknown CAD resource URI: {uri}",
            data={"resources": CAD_RESOURCE_URIS},
            next_tools=["list_cad_resources"],
        )
    return ok_result(
        f"Loaded CAD resource {uri}.",
        data={"uri": uri, "payload": payload},
        next_tools=["list_cad_resources"],
    )
