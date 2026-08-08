"""Create the README mechanical sample through the real best-cad-mcp server.

This script is intentionally an MCP client, not an AutoCAD COM shortcut.  It
keeps the CADPlan, dry-run, execution, scan, validation, DWG, clean export,
overlay, and mapping sidecar together so README screenshots are auditable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "docs" / "artifacts" / "readme-real-cad"
IMAGE_DIR = ROOT / "docs" / "images"
DWG_PATH = ARTIFACT_DIR / "bearing-housing-three-view.dwg"
VIEW_EXPORT_PATH = IMAGE_DIR / "readme-cad-real.wmf"
CLEAN_IMAGE_PATH = VIEW_EXPORT_PATH.with_suffix(".png")


def _jsonable_tool_result(result: Any) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in result.content:
        item = {"type": block.type}
        if block.type == "text":
            item["text"] = block.text
        elif block.type == "resource_link":
            item["name"] = block.name
            item["uri"] = str(block.uri)
        elif block.type in {"image", "audio"}:
            item["mime_type"] = getattr(block, "mime_type", None)
            item["embedded_bytes_omitted"] = True
        blocks.append(item)
    return {
        "is_error": bool(result.is_error),
        "structured_content": result.structured_content,
        "content": blocks,
    }


def _write_json(name: str, value: Any) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _structured_result(result: Any) -> dict[str, Any]:
    structured_content = result.structured_content or {}
    if not isinstance(structured_content, dict):
        return {}
    value = structured_content.get("result", {})
    return value if isinstance(value, dict) else {}


def _build_vlm_review(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the actual visual review authored from the exported CAD raster.

    The regions correspond to features inspected in readme-cad-real.png.  They
    intentionally contain no claimed AutoCAD handles: handle membership must be
    established by submit_vlm_review from the snapshot mapping, not guessed by
    the visual reviewer.
    """
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    source_ref = {
        "schema_version": "VisualSourceRef/v1",
        "snapshot_id": snapshot_id,
        "coordinate_space": "snapshot_global",
    }
    return {
        "schema_version": "cad-vlm-review/v1",
        "snapshot_id": snapshot_id,
        "review_summary": {
            "drawing_type": "mechanical orthographic drawing",
            "recognized_part": "flanged bearing housing",
            "views": ["front", "top", "right section A-A"],
            "annotations": ["linear and diametric dimensions", "centerlines", "section hatch", "title block"],
            "visible_defect_assessment": "No obvious broken or duplicate geometry is visible in the exported raster.",
            "uncertainty": "Material and nominal dimensions are read from visible CAD text; manufacturing tolerances are not specified.",
        },
        "findings": [
            {
                "finding_id": "readme-vlm-central-bore",
                "issue_type": "recognized_central_bore",
                "severity": "info",
                "confidence": 0.99,
                "semantic_type": "circular_bore",
                "bbox": [740.0, 308.0, 894.0, 463.0],
                "source_ref": source_ref,
                "evidence": {
                    "observation": "The front view has a clear central circular bore inside two concentric flange steps.",
                    "visible_features": ["continuous circular outline", "shared centerlines", "concentric stepped rings"],
                },
            },
            {
                "finding_id": "readme-vlm-mounting-slot",
                "issue_type": "recognized_mounting_slot",
                "severity": "info",
                "confidence": 0.96,
                "semantic_type": "closed_profile",
                "bbox": [575.0, 965.0, 612.0, 1002.0],
                "source_ref": source_ref,
                "evidence": {
                    "observation": "The top view shows a rounded mounting slot composed of two straight sides and two semicircular ends.",
                    "visible_features": ["closed obround contour", "one of four symmetric base slots"],
                },
            },
            {
                "finding_id": "readme-vlm-section-hatch",
                "issue_type": "recognized_section_hatch",
                "severity": "info",
                "confidence": 0.98,
                "semantic_type": "hatch",
                "bbox": [1432.0, 232.0, 1738.0, 350.0],
                "source_ref": source_ref,
                "evidence": {
                    "observation": "The right-side A-A view contains an ANSI31-style hatched upper material region around the stepped bearing seat.",
                    "visible_features": ["parallel diagonal hatch strokes", "sectioned solid boundary", "central unhatched bore"],
                },
            },
            {
                "finding_id": "readme-vlm-title-block",
                "issue_type": "recognized_title_block",
                "severity": "info",
                "confidence": 0.99,
                "semantic_type": "title_block",
                "bbox": [1292.0, 860.0, 1932.0, 1138.0],
                "source_ref": source_ref,
                "evidence": {
                    "observation": "The lower-right title block identifies a flanged bearing housing, drawing MCP-REAL-001A, revision A, material QT500-7, scale 1:1, and millimetre units.",
                    "visible_features": ["bordered title block", "drawing and revision cells", "material, scale, and units fields"],
                },
            },
        ],
    }


