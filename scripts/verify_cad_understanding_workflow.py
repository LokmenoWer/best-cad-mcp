"""Real AutoCAD smoke benchmark for the CAD understanding workflow.

This script is intentionally separate from unit tests. It requires Windows
AutoCAD COM and writes only test drawings/artifacts in the current workspace.
When AutoCAD is unavailable it emits a skipped JSON report and exits 0.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cad_controller import get_controller
from src.cad_tools import dimension_tools, drawing_tools, query_tools
from src.cad_understanding.analysis import summarize_drawing
from src.cad_understanding.constraints import extract_drawing_constraints
from src.cad_understanding.ir_builder import build_drawing_ir
from src.cad_understanding.plan import dry_run_cad_plan, execute_cad_plan
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.engineering_review import analyze_engineering_drawing_stages
from src.cad_understanding.validators import validate_geometry
from src.cad_understanding.view_grounding import export_view_image_with_mapping, ground_vlm_overlay_id
from src.cad_understanding.vlm import (
    evaluate_vlm_grounding,
    fuse_vlm_findings_into_semantic_graph,
    submit_vlm_review,
    validate_vlm_review_output,
)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


def _looks_failed(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("ok") is False or value.get("success") is False:
            return True
        message = str(value.get("message") or value.get("error") or "")
    else:
        message = str(value or "")
    lowered = message.lower()
    return any(token in lowered for token in ("error", "failed", "failure", "失败", "错误", "拒绝"))


def _summarize_result(value: Any) -> Any:
    if isinstance(value, dict):
        if "drawing_ir" in value:
            ir = value["drawing_ir"]
            return {"entity_count": ir.get("entity_count"), "views": len(ir.get("views", []))}
        if "entity_count" in value and "entities" in value:
            return {"entity_count": value.get("entity_count"), "keys": sorted(value.keys())[:12]}
        if {"ok", "message", "handles", "warnings"}.intersection(value.keys()):
            summary = {key: value.get(key) for key in ("ok", "message", "handles", "warnings", "next_tools") if key in value}
            data = value.get("data")
            if isinstance(data, dict):
                if "snapshot" in data:
                    snap = data["snapshot"]
                    summary["snapshot"] = {
                        "snapshot_id": snap.get("snapshot_id"),
                        "clean_image_path": snap.get("clean_image_path"),
                        "overlay_image_path": snap.get("overlay_image_path"),
                        "context_json_path": snap.get("context_json_path"),
                        "overlay_item_count": len(snap.get("overlay_items", [])),
                    }
                if "validation_report" in data:
                    report = data["validation_report"]
                    summary["validation_report"] = {
                        "passed": report.get("passed"),
                        "score": report.get("score"),
                        "issue_count": report.get("issue_count"),
                    }
            return summary
        return {key: _summarize_result(value[key]) for key in list(value.keys())[:12]}
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...[truncated]"
    if isinstance(value, list):
        return [_summarize_result(item) for item in value[:10]]
    return _json_safe(value)


def main() -> int:
    report: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "autocad_version": "",
        "steps": [],
        "artifacts": [],
        "failures": [],
        "warnings": [],
    }
    artifacts_dir = ROOT / "cad_visual_exports" / "smoke"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    ctrl = get_controller()
    if not ctrl.connect(visible=True):
        report.update({
            "ok": True,
            "skipped": True,
            "warnings": ["AutoCAD COM is unavailable; smoke benchmark skipped."],
        })
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0
    try:
        info = ctrl.get_app_info()
        report["autocad_version"] = str(info.get("version") or info.get("caption") or "")
    except Exception as exc:
        report["warnings"].append(f"Could not read AutoCAD version: {exc}")

    def step(name: str, fn: Callable[[], Any], required: bool = True) -> Any:
        try:
            value = fn()
            ok = not _looks_failed(value)
            report["steps"].append({"name": name, "ok": ok, "result": _summarize_result(value)})
            if required and not ok:
                report["failures"].append({"name": name, "result": _summarize_result(value)})
            return value
        except Exception as exc:
            failure = {
                "name": name,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=5),
            }
            report["steps"].append({"name": name, "ok": False, "failure": failure})
            if required:
                report["failures"].append(failure)
            return None

    create_result = step("create_new_drawing", lambda: drawing_tools.create_new_drawing())
    if _looks_failed(create_result):
        report["ok"] = False
        report_path = artifacts_dir / "verify_cad_understanding_workflow_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        report["artifacts"].append(str(report_path))
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 1
    step("draw_outer_rectangle", lambda: drawing_tools.draw_rectangle(0, 0, 120, 80, layer="OUTLINE"))
    step("draw_hole_1", lambda: drawing_tools.draw_circle(30, 40, 8, layer="HOLES"))
    step("draw_hole_2", lambda: drawing_tools.draw_circle(90, 40, 8, layer="HOLES"))
    step("draw_centerline", lambda: drawing_tools.draw_line(0, 40, 120, 40, layer="CENTER"))
    step("add_radial_dimension", lambda: dimension_tools.add_radial_dimension(30, 40, 38, 40, layer="DIM"))
    step("add_linear_dimension", lambda: dimension_tools.add_linear_dimension(0, 0, 120, 0, 60, -12, layer="DIM"))
    step(
        "scan_all_entities",
        lambda: query_tools.scan_all_entities(
            clear_db=True,
            max_entities=500,
            detail_level="standard",
            include_bounding_boxes=True,
            derive_topology=True,
            topology_detail="full",
        ),
    )
    step("build_drawing_ir", lambda: build_drawing_ir())
    step("summarize_drawing", lambda: summarize_drawing(level="normal"))
    step("detect_semantic_objects", lambda: detect_semantic_objects(domain="mechanical"))
    step("extract_drawing_constraints", lambda: extract_drawing_constraints())
    step("validate_geometry", lambda: validate_geometry())
    snapshot = step(
        "export_view_image_with_mapping",
        lambda: export_view_image_with_mapping(
            filepath=str(artifacts_dir / "smoke_view.wmf"),
            include_overlay=True,
            include_entity_bboxes=True,
            overlay_granularity="both",
            overlay_style="som",
            include_tiles=True,
        ),
        required=False,
    )
    if isinstance(snapshot, dict) and snapshot.get("ok"):
        snap = snapshot["data"]["snapshot"]
        report["artifacts"].extend([
            snap.get("clean_image_path"),
            snap.get("overlay_image_path"),
            snap.get("context_json_path"),
        ])
        items = snap.get("overlay_items") or []
        if items:
            review = {
                "findings": [
                    {
                        "overlay_id": items[0]["overlay_id"],
                        "issue_type": "smoke_visual_review",
                        "semantic_type": "vlm_review_finding",
                        "severity": "info",
                        "confidence": 0.8,
                        "evidence": {"reason": "Smoke test review finding for grounding workflow."},
                    }
                ]
            }
            step(
                "ground_vlm_overlay_id",
                lambda: ground_vlm_overlay_id(snap["snapshot_id"], items[0]["overlay_id"]),
                required=False,
            )
            step(
                "validate_vlm_review_output",
                lambda: validate_vlm_review_output(review, snapshot_id=snap["snapshot_id"]),
                required=False,
            )
            step(
                "submit_vlm_review",
                lambda: submit_vlm_review(snap["snapshot_id"], review, source_model="smoke-vlm"),
                required=False,
            )
            step(
                "fuse_vlm_findings_into_semantic_graph",
                lambda: fuse_vlm_findings_into_semantic_graph(min_confidence=0.1),
                required=False,
            )
            step(
                "evaluate_vlm_grounding",
                lambda: evaluate_vlm_grounding(
                    [{
                        "overlay_id": items[0]["overlay_id"],
                        "issue_type": "smoke_visual_review",
                        "expected_handles": [items[0].get("handle")],
                    }],
                    snapshot_id=snap["snapshot_id"],
                ),
                required=False,
            )
            step(
                "analyze_engineering_drawing_stages",
                lambda: analyze_engineering_drawing_stages(snapshot_id=snap["snapshot_id"]),
                required=False,
            )

    plan = {
        "plan_id": "smoke_variable_plan",
        "description": "Draw one inspection circle through CADPlan variable binding.",
        "units": "drawing_units",
        "steps": [
            {
                "step_id": "draw_probe",
                "op": "draw_circle",
                "args": {"center_x": 60, "center_y": 40, "radius": 4, "layer": "CHECK"},
                "save_as": "$probe_circle",
                "postconditions": [{"type": "exists", "target": "$probe_circle"}],
            }
        ],
        "risk_level": "low",
        "requires_confirmation": True,
    }
    step("dry_run_cad_plan", lambda: dry_run_cad_plan(plan))
    step(
        "execute_cad_plan",
        lambda: execute_cad_plan(
            plan,
            allow_modify=True,
            transactional=True,
            rollback_on_error=True,
            validate_after_plan=False,
            rescan_after_plan=False,
        ),
    )
    step("rescan_after_plan", lambda: query_tools.scan_all_entities(clear_db=True, max_entities=500, topology_detail="full"))
    step("validate_geometry_after_plan", lambda: validate_geometry())

    report["artifacts"] = [str(item) for item in report["artifacts"] if item]
    report["ok"] = not report["failures"]
    report_path = artifacts_dir / "verify_cad_understanding_workflow_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["artifacts"].append(str(report_path))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
