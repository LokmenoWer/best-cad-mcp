"""Run real AutoCAD smoke calls for registered CAD MCP tools.

This verifier is intentionally pragmatic: it calls every registered MCP tool
through ``server.mcp.call_tool`` with generated, CAD-backed arguments, records
OK/ERROR/TIMEOUT, and keeps going when a tool fails.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pythoncom
import win32com.client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cad_utils import com_set

SKIP_TOOL_REASONS = {
    # Document/application lifecycle.
    "create_new_drawing": (
        "skipped_lifecycle",
        "Creates and activates a new DWG document; verify in an isolated document-lifecycle batch.",
    ),
    "open_drawing": (
        "skipped_lifecycle",
        "Opens and activates another DWG document; verify in an isolated document-lifecycle batch.",
    ),
    "save_drawing": (
        "skipped_lifecycle",
        "Writes/renames the active drawing; verify in an isolated document-lifecycle batch.",
    ),
    "close_drawing": (
        "skipped_lifecycle",
        "Closes the active document and can interrupt the user's AutoCAD session.",
    ),
    "restart_mcp": (
        "skipped_lifecycle",
        "Restarts the MCP server rather than validating a CAD operation.",
    ),
    "set_drawing_password": (
        "skipped_lifecycle",
        "Changes document security/global file state.",
    ),
    # Raw or interactive command paths can leave AutoCAD waiting for input.
    "send_command": (
        "skipped_interactive",
        "Raw AutoCAD command escape hatch; correctness depends on a fully closed command string.",
    ),
    "select_on_screen": (
        "skipped_interactive",
        "Calls SelectOnScreen and must wait for a human selection in AutoCAD.",
    ),
    "break_entity": (
        "skipped_interactive",
        "Uses BREAK through command input; even valid points can leave AutoCAD waiting when command state differs.",
    ),
    "stretch_entities": (
        "skipped_interactive",
        "Uses STRETCH through SendCommand with crossing-window selection; keep out of default runs until converted to a closed LISP/COM path.",
    ),
    "lengthen_entity": (
        "skipped_interactive",
        "LENGTHEN needs a near-end pick point; the current wrapper only supplies a handle and can leave AutoCAD waiting.",
    ),
    "align_entities": (
        "skipped_interactive",
        "ALIGN is command-driven and its point-pair prompt sequence is not closed reliably by the current wrapper.",
    ),
    "add_shape": (
        "skipped_precondition",
        "Requires a shape name loaded in the drawing's shape table.",
    ),
    "set_entity_plot_style": (
        "skipped_precondition",
        "Requires an STB/named plot-style drawing; the active DWG may be in CTB color-dependent plot style mode.",
    ),
    "hatch_set_gradient": (
        "skipped_precondition",
        "AutoCAD 2020 COM Hatch object does not expose SetGradient in this environment.",
    ),
    # Chain dimension commands can leave AutoCAD in a command/modal state on 2020 COM.
    "add_baseline_dimension": (
        "skipped_interactive",
        "DIMBASELINE may prompt for a base dimension unless command state already contains one.",
    ),
    "add_continue_dimension": (
        "skipped_interactive",
        "DIMCONTINUE may prompt for a continued dimension unless command state already contains one.",
    ),
    # Plot/preview/device calls may show modal UI or target real devices.
    "plot_preview": (
        "skipped_modal",
        "Can open plot preview UI.",
    ),
    "plot_to_device": (
        "skipped_modal",
        "Targets configured plot devices and may show driver/UI prompts.",
    ),
    "plot_to_file": (
        "skipped_modal",
        "Plot configuration or driver prompts can block AutoCAD unless a known noninteractive plot setup is supplied.",
    ),
    "unload_xref": (
        "skipped_precondition",
        "Requires an existing xref name created in the same isolated xref test context.",
    ),
    "reload_xref": (
        "skipped_precondition",
        "Requires an existing unloaded xref name created in the same isolated xref test context.",
    ),
    # Destructive or global operations are only safe in a dedicated sandbox.
    "delete_selection_set": (
        "skipped_destructive",
        "Deletes named selection-set state.",
    ),
    "erase_selection_entities": (
        "skipped_destructive",
        "Erases all entities in a named selection set.",
    ),
    "purge_drawing": (
        "skipped_destructive",
        "Purges global drawing definitions.",
    ),
    "undo": (
        "skipped_command_state",
        "Uses AutoCAD command input and can be consumed by an already-active command.",
    ),
    "redo": (
        "skipped_command_state",
        "Uses AutoCAD command input and can be consumed by an already-active command.",
    ),
}
DEFAULT_RISKY_TOOLS = set(SKIP_TOOL_REASONS)
VIEW_SNAPSHOT_TOOLS = {
    "get_visible_entities_in_view",
    "map_pixel_to_world",
    "map_world_to_pixel",
    "ground_vlm_region",
    "ground_vlm_overlay_id",
}
VLM_REVIEW_TOOLS = {
    "validate_vlm_review_output",
    "submit_vlm_review",
    "get_vlm_findings",
    "fuse_vlm_findings_into_semantic_graph",
    "evaluate_vlm_grounding",
    "promote_vlm_finding_to_validation_issue",
    "analyze_engineering_drawing_stages",
}
CAD_PLAN_TOOLS = {"validate_cad_plan", "dry_run_cad_plan", "execute_cad_plan"}
ERROR_TEXT_RE = re.compile(
    r"(^ERROR:|\u5931\u8d25|\u9519\u8bef|failed|exception|unable to connect|no drawing)",
    re.I,
)


def _variant_point(x: float, y: float, z: float = 0.0):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                   [float(x), float(y), float(z)])


def _variant_array(values: list[float]):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                   [float(v) for v in values])


def _dispatch_autocad():
    acad = win32com.client.GetActiveObject("AutoCAD.Application")
    acad.Visible = True
    if acad.Documents.Count == 0:
        acad.Documents.Add()
    return acad


def probe_existing_autocad(wait_seconds: float = 6.0,
                           interval: float = 0.5) -> tuple[bool, str]:
    pythoncom.CoInitialize()
    try:
        deadline = time.time() + wait_seconds
        last_error = ""
        while True:
            try:
                acad = win32com.client.GetActiveObject("AutoCAD.Application")
                count = acad.Documents.Count
                active = acad.ActiveDocument.Name if count else ""
                idle = acad.GetAcadState().IsQuiescent if count else True
                if idle:
                    return True, f"documents={count} active={active} idle={idle}"
                last_error = f"documents={count} active={active} idle={idle}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if time.time() >= deadline:
                return False, last_error
            time.sleep(interval)
    finally:
        pythoncom.CoUninitialize()


def _handle(obj: Any) -> str:
    return str(getattr(obj, "Handle", ""))


def _extract_payload(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict):
        return result[1].get("result")
    return str(result)


def _payload_message(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or "")
    return str(payload)


def _status_for_payload(tool_name: str, payload: Any) -> str:
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            return "semantic_error"
        message = _payload_message(payload)
        if ERROR_TEXT_RE.search(message):
            return "error"
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if tool_name == "export_view_image_with_mapping":
            snapshot = data.get("snapshot") if isinstance(data, dict) else {}
            image_path = str(snapshot.get("image_path") or "") if isinstance(snapshot, dict) else ""
            context_path = str(snapshot.get("context_json_path") or "") if isinstance(snapshot, dict) else ""
            export_message = str(snapshot.get("export_message") or "") if isinstance(snapshot, dict) else ""
            if (
                not isinstance(snapshot, dict)
                or not snapshot.get("snapshot_id")
                or not context_path
                or not Path(context_path).exists()
                or not image_path
                or not Path(image_path).exists()
                or ERROR_TEXT_RE.search(export_message)
            ):
                return "semantic_error"
        if tool_name == "get_visible_entities_in_view":
            if not data.get("visible_handles"):
                return "semantic_error"
        if tool_name == "map_pixel_to_world":
            if not data.get("world"):
                return "semantic_error"
        if tool_name == "map_world_to_pixel":
            if not data.get("pixel"):
                return "semantic_error"
        if tool_name == "ground_vlm_region":
            if not data.get("candidates"):
                return "semantic_error"
        if tool_name == "ground_vlm_overlay_id":
            if not data.get("candidate"):
                return "semantic_error"
        if tool_name in {"validate_vlm_review_output", "submit_vlm_review", "get_vlm_findings"}:
            if not data.get("findings"):
                return "semantic_error"
        if tool_name == "fuse_vlm_findings_into_semantic_graph":
            if not data.get("semantic_objects"):
                return "semantic_error"
        if tool_name == "evaluate_vlm_grounding":
            if not data.get("metrics"):
                return "semantic_error"
        if tool_name == "promote_vlm_finding_to_validation_issue":
            if "promoted_issues" not in data:
                return "semantic_error"
        if tool_name == "analyze_engineering_drawing_stages":
            if not data.get("interpretation"):
                return "semantic_error"
        if tool_name == "find_semantic_objects":
            if not data.get("semantic_objects"):
                return "semantic_error"
        if tool_name == "get_drawing_constraints":
            if not data.get("constraints"):
                return "semantic_error"
        if tool_name == "validate_cad_plan":
            plan_data = data if isinstance(data, dict) else {}
            if not plan_data.get("valid"):
                return "semantic_error"
        if tool_name == "dry_run_cad_plan":
            if not data.get("steps"):
                return "semantic_error"
        if tool_name == "execute_cad_plan":
            if not data.get("results"):
                return "semantic_error"
        return "ok"
    if isinstance(payload, str) and ERROR_TEXT_RE.search(payload):
        return "error"
    return "ok"


def _call_parent_tool(tool_name: str, args: dict[str, Any]) -> Any:
    pythoncom.CoInitialize()
    try:
        from src import server

        _sync_db_active_drawing_for_process()
        return _extract_payload(asyncio.run(server.mcp.call_tool(tool_name, args)))
    finally:
        pythoncom.CoUninitialize()


def _sync_db_active_drawing_for_process() -> None:
    try:
        from src.cad_controller import get_controller
        from src.cad_database import get_database

        ctrl = get_controller()
        db = get_database()
        info = ctrl.get_document_info()
        if isinstance(info, dict) and "error" not in info:
            db.activate_drawing(
                name=info.get("name", "active"),
                path=info.get("full_name") or info.get("path", ""),
            )
    except Exception:
        pass


def _prime_view_for_mapping(ctx: dict[str, Any]) -> None:
    if ctx.get("view_mapping_primed"):
        return
    bx = float(ctx.get("base_x", 1000))
    _call_parent_tool("set_active_layout", {"name": "Model"})
    pythoncom.CoInitialize()
    try:
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
        doc = acad.ActiveDocument
        viewport = doc.ActiveViewport
        viewport.Center = win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            [bx + 60.0, 55.0],
        )
        viewport.Height = 150.0
        doc.ActiveViewport = viewport
        try:
            doc.Regen(1)
        except Exception:
            pass
    finally:
        pythoncom.CoUninitialize()
    _call_parent_tool("scan_all_entities", {"clear_db": True, "max_entities": 10000})
    ctx["view_mapping_primed"] = True


def _prepare_view_snapshot(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("view_snapshot_id"):
        return ctx
    _prime_view_for_mapping(ctx)
    path = Path(ctx["tmp"]) / f"mcp_verify_view_{ctx.get('stamp', int(time.time()))}.wmf"
    payload = _call_parent_tool("export_view_image_with_mapping", {
        "filepath": str(path),
        "include_overlay": True,
        "include_entity_bboxes": True,
        "overlay_granularity": "both",
        "overlay_style": "som",
    })
    if not isinstance(payload, dict) or payload.get("ok") is False:
        ctx["view_snapshot_id"] = "MCP_VERIFY_MISSING_SNAPSHOT"
        return ctx
    snapshot = ((payload.get("data") or {}).get("snapshot") or {})
    ctx["view_snapshot_id"] = str(snapshot.get("snapshot_id") or "MCP_VERIFY_MISSING_SNAPSHOT")
    image = snapshot.get("image") or {}
    ctx["view_pixel"] = [
        float(image.get("width") or 1600) / 2.0,
        float(image.get("height") or 1000) / 2.0,
    ]
    bboxes = snapshot.get("entity_screen_bboxes") or {}
    overlay_items = snapshot.get("overlay_items") or []
    if overlay_items:
        ctx["view_overlay_id"] = str(overlay_items[0].get("overlay_id") or "")
    if bboxes:
        first_bbox = [float(value) for value in next(iter(bboxes.values()))]
        ctx["view_query_bbox"] = first_bbox
        ctx["view_pixel"] = [
            (first_bbox[0] + first_bbox[2]) / 2.0,
            (first_bbox[1] + first_bbox[3]) / 2.0,
        ]
    else:
        cx, cy = ctx["view_pixel"]
        ctx["view_query_bbox"] = [cx - 25.0, cy - 25.0, cx + 25.0, cy + 25.0]
    return ctx


def _prepare_vlm_review(ctx: dict[str, Any], submit: bool = False) -> dict[str, Any]:
    _prepare_view_snapshot(ctx)
    overlay_id = ctx.get("view_overlay_id") or "E001"
    review = {
        "findings": [
            {
                "overlay_id": overlay_id,
                "issue_type": "mcp_verify_visual_review",
                "semantic_type": "vlm_review_finding",
                "severity": "info",
                "confidence": 0.8,
                "evidence": {"reason": "Verifier synthetic VLM finding."},
            }
        ]
    }
    ctx["vlm_review_payload"] = review
    if submit and not ctx.get("vlm_review_submitted"):
        payload = _call_parent_tool("submit_vlm_review", {
            "snapshot_id": ctx.get("view_snapshot_id", ""),
            "review": review,
            "source_model": "mcp-verifier",
            "prompt_version": "verify/vlm",
        })
        ctx["vlm_review_submitted"] = bool(isinstance(payload, dict) and payload.get("ok") is not False)
    return ctx


def _cad_plan(ctx: dict[str, Any]) -> dict[str, Any]:
    bx = float(ctx.get("base_x", 1000))
    return {
        "plan_id": f"mcp_verify_plan_{ctx.get('stamp', int(time.time()))}",
        "description": "Smoke verifier safe edit plan in an isolated verification drawing.",
        "units": "drawing_units",
        "risk_level": "low",
        "requires_confirmation": False,
        "steps": [
            {
                "step_id": "draw_line_1",
                "op": "draw_line",
                "args": {
                    "start_x": bx,
                    "start_y": 260.0,
                    "end_x": bx + 12.0,
                    "end_y": 260.0,
                },
                "writes": True,
            }
        ],
    }


def _create_xref_fixture(acad: Any, active_doc: Any, xref_path: Path) -> Path:
    """Create a tiny standalone DWG that AutoCAD can attach as an xref."""
    if xref_path.exists():
        try:
            xref_path.unlink()
        except Exception:
            pass
    xref_doc = acad.Documents.Add()
    saved = False
    try:
        xref_doc.ModelSpace.AddLine(_variant_point(0, 0, 0), _variant_point(10, 0, 0))
        xref_doc.ModelSpace.AddCircle(_variant_point(5, 3, 0), 1)
        xref_doc.SaveAs(str(xref_path))
        saved = True
    finally:
        last_close_error = None
        for attempt in range(6):
            try:
                xref_doc.Close(bool(saved))
                last_close_error = None
                break
            except Exception as exc:
                last_close_error = exc
                time.sleep(0.5 * (attempt + 1))
        if last_close_error is not None:
            try:
                acad.ActiveDocument.Close(bool(saved))
                last_close_error = None
            except Exception:
                pass
        try:
            active_doc.Activate()
        except Exception:
            pass
    deadline = time.time() + 6.0
    still_open = True
    while time.time() < deadline:
        still_open = False
        try:
            doc_count = acad.Documents.Count
        except Exception:
            still_open = True
            time.sleep(0.5)
            continue
        for i in range(doc_count):
            try:
                doc = acad.Documents.Item(i)
                name = str(getattr(doc, "Name", "") or "")
                full = str(getattr(doc, "FullName", "") or "")
            except Exception:
                still_open = True
                break
            if name.lower() == xref_path.name.lower() or full.lower() == str(xref_path).lower():
                still_open = True
                break
        if not still_open:
            break
        time.sleep(0.5)
    if not xref_path.exists():
        raise FileNotFoundError(str(xref_path))
    if still_open:
        raise RuntimeError(f"xref fixture remained open in AutoCAD: {xref_path}")
    return xref_path


def _prepare_xref_fixture(ctx: dict[str, Any]) -> Path:
    path = Path(ctx.get("xref") or Path(tempfile.gettempdir()) / f"mcp_verify_xref_{int(time.time())}.dwg")
    pythoncom.CoInitialize()
    try:
        acad = _dispatch_autocad()
        doc = acad.ActiveDocument
        path = _create_xref_fixture(acad, doc, path)
        ctx["xref"] = str(path)
        return path
    finally:
        pythoncom.CoUninitialize()


def setup_context() -> dict[str, Any]:
    """Create durable helper geometry in the active AutoCAD document."""
    pythoncom.CoInitialize()
    try:
        acad = _dispatch_autocad()
        doc = acad.ActiveDocument
        selection_prefixes = (
            "MCP_VERIFY_SS_", "MCP_TEMP_SS", "MCP_AREA_SCAN",
            "MCP_POLY_SS", "MCP_PT_SS", "MCP_EXPORT_EMPTY_SS",
        )
        try:
            for i in range(doc.SelectionSets.Count - 1, -1, -1):
                try:
                    ss = doc.SelectionSets.Item(i)
                    if str(ss.Name).startswith(selection_prefixes):
                        ss.Delete()
                except Exception:
                    pass
        except Exception:
            pass
        stamp = time.time_ns() % 1_000_000_000
        image_path = Path(tempfile.gettempdir()) / "mcp_verify.png"
        if not image_path.exists():
            image_path.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
                "//8/AwAI/AL+X9c7AAAAAElFTkSuQmCC"
            ))
        tmp_dir = Path(tempfile.gettempdir())
        xref_path = tmp_dir / f"mcp_verify_xref_{stamp}.dwg"
        ms = doc.ModelSpace
        layer_name = f"MCP_VERIFY_{stamp}"
        try:
            layer = doc.Layers.Add(layer_name)
            try:
                com_set(layer, "Color", 3)
            except Exception:
                pass
        except Exception:
            layer = doc.Layers.Item("0")
            layer_name = "0"
        empty_layer_name = f"MCP_VERIFY_EMPTY_{stamp}"
        try:
            empty_layer = doc.Layers.Add(empty_layer_name)
            try:
                com_set(empty_layer, "Color", 4)
            except Exception:
                pass
        except Exception:
            empty_layer_name = layer_name

        base_x = 1000 + (stamp % 1000)
        text_value = f"MCP_VERIFY_TEXT_{stamp}"
        line1 = ms.AddLine(_variant_point(base_x, 0), _variant_point(base_x + 10, 0))
        line2 = ms.AddLine(_variant_point(base_x, 5), _variant_point(base_x + 10, 5))
        trim_target = ms.AddLine(_variant_point(base_x + 60, 0), _variant_point(base_x + 80, 0))
        trim_cutter = ms.AddLine(_variant_point(base_x + 70, -5), _variant_point(base_x + 70, 5))
        extend_target = ms.AddLine(_variant_point(base_x + 90, 0), _variant_point(base_x + 95, 0))
        extend_boundary = ms.AddLine(_variant_point(base_x + 100, -5), _variant_point(base_x + 100, 5))
        circle = ms.AddCircle(_variant_point(base_x + 25, 0), 5)
        poly = ms.AddLightWeightPolyline(_variant_array([
            base_x, 20, base_x + 20, 20, base_x + 20, 35, base_x, 35
        ]))
        poly.Closed = True
        inner_circle = ms.AddCircle(_variant_point(base_x + 10, 27), 2)
        text = ms.AddText(text_value, _variant_point(base_x, 45), 2.5)
        mtext = ms.AddMText(_variant_point(base_x, 55), 30, f"{text_value} MTEXT")
        dim = ms.AddDimAligned(_variant_point(base_x, 95), _variant_point(base_x + 10, 95),
                               _variant_point(base_x + 5, 100))
        try:
            table = ms.AddTable(_variant_point(base_x, 65), 2, 2, 3, 10)
            table_handle = _handle(table)
        except Exception:
            table_handle = ""
        block_name = f"MCP_VERIFY_BLOCK_{stamp}"
        attr_block_name = f"MCP_VERIFY_ATTR_{stamp}"
        block_ref_handle = ""
        attr_ref_handle = ""
        try:
            block = doc.Blocks.Add(_variant_point(0, 0, 0), block_name)
            block.AddLine(_variant_point(0, 0, 0), _variant_point(6, 0, 0))
            block.AddCircle(_variant_point(3, 2, 0), 1)
            block_ref = ms.InsertBlock(_variant_point(base_x + 40, 65), block_name, 1, 1, 1, 0)
            block_ref_handle = _handle(block_ref)
        except Exception:
            block_name = ""
        try:
            attr_block = doc.Blocks.Add(_variant_point(0, 0, 0), attr_block_name)
            attr_block.AddLine(_variant_point(0, 0, 0), _variant_point(8, 0, 0))
            attr_block.AddAttribute(2.5, 0, "TAG1", _variant_point(0, 3, 0), "TAG1", "VALUE1")
            attr_ref = ms.InsertBlock(_variant_point(base_x + 55, 65), attr_block_name, 1, 1, 1, 0)
            attr_ref_handle = _handle(attr_ref)
        except Exception:
            attr_block_name = ""
        try:
            box = ms.AddBox(_variant_point(base_x, 80, 5), 5, 5, 5)
            solid_handle = _handle(box)
        except Exception:
            solid_handle = _handle(line1)
        try:
            box2 = ms.AddBox(_variant_point(base_x + 8, 80, 5), 5, 5, 5)
            solid2_handle = _handle(box2)
        except Exception:
            solid2_handle = solid_handle
        region_handle = ""
        region2_handle = ""
        path_handle = ""
        try:
            region_circle = ms.AddCircle(_variant_point(base_x + 90, 80), 2)
            regions = ms.AddRegion(win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [region_circle]))
            region = regions[0] if isinstance(regions, (list, tuple)) else regions.Item(0)
            region_handle = _handle(region)
        except Exception:
            pass
        try:
            region_circle2 = ms.AddCircle(_variant_point(base_x + 100, 80), 2)
            regions2 = ms.AddRegion(win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [region_circle2]))
            region2 = regions2[0] if isinstance(regions2, (list, tuple)) else regions2.Item(0)
            region2_handle = _handle(region2)
        except Exception:
            pass
        try:
            path = ms.AddLine(_variant_point(base_x + 105, 80, 0),
                              _variant_point(base_x + 105, 80, 8))
            path_handle = _handle(path)
        except Exception:
            pass
        hatch_handle = ""
        bounded_hatch_handle = ""
        try:
            hatch = ms.AddHatch(0, "ANSI31", True)
            hatch_handle = _handle(hatch)
        except Exception:
            pass
        try:
            bounded_hatch = ms.AddHatch(0, "ANSI31", True)
            bounded_hatch.AppendOuterLoop(win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [poly]))
            bounded_hatch.Evaluate()
            bounded_hatch_handle = _handle(bounded_hatch)
        except Exception:
            pass
        selection_set_name = f"MCP_VERIFY_SS_{stamp}"
        try:
            ss = doc.SelectionSets.Add(selection_set_name)
            ss.AddItems(win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [line1]))
        except Exception:
            selection_set_name = ""
        material_name = f"MCP_VERIFY_MAT_{stamp}"
        try:
            doc.Materials.Add(material_name)
        except Exception:
            material_name = "ByLayer"
        view_name = f"MCP_VERIFY_VIEW_{stamp}"
        try:
            doc.Views.Add(view_name)
        except Exception:
            view_name = ""
        ucs_name = f"MCP_VERIFY_UCS_{stamp}"
        try:
            doc.UserCoordinateSystems.Add(
                _variant_point(base_x, 0, 0),
                _variant_point(base_x + 1, 0, 0),
                _variant_point(base_x, 1, 0),
                ucs_name)
        except Exception:
            ucs_name = ""
        hyperlink_line_handle = ""
        try:
            hyperlink_line = ms.AddLine(_variant_point(base_x + 120, 0),
                                        _variant_point(base_x + 130, 0))
            hyperlink_line.Hyperlinks.Add("https://example.com", "MCP_VERIFY", "")
            hyperlink_line_handle = _handle(hyperlink_line)
        except Exception:
            pass
        viewport_handle = ""
        try:
            viewport = doc.PaperSpace.AddPViewport(
                _variant_point(base_x + 40, 40, 0), 50, 30)
            try:
                viewport.Display(True)
            except Exception:
                pass
            viewport_handle = _handle(viewport)
        except Exception:
            pass

        for obj in [
            line1, line2, trim_target, trim_cutter, extend_target, extend_boundary,
            circle, poly, inner_circle, text, mtext, dim
        ]:
            try:
                obj.Layer = layer_name
            except Exception:
                pass

        return {
            "stamp": stamp,
            "document": getattr(doc, "Name", ""),
            "layer": layer_name,
            "empty_layer": empty_layer_name,
            "text_value": text_value,
            "base_x": base_x,
            "line": _handle(line1),
            "line2": _handle(line2),
            "trim_target": _handle(trim_target),
            "trim_cutter": _handle(trim_cutter),
            "extend_target": _handle(extend_target),
            "extend_boundary": _handle(extend_boundary),
            "circle": _handle(circle),
            "polyline": _handle(poly),
            "inner_circle": _handle(inner_circle),
            "text": _handle(text),
            "mtext": _handle(mtext),
            "dimension": _handle(dim),
            "table": table_handle,
            "solid": solid_handle,
            "solid2": solid2_handle,
            "region": region_handle,
            "region2": region2_handle,
            "path": path_handle,
            "hatch": hatch_handle,
            "bounded_hatch": bounded_hatch_handle,
            "selection_set": selection_set_name,
            "block_name": block_name,
            "block_ref": block_ref_handle,
            "attr_block_name": attr_block_name,
            "attr_ref": attr_ref_handle,
            "material_name": material_name,
            "view_name": view_name,
            "ucs_name": ucs_name,
            "hyperlink_line": hyperlink_line_handle,
            "viewport": viewport_handle,
            "handles": [_handle(line1), _handle(line2), _handle(circle), _handle(poly)],
            "tmp": tempfile.gettempdir(),
            "image": str(image_path),
            "xref": str(xref_path),
        }
    finally:
        pythoncom.CoUninitialize()


async def _list_tools() -> list[Any]:
    from src import server

    return await server.mcp.list_tools()


def list_tool_names() -> list[str]:
    return [tool.name for tool in asyncio.run(_list_tools())]


def _write_results(output: Path, results: list[dict[str, Any]]) -> None:
    payload = json.dumps({"results": results},
                         ensure_ascii=False, indent=2, default=str)
    tmp = output.with_name(f"{output.name}.{os.getpid()}.tmp")
    last_error: OSError | None = None
    for attempt in range(6):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(output)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2 * (attempt + 1))
    raise last_error or OSError(f"Could not write {output}")


def _schema_sample(schema: dict[str, Any], ctx: dict[str, Any], name: str = "") -> Any:
    if not isinstance(schema, dict):
        return "MCP_VERIFY"
    if "$ref" in schema:
        return "MCP_VERIFY"
    if "anyOf" in schema:
        choices = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _schema_sample(choices[0] if choices else schema["anyOf"][0], ctx, name)
    if "enum" in schema:
        return schema["enum"][0]
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), typ[0])
    lname = name.lower()
    if typ == "string":
        if "query" in lname:
            return "select handle,type,layer,color from cad_entities limit 5"
        if "filepath" in lname or lname.endswith("file"):
            suffix = ".dwg"
            if "pdf" in lname:
                suffix = ".pdf"
            elif "dxf" in lname:
                suffix = ".dxf"
            elif "dwf" in lname:
                suffix = ".dwf"
            return str(Path(ctx["tmp"]) / f"mcp_verify{suffix}")
        if "tool_name" in lname:
            return "draw_line"
        if "table_name" in lname:
            return "cad_entities"
        if "variable" in lname:
            return "CMDECHO"
        if "old_name" in lname:
            return ctx.get("layer", "0")
        if "new_name" in lname:
            return f"{ctx.get('layer', 'MCP_VERIFY')}_RENAMED"
        if lname == "name" or lname.endswith("_name"):
            return f"MCP_VERIFY_{int(time.time())}"
        if "handle" in lname:
            return ctx.get("line", "")
        if "pattern" in lname:
            return "MCP"
        if "command" in lname:
            return "._REGEN"
        if "intent" in lname:
            return "draw a rectangle with dimensions and hatch"
        return "MCP_VERIFY"
    if typ == "integer":
        if "color" in lname:
            return 3
        if "rows" in lname or "columns" in lname or "count" in lname:
            return 2
        return 1
    if typ == "number":
        if "scale" in lname:
            return 1.0
        if "radius" in lname:
            return 2.0
        if "angle" in lname:
            return 30.0
        if lname in {"x", "insert_x", "center_x", "base_x"}:
            return float(ctx.get("base_x", 1000))
        if lname in {"y", "insert_y", "center_y", "base_y"}:
            return 0.0
        return 1.0
    if typ == "boolean":
        return False
    if typ == "array":
        if "handles" in lname or "entity_handles" in lname:
            return ctx.get("handles", [ctx.get("line", "")])
        if "matrix" in lname:
            return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        if "data_pairs" in lname:
            return [{"code": 1000, "value": "MCP_VERIFY"}]
        if "attributes" in lname:
            return [{"tag": "TAG1", "value": "VALUE1"}]
        if "points" in lname:
            return [[ctx.get("base_x", 1000), 0, 0], [ctx.get("base_x", 1000) + 10, 10, 0]]
        item = schema.get("items", {"type": "number"})
        return [_schema_sample(item, ctx, name), _schema_sample(item, ctx, name)]
    if typ == "object":
        return {
            key: _schema_sample(value, ctx, key)
            for key, value in schema.get("properties", {}).items()
        }
    return "MCP_VERIFY"


def args_for_tool(tool: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    schema = tool.inputSchema or {}
    props = schema.get("properties", {})
    required = schema.get("required", list(props.keys()))
    args = {name: _schema_sample(props[name], ctx, name)
            for name in required if name in props}

    name = tool.name
    if name in {"draw_line"}:
        args.update({"start_x": ctx["base_x"], "start_y": 100, "end_x": ctx["base_x"] + 10, "end_y": 100})
    if name in {"draw_circle"}:
        args.update({"center_x": ctx["base_x"] + 20, "center_y": 100, "radius": 3})
    if name in {"draw_arc"}:
        args.update({"center_x": ctx["base_x"] + 30, "center_y": 100, "radius": 3,
                     "start_angle": 0, "end_angle": 90})
    if name in {"draw_ellipse"}:
        args.update({"center_x": ctx["base_x"] + 40, "center_y": 100,
                     "major_x": 10, "major_y": 0, "radius_ratio": 0.5})
    if name in {"draw_rectangle"}:
        args.update({"x1": ctx["base_x"], "y1": 110, "x2": ctx["base_x"] + 10, "y2": 120})
    if name in {"draw_polygon"}:
        args.update({"center_x": ctx["base_x"] + 50, "center_y": 100, "radius": 5, "sides": 5})
    if name in {"draw_polyline"}:
        args.update({"points": [ctx["base_x"], 130, ctx["base_x"] + 10, 130, ctx["base_x"] + 10, 140], "closed": False})
    if name in {"draw_spline"}:
        args.update({"fit_points": [ctx["base_x"], 150, 0, ctx["base_x"] + 5, 155, 0, ctx["base_x"] + 10, 150, 0]})
    if name in {"draw_3d_polyline"}:
        args.update({"points": [ctx["base_x"], 160, 0, ctx["base_x"] + 10, 160, 5, ctx["base_x"] + 20, 160, 0]})
    if name in {"draw_mline"}:
        args.update({"points": [ctx["base_x"], 170, ctx["base_x"] + 10, 170, ctx["base_x"] + 20, 175]})
    if name in {"draw_trace"}:
        args.update({"points": [ctx["base_x"], 180, 0, ctx["base_x"] + 10, 180, 0,
                                ctx["base_x"] + 10, 185, 0, ctx["base_x"], 185, 0]})
    if name in {"draw_3d_mesh"}:
        bx = ctx["base_x"]
        args.update({"m_size": 2, "n_size": 2,
                     "vertices": [bx, 230, 0, bx + 5, 230, 0,
                                  bx, 235, 2, bx + 5, 235, 2]})
    if name in {"draw_polyface_mesh"}:
        bx = ctx["base_x"]
        args.update({"vertices": [bx, 240, 0, bx + 5, 240, 0,
                                  bx + 5, 245, 0, bx, 245, 0],
                     "face_list": [1, 2, 3, 4]})
    if name in {"draw_raster_image"}:
        args.update({"filepath": ctx["image"], "insert_x": ctx["base_x"], "insert_y": 190})
    if name in {"attach_xref"}:
        _prepare_xref_fixture(ctx)
        args.update({"filepath": ctx.get("xref", str(Path(ctx["tmp"]) / "mcp_verify.dwg")),
                     "insert_x": ctx["base_x"] + 140, "insert_y": 0,
                     "insert_z": 0, "scale": 1.0, "rotation": 0.0})
    if name in {"export_pdf"}:
        args.update({"filepath": str(Path(ctx["tmp"]) / f"mcp_verify_{ctx.get('stamp', int(time.time()))}.pdf")})
    if name in {"export_dxf"}:
        args.update({"filepath": str(Path(ctx["tmp"]) / f"mcp_verify_{ctx.get('stamp', int(time.time()))}.dxf")})
    if name in {"export_dwf"}:
        args.update({"filepath": str(Path(ctx["tmp"]) / f"mcp_verify_{ctx.get('stamp', int(time.time()))}.dwf")})
    if name in {"export_image", "export_view_image"}:
        args.update({"filepath": str(Path(ctx["tmp"]) / f"mcp_verify_{ctx.get('stamp', int(time.time()))}.wmf")})
    if name in {"export_view_image_with_mapping"}:
        _prime_view_for_mapping(ctx)
        args.update({
            "filepath": str(Path(ctx["tmp"]) / f"mcp_verify_view_{ctx.get('stamp', int(time.time()))}.wmf"),
            "include_overlay": True,
            "include_entity_bboxes": True,
            "overlay_granularity": "both",
            "overlay_style": "som",
            "include_tiles": True,
        })
    if name in {"set_current_text_style"}:
        args.update({"name": "Standard"})
    if name in {"edit_table_cell"}:
        args.update({"table_handle": ctx.get("table", ""), "row": 1, "col": 1, "text": "MCP_VERIFY_CELL"})
    if name in {"find_text"}:
        args.update({"pattern": ctx.get("text_value", "MCP_VERIFY_TEXT")})
    if name in {"replace_text"}:
        args.update({"find": ctx.get("text_value", "MCP_VERIFY_TEXT"), "replace": "MCP_VERIFY_REPLACED"})
    if name in {"set_current_dimension_style"}:
        args.update({"name": "ISO-25"})
    if name in {"copy_dimension_style"}:
        args.update({"source_name": "ISO-25", "new_name": f"MCP_DIM_{int(time.time())}"})
    if name in {"set_dimension_text_override"}:
        args.update({"handle": ctx.get("dimension", ""), "text": "MCP_VERIFY_DIM"})
    if name in {"get_dimension_measurement"}:
        args.update({"handle": ctx.get("dimension", "")})
    if name in {"set_text_alignment"}:
        args.update({"handle": ctx.get("text", ""), "alignment": 1,
                     "align_x": ctx["base_x"], "align_y": 45, "align_z": 0})
    if name in {"set_text_properties"}:
        args.update({"handle": ctx.get("text", ""), "oblique_angle": 5,
                     "scale_factor": 1.0, "style_name": "Standard"})
    if name in {"delete_layer"}:
        args.update({"name": ctx.get("empty_layer", ctx.get("layer", "0"))})
    if name in {"rename_layer"}:
        args.update({"old_name": ctx.get("empty_layer", ctx.get("layer", "0")),
                     "new_name": f"{ctx.get('empty_layer', 'MCP_VERIFY_EMPTY')}_RENAMED"})
    if name in {"freeze_layer", "thaw_layer", "lock_layer", "unlock_layer",
                "turn_off_layer", "turn_on_layer", "isolate_layer"}:
        args.update({"name": ctx.get("empty_layer", ctx.get("layer", "0"))})
    if name in {"set_current_layer"}:
        args.update({"name": ctx.get("layer", "0")})
    if name in {"scan_all_entities"}:
        args.update({"clear_db": True, "max_entities": 100})
    if name in {"scan_entities_in_area", "select_by_window", "select_by_crossing"}:
        args.update({"x_min": ctx["base_x"] - 2, "y_min": -2,
                     "x_max": ctx["base_x"] + 12, "y_max": 7,
                     "x1": ctx["base_x"] - 2, "y1": -2,
                     "x2": ctx["base_x"] + 12, "y2": 7})
    if name in {"set_active_layout"}:
        args.update({"name": "Model"})
    if name in {"set_viewport_properties"}:
        args.update({"handle": ctx.get("viewport", ""),
                     "display_locked": True, "custom_scale": 1.0, "on": True})
    if name in {"restore_named_view", "delete_named_view"}:
        args.update({"name": ctx.get("view_name", "")})
    if name in {"create_ucs"}:
        bx = ctx["base_x"]
        args.update({"origin_x": bx, "origin_y": 0, "origin_z": 0,
                     "x_axis_x": bx + 1, "x_axis_y": 0, "x_axis_z": 0,
                     "y_axis_x": bx, "y_axis_y": 1, "y_axis_z": 0,
                     "name": f"MCP_UCS_{int(time.time())}"})
    if name in {"set_active_ucs"}:
        args.update({"name": ctx.get("ucs_name", "")})
    if name in {"set_variable"}:
        args.update({"variable_name": "CMDECHO", "value": "0"})
    if name in {"get_preference"}:
        args.update({"pref_path": "Display.CursorSize"})
    if name in {"set_preference"}:
        args.update({"pref_path": "Display.CursorSize", "value": "5"})
    if name in {"angle_to_real"}:
        args.update({"angle_str": "45", "unit": 0})
    if name in {"distance_to_real", "real_to_string"}:
        args.update({"dist_str": "12.5", "value": 12.5, "unit": 2, "precision": 2})
    if name in {"execute_query", "execute_sql_query"}:
        args.update({"query": "select handle,type,layer,color from cad_entities limit 10"})
    if name in {"get_table_schema"}:
        args.update({"table_name": "cad_entities"})
    if name in {"get_entity_topology", "get_entity_properties", "get_bounding_box"}:
        args.update({"handle": ctx.get("line", "")})
    if name in {"explain_entity"}:
        _call_parent_tool("scan_all_entities", {"clear_db": True, "max_entities": 10000})
        args.update({"handle": ctx.get("line", "")})
    if name in {"find_entities_by_description"}:
        args.update({"query": "line circle dimension text block", "top_k": 20})
    if name in {"find_semantic_objects"}:
        args.update({"object_type": None, "label_query": None, "top_k": 20})
    if name in {"get_drawing_constraints"}:
        args.update({"status": None})
    if name in {"propose_repair_plan"}:
        args.update({"issue_ids": []})
    if name in {"get_cad_resource"}:
        _call_parent_tool("scan_all_entities", {"clear_db": True, "max_entities": 10000})
        args.update({"uri": "cad://drawing/current/ir"})
    if name in VIEW_SNAPSHOT_TOOLS:
        _prepare_view_snapshot(ctx)
        args.update({"snapshot_id": ctx.get("view_snapshot_id", "")})
    if name in {"map_pixel_to_world"}:
        px, py = ctx.get("view_pixel", [800.0, 500.0])
        args.update({"x": px, "y": py})
    if name in {"map_world_to_pixel"}:
        args.update({"x": float(ctx.get("base_x", 1000)), "y": 0.0, "z": 0.0})
    if name in {"ground_vlm_region"}:
        args.update({"bbox": ctx.get("view_query_bbox", [775.0, 475.0, 825.0, 525.0]), "top_k": 5})
    if name in {"ground_vlm_overlay_id"}:
        args.update({"overlay_id": ctx.get("view_overlay_id", "E001")})
    if name in {"validate_vlm_review_output"}:
        _prepare_vlm_review(ctx, submit=False)
        args.update({
            "snapshot_id": ctx.get("view_snapshot_id", ""),
            "review": ctx.get("vlm_review_payload", {}),
        })
    if name in {"submit_vlm_review"}:
        _prepare_vlm_review(ctx, submit=False)
        args.update({
            "snapshot_id": ctx.get("view_snapshot_id", ""),
            "review": ctx.get("vlm_review_payload", {}),
            "source_model": "mcp-verifier",
            "prompt_version": "verify/vlm",
        })
    if name in {"get_vlm_findings", "fuse_vlm_findings_into_semantic_graph",
                "evaluate_vlm_grounding", "promote_vlm_finding_to_validation_issue",
                "analyze_engineering_drawing_stages"}:
        _prepare_vlm_review(ctx, submit=True)
    if name in {"get_vlm_findings"}:
        args.update({"snapshot_id": ctx.get("view_snapshot_id", ""), "limit": 20})
    if name in {"fuse_vlm_findings_into_semantic_graph"}:
        args.update({"min_confidence": 0.1})
    if name in {"evaluate_vlm_grounding"}:
        args.update({
            "snapshot_id": ctx.get("view_snapshot_id", ""),
            "ground_truth": [{
                "overlay_id": ctx.get("view_overlay_id", "E001"),
                "issue_type": "mcp_verify_visual_review",
                "expected_handles": [ctx.get("line", "")],
            }],
            "top_k": 3,
        })
    if name in {"promote_vlm_finding_to_validation_issue"}:
        args.update({"min_confidence": 0.1})
    if name in {"analyze_engineering_drawing_stages"}:
        args.update({"snapshot_id": ctx.get("view_snapshot_id", ""), "domain": "mechanical"})
    if name in CAD_PLAN_TOOLS:
        args.update({"plan": _cad_plan(ctx)})
    if name in {"execute_cad_plan"}:
        args.update({"allow_modify": True})
    if name in {"add_spatial_annotation"}:
        args.update({
            "label": "MCP_VERIFY_BASE_LINE",
            "target_kind": "entity",
            "handle": ctx.get("line", ""),
            "description": "Verifier model-private label for a helper line.",
            "confidence": 1.0,
        })
    if name in {"polyline_num_vertices", "polyline_get_bulge", "polyline_get_width", "polyline_get_point_at_param", "polyline_get_segment_type"}:
        args.update({"handle": ctx.get("polyline", ""), "index": 0, "seg_index": 0, "param": 0})
    if name in {"polyline_set_bulge", "polyline_set_width", "polyline_add_vertex", "polyline_constant_width", "fillet_polyline", "chamfer_polyline"}:
        args.update({"handle": ctx.get("polyline", ""), "index": 1, "seg_index": 0, "x": ctx["base_x"] + 5, "y": 145})
    if name in {"move_entity", "copy_entity", "rotate_entity", "scale_entity", "mirror_entity", "offset_entity", "explode_entity"}:
        args.update({"handle": ctx.get("line", ""), "from_point": [0, 0, 0], "to_point": [1, 1, 0],
                     "base_point": [0, 0, 0], "line_start": [0, 0, 0], "line_end": [1, 0, 0]})
    if name in {"explode_entity"}:
        args.update({"handle": ctx.get("block_ref", "")})
    if name in {"set_entity_properties"}:
        args.update({"handle": ctx.get("line", ""), "color": 2})
    if name in {"break_entity"}:
        args.update({"handle": ctx.get("line", ""), "point1_x": ctx["base_x"] + 5,
                     "point1_y": 0, "point1_z": 0})
    if name in {"trim_entity"}:
        args.update({"trim_handle": ctx.get("trim_target", ""),
                     "cutting_handles": [ctx.get("trim_cutter", "")]})
    if name in {"extend_entity"}:
        args.update({"extend_handle": ctx.get("extend_target", ""),
                     "boundary_handles": [ctx.get("extend_boundary", "")]})
    if name in {"lengthen_entity"}:
        args.update({"handle": ctx.get("line", ""), "mode": "delta", "value": 2.0, "end": "end"})
    if name in {"stretch_entities"}:
        args.update({"x1": ctx["base_x"] - 1, "y1": -1, "x2": ctx["base_x"] + 2, "y2": 1,
                     "from_x": ctx["base_x"], "from_y": 0, "from_z": 0,
                     "to_x": ctx["base_x"], "to_y": 3, "to_z": 0})
    if name in {"array_rectangular", "array_polar"}:
        args.update({"handle": ctx.get("line", ""), "rows": 2, "columns": 2, "row_spacing": 5,
                     "column_spacing": 5, "count": 3, "fill_angle": 180, "center_x": ctx["base_x"], "center_y": 0})
    if name in {"delete_entity"}:
        args.update({"handle": ctx.get("line2", "")})
    if name in {"delete_entities"}:
        args.update({"handles": [ctx.get("line2", "")]})
    if name in {"highlight_entity", "reset_entity_color"}:
        args.update({"handle": ctx.get("line", ""), "color": 1, "original_color": 256})
    if name in {"highlight_entities", "create_group", "add_qdim"}:
        args.update({"handles": ctx.get("handles", []), "entity_handles": ctx.get("handles", [])})
    if name in {"hatch_add_boundary", "hatch_add_inner_loop"}:
        args.update({"hatch_handle": ctx.get("hatch", ""),
                     "handle": ctx.get("hatch", ""),
                     "boundary_handles": [ctx.get("polyline", "")],
                     "inner_handles": [ctx.get("inner_circle", "")]})
    if name in {"hatch_add_inner_loop"}:
        args.update({"handle": ctx.get("bounded_hatch", "")})
    if name in {"add_hatch"}:
        args.update({"pattern_name": "ANSI31", "associativity": True})
    if name in {"hatch_set_properties", "hatch_get_properties", "hatch_set_gradient"}:
        args.update({"hatch_handle": ctx.get("bounded_hatch", ""), "handle": ctx.get("bounded_hatch", ""),
                     "pattern_scale": 1.0, "pattern_angle": 0.0})
    if name in {"clear_selection_set"}:
        args.update({"ss_name": ctx.get("selection_set", "MCP_VERIFY_SS")})
    if name in {"solid_boolean", "check_interference", "intersect_with"}:
        args.update({"target_handle": ctx.get("solid", ""), "tool_handle": ctx.get("solid2", ""),
                     "handle1": ctx.get("line", ""), "handle2": ctx.get("line2", "")})
    if name in {"solid_boolean"}:
        args.update({"operation": "union"})
    if name in {"check_interference"}:
        args.update({"handle1": ctx.get("solid", ""), "handle2": ctx.get("solid2", ""),
                     "create_solid": False})
    if name in {"intersect_with"}:
        args.update({"handle1": ctx.get("line", ""), "handle2": ctx.get("trim_cutter", ""),
                     "extend_option": 0})
    if name in {"add_region"}:
        args.update({"entity_handles": [ctx.get("polyline", "")]})
    if name in {"extrude_region"}:
        args.update({"region_handle": ctx.get("region", ""), "height": 5.0, "taper_angle": 0.0})
    if name in {"extrude_region_along_path"}:
        args.update({"region_handle": ctx.get("region", ""), "path_handle": ctx.get("path", "")})
    if name in {"revolve_region"}:
        args.update({"region_handle": ctx.get("region", ""), "axis_x": ctx["base_x"] + 85,
                     "axis_y": 75, "axis_z": 0, "dir_x": 0, "dir_y": 1, "dir_z": 0,
                     "angle": 360})
    if name in {"rotate_3d", "mirror_3d", "transform_entity", "slice_solid", "section_solid"}:
        args.update({"handle": ctx.get("solid", ""),
                     "axis_point1": [ctx["base_x"], 80, 0],
                     "axis_point2": [ctx["base_x"], 80, 10],
                     "axis_x1": ctx["base_x"], "axis_y1": 80, "axis_z1": 0,
                     "axis_x2": ctx["base_x"], "axis_y2": 80, "axis_z2": 10,
                     "angle": 15,
                     "p1": [ctx["base_x"], 80, 0],
                     "p2": [ctx["base_x"] + 1, 80, 0],
                     "p3": [ctx["base_x"], 81, 0],
                     "p1_x": ctx["base_x"], "p1_y": 80, "p1_z": 0,
                     "p2_x": ctx["base_x"] + 1, "p2_y": 80, "p2_z": 0,
                     "p3_x": ctx["base_x"], "p3_y": 81, "p3_z": 0,
                     "matrix": [[1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]})
    if name in {"select_by_fence"}:
        args.update({"points": [ctx["base_x"] - 5, -1, 0,
                                ctx["base_x"] + 5, 1, 0,
                                ctx["base_x"] + 15, -1, 0]})
    if name in {"select_by_wpolygon", "select_by_cpolygon"}:
        bx = ctx["base_x"]
        args.update({"points": [bx - 5, -5, 0, bx + 45, -5, 0, bx + 45, 40, 0, bx - 5, 40, 0]})
    if name in {"set_entity_material"}:
        args.update({"handle": ctx.get("solid", ""), "material_name": ctx.get("material_name", "ByLayer")})
    if name in {"set_active_material"}:
        args.update({"material_name": ctx.get("material_name", "ByLayer")})
    if name in {"load_linetype"}:
        args.update({"name": "CENTER", "filename": "acad.lin"})
    if name in {"remove_hyperlink"}:
        args.update({"handle": ctx.get("hyperlink_line", ""), "index": 0})
    if name in {"draw_wipeout"}:
        args.update({"p1_x": ctx["base_x"], "p1_y": 210,
                     "p2_x": ctx["base_x"] + 12, "p2_y": 210,
                     "p3_x": ctx["base_x"] + 12, "p3_y": 218,
                     "p4_x": ctx["base_x"], "p4_y": 218})
    if name in {"insert_minsert_block", "insert_minert_block"}:
        args.update({"block_name": ctx.get("block_name", ""), "x": ctx["base_x"] + 35, "y": 90})
    if name in {"divide_entity"}:
        args.update({"handle": ctx.get("line", ""), "segments": 2})
    if name in {"create_block"}:
        args.update({"name": f"MCP_CREATED_BLOCK_{int(time.time())}",
                     "base_x": ctx["base_x"], "base_y": 0, "base_z": 0,
                     "entity_handles": [ctx.get("line", ""), ctx.get("circle", "")]})
    if name in {"insert_block"}:
        args.update({"name": ctx.get("block_name", ""), "x": ctx["base_x"] + 40, "y": 90})
    if name in {"explode_block"}:
        args.update({"handle": ctx.get("block_ref", "")})
    if name in {"insert_block_with_attributes"}:
        args.update({"block_name": ctx.get("attr_block_name", ""),
                     "x": ctx["base_x"] + 55, "y": 90,
                     "attributes": [{"tag": "TAG1", "value": "VALUE2"}]})
    if name in {"get_block_attributes", "set_block_attribute"}:
        args.update({"handle": ctx.get("attr_ref", ""), "tag": "TAG1", "value": "VALUE3"})
    return args


async def call_one(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    pythoncom.CoInitialize()
    try:
        from src import server

        _sync_db_active_drawing_for_process()
        start = time.time()
        result = await server.mcp.call_tool(tool_name, args)
        elapsed = round(time.time() - start, 3)
        payload = _extract_payload(result)
        status = _status_for_payload(tool_name, payload)
        return {"tool": tool_name, "status": status, "elapsed": elapsed,
                "args": args, "result": payload}
    except BaseException as exc:
        return {"tool": tool_name, "status": "exception", "args": args,
                "error_type": type(exc).__name__, "error": str(exc)}
    finally:
        pythoncom.CoUninitialize()


def run_child(tool_name: str, args_path: Path) -> None:
    args = json.loads(args_path.read_text(encoding="utf-8"))
    print(json.dumps(asyncio.run(call_one(tool_name, args)),
                     ensure_ascii=False, default=str))


def run_driver(output: Path, limit: int | None, timeout: int,
               include_risky: bool = False, only: list[str] | None = None) -> int:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tools = asyncio.run(_list_tools())
    if only:
        wanted = set(only)
        tools = [tool for tool in tools if tool.name in wanted]
    if limit:
        tools = tools[:limit]
    results: list[dict[str, Any]] = []
    if output.exists():
        try:
            results = json.loads(output.read_text(encoding="utf-8")).get("results", [])
        except Exception:
            results = []
    while results and (
        results[-1].get("status", "").startswith("paused_")
        or results[-1].get("status") == "timeout"
    ):
        results.pop()
    completed = len(results)
    args_dir = output.parent / f"{output.stem}_args"
    args_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for index, tool in enumerate(tools, 1):
        if index <= completed:
            continue
        if tool.name in DEFAULT_RISKY_TOOLS and not include_risky:
            status, reason = SKIP_TOOL_REASONS[tool.name]
            result = {
                "tool": tool.name,
                "status": status,
                "reason": reason,
            }
            results.append(result)
            _write_results(output, results)
            print(f"[{index:03d}/{len(tools):03d}] {tool.name}: {status}")
            continue
        ready, ready_detail = probe_existing_autocad()
        if not ready:
            result = {
                "tool": tool.name,
                "status": "paused_autocad_busy",
                "reason": "Existing AutoCAD instance is not accepting COM calls; verification paused without opening a new process.",
                "detail": ready_detail,
            }
            results.append(result)
            _write_results(output, results)
            print(f"[{index:03d}/{len(tools):03d}] {tool.name}: paused_autocad_busy")
            return 2
        setup_error = None
        for setup_attempt in range(4):
            try:
                ctx = setup_context()
                setup_error = None
                break
            except Exception as exc:
                setup_error = exc
                time.sleep(0.75 * (setup_attempt + 1))
        else:
            ctx = {}
        if setup_error is not None:
            result = {
                "tool": tool.name,
                "status": "paused_setup_failed",
                "reason": "Could not create helper geometry in the existing AutoCAD instance; verification paused.",
                "error_type": type(setup_error).__name__,
                "error": str(setup_error),
            }
            results.append(result)
            _write_results(output, results)
            print(f"[{index:03d}/{len(tools):03d}] {tool.name}: paused_setup_failed")
            return 2
        try:
            args = args_for_tool(tool, ctx)
        except Exception as exc:
            result = {
                "tool": tool.name,
                "status": "paused_args_failed",
                "reason": "Could not generate verifier arguments for this tool.",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "context": ctx,
            }
            results.append(result)
            _write_results(output, results)
            print(f"[{index:03d}/{len(tools):03d}] {tool.name}: paused_args_failed")
            return 2
        args_file = args_dir / f"{index:03d}_{tool.name}.json"
        args_file.write_text(json.dumps(args, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [sys.executable, str(Path(__file__).resolve()), "--call-one", tool.name,
               "--args-file", str(args_file)]
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout)
            line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            result = json.loads(line) if line.startswith("{") else {
                "tool": tool.name, "status": "harness_error",
                "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            result = {"tool": tool.name, "status": "timeout", "args": args,
                      "timeout": timeout, "stdout": (exc.stdout or "")[-1000:],
                      "stderr": (exc.stderr or "")[-1000:],
                      "reason": "The real AutoCAD call did not return before the timeout; verification paused to avoid cascading into a command prompt that may still be waiting for input."}
        result["wall_elapsed"] = round(time.time() - started, 3)
        result["context"] = ctx
        results.append(result)
        _write_results(output, results)
        print(f"[{index:03d}/{len(tools):03d}] {tool.name}: {result['status']}")
        if result["status"] == "timeout":
            return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "autocad_mcp_verify_results.json"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-risky", action="store_true",
                        help="Also run tools that may block AutoCAD, alter global state, or show UI.")
    parser.add_argument("--only", nargs="*", help="Run only these tool names.")
    parser.add_argument("--call-one")
    parser.add_argument("--args-file")
    args = parser.parse_args()

    if args.call_one:
        run_child(args.call_one, Path(args.args_file))
        return 0
    return run_driver(Path(args.output), args.limit, args.timeout,
                      args.include_risky, args.only)


if __name__ == "__main__":
    raise SystemExit(main())