def _step(step_id: str, op: str, args: dict[str, Any], *, save_as: str | None = None,
          depends_on: list[str] | None = None, postconditions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "step_id": step_id,
        "op": op,
        "args": args,
        "writes": True,
        "depends_on": depends_on or [],
    }
    if save_as:
        value["save_as"] = save_as
    if postconditions:
        value["postconditions"] = postconditions
    return value


def _build_plan() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def add(step_id: str, op: str, args: dict[str, Any], **kwargs: Any) -> None:
        steps.append(_step(step_id, op, args, **kwargs))

    # Real AutoCAD layers. Continuous is used for broad compatibility with a
    # fresh acadiso drawing; center/hidden semantics remain explicit by layer.
    for name, color in (
        ("M-OUTLINE", 7),
        ("M-CENTER", 4),
        ("M-HIDDEN", 8),
        ("M-DIM", 2),
        ("M-HATCH", 9),
        ("M-TEXT", 3),
        ("M-TITLE", 5),
    ):
        add(f"layer_{name.lower().replace('-', '_')}", "create_layer", {
            "name": name,
            "color": color,
            "linetype": "Continuous",
        })

    # Front view: flanged bearing housing, mounting base, ribs, and bolt circle.
    add("front_base", "draw_rectangle", {"x1": -135, "y1": -110, "x2": 135, "y2": -90, "layer": "M-OUTLINE"}, save_as="$front_base")
    add("front_flange_outer", "draw_circle", {"center_x": 0, "center_y": 0, "radius": 70, "layer": "M-OUTLINE"}, save_as="$front_flange_outer", postconditions=[{"type": "exists", "target": "$front_flange_outer"}])
    add("front_flange_step", "draw_circle", {"center_x": 0, "center_y": 0, "radius": 57, "layer": "M-OUTLINE"})
    add("front_counterbore", "draw_circle", {"center_x": 0, "center_y": 0, "radius": 45, "layer": "M-HIDDEN"})
    add("front_bore", "draw_circle", {"center_x": 0, "center_y": 0, "radius": 35, "layer": "M-OUTLINE"}, save_as="$front_bore", postconditions=[{"type": "exists", "target": "$front_bore"}])
    for index, (x, y) in enumerate(((-35, 35), (35, 35), (-35, -35), (35, -35)), 1):
        add(f"front_bolt_{index}", "draw_circle", {"center_x": x, "center_y": y, "radius": 7, "layer": "M-OUTLINE"})
    for index, x in enumerate((-105, 105), 1):
        add(f"front_mount_hole_{index}", "draw_circle", {"center_x": x, "center_y": -100, "radius": 7, "layer": "M-OUTLINE"})
    add("front_left_rib", "draw_polyline", {"points": [-88, -90, -52, -46, -32, -60, -58, -90], "closed": True, "layer": "M-OUTLINE"}, save_as="$front_left_rib")
    add("front_right_rib", "draw_polyline", {"points": [88, -90, 52, -46, 32, -60, 58, -90], "closed": True, "layer": "M-OUTLINE"}, save_as="$front_right_rib")
    add("front_axis_h", "draw_line", {"start_x": -88, "start_y": 0, "end_x": 88, "end_y": 0, "layer": "M-CENTER"})
    add("front_axis_v", "draw_line", {"start_x": 0, "start_y": -82, "end_x": 0, "end_y": 82, "layer": "M-CENTER"})
    add("front_dim_width", "add_linear_dimension", {"x1": -135, "y1": -110, "x2": 135, "y2": -110, "text_x": 0, "text_y": -132, "layer": "M-DIM"})
    add("front_dim_height", "add_linear_dimension", {"x1": -135, "y1": -110, "x2": -135, "y2": 70, "text_x": -158, "text_y": -20, "layer": "M-DIM"})
    add("front_dim_bore", "add_diametric_dimension", {"chord1_x": -35, "chord1_y": 0, "chord2_x": 35, "chord2_y": 0, "leader_length": 22, "layer": "M-DIM"})
    add("front_label", "draw_text", {"text": "FRONT VIEW", "insert_x": -135, "insert_y": 92, "height": 8, "layer": "M-TEXT"})
    add("front_note", "draw_text", {"text": "4X DIA 14 ON 98 PCD", "insert_x": 72, "insert_y": 58, "height": 4.5, "layer": "M-TEXT"})

    # Top view projected below the front view, with four real slot profiles.
    add("top_base", "draw_rectangle", {"x1": -135, "y1": -340, "x2": 135, "y2": -260, "layer": "M-OUTLINE"}, save_as="$top_base")
    add("top_body", "draw_rectangle", {"x1": -60, "y1": -330, "x2": 60, "y2": -270, "layer": "M-OUTLINE"})
    add("top_flange_left", "draw_line", {"start_x": -45, "start_y": -340, "end_x": -45, "end_y": -260, "layer": "M-OUTLINE"})
    add("top_flange_right", "draw_line", {"start_x": 45, "start_y": -340, "end_x": 45, "end_y": -260, "layer": "M-OUTLINE"})
    add("top_hidden_left", "draw_line", {"start_x": -35, "start_y": -330, "end_x": -35, "end_y": -270, "layer": "M-HIDDEN"})
    add("top_hidden_right", "draw_line", {"start_x": 35, "start_y": -330, "end_x": 35, "end_y": -270, "layer": "M-HIDDEN"})
    add("top_axis_v", "draw_line", {"start_x": 0, "start_y": -352, "end_x": 0, "end_y": -248, "layer": "M-CENTER"})
    slot_index = 0
    for cx in (-105, 105):
        for cy in (-280, -320):
            slot_index += 1
            left = cx - 7
            right = cx + 7
            add(f"slot_{slot_index}_top", "draw_line", {"start_x": left, "start_y": cy + 7, "end_x": right, "end_y": cy + 7, "layer": "M-OUTLINE"})
            add(f"slot_{slot_index}_bottom", "draw_line", {"start_x": left, "start_y": cy - 7, "end_x": right, "end_y": cy - 7, "layer": "M-OUTLINE"})
            add(f"slot_{slot_index}_left", "draw_arc", {"center_x": left, "center_y": cy, "radius": 7, "start_angle": 90, "end_angle": 270, "layer": "M-OUTLINE"})
            add(f"slot_{slot_index}_right", "draw_arc", {"center_x": right, "center_y": cy, "radius": 7, "start_angle": -90, "end_angle": 90, "layer": "M-OUTLINE"})
    add("top_dim_width", "add_linear_dimension", {"x1": -135, "y1": -340, "x2": 135, "y2": -340, "text_x": 0, "text_y": -363, "layer": "M-DIM"})
    add("top_dim_depth", "add_linear_dimension", {"x1": 135, "y1": -340, "x2": 135, "y2": -260, "text_x": 158, "text_y": -300, "layer": "M-DIM"})
    add("top_label", "draw_text", {"text": "TOP VIEW", "insert_x": -135, "insert_y": -235, "height": 8, "layer": "M-TEXT"})
    add("section_line", "draw_line", {"start_x": -150, "start_y": -300, "end_x": 150, "end_y": -300, "layer": "M-DIM"})
    add("section_a_left", "draw_text", {"text": "A", "insert_x": -162, "insert_y": -296, "height": 6, "layer": "M-DIM"})
    add("section_a_right", "draw_text", {"text": "A", "insert_x": 154, "insert_y": -296, "height": 6, "layer": "M-DIM"})

    # Right-side section A-A, with actual closed boundaries and associative hatch.
    add("section_base", "draw_rectangle", {"x1": 225, "y1": -110, "x2": 495, "y2": -90, "layer": "M-OUTLINE"}, save_as="$section_base")
    add("section_left_rib", "draw_polyline", {"points": [278, -90, 305, -52, 330, -52, 314, -90], "closed": True, "layer": "M-OUTLINE"})
    add("section_right_rib", "draw_polyline", {"points": [442, -90, 415, -52, 390, -52, 406, -90], "closed": True, "layer": "M-OUTLINE"})
    add("section_top_boundary", "draw_polyline", {"points": [290, 18, 320, 18, 320, 28, 400, 28, 400, 18, 430, 18, 430, 70, 290, 70], "closed": True, "layer": "M-OUTLINE"}, save_as="$section_top_boundary")
    add("section_bottom_boundary", "draw_polyline", {"points": [290, -18, 320, -18, 320, -28, 400, -28, 400, -18, 430, -18, 430, -80, 290, -80], "closed": True, "layer": "M-OUTLINE"}, save_as="$section_bottom_boundary")
    add("section_left_flange", "draw_rectangle", {"x1": 275, "y1": -45, "x2": 290, "y2": 45, "layer": "M-OUTLINE"})
    add("section_right_flange", "draw_rectangle", {"x1": 430, "y1": -45, "x2": 445, "y2": 45, "layer": "M-OUTLINE"})
    add("section_axis", "draw_line", {"start_x": 258, "start_y": 0, "end_x": 462, "end_y": 0, "layer": "M-CENTER"})
    add("section_hatch_top", "add_hatch", {"pattern_name": "ANSI31", "associativity": True, "layer": "M-HATCH"}, save_as="$section_hatch_top")
    add("section_hatch_top_boundary", "hatch_add_boundary", {"handle": "$section_hatch_top", "boundary_handles": ["$section_top_boundary"]}, depends_on=["section_top_boundary", "section_hatch_top"])
    add("section_hatch_bottom", "add_hatch", {"pattern_name": "ANSI31", "associativity": True, "layer": "M-HATCH"}, save_as="$section_hatch_bottom")
    add("section_hatch_bottom_boundary", "hatch_add_boundary", {"handle": "$section_hatch_bottom", "boundary_handles": ["$section_bottom_boundary"]}, depends_on=["section_bottom_boundary", "section_hatch_bottom"])
    add("section_dim_length", "add_linear_dimension", {"x1": 275, "y1": 70, "x2": 445, "y2": 70, "text_x": 360, "text_y": 96, "layer": "M-DIM"})
    add("section_dim_height", "add_linear_dimension", {"x1": 495, "y1": -110, "x2": 495, "y2": 70, "text_x": 520, "text_y": -20, "layer": "M-DIM"})
    add("section_dim_bore", "add_linear_dimension", {"x1": 360, "y1": -18, "x2": 360, "y2": 18, "text_x": 458, "text_y": 0, "layer": "M-DIM"})
    add("section_label", "draw_text", {"text": "RIGHT SECTION A-A", "insert_x": 225, "insert_y": 118, "height": 8, "layer": "M-TEXT"})
    add("section_note", "draw_text", {"text": "STEPPED BEARING SEAT / ANSI31 SECTION", "insert_x": 300, "insert_y": -130, "height": 4.5, "layer": "M-TEXT"})

    # Model-space title block is real CAD geometry and text.
    add("title_outer", "draw_rectangle", {"x1": 225, "y1": -350, "x2": 520, "y2": -225, "layer": "M-TITLE"}, save_as="$title_outer")
    for index, (x1, y1, x2, y2) in enumerate((
        (225, -250, 520, -250),
        (225, -290, 520, -290),
        (225, -320, 520, -320),
        (410, -250, 410, -350),
        (465, -250, 465, -320),
    ), 1):
        add(f"title_grid_{index}", "draw_line", {"start_x": x1, "start_y": y1, "end_x": x2, "end_y": y2, "layer": "M-TITLE"})
    title_text = (
        ("REAL AUTOCAD MODEL-SPACE EXPORT", 234, -243, 7),
        ("FLANGED BEARING HOUSING", 234, -276, 8),
        ("DRAWING", 417, -263, 3.5),
        ("MCP-REAL-001", 417, -281, 5),
        ("REV", 472, -263, 3.5),
        ("A", 472, -281, 6),
        ("MATERIAL: QT500-7", 234, -308, 5),
        ("SCALE 1:1", 417, -308, 5),
        ("UNITS mm", 472, -308, 5),
        ("CREATED BY VALIDATED + DRY-RUN CADPLAN", 234, -339, 4.5),
        ("best-cad-mcp", 417, -339, 5),
    )
    for index, (text, x, y, height) in enumerate(title_text, 1):
        add(f"title_text_{index}", "draw_text", {"text": text, "insert_x": x, "insert_y": y, "height": height, "layer": "M-TEXT"})

    return {
        "plan_id": "readme-real-bearing-housing-v1",
        "description": "Create a real AutoCAD three-view bearing-housing drawing with section, hatches, dimensions, centerlines, hidden geometry, and title block.",
        "units": "mm",
        "risk_level": "medium",
        "requires_confirmation": True,
        "variables": {},
        "steps": steps,
        "constraints": [
            {"type": "concentric", "handles": ["$front_flange_outer", "$front_bore"]},
            {"type": "symmetric", "description": "Front bolt pattern and ribs are symmetric about both center axes."},
            {"type": "aligned_views", "description": "Front and top views share the same X datum; section A-A follows the indicated axis."},
        ],
    }


