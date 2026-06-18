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
from .ir_builder import build_drawing_ir
from .semantic_graph import get_semantic_graph
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
    if uri == "cad://drawing/current/tool-guide":
        return {
            "workflow": [
                "scan_all_entities",
                "build_drawing_ir",
                "summarize_drawing",
                "detect_semantic_objects",
                "extract_drawing_constraints",
                "validate_geometry",
                "export_view_image_with_mapping",
                "ground_vlm_region",
                "propose_repair_plan",
                "dry_run_cad_plan",
                "execute_cad_plan only with allow_modify=True",
            ],
            "safety": [
                "Understanding tools read scanned metadata and do not modify the DWG.",
                "Semantic, constraint, validation, and view grounding metadata is stored in SQLite.",
                "Plan execution refuses to modify AutoCAD unless allow_modify=True.",
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