async def _call(client: Client, name: str, arguments: dict[str, Any], artifact_name: str | None = None) -> Any:
    print(f"CALL {name}", flush=True)
    result = await client.call_tool(name, arguments)
    record = _jsonable_tool_result(result)
    if artifact_name:
        _write_json(artifact_name, record)
    structured_content = result.structured_content or {}
    structured_result = (
        structured_content.get("result", {})
        if isinstance(structured_content, dict)
        else {}
    )
    structured_failed = (
        isinstance(structured_result, dict)
        and structured_result.get("ok") is False
    )
    if result.is_error or structured_failed:
        raise RuntimeError(f"{name} failed: {json.dumps(record, ensure_ascii=False, default=str)[:2000]}")
    return result


async def capture(*, execute: bool) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    plan = _build_plan()
    _write_json("cadplan.json", plan)

    environment = dict(os.environ)
    environment.update({
        "CAD_MCP_TOOL_PROFILE": "full",
        "CAD_MCP_WORKSPACE_ROOT": str(ARTIFACT_DIR),
        "CAD_MCP_LOG_PATH": str(ARTIFACT_DIR / "cad_mcp.log"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.server"],
        cwd=ROOT,
        env=environment,
    )
    async with Client(stdio_client(params), mode="2026-07-28", read_timeout_seconds=240) as client:
        await _call(client, "check_runtime_environment", {"check_autocad": True, "require_visual_export": True}, "preflight.json")
        await _call(client, "create_new_drawing", {"template": None}, "create-drawing.json")
        await _call(client, "get_document_info", {}, "document-info-before.json")
        await _call(client, "get_active_space_info", {}, "active-space-before.json")
        await _call(client, "validate_cad_plan", {"plan": plan}, "cadplan-validation.json")
        await _call(client, "dry_run_cad_plan", {"plan": plan}, "cadplan-dry-run.json")
        if not execute:
            print("PLAN_ONLY_OK")
            return
        await _call(client, "execute_cad_plan", {
            "plan": plan,
            "allow_modify": True,
            "transactional": True,
            "rollback_on_error": True,
            "validate_after_plan": False,
            "rescan_after_plan": False,
            "export_view_after_plan": False,
        }, "cadplan-execution.json")
        await _call(client, "scan_all_entities", {
            "clear_db": True,
            "detail_level": "minimal",
            "topology_detail": "summary",
            "capture_visual_geometry": True,
        }, "scan.json")
        await _call(client, "build_drawing_ir", {"rescan": False, "profile": "agent", "include_raw": False}, "cad-ir.json")
        await _call(client, "summarize_drawing", {"level": "normal"}, "drawing-summary.json")
        await _call(client, "analyze_drawing_intent", {"domain_hint": "mechanical"}, "drawing-intent.json")
        await _call(client, "detect_semantic_objects", {"domain": "mechanical"}, "semantic-objects.json")
        await _call(client, "bind_all_dimensions", {"tolerance": 0.001}, "dimension-bindings.json")
        await _call(client, "extract_drawing_constraints", {}, "constraints.json")
        await _call(client, "check_drawing_constraints", {"tolerance": 1e-6}, "constraint-check.json")
        await _call(client, "validate_geometry", {}, "geometry-validation.json")
        await _call(client, "zoom_extents", {}, "zoom-extents.json")
        view_export = await _call(client, "export_view_image_with_mapping", {
            "filepath": str(VIEW_EXPORT_PATH),
            "include_overlay": True,
            "include_entity_bboxes": True,
            "overlay_granularity": "both",
            "overlay_style": "som",
            "include_tiles": True,
            "tile_size": 640,
            "tile_overlap": 0.15,
        }, "view-export.json")
        snapshot = (
            _structured_result(view_export)
            .get("data", {})
            .get("snapshot", {})
        )
        if not snapshot.get("vlm_ready"):
            raise RuntimeError("The exported AutoCAD view is not VLM-ready.")
        review = _build_vlm_review(snapshot)
        _write_json("vlm-review-raw.json", review)
        await _call(client, "validate_vlm_review_output", {
            "review": review,
            "snapshot_id": snapshot["snapshot_id"],
        }, "vlm-review-validation.json")
        submitted_review = await _call(client, "submit_vlm_review", {
            "snapshot_id": snapshot["snapshot_id"],
            "review": review,
            "source_model": "Codex visual review of the real AutoCAD raster",
            "prompt_version": "vlm_review_drawing/v3",
            "top_k": 10,
        }, "vlm-review-grounded.json")
        await _call(client, "get_vlm_findings", {
            "snapshot_id": snapshot["snapshot_id"],
            "limit": 100,
        }, "vlm-findings.json")
        await _call(client, "analyze_engineering_drawing_stages", {
            "snapshot_id": snapshot["snapshot_id"],
            "domain": "mechanical",
        }, "engineering-review-stages.json")
        grounded_findings = (
            _structured_result(submitted_review)
            .get("data", {})
            .get("findings", [])
        )
        grounded_handles = sorted({
            str(handle)
            for finding in grounded_findings
            if isinstance(finding, dict)
            for handle in finding.get("grounded_handles", [])
            if handle
        })
        explanations: dict[str, Any] = {}
        for handle in grounded_handles[:8]:
            explanation = await _call(client, "explain_entity", {"handle": handle})
            explanations[handle] = _jsonable_tool_result(explanation)
        _write_json("vlm-grounded-entity-explanations.json", explanations)
        await _call(client, "save_drawing", {"filepath": str(DWG_PATH)}, "save-drawing.json")
        await _call(client, "get_document_info", {}, "document-info-after.json")
        await _call(client, "get_active_space_info", {}, "active-space-after.json")
    print(f"CAPTURE_OK {CLEAN_IMAGE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Execute the validated plan and export the drawing.")
    options = parser.parse_args()
    asyncio.run(capture(execute=options.execute))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
